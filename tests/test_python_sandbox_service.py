import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from app.services.python_sandbox_service import PythonSandboxService


def _mount_path(command: list[str], container_mount: str) -> Path:
    suffix = f":{container_mount}"
    for index, item in enumerate(command):
        if item == "-v" and command[index + 1].endswith(suffix):
            return Path(command[index + 1][: -len(suffix)])
    raise AssertionError(f"mount not found: {container_mount}")


def test_python_sandbox_writes_inputs_and_reads_result(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        assert check is False
        assert capture_output is True
        assert text is True
        assert timeout == 9
        assert "--network" in command
        assert "none" in command
        assert "-e" in command
        assert "MPLCONFIGDIR=/tmp/matplotlib" in command
        output_dir = _mount_path(command, "/workspace/output:rw")
        (output_dir / "result.json").write_text(
            json.dumps({"summary": "ok", "metrics": {"rows": 2}}, ensure_ascii=False),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        image="sandbox:test",
        timeout_seconds=9,
        runner=fake_runner,
    )

    result = service.run_analysis(
        dataframe=pd.DataFrame([{"category": "A", "amount": 1}, {"category": "B", "amount": 2}]),
        analysis_goal="测试沙箱",
        python_code="""```python
print("hello")
```""",
    )

    assert result.result["summary"] == "ok"
    assert result.input_rows == 2
    assert result.stdout == "done"
    assert (result.run_dir / "input" / "data.json").exists()
    assert (result.run_dir / "input" / "schema.json").exists()
    script = (result.run_dir / "work" / "analysis.py").read_text(encoding="utf-8")
    assert "Noto Sans CJK SC" in script
    assert "axes.unicode_minus" in script
    assert script.rstrip().endswith('print("hello")')


def test_python_sandbox_publishes_output_figures(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        output_dir = _mount_path(command, "/workspace/output:rw")
        (output_dir / "correlation_analysis.png").write_bytes(b"fake png")
        (output_dir / "result.json").write_text(
            json.dumps(
                {
                    "summary": "ok",
                    "figures": [
                        {
                            "title": "相关性热力图",
                            "path": "/workspace/output/correlation_analysis.png",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        chart_dir=tmp_path / "charts",
        runner=fake_runner,
    )

    result = service.run_analysis(
        dataframe=pd.DataFrame([{"value": 1}]),
        analysis_goal="图像测试",
        python_code="print('plot')",
    )

    assert len(result.figures) == 1
    assert result.figures[0].title == "相关性热力图"
    assert result.figures[0].url.startswith("/charts/")
    assert result.figures[0].path.read_bytes() == b"fake png"
    assert result.result["figures"][0]["chart_url"] == result.figures[0].url
    assert result.result["figures"][0]["path"] == result.figures[0].url


def test_python_sandbox_timeout_has_clear_error(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(command, timeout)

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        timeout_seconds=3,
        runner=fake_runner,
    )

    with pytest.raises(TimeoutError, match="超过 3 秒"):
        service.run_analysis(
            dataframe=pd.DataFrame([{"value": 1}]),
            analysis_goal="超时测试",
            python_code="print('slow')",
        )


def test_python_sandbox_failure_includes_stderr(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="boom")

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        runner=fake_runner,
    )

    with pytest.raises(RuntimeError, match="退出码 2"):
        service.run_analysis(
            dataframe=pd.DataFrame([{"value": 1}]),
            analysis_goal="失败测试",
            python_code="raise RuntimeError('boom')",
        )


def test_python_sandbox_requires_result_json(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        runner=fake_runner,
    )

    with pytest.raises(RuntimeError, match="result.json"):
        service.run_analysis(
            dataframe=pd.DataFrame([{"value": 1}]),
            analysis_goal="缺少结果测试",
            python_code="print('no result')",
        )


def test_python_sandbox_rejects_large_result_json(tmp_path) -> None:
    def fake_runner(command, check, capture_output, text, timeout):
        output_dir = _mount_path(command, "/workspace/output:rw")
        (output_dir / "result.json").write_text(
            json.dumps({"summary": "x" * (2 * 1024 * 1024)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    service = PythonSandboxService(
        runs_dir=tmp_path / "python_runs",
        runner=fake_runner,
    )

    with pytest.raises(RuntimeError, match="result.json 过大"):
        service.run_analysis(
            dataframe=pd.DataFrame([{"value": 1}]),
            analysis_goal="大结果测试",
            python_code="print('large')",
        )
