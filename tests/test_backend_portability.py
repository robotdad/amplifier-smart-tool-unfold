"""Launch actual Node processes without shell wrappers or command interpolation."""

import json
import shutil

import pytest

from unfold.backend import Backend
from unfold.models import UnfoldError


@pytest.fixture
def backend(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Install Node to run backend portability checks")
    backend = Backend(tmp_path / "backend with spaces")
    backend.cli.parent.mkdir(parents=True)
    return backend


def test_launcher_preserves_arguments_and_scrubs_environment(backend, monkeypatch):
    backend.cli.write_text("""
console.log(JSON.stringify({
  args: process.argv.slice(2),
  secret: process.env.OPENAI_API_KEY ?? null,
  options: process.env.NODE_OPTIONS ?? null,
  temp: process.env.TEMP,
  telemetry: process.env.HYPERFRAMES_NO_TELEMETRY
}));
""")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("NODE_OPTIONS", "--invalid-option-must-not-be-inherited")
    monkeypatch.setenv("TEMP", str(backend.root.parent))
    arguments = ["render", "directory with spaces", "--output", "clip & (final).mp4", "$HOME"]
    result = json.loads(backend._run_cli(arguments))
    assert result["args"] == arguments
    assert result["secret"] is None
    assert result["options"] is None
    assert result["temp"] == str(backend.root.parent)
    assert result["telemetry"] == "1"


def test_launcher_reports_process_failure(backend):
    backend.cli.write_text('console.error("render failure"); process.exitCode = 7;')
    with pytest.raises(UnfoldError, match="render failure"):
        backend._run_cli(["render"])


@pytest.mark.parametrize("ambient", ["true", "false"])
@pytest.mark.parametrize("selected", [False, True])
def test_screenshot_selection_is_code_owned_and_environment_stays_sanitized(backend, monkeypatch, ambient, selected):
    backend.cli.write_text("""
console.log(JSON.stringify({
  capture: process.env.PRODUCER_FORCE_SCREENSHOT ?? null,
  other: process.env.PRODUCER_EXPERIMENTAL_FAST_CAPTURE ?? null,
  gpu: process.env.PRODUCER_BROWSER_GPU_MODE ?? null,
  disableGpu: process.env.PRODUCER_DISABLE_GPU ?? null,
  args: process.argv.slice(2),
  secret: process.env.OPENAI_API_KEY ?? null,
  options: process.env.NODE_OPTIONS ?? null,
  telemetry: process.env.HYPERFRAMES_NO_TELEMETRY
}));
""")
    monkeypatch.setenv("PRODUCER_FORCE_SCREENSHOT", ambient)
    monkeypatch.setenv("PRODUCER_EXPERIMENTAL_FAST_CAPTURE", "true")
    monkeypatch.setenv("PRODUCER_BROWSER_GPU_MODE", "hardware")
    monkeypatch.setenv("PRODUCER_DISABLE_GPU", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("NODE_OPTIONS", "--invalid-option-must-not-be-inherited")
    arguments = ["render", "--no-browser-gpu"] if selected else ["render"]
    result = json.loads(backend._run_cli(arguments, force_screenshot=selected))
    assert result == {
        "capture": "true" if selected else None, "other": None, "secret": None,
        "options": None, "telemetry": "1", "gpu": None, "disableGpu": None, "args": arguments,
    }
    # No mutation of the parent environment and no setting leaked to a later command.
    import os

    assert os.environ["PRODUCER_FORCE_SCREENSHOT"] == ambient
    assert os.environ["PRODUCER_BROWSER_GPU_MODE"] == "hardware"
    assert json.loads(backend._run_cli(["doctor"]))["capture"] is None


def test_dynamic_layer_capture_is_code_owned_not_ambient(backend,monkeypatch):
    backend.cli.write_text('console.log(JSON.stringify({dedup:process.env.HF_STATIC_DEDUP??null}));')
    monkeypatch.setenv("HF_STATIC_DEDUP","true")
    assert json.loads(backend._run_cli(["render"],dynamic_frames=True)) == {"dedup":"false"}
    assert json.loads(backend._run_cli(["doctor"])) == {"dedup":None}
