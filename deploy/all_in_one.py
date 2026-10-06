#!/usr/bin/env python3
"""Run Muneer's model server and app together, in one container.

The all-in-one image (deploy/all-in-one.Dockerfile) starts here, so a single
RunPod pod serves the whole demo at one public address. This script:

1. reads the settings: .env.example gives the defaults, and the container's
   environment variables (the pod's settings) override them;
2. starts the model server (vLLM, or the mock with MODEL_SERVER=mock) on
   127.0.0.1 only, with an API key made fresh at every start, so the model
   can't be reached from outside the container. If the pod sets
   MUNEER_API_KEY, the server listens on every address instead, behind that
   key, so the pod can publish its port for direct calls, such as an
   evaluation;
3. starts the Gradio app as an unprivileged user, pointed at that server;
4. starts either one again if it stops, or stops answering, and stops both
   when the container stops.

The app faces the internet, so it gets only the settings it uses, never the
Hugging Face token, and the folders holding the weights are closed to its
user. The model server never sees the OpenAI key.

Usage (inside the image):  python deploy/all_in_one.py
"""

from __future__ import annotations

import http.client
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # /opt/muneer in the image
DEFAULTS_FILE = ROOT / ".env.example"
WORKSPACE = Path("/workspace")  # RunPod's persistent volume
APP_USER = "muneer"  # created by the Dockerfile
MODEL_HOST = "127.0.0.1"
OPEN_MODEL_HOST = "0.0.0.0"  # when the pod sets MUNEER_API_KEY
MIN_API_KEY_CHARS = 32  # an open model API faces the internet

# Secrets each process is kept from. RunPod gives every container a
# RUNPOD_API_KEY scoped to the pod; nothing here needs it.
HF_SECRETS = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
APP_SECRETS = ("OPENAI_API_KEY", "ADMIN_PASSCODE")
PLATFORM_SECRETS = ("RUNPOD_API_KEY",)

# Besides the settings named in .env.example, the app and the mock get only
# these variables from the container, so a secret added to the pod later
# doesn't reach them.
RUNTIME_VARIABLES = {"PATH", "LANG", "LANGUAGE", "TZ", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}
RUNTIME_PREFIXES = ("LC_", "PYTHON", "GRADIO_", "OPENAI_")

# Folders with the weights and the download caches, closed to the app's user.
PRIVATE_DIRS = ("MUNEER_DOWNLOAD_DIR", "MUNEER_MODEL_PATH", "HF_HOME", "VLLM_CACHE_ROOT")

# Time to stop before being killed. docker stop waits 10 s, and a health check
# under way when the stop arrives may use up to HEALTH_TIMEOUT of that.
GRACE_SECONDS = 7
STABLE_SECONDS = 600  # a process that ran this long restarts without extra delay
HEALTH_INTERVAL = 10  # seconds between checks that both processes answer
HEALTH_TIMEOUT = 2
UNHEALTHY_CHECKS = 6  # misses in a row, once it has answered, before a restart


class ConfigError(Exception):
    """The settings are missing or invalid."""


def log(message: str) -> None:
    print(f"[all-in-one] {message}", flush=True)


def _text(settings: Mapping[str, str], name: str, default: str | None = None) -> str | None:
    value = settings.get(name, "").strip()
    return value or default


def _without(settings: Mapping[str, str], names: Iterable[str]) -> dict[str, str]:
    names = set(names)
    return {key: value for key, value in settings.items() if key not in names}


def load_settings(environ: Mapping[str, str], defaults_file: Path = DEFAULTS_FILE) -> dict[str, str]:
    """.env.example's values, overridden by the container's environment."""
    defaults: dict[str, str] = {}
    if defaults_file.is_file():
        from dotenv import dotenv_values

        defaults = {key: value for key, value in dotenv_values(defaults_file).items() if value is not None}
    return {**defaults, **environ}


def setting_names(defaults_file: Path = DEFAULTS_FILE) -> set[str]:
    """Every setting .env.example names, including the commented-out ones."""
    if not defaults_file.is_file():
        return set()
    pattern = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)
    return set(pattern.findall(defaults_file.read_text(encoding="utf-8")))


def _app_side(settings: Mapping[str, str], known: set[str]) -> dict[str, str]:
    """The settings the app and the mock may see."""
    return {
        key: value
        for key, value in settings.items()
        if key in known or key in RUNTIME_VARIABLES or key.startswith(RUNTIME_PREFIXES)
    }


def check_settings(settings: Mapping[str, str]) -> str:
    """Fail early, with a clear message, on settings the processes can't run without.

    Returns the model server to run: "vllm" or "mock".
    """
    model_server = (_text(settings, "MODEL_SERVER") or "vllm").lower()
    if model_server not in ("vllm", "mock"):
        raise ConfigError(f"MODEL_SERVER must be vllm or mock, got {model_server!r}.")
    # If a pod refers to a secret that doesn't exist, the reference itself may
    # arrive as the value.
    unresolved = sorted(name for name, value in settings.items() if "RUNPOD_SECRET_" in value)
    if unresolved:
        raise ConfigError(
            f"{', '.join(unresolved)} {'holds' if len(unresolved) == 1 else 'hold'} a RunPod secret "
            "reference instead of its value. Create the secret in RunPod (Secrets), with exactly "
            "the name the variable refers to."
        )
    for old, new in (("REVIEW_QUEUE_PASSCODE", "ADMIN_PASSCODE"), ("REVIEW_QUEUE_PATH", "DATABASE_PATH")):
        if _text(settings, old):
            raise ConfigError(f"{old} is now called {new}; rename it in the pod's settings.")
    if not _text(settings, "OPENAI_API_KEY"):
        raise ConfigError("Set OPENAI_API_KEY: the guardrail and the simulated da'i need it.")
    if model_server == "vllm" and not (_text(settings, "MUNEER_HF_REPO") or _text(settings, "MUNEER_MODEL_PATH")):
        raise ConfigError(
            "Set MUNEER_HF_REPO (the private Hugging Face repo with the weights) and HF_TOKEN. "
            "To try the image without the model, set MODEL_SERVER=mock."
        )
    if (_text(settings, "MUNEER_PORT") or "8000") == (_text(settings, "APP_PORT") or "7860"):
        raise ConfigError("MUNEER_PORT and APP_PORT must differ.")
    api_key = _text(settings, "MUNEER_API_KEY")
    if api_key and len(api_key) < MIN_API_KEY_CHARS:
        raise ConfigError(
            f"MUNEER_API_KEY opens the model's API, so it must be at least {MIN_API_KEY_CHARS} characters. "
            "Leave it empty to keep the model inside the pod."
        )
    return model_server


@dataclass
class Service:
    name: str
    command: list[str]
    env: dict[str, str]
    cwd: Path | None = None
    user: str | None = None  # run as this user; needs the script to run as root
    health_url: str | None = None
    process: subprocess.Popen | None = None
    started_at: float = 0.0
    failures: int = 0
    next_start: float = 0.0
    answered: bool = False  # has answered health checks since it started
    misses: int = 0
    stopping_since: float | None = None


@dataclass
class Plan:
    services: list[Service]
    database: Path
    public_url: str | None = None
    model_server: str = "vllm"  # or "mock"
    model_api: str | None = None  # where the model's API is reached, when the pod opens it


def database_path(settings: Mapping[str, str], workspace: Path = WORKSPACE) -> Path:
    # A relative path, such as .env.example's data/muneer.sqlite3, lands on the
    # persistent volume.
    path = Path(_text(settings, "DATABASE_PATH") or "data/muneer.sqlite3")
    return (path if path.is_absolute() else workspace / path).resolve()


def private_dirs(settings: Mapping[str, str]) -> list[Path]:
    return [Path(value) for name in PRIVATE_DIRS if (value := _text(settings, name))]


def check_database(database: Path, settings: Mapping[str, str], workspace: Path = WORKSPACE) -> None:
    """The database's folder is handed to the app's user, so it must be its own.

    It must sit inside the volume, and share nothing with the weights or caches.
    """
    folder = database.parent
    if workspace.resolve() not in folder.parents:
        raise ConfigError(f"DATABASE_PATH must be in a folder of its own under {workspace}, such as data/muneer.sqlite3.")
    for private in private_dirs(settings):
        private = private.resolve()
        if folder == private or private in folder.parents or folder in private.parents:
            raise ConfigError(f"DATABASE_PATH must not share a folder with {private}.")


def public_url(settings: Mapping[str, str]) -> str | None:
    pod = _text(settings, "RUNPOD_POD_ID")
    return f"https://{pod}-{_text(settings, 'APP_PORT') or '7860'}.proxy.runpod.net" if pod else None


def model_api(settings: Mapping[str, str]) -> str | None:
    """Where callers reach the model's API, or None while it stays inside the container."""
    if not _text(settings, "MUNEER_API_KEY"):
        return None
    port = _text(settings, "MUNEER_PORT") or "8000"
    pod = _text(settings, "RUNPOD_POD_ID")
    return f"https://{pod}-{port}.proxy.runpod.net/v1" if pod else f"port {port} of the container"


def build_plan(
    settings: Mapping[str, str],
    fresh_key: str,
    *,
    python: str = sys.executable,
    workspace: Path = WORKSPACE,
    user: str | None = None,
    home: str | None = None,
    known: set[str] | None = None,
) -> Plan:
    """The two processes to run, each with its own settings."""
    model_server = check_settings(settings)
    known = setting_names() if known is None else known
    model_port = _text(settings, "MUNEER_PORT") or "8000"
    app_port = _text(settings, "APP_PORT") or "7860"
    model_name = _text(settings, "MUNEER_SERVED_MODEL_NAME") or "muneer"
    as_user = {"HOME": home} if home else {}

    # The model server listens on 127.0.0.1 only, and the fresh key is a second
    # lock. A key set on the pod opens it on every address, behind that key; the
    # pod must also publish the port.
    api_key = _text(settings, "MUNEER_API_KEY") or fresh_key
    model_host = OPEN_MODEL_HOST if model_api(settings) else MODEL_HOST
    link = {"MUNEER_API_KEY": api_key, "MUNEER_PORT": model_port}
    model_health = f"http://{MODEL_HOST}:{model_port}/health"
    if model_server == "vllm":
        # vLLM keeps root and the container's whole environment (CUDA and so
        # on), as in the server image. The launcher drops HF_TOKEN before it
        # starts vLLM.
        model = Service(
            "model server",
            ["python3", str(ROOT / "server" / "launch.py")],
            env=_without(settings, APP_SECRETS + PLATFORM_SECRETS) | link | {"MUNEER_HOST": model_host},
            health_url=model_health,
        )
    else:
        model = Service(
            "mock model server",
            [python, "-m", "mock_muneer", "--host", model_host, "--port", model_port],
            env=_without(_app_side(settings, known), HF_SECRETS + APP_SECRETS + PLATFORM_SECRETS)
            | link
            | {"MUNEER_MODEL": model_name}
            | as_user,
            cwd=ROOT,
            user=user,
            health_url=model_health,
        )

    database = database_path(settings, workspace)
    check_database(database, settings, workspace)
    app = Service(
        "app",
        [python, "-m", "app"],
        env=_without(_app_side(settings, known), HF_SECRETS + PLATFORM_SECRETS)
        | {
            "MUNEER_BASE_URL": f"http://{MODEL_HOST}:{model_port}/v1",
            "MUNEER_API_KEY": api_key,
            "MUNEER_MODEL": model_name,
            "APP_HOST": "0.0.0.0",  # reachable through the pod's HTTP port
            "APP_PORT": app_port,
            "DATABASE_PATH": str(database),
        }
        | as_user,
        cwd=ROOT,
        user=user,
        health_url=f"http://127.0.0.1:{app_port}/",
    )
    return Plan(
        services=[model, app],
        database=database,
        public_url=public_url(settings),
        model_server=model_server,
        model_api=model_api(settings),
    )


def app_account() -> tuple[str | None, str | None]:
    """The user and home folder to run the app as, or (None, None) when not root."""
    if os.geteuid() != 0:
        return None, None
    import pwd

    try:
        return APP_USER, pwd.getpwnam(APP_USER).pw_dir
    except KeyError:
        raise ConfigError(f"The image has no {APP_USER!r} user to run the app as.") from None


def give_database_folder_to(user: str, database: Path) -> None:
    """Create the database's folder and hand it to the user.

    Only the folder: the app's user can write in it, so root must never follow
    a link found there. Some network storage won't give a folder to another
    user (a RunPod volume can be NFS, where this fails with "Operation not
    permitted"); the folder is then opened to every user instead. The app's
    user is the only one besides root that runs here.
    """
    import pwd

    account = pwd.getpwnam(user)
    folder = database.parent
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chown(folder, account.pw_uid, account.pw_gid, follow_symlinks=False)
    except OSError as err:
        if folder.is_symlink():
            raise
        folder.chmod(0o777)
        log(f"This storage won't give {folder} to the app's user ({err}), so it is open to every user instead.")


def close_private_dirs(settings: Mapping[str, str]) -> None:
    """Make the weights and caches root's alone, so the app's user can't read them."""
    for folder in private_dirs(settings):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if folder.is_symlink():
                log(f"warning: {folder} is a link; leaving it as it is.")
                continue
            os.chown(folder, 0, 0, follow_symlinks=False)
            folder.chmod(0o700)
        except OSError as err:  # e.g. weights mounted read-only
            log(f"warning: couldn't close {folder} to the app's user: {err}")


def restart_delay(failures: int) -> int:
    """Seconds to wait before starting a stopped process again: 5, 10, 20… up to 5 minutes."""
    return min(5 * 2 ** (max(failures, 1) - 1), 300)


def is_healthy(url: str, timeout: float = HEALTH_TIMEOUT) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, http.client.HTTPException):  # refused, timed out, HTTP errors
        return False


def _signal(pid: int, signum: int) -> None:
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        pass


def _kill_group(pid: int) -> None:
    # Each process leads its own group, so this also ends its workers.
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


class Supervisor:
    def __init__(
        self,
        services: list[Service],
        *,
        public_url: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        grace: float = GRACE_SECONDS,
    ):
        self.services = services
        self.public_url = public_url
        self.clock = clock
        self.grace = grace
        self.ready = False
        self._next_health_check = 0.0
        self._should_stop: Callable[[], bool] = lambda: False

    def run(self, should_stop: Callable[[], bool], poll: float = 1.0) -> None:
        self._should_stop = should_stop
        try:
            while not should_stop():
                self.tick()
                if not should_stop():
                    time.sleep(poll)
        finally:
            self.stop()

    def tick(self) -> None:
        now = self.clock()
        for service, code in self._reap():
            log(f"The {service.name} stopped (exit code {code}); starting it again in {self._schedule_restart(service, now)} s.")
        for service in self.services:
            if service.process is None and now >= service.next_start:
                self._start(service, now)
        self._check_health(now)

    def stop(self) -> None:
        for service in self.services:
            if service.process:
                log(f"Stopping the {service.name}.")
                _signal(service.process.pid, signal.SIGTERM)
        deadline = self.clock() + self.grace
        while any(service.process for service in self.services) and self.clock() < deadline:
            self._reap()
            time.sleep(0.1)
        self._reap()
        for service in self.services:
            if service.process:
                log(f"The {service.name} didn't stop within {self.grace:g} s; killing it.")
                pid = service.process.pid
                _kill_group(pid)
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
                service.process.returncode = -signal.SIGKILL
                service.process = None

    def _schedule_restart(self, service: Service, now: float) -> int:
        service.failures = 1 if now - service.started_at >= STABLE_SECONDS else service.failures + 1
        delay = restart_delay(service.failures)
        service.next_start = now + delay
        return delay

    def _start(self, service: Service, now: float) -> None:
        service.started_at = now
        service.answered, service.misses, service.stopping_since = False, 0, None
        try:
            options = {}
            if service.user:
                import pwd

                account = pwd.getpwnam(service.user)
                options = {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": []}
            service.process = subprocess.Popen(
                service.command,
                env=service.env,
                cwd=service.cwd,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # its own process group, so its workers stop with it
                **options,
            )
        except (OSError, subprocess.SubprocessError, KeyError) as err:
            # e.g. out of memory: try again later, like a process that stopped.
            log(f"Couldn't start the {service.name} ({err}); trying again in {self._schedule_restart(service, now)} s.")
            return
        log(f"Started the {service.name} (pid {service.process.pid}).")

    def _reap(self) -> list[tuple[Service, int]]:
        """Collect every child that has exited; return our services among them.

        As the container's first process, this script also inherits processes
        orphaned by the others, and collects those too.
        """
        exited = []
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            for service in self.services:
                if service.process and service.process.pid == pid:
                    code = os.waitstatus_to_exitcode(status)
                    service.process.returncode = code
                    service.process = None
                    _kill_group(pid)  # workers it left behind
                    exited.append((service, code))
        return exited

    def _check_health(self, now: float) -> None:
        """Announce when both answer, and restart one that stops answering."""
        if now < self._next_health_check:
            return
        self._next_health_check = now + HEALTH_INTERVAL
        for service in self.services:
            if self._should_stop():  # a check can take a while; stopping comes first
                return
            if not service.process or not service.health_url:
                continue
            if service.stopping_since is not None:
                if now - service.stopping_since >= self.grace:
                    log(f"The {service.name} didn't stop within {self.grace:g} s; killing it.")
                    _kill_group(service.process.pid)
            elif is_healthy(service.health_url):
                service.answered, service.misses = True, 0
            elif service.answered:  # still starting until it first answers
                service.misses += 1
                if service.misses >= UNHEALTHY_CHECKS:
                    log(f"The {service.name} stopped answering; restarting it.")
                    _signal(service.process.pid, signal.SIGTERM)
                    service.stopping_since = self.clock()  # the check itself may have taken a while
        ready = all(
            service.process and service.answered and service.misses == 0 and service.stopping_since is None
            for service in self.services
            if service.health_url
        )
        if ready and not self.ready:
            log(f"Muneer is ready: {self.public_url}" if self.public_url else "Muneer is ready.")
        self.ready = ready


def main() -> int:
    try:
        settings = load_settings(os.environ)
        user, home = app_account()
        plan = build_plan(settings, secrets.token_urlsafe(32), user=user, home=home)
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    try:
        if user:
            close_private_dirs(settings)
            give_database_folder_to(user, plan.database)
        else:
            plan.database.parent.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        log(f"warning: couldn't prepare the database's folder ({err}); conversations and flagged replies may not be saved.")

    stopping = False

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    if plan.model_server == "vllm":
        log("Starting Muneer. The first start downloads the weights, which takes a while.")
    else:
        log("Starting Muneer with the mock model server (scripted replies, no GPU).")
    if plan.model_api:
        log(f"The model's API is open to callers with MUNEER_API_KEY, at {plan.model_api} (the pod must publish the port).")
    Supervisor(plan.services, public_url=plan.public_url).run(lambda: stopping)
    log("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
