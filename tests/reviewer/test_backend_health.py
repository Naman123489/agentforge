
import json

from engine.reviewer.backend_health import _detect_backend, _python_entrypoint


def test_detect_backend_reports_missing_backend(tmp_path):
    command, message = _detect_backend(tmp_path / "missing")

    assert command is None
    assert "No supported" in message


def test_detect_backend_rejects_invalid_package_json(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "package.json").write_text("{invalid", encoding="utf-8")

    command, message = _detect_backend(backend)

    assert command is None
    assert "Could not read" in message


def test_detect_backend_rejects_express_without_start_script(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "package.json").write_text(
        json.dumps({"dependencies": {"express": "^4.0.0"}}),
        encoding="utf-8",
    )

    command, message = _detect_backend(backend)

    assert command is None
    assert "no supported Express start configuration" in message


def test_python_entrypoint_detects_fastapi(tmp_path):
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n",
        encoding="utf-8",
    )

    result = _python_entrypoint(backend)

    assert result == ("fastapi", "main")


def test_python_entrypoint_ignores_virtual_environment(tmp_path):
    backend = tmp_path / "backend"
    virtual_env = backend / ".venv" / "Lib"
    virtual_env.mkdir(parents=True)
    (virtual_env / "installed_app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n",
        encoding="utf-8",
    )

    assert _python_entrypoint(backend) is None
