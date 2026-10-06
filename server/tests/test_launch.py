import json
import os
from pathlib import Path

import pytest
from jinja2.sandbox import ImmutableSandboxedEnvironment

import launch
from launch import SYSTEM_PROMPT_FILE, ConfigError, Settings, build_vllm_command, build_vllm_env, check_model_dir


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def flags(cmd, name):
    return [cmd[i + 1] for i, part in enumerate(cmd) if part == name]


@pytest.fixture
def model_dir(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model.safetensors").write_bytes(b"")
    return tmp_path


def test_needs_a_model_source():
    with pytest.raises(ConfigError, match="MUNEER_HF_REPO"):
        Settings.from_env({})


def test_defaults():
    s = Settings.from_env({"MUNEER_HF_REPO": "someone/muneer"})
    assert (s.served_model_name, s.host, s.port) == ("muneer", "0.0.0.0", 8000)
    assert (s.max_model_len, s.gpu_memory_utilization, s.tensor_parallel_size) == (8192, 0.92, 1)
    assert (s.temperature, s.top_p, s.top_k, s.max_output_tokens) == (0.3, 0.95, 20, 1500)
    assert (s.repetition_penalty, s.frequency_penalty, s.presence_penalty) == (1.1, 0.2, 0.0)
    assert s.api_key is None and s.quantization is None and s.max_num_seqs is None
    assert s.system_prompt and s.system_prompt == SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    assert s.extra_args == []


def test_reads_every_setting(tmp_path):
    s = Settings.from_env({
        "MUNEER_MODEL_PATH": str(tmp_path),
        "MUNEER_API_KEY": " secret ",
        "MUNEER_SERVED_MODEL_NAME": "muneer-v2",
        "MUNEER_PORT": "9000",
        "MUNEER_MAX_MODEL_LEN": "4096",
        "MUNEER_GPU_MEMORY_UTILIZATION": "0.85",
        "MUNEER_TENSOR_PARALLEL_SIZE": "2",
        "MUNEER_MAX_NUM_SEQS": "64",
        "MUNEER_QUANTIZATION": "fp8",
        "MUNEER_TEMPERATURE": "0.7",
        "MUNEER_REPETITION_PENALTY": "1.05",
        "MUNEER_FREQUENCY_PENALTY": "-0.5",
        "MUNEER_PRESENCE_PENALTY": "0.4",
        "MUNEER_MAX_OUTPUT_TOKENS": "512",
        "MUNEER_VLLM_EXTRA_ARGS": "--seed 7 --enforce-eager",
    })
    assert s.model_path == tmp_path and s.api_key == "secret"
    assert (s.served_model_name, s.port, s.max_model_len) == ("muneer-v2", 9000, 4096)
    assert (s.gpu_memory_utilization, s.tensor_parallel_size, s.max_num_seqs) == (0.85, 2, 64)
    assert (s.quantization, s.temperature, s.max_output_tokens) == ("fp8", 0.7, 512)
    assert (s.repetition_penalty, s.frequency_penalty, s.presence_penalty) == (1.05, -0.5, 0.4)
    assert s.extra_args == ["--seed", "7", "--enforce-eager"]


@pytest.mark.parametrize(
    "name, value",
    [
        ("MUNEER_PORT", "eighty"),
        ("MUNEER_PORT", "70000"),
        ("MUNEER_GPU_MEMORY_UTILIZATION", "1.5"),
        ("MUNEER_TOP_P", "0"),
        ("MUNEER_REPETITION_PENALTY", "0"),
        ("MUNEER_FREQUENCY_PENALTY", "2.5"),
        ("MUNEER_PRESENCE_PENALTY", "-3"),
        ("MUNEER_FREQUENCY_PENALTY", "nan"),
        ("MUNEER_TEMPERATURE", "nan"),
        ("MUNEER_TOP_P", "inf"),
        ("MUNEER_MAX_MODEL_LEN", "8k"),
    ],
)
def test_rejects_bad_numbers(name, value):
    with pytest.raises(ConfigError, match=name):
        Settings.from_env({"MUNEER_HF_REPO": "someone/muneer", name: value})


def test_command(model_dir):
    s = Settings.from_env({"MUNEER_MODEL_PATH": str(model_dir)})
    cmd = build_vllm_command(s, model_dir)
    assert cmd[:3] == ["vllm", "serve", str(model_dir)]
    assert flag(cmd, "--served-model-name") == "muneer"
    assert flag(cmd, "--dtype") == "bfloat16"
    # vLLM runs the last middleware first, so the key is checked before a body is read.
    assert flags(cmd, "--middleware") == ["muneer_sampling.PenaltyDefaultsMiddleware", "muneer_auth.ApiKeyMiddleware"]
    assert Path(flag(cmd, "--chat-template")).is_file()
    assert json.loads(flag(cmd, "--override-generation-config")) == {
        "temperature": 0.3, "top_p": 0.95, "top_k": 20, "repetition_penalty": 1.1, "max_new_tokens": 1500,
    }
    assert "--quantization" not in cmd and "--max-num-seqs" not in cmd
    assert json.loads(flag(cmd, "--default-chat-template-kwargs")) == {"default_system_prompt": s.system_prompt}


def test_the_system_prompt_file_can_be_moved_or_emptied(tmp_path, model_dir):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("  Be kind.\n", encoding="utf-8")
    s = Settings.from_env({"MUNEER_MODEL_PATH": str(model_dir), "MUNEER_SYSTEM_PROMPT_FILE": str(prompt)})
    assert json.loads(flag(build_vllm_command(s, model_dir), "--default-chat-template-kwargs")) == {
        "default_system_prompt": "Be kind."
    }
    prompt.write_text("\n", encoding="utf-8")
    s = Settings.from_env({"MUNEER_MODEL_PATH": str(model_dir), "MUNEER_SYSTEM_PROMPT_FILE": str(prompt)})
    assert "--default-chat-template-kwargs" not in build_vllm_command(s, model_dir)


def test_a_missing_system_prompt_file_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="MUNEER_SYSTEM_PROMPT_FILE"):
        Settings.from_env({"MUNEER_HF_REPO": "someone/muneer", "MUNEER_SYSTEM_PROMPT_FILE": str(tmp_path / "none.md")})


def test_command_options(model_dir):
    s = Settings.from_env({
        "MUNEER_MODEL_PATH": str(model_dir),
        "MUNEER_QUANTIZATION": "fp8",
        "MUNEER_MAX_NUM_SEQS": "32",
        "MUNEER_VLLM_EXTRA_ARGS": "--seed 7",
    })
    cmd = build_vllm_command(s, model_dir, auth=False)
    assert flags(cmd, "--middleware") == ["muneer_sampling.PenaltyDefaultsMiddleware"]
    assert flag(cmd, "--quantization") == "fp8"
    assert flag(cmd, "--max-num-seqs") == "32"
    assert cmd[-2:] == ["--seed", "7"]


def test_the_api_key_never_appears_in_the_command(model_dir):
    s = Settings.from_env({"MUNEER_MODEL_PATH": str(model_dir), "MUNEER_API_KEY": "top-secret"})
    assert "top-secret" not in " ".join(build_vllm_command(s, model_dir))


def test_vllm_environment():
    settings = Settings.from_env({"MUNEER_HF_REPO": "someone/muneer", "MUNEER_FREQUENCY_PENALTY": "0.25"})
    env = build_vllm_env({"HF_TOKEN": "hf_x", "PATH": "/bin", "PYTHONPATH": "/extra", "MUNEER_API_KEY": "k"}, settings)
    assert "HF_TOKEN" not in env
    assert env["MUNEER_API_KEY"] == "k"  # the auth middleware reads it
    assert env["PYTHONPATH"].split(os.pathsep) == [str(launch.SERVER_DIR), "/extra"]
    assert env["VLLM_NO_USAGE_STATS"] == "1" and env["DO_NOT_TRACK"] == "1"
    # The penalties middleware reads them.
    assert (env["MUNEER_FREQUENCY_PENALTY"], env["MUNEER_PRESENCE_PENALTY"]) == ("0.25", "0.0")


def test_check_model_dir(tmp_path, model_dir):
    check_model_dir(model_dir)
    with pytest.raises(ConfigError, match="config.json"):
        check_model_dir(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "config.json").write_text("{}")
    with pytest.raises(ConfigError, match="safetensors"):
        check_model_dir(tmp_path / "empty")


def test_local_model_path_skips_download(model_dir):
    s = Settings.from_env({"MUNEER_MODEL_PATH": str(model_dir)})
    assert launch.resolve_model(s) == model_dir


def test_serving_requires_an_api_key(monkeypatch, model_dir, capsys):
    monkeypatch.setenv("MUNEER_MODEL_PATH", str(model_dir))
    monkeypatch.delenv("MUNEER_API_KEY", raising=False)
    assert launch.main(["--env-file", "none"]) == 2
    assert "MUNEER_API_KEY" in capsys.readouterr().err


def test_dry_run_prints_the_command(monkeypatch, model_dir, capsys):
    monkeypatch.setenv("MUNEER_MODEL_PATH", str(model_dir))
    assert launch.main(["--dry-run", "--env-file", "none"]) == 0
    assert capsys.readouterr().out.startswith("vllm serve ")


def render(messages, **kwargs):
    # Rendered with the Jinja settings transformers (and so vLLM) uses.
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    template = env.from_string(launch.CHAT_TEMPLATE.read_text(encoding="utf-8"))
    return template.render(messages=messages, add_generation_prompt=True, **kwargs)


def test_chat_template_matches_training_format():
    # Muneer was trained on plain ChatML; the server must build the same prompt.
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    template = env.from_string(launch.CHAT_TEMPLATE.read_text(encoding="utf-8"))
    prompt = template.render(
        messages=[
            {"role": "system", "content": "S"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "Q2"},
        ],
        add_generation_prompt=True,
    )
    assert prompt == (
        "<|im_start|>system\nS<|im_end|>\n"
        "<|im_start|>user\nQ1<|im_end|>\n"
        "<|im_start|>assistant\nA1<|im_end|>\n"
        "<|im_start|>assistant\nA2<|im_end|>\n"
        "<|im_start|>user\nQ2<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def test_the_template_adds_the_default_system_prompt_first():
    prompt = render([{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"},
                     {"role": "user", "content": "Q2"}], default_system_prompt="D")
    assert prompt == (
        "<|im_start|>system\nD<|im_end|>\n"
        "<|im_start|>user\nQ1<|im_end|>\n"
        "<|im_start|>assistant\nA1<|im_end|>\n"
        "<|im_start|>user\nQ2<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def test_a_request_s_own_system_message_replaces_the_default():
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}]
    expected = "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\nQ<|im_end|>\n<|im_start|>assistant\n"
    assert render(messages, default_system_prompt="D") == expected


def test_a_system_message_anywhere_counts_as_the_request_s_own():
    messages = [{"role": "user", "content": "Q"}, {"role": "system", "content": "S"}]
    assert "D" not in render(messages, default_system_prompt="D")


@pytest.mark.parametrize("kwargs", [{}, {"default_system_prompt": ""}, {"default_system_prompt": None}])
def test_without_a_default_no_system_message_is_added(kwargs):
    assert render([{"role": "user", "content": "Q"}], **kwargs) == (
        "<|im_start|>user\nQ<|im_end|>\n<|im_start|>assistant\n"
    )
