import json

from app.agent.artifacts import build_tool_artifacts
from app.services.context_service import ContextService


def test_python_analysis_result_builds_compact_artifact() -> None:
    result = json.dumps(
        {
            "ok": True,
            "run_id": "run-1",
            "input_rows": 12,
            "result": {
                "summary": "相关性分析完成，price 与 freight_value 存在弱相关。",
                "metrics": {"correlation": 0.42},
                "figures": [
                    {
                        "chart_id": "chart-1",
                        "chart_url": "/charts/chart-1.png",
                        "chart_type": "heatmap",
                        "title": "相关性热力图",
                    }
                ],
            },
        },
        ensure_ascii=False,
    )

    artifacts = build_tool_artifacts(
        step_id="step-1",
        tool_name="python_analysis",
        tool_args={"analysis_goal": "做相关性分析", "python_code": "print('hidden')"},
        result=result,
        success=True,
        duration_ms=1234,
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.type == "python_result"
    assert artifact.source_tool == "python_analysis"
    assert artifact.summary == "相关性分析完成，price 与 freight_value 存在弱相关。"
    assert artifact.preview["run_id"] == "run-1"
    assert artifact.preview["figures"][0]["chart_url"] == "/charts/chart-1.png"


def test_context_uses_artifact_summary_and_tool_preview_not_raw_python_code() -> None:
    service = ContextService()
    raw_result = json.dumps(
        {
            "ok": True,
            "run_id": "run-2",
            "result": {
                "summary": "Python 分析完成。",
                "metrics": {"mean": 10},
                "raw_rows": ["x" * 4000],
            },
            "stdout": "very long stdout " * 300,
        },
        ensure_ascii=False,
    )
    conversation = {
        "messages": [
            {
                "role": "tool",
                "type": "tool_end",
                "name": "python_analysis",
                "args": {
                    "dataset_id": "dataset-1",
                    "sql": "SELECT * FROM orders",
                    "python_code": "import pandas as pd\nprint('should not enter context')",
                },
                "result": raw_result,
            },
            {
                "role": "artifact",
                "type": "artifact",
                "artifact": {
                    "artifact_id": "artifact-1",
                    "type": "python_result",
                    "title": "Python 分析结果",
                    "summary": "Python 分析完成。",
                    "source_tool": "python_analysis",
                    "success": True,
                    "preview": {"run_id": "run-2"},
                },
            },
        ]
    }

    serialized = "\n\n".join(str(message.content) for message in service.build_history_messages(conversation))

    assert "python_code" not in serialized
    assert "should not enter context" not in serialized
    assert "very long stdout" not in serialized
    assert "Python 分析完成。" in serialized
    assert "artifact-1" in serialized
