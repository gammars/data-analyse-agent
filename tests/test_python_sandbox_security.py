import subprocess

import pandas as pd
import pytest

from app.services.python_sandbox_service import PythonSandboxService


def _no_run(*args, **kwargs):
    raise AssertionError("runner should not be called when static validation fails")


def test_python_sandbox_rejects_prohibited_imports(tmp_path) -> None:
    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        runner=_no_run,
    )

    with pytest.raises(ValueError, match="禁止模块：subprocess"):
        service.run_analysis(
            dataframe=pd.DataFrame([{"value": 1}]),
            analysis_goal="安全测试",
            python_code="import subprocess\nprint('bad')",
        )


def test_python_sandbox_rejects_dynamic_prohibited_imports(tmp_path) -> None:
    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        runner=_no_run,
    )

    with pytest.raises(ValueError, match="禁止模块：socket"):
        service.run_analysis(
            dataframe=pd.DataFrame([{"value": 1}]),
            analysis_goal="安全测试",
            python_code='__import__("socket")',
        )


def test_python_sandbox_rejects_paths_outside_workspace(tmp_path) -> None:
    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        runner=_no_run,
    )

    with pytest.raises(ValueError, match="非 /workspace 绝对路径"):
        service.run_analysis(
            dataframe=pd.DataFrame([{"value": 1}]),
            analysis_goal="路径测试",
            python_code='open("/etc/passwd").read()',
        )


def test_python_sandbox_health_check_success(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        image="sandbox:test",
        runner=fake_runner,
    )

    result = service.health_check()

    assert result["ok"] is True
    assert [item["name"] for item in result["checks"]] == [
        "docker_cli",
        "docker_daemon",
        "sandbox_image",
    ]
    assert all(item["fix"] == "" for item in result["checks"])


def test_python_sandbox_health_check_reports_fix(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="missing image")
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        image="sandbox:test",
        runner=fake_runner,
    )

    result = service.health_check()

    assert result["ok"] is False
    image_check = result["checks"][2]
    assert image_check["name"] == "sandbox_image"
    assert image_check["message"] == "missing image"
    assert "docker build -t sandbox:test" in image_check["fix"]
