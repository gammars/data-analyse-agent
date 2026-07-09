import json
from pathlib import Path

import pandas as pd

from app.agent.tools import build_tools, make_python_analysis_tool
from app.services.analysis_service import AnalysisService
from app.services.chart_service import ChartService
from app.services.dataset_service import DatasetService
from app.services.python_sandbox_service import PythonSandboxResult
from app.services.sql_service import SQLService


def _csv(content: str) -> bytes:
    return content.encode("utf-8")


class FakeSandbox:
    def __init__(self) -> None:
        self.received_dataframe = None

    def run_analysis(
        self,
        dataframe: pd.DataFrame,
        python_code: str,
        analysis_goal: str,
    ) -> PythonSandboxResult:
        self.received_dataframe = dataframe
        return PythonSandboxResult(
            run_id="run-1",
            run_dir=Path("unused"),
            result={"summary": "ok", "columns": list(dataframe.columns)},
            stdout="",
            stderr="",
            input_rows=len(dataframe),
            analysis_goal=analysis_goal,
            figures=[],
        )


def test_python_analysis_tool_is_registered(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    datasets.save_dataset("sales.csv", _csv("category,amount\nA,10\nB,20\n"))

    tools = build_tools(
        SQLService(datasets),
        ChartService(tmp_path / "charts"),
        AnalysisService(datasets),
    )

    assert "python_analysis" in {tool.name for tool in tools}


def test_python_analysis_tool_queries_sql_then_runs_sandbox(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset("sales.csv", _csv("category,amount\nA,10\nB,20\n"))
    sandbox = FakeSandbox()
    tool = make_python_analysis_tool(SQLService(datasets), sandbox)

    payload = json.loads(
        tool.invoke(
            {
                "dataset_id": record.dataset_id,
                "sql": "SELECT category, amount FROM data_table ORDER BY amount",
                "analysis_goal": "计算金额分布",
                "python_code": "print('ok')",
                "max_rows": 10,
            }
        )
    )

    assert payload["message"] == "Python 沙箱分析已完成。"
    assert payload["input_rows"] == 2
    assert payload["result"]["summary"] == "ok"
    assert sandbox.received_dataframe.to_dict("records") == [
        {"category": "A", "amount": 10},
        {"category": "B", "amount": 20},
    ]


def test_python_analysis_tool_rejects_non_select_sql(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset("sales.csv", _csv("category,amount\nA,10\n"))
    sandbox = FakeSandbox()
    tool = make_python_analysis_tool(SQLService(datasets), sandbox)

    result = tool.invoke(
        {
            "dataset_id": record.dataset_id,
            "sql": "DELETE FROM data_table",
            "analysis_goal": "非法 SQL 测试",
            "python_code": "print('should not run')",
            "max_rows": 10,
        }
    )

    assert "Python 沙箱分析失败" in result
    assert "只允许 SELECT / WITH" in result
    assert sandbox.received_dataframe is None
