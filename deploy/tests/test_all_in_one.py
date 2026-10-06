import os
import signal
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy import all_in_one
from deploy.all_in_one import (
    ConfigError,
    Service,
    Supervisor,
    build_plan,
    check_settings,
    close_private_dirs,
    database_path,
    give_database_folder_to,
    load_settings,
    model_api,
    public_url,
    restart_delay,
    setting_names,
)

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="runs in the Linux container")
root_only = pytest.mark.skipif(sys.platform == "win32" or os.geteuid() != 0, reason="changes file owners")

POD = {
    "MUNEER_HF_REPO": "someone/muneer-14b",
    "HF_TOKEN": "hf_secret",
    "OPENAI_API_KEY": "sk-secret",
    "ADMIN_PASSCODE": "passcode",
    "RUNPOD_API_KEY": "pod-scoped",
    "RUNPOD_POD_ID": "abc123",
    "PATH": "/usr/bin",
}
OPEN_KEY = "k" * 32  # the shortest key that opens the model's API


def plan_for(settings, **kwargs):
    return build_plan(settings, "internal-key", python="/venv/python", workspace=Path("/workspace"), **kwargs)


# ---- settings ----


def test_pod_settings_override_the_env_example_defaults(tmp_path):
    defaults = tmp_path / ".env.example"
    defaults.write_text('PLACEHOLDERS={"org_name": "Example"}\nMAX_MESSAGE_CHARS=2000\nOPENAI_API_KEY=\n')
    settings = load_settings({"MAX_MESSAGE_CHARS": "500", "OPENAI_API_KEY": "sk-x"}, defaults)
    assert settings["PLACEHOLDERS"] == '{"org_name": "Example"}'
    assert settings["MAX_MESSAGE_CHARS"] == "500"
    assert settings["OPENAI_API_KEY"] == "sk-x"


def test_no_defaults_file_is_fine(tmp_path):
    assert load_settings({"A": "1"}, tmp_path / "missing") == {"A": "1"}


def test_the_image_reads_the_repository_defaults():
    # The Dockerfile copies .env.example next to the script's parent folder.
    assert all_in_one.DEFAULTS_FILE == Path(all_in_one.__file__).resolve().parent.parent / ".env.example"
    assert all_in_one.DEFAULTS_FILE.is_file()


@pytest.mark.parametrize(
    "change, message",
    [
        ({"OPENAI_API_KEY": ""}, "OPENAI_API_KEY"),
        ({"MUNEER_HF_REPO": ""}, "MUNEER_HF_REPO"),
        ({"MODEL_SERVER": "ollama"}, "MODEL_SERVER"),
        ({"MUNEER_PORT": "7860"}, "must differ"),
        ({"HF_TOKEN": "{{ RUNPOD_SECRET_hf_tokn }}"}, "HF_TOKEN holds a RunPod secret reference"),
        ({"REVIEW_QUEUE_PASSCODE": "old-name"}, "REVIEW_QUEUE_PASSCODE is now called ADMIN_PASSCODE"),
        ({"REVIEW_QUEUE_PATH": "data/q.sqlite3"}, "REVIEW_QUEUE_PATH is now called DATABASE_PATH"),
        ({"MUNEER_API_KEY": "k" * 31}, "at least 32 characters"),
    ],
)
def test_settings_it_cannot_run_without_fail_early(change, message):
    with pytest.raises(ConfigError, match=message):
        check_settings(POD | change)


def test_the_mock_needs_no_weights():
    assert check_settings(POD | {"MODEL_SERVER": "mock", "MUNEER_HF_REPO": ""}) == "mock"


def test_local_weights_replace_the_repo():
    assert check_settings(POD | {"MUNEER_HF_REPO": "", "MUNEER_MODEL_PATH": "/model"}) == "vllm"


# ---- the two processes ----


def test_the_model_server_listens_inside_the_container_only():
    plan = plan_for(POD | {"MUNEER_HOST": "0.0.0.0", "MUNEER_API_KEY": ""})
    model, app = plan.services
    assert Path(model.command[-1]).parts[-2:] == ("server", "launch.py")
    assert model.env["MUNEER_HOST"] == "127.0.0.1"
    assert model.env["MUNEER_API_KEY"] == app.env["MUNEER_API_KEY"] == "internal-key"
    assert app.env["MUNEER_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert app.env["APP_HOST"] == "0.0.0.0"
    assert plan.model_api is None


def test_a_key_on_the_pod_opens_the_model_api():
    plan = plan_for(POD | {"MUNEER_API_KEY": OPEN_KEY})
    model, app = plan.services
    assert model.env["MUNEER_HOST"] == "0.0.0.0"
    assert model.env["MUNEER_API_KEY"] == app.env["MUNEER_API_KEY"] == OPEN_KEY
    # The app and the health checks still use the address inside the container.
    assert app.env["MUNEER_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert model.health_url == "http://127.0.0.1:8000/health"
    assert plan.model_api == "https://abc123-8000.proxy.runpod.net/v1"


def test_the_open_model_api_address():
    assert model_api(POD) is None
    assert model_api(POD | {"MUNEER_API_KEY": OPEN_KEY, "MUNEER_PORT": "9000"}) == "https://abc123-9000.proxy.runpod.net/v1"
    assert model_api({"MUNEER_API_KEY": OPEN_KEY}) == "port 8000 of the container"


def test_the_app_uses_the_served_model_name_and_ports():
    _, app = plan_for(POD | {"MUNEER_SERVED_MODEL_NAME": "munir", "MUNEER_PORT": "9000", "APP_PORT": "8080"}).services
    assert app.env["MUNEER_MODEL"] == "munir"
    assert app.env["MUNEER_BASE_URL"] == "http://127.0.0.1:9000/v1"
    assert app.env["APP_PORT"] == "8080"


def test_each_process_gets_only_the_secrets_it_needs():
    model, app = plan_for(POD).services
    assert model.env["HF_TOKEN"] == "hf_secret"  # the launcher drops it before vLLM starts
    for secret in ("OPENAI_API_KEY", "ADMIN_PASSCODE", "RUNPOD_API_KEY"):
        assert secret not in model.env
    assert app.env["OPENAI_API_KEY"] == "sk-secret"
    assert app.env["ADMIN_PASSCODE"] == "passcode"
    for secret in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "RUNPOD_API_KEY"):
        assert secret not in app.env


def test_the_app_gets_only_the_settings_it_uses():
    extra = {
        "GITHUB_TOKEN": "ghp_x",  # a secret added to the pod later
        "WANDB_API_KEY": "w",
        "PLACEHOLDERS": "{}",
        "OPENAI_BASE_URL": "https://proxy.example",  # commented out in .env.example
        "GRADIO_ANALYTICS_ENABLED": "False",
        "LANG": "C.UTF-8",
    }
    model, app = plan_for(POD | extra).services
    for name in ("PLACEHOLDERS", "OPENAI_BASE_URL", "GRADIO_ANALYTICS_ENABLED", "LANG", "PATH"):
        assert name in app.env
    assert "GITHUB_TOKEN" not in app.env and "WANDB_API_KEY" not in app.env
    assert model.env["GITHUB_TOKEN"] == "ghp_x"  # vLLM keeps the container's environment
    mock, _ = plan_for(POD | extra | {"MODEL_SERVER": "mock"}).services
    assert "GITHUB_TOKEN" not in mock.env


def test_setting_names_include_the_commented_out_ones(tmp_path):
    defaults = tmp_path / ".env.example"
    defaults.write_text("# A comment\nMAX_MESSAGE_CHARS=2000\n# OPENAI_BASE_URL=\n#   python -c x=1\n")
    assert setting_names(defaults) == {"MAX_MESSAGE_CHARS", "OPENAI_BASE_URL"}
    assert {"PLACEHOLDERS", "OPENAI_API_KEY", "ADMIN_PASSCODE"} <= setting_names()


def test_the_app_runs_as_the_unprivileged_user_and_vllm_keeps_root():
    model, app = plan_for(POD, user="muneer", home="/home/muneer").services
    assert (app.user, app.env["HOME"]) == ("muneer", "/home/muneer")
    assert model.user is None and model.env.get("HOME") != "/home/muneer"


def test_mock_mode_runs_the_mock_without_any_secret():
    model, app = plan_for(POD | {"MODEL_SERVER": "mock"}, user="muneer", home="/home/muneer").services
    assert model.command == ["/venv/python", "-m", "mock_muneer", "--host", "127.0.0.1", "--port", "8000"]
    assert model.user == "muneer"
    assert model.env["MUNEER_API_KEY"] == app.env["MUNEER_API_KEY"]
    assert model.env["MUNEER_MODEL"] == "muneer"
    for secret in ("HF_TOKEN", "OPENAI_API_KEY", "ADMIN_PASSCODE", "RUNPOD_API_KEY"):
        assert secret not in model.env


def test_a_key_on_the_pod_opens_the_mock_too():
    model, app = plan_for(POD | {"MODEL_SERVER": "mock", "MUNEER_API_KEY": OPEN_KEY}).services
    assert model.command[-4:] == ["--host", "0.0.0.0", "--port", "8000"]
    assert model.env["MUNEER_API_KEY"] == app.env["MUNEER_API_KEY"] == OPEN_KEY


def test_the_database_lands_on_the_volume():
    expected = Path("/workspace/data/muneer.sqlite3").resolve()
    assert database_path({"DATABASE_PATH": "data/muneer.sqlite3"}) == expected
    assert database_path({}) == expected
    elsewhere = Path("/workspace/records/m.sqlite3").resolve()
    assert database_path({"DATABASE_PATH": "/workspace/records/m.sqlite3"}) == elsewhere
    assert plan_for(POD).services[1].env["DATABASE_PATH"] == str(expected)
    assert plan_for(POD).database == expected


@pytest.mark.parametrize(
    "path",
    [
        "muneer.sqlite3",  # the volume itself
        "data/../muneer.sqlite3",
        "/etc/muneer.sqlite3",  # outside the volume
        "models/muneer/muneer.sqlite3",  # the weights
        ".cache/muneer.sqlite3",  # holds the Hugging Face cache
        ".cache/huggingface/hub/muneer.sqlite3",
    ],
)
def test_the_database_needs_a_folder_of_its_own(path):
    # That folder is handed to the app's user, so it can't hold anything else.
    settings = POD | {
        "DATABASE_PATH": path,
        "MUNEER_DOWNLOAD_DIR": "/workspace/models/muneer",
        "HF_HOME": "/workspace/.cache/huggingface",
    }
    with pytest.raises(ConfigError, match="DATABASE_PATH"):
        plan_for(settings)


def test_the_public_address_comes_from_the_pod_id():
    assert public_url(POD) == "https://abc123-7860.proxy.runpod.net"
    assert public_url(POD | {"APP_PORT": "8080"}) == "https://abc123-8080.proxy.runpod.net"
    assert public_url({}) is None


def test_restarts_back_off_up_to_five_minutes():
    assert [restart_delay(n) for n in range(1, 9)] == [5, 10, 20, 40, 80, 160, 300, 300]


@posix_only
def test_the_weights_folders_are_closed_to_other_users(tmp_path):
    models, cache = tmp_path / "models" / "muneer", tmp_path / "cache"
    close_private_dirs({"MUNEER_DOWNLOAD_DIR": str(models), "HF_HOME": str(cache)})
    for folder in (models, cache):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700


@root_only
def test_the_database_folder_is_handed_over_without_following_links(tmp_path):
    import pwd

    nobody = pwd.getpwnam("nobody")
    secret = tmp_path / "weights.safetensors"
    secret.write_text("x")
    folder = tmp_path / "data"
    folder.mkdir()
    # Planted by a compromised app: root must not hand the target over.
    (folder / "muneer.sqlite3-x").symlink_to(secret)
    give_database_folder_to("nobody", folder / "muneer.sqlite3")
    assert folder.stat().st_uid == nobody.pw_uid
    assert secret.stat().st_uid == 0


def _refuse_owner_changes(monkeypatch):
    # As RunPod's NFS volumes do: root can't give a folder to another user.
    def chown(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(all_in_one.os, "chown", chown)


@posix_only
def test_storage_that_wont_change_owners_gets_a_folder_open_to_every_user(tmp_path, monkeypatch):
    _refuse_owner_changes(monkeypatch)
    folder = tmp_path / "data"
    give_database_folder_to("nobody", folder / "muneer.sqlite3")
    assert stat.S_IMODE(folder.stat().st_mode) == 0o777


@posix_only
def test_a_linked_database_folder_is_never_opened(tmp_path, monkeypatch):
    _refuse_owner_changes(monkeypatch)
    elsewhere, folder = tmp_path / "elsewhere", tmp_path / "data"
    elsewhere.mkdir(mode=0o755)
    folder.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PermissionError):
        give_database_folder_to("nobody", folder / "muneer.sqlite3")
    assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o755


@root_only
def test_the_weights_folders_are_taken_back_by_root(tmp_path):
    import pwd

    nobody = pwd.getpwnam("nobody")
    models, linked, elsewhere = tmp_path / "models", tmp_path / "linked", tmp_path / "elsewhere"
    models.mkdir()
    elsewhere.mkdir(mode=0o755)
    os.chown(models, nobody.pw_uid, nobody.pw_gid)
    linked.symlink_to(elsewhere, target_is_directory=True)
    close_private_dirs({"MUNEER_DOWNLOAD_DIR": str(models), "HF_HOME": str(linked)})
    assert models.stat().st_uid == 0 and stat.S_IMODE(models.stat().st_mode) == 0o700
    assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o755  # a link is left alone


# ---- supervision (real processes) ----


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def python(code):
    return [sys.executable, "-c", code]


def tick_until(supervisor, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        supervisor.tick()
        time.sleep(0.05)


def alive(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"  # a zombie has already ended


@posix_only
def test_a_process_that_stops_is_started_again_after_a_delay(capsys):
    clock = FakeClock()
    service = Service("model server", python("import sys; sys.exit(3)"), env=dict(os.environ))
    supervisor = Supervisor([service], clock=clock)
    supervisor.tick()
    first = service.process.pid
    tick_until(supervisor, lambda: service.failures == 1)
    assert service.process is None and service.next_start == clock.now + 5
    assert "The model server stopped (exit code 3); starting it again in 5 s." in capsys.readouterr().out
    supervisor.tick()
    assert service.process is None  # still waiting
    clock.now += 5
    supervisor.tick()
    assert service.process.pid != first
    tick_until(supervisor, lambda: service.failures == 2)
    assert service.next_start == clock.now + 10


@posix_only
def test_a_process_that_ran_a_long_time_restarts_quickly():
    clock = FakeClock()
    service = Service("app", python("import time; time.sleep(0.3)"), env=dict(os.environ), failures=4)
    supervisor = Supervisor([service], clock=clock)
    supervisor.tick()
    clock.now += all_in_one.STABLE_SECONDS
    tick_until(supervisor, lambda: service.process is None)
    assert service.failures == 1 and service.next_start == clock.now + 5


@posix_only
def test_stop_ends_every_process():
    services = [Service(name, python("import time; time.sleep(60)"), env=dict(os.environ)) for name in ("model server", "app")]
    supervisor = Supervisor(services)
    supervisor.tick()
    pids = [s.process.pid for s in services]
    started = time.monotonic()
    supervisor.stop()
    assert time.monotonic() - started < 5
    assert all(s.process is None for s in services)
    assert not any(alive(pid) for pid in pids)


@posix_only
def test_a_process_that_ignores_sigterm_is_killed_after_the_grace_period(tmp_path):
    ready = tmp_path / "ready"
    code = (
        "import signal, time, pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready)!r}).touch(); time.sleep(60)"
    )
    service = Service("model server", python(code), env=dict(os.environ))
    supervisor = Supervisor([service], grace=0.5)
    supervisor.tick()
    pid = service.process.pid
    deadline = time.monotonic() + 10
    while not ready.exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    supervisor.stop()
    assert service.process is None and not alive(pid)


@posix_only
def test_workers_left_behind_are_stopped_with_their_process(tmp_path):
    worker_pid = tmp_path / "worker"
    code = (
        "import subprocess, sys, pathlib; "
        "w = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(worker_pid)!r}).write_text(str(w.pid))"
    )
    service = Service("model server", python(code), env=dict(os.environ))
    supervisor = Supervisor([service], clock=FakeClock())
    supervisor.tick()
    tick_until(supervisor, lambda: service.failures == 1)
    worker = int(worker_pid.read_text())
    deadline = time.monotonic() + 5
    while alive(worker):
        assert time.monotonic() < deadline, "the worker outlived its process"
        time.sleep(0.05)


@posix_only
def test_a_process_that_cannot_start_is_tried_again_later(capsys):
    clock = FakeClock()
    service = Service("app", ["/nonexistent/muneer-app"], env={})
    supervisor = Supervisor([service], clock=clock)
    supervisor.tick()
    assert service.process is None and service.failures == 1 and service.next_start == clock.now + 5
    assert "Couldn't start the app" in capsys.readouterr().out


@posix_only
def test_run_stops_every_process_even_when_it_fails():
    service = Service("model server", python("import time; time.sleep(60)"), env=dict(os.environ))
    supervisor = Supervisor([service])
    supervisor.tick()
    pid = service.process.pid

    def broken():
        raise RuntimeError("bug")

    supervisor.tick = broken
    with pytest.raises(RuntimeError):
        supervisor.run(lambda: False)
    assert service.process is None and not alive(pid)


class Health:
    """Stands in for the processes and their health checks."""

    def __init__(self, monkeypatch, *names):
        self.answers = {}
        self.signals = []
        monkeypatch.setattr(all_in_one, "is_healthy", lambda url: self.answers.get(url, False))
        monkeypatch.setattr(all_in_one, "_signal", lambda pid, signum: self.signals.append((pid, signum)))
        monkeypatch.setattr(all_in_one, "_kill_group", lambda pid: self.signals.append((pid, "kill")))
        self.services = []
        for pid, name in enumerate(names, start=100):
            service = Service(name, ["x"], env={}, health_url=f"http://{name}/")
            service.process = SimpleNamespace(pid=pid)
            self.services.append(service)

    def answer(self, name, ok=True):
        self.answers[f"http://{name}/"] = ok


def check(supervisor, clock, times=1):
    for _ in range(times):
        clock.now += all_in_one.HEALTH_INTERVAL
        supervisor._check_health(clock.now)


def test_ready_once_every_process_answers(monkeypatch, capsys):
    health = Health(monkeypatch, "model", "app")
    clock = FakeClock()
    supervisor = Supervisor(health.services, public_url="https://p-7860.proxy.runpod.net", clock=clock)
    health.answer("app")
    check(supervisor, clock)
    assert not supervisor.ready  # the model is still loading
    health.answer("model")
    check(supervisor, clock)
    assert supervisor.ready
    assert "Muneer is ready: https://p-7860.proxy.runpod.net" in capsys.readouterr().out


def test_health_is_checked_at_most_every_interval(monkeypatch):
    health = Health(monkeypatch, "app")
    clock = FakeClock()
    supervisor = Supervisor(health.services, clock=clock)
    supervisor._check_health(clock.now)
    health.answer("app")
    supervisor._check_health(clock.now + 1)
    assert not supervisor.ready
    check(supervisor, clock)
    assert supervisor.ready


def test_a_process_still_starting_is_left_alone(monkeypatch):
    # The first start downloads 30 GB; that mustn't count as a hang.
    health = Health(monkeypatch, "model")
    clock = FakeClock()
    supervisor = Supervisor(health.services, clock=clock)
    check(supervisor, clock, times=100)
    assert health.signals == [] and not supervisor.ready


def test_a_process_that_stops_answering_is_restarted(monkeypatch, capsys):
    health = Health(monkeypatch, "model", "app")
    clock = FakeClock()
    supervisor = Supervisor(health.services, clock=clock, grace=8)
    health.answer("model")
    health.answer("app")
    check(supervisor, clock)
    assert supervisor.ready
    health.answer("model", ok=False)
    check(supervisor, clock, times=all_in_one.UNHEALTHY_CHECKS - 1)
    assert health.signals == [] and not supervisor.ready
    check(supervisor, clock)
    assert health.signals == [(100, signal.SIGTERM)]
    assert "The model stopped answering; restarting it." in capsys.readouterr().out
    check(supervisor, clock)  # it didn't stop within the grace period
    assert health.signals[-1] == (100, "kill")


def test_health_checks_give_way_to_a_stop(monkeypatch):
    # A check can take HEALTH_TIMEOUT; once a stop is asked for, the rest are skipped.
    health = Health(monkeypatch, "model", "app")
    checked = []
    monkeypatch.setattr(all_in_one, "is_healthy", lambda url: checked.append(url) or True)
    supervisor = Supervisor(health.services, clock=FakeClock())
    supervisor._should_stop = lambda: bool(checked)  # the stop arrives during the first check
    supervisor._check_health(0)
    assert checked == ["http://model/"]
    assert all_in_one.HEALTH_TIMEOUT + all_in_one.GRACE_SECONDS < 10  # docker stop's limit


def test_one_missed_check_is_forgiven(monkeypatch):
    health = Health(monkeypatch, "app")
    clock = FakeClock()
    supervisor = Supervisor(health.services, clock=clock)
    health.answer("app")
    check(supervisor, clock)
    health.answer("app", ok=False)
    check(supervisor, clock, times=all_in_one.UNHEALTHY_CHECKS - 1)
    health.answer("app")
    check(supervisor, clock)
    health.answer("app", ok=False)
    check(supervisor, clock, times=all_in_one.UNHEALTHY_CHECKS - 1)
    assert health.signals == []


def test_main_explains_a_missing_setting(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("MODEL_SERVER", "mock")
    monkeypatch.setattr(all_in_one, "app_account", lambda: (None, None))  # the image has the app's user
    assert all_in_one.main() == 2
    assert "Set OPENAI_API_KEY" in capsys.readouterr().err


def test_the_plan_says_which_model_server_runs():
    assert plan_for(POD).model_server == "vllm"
    assert plan_for(POD | {"MODEL_SERVER": "mock"}).model_server == "mock"


def test_is_healthy_is_false_when_nothing_listens():
    assert all_in_one.is_healthy("http://127.0.0.1:9/health", timeout=1) is False
