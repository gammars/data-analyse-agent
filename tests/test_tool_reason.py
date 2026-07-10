from langchain_core.messages import AIMessage, ToolMessage

from app.agent.runtime import (
    _format_duration_ms,
    _generate_tool_reason,
    _latest_tool_result,
    _tool_result_indicates_error,
    _tool_result_success,
)


class FakeReasonModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=self.content)


def test_second_model_generates_reason_for_selected_tool() -> None:
    model = FakeReasonModel("先汇总各小组的平均 FIFA 排名，以便比较首轮比赛的实力差距。")

    reason = _generate_tool_reason(
        reason_model=model,
        user_question="第一轮哪些比赛最有看点？",
        tool_name="query_data",
        tool_args={
            "dataset_id": "secret-id",
            "sql": 'SELECT "Group", AVG("rank") FROM "teams" GROUP BY "Group"',
            "max_rows": 100,
        },
        previous_tool_result="",
    )

    assert reason == "先汇总各小组的平均 FIFA 排名，以便比较首轮比赛的实力差距。"
    assert len(model.calls) == 1
    prompt = model.calls[0][1].content
    assert "secret-id" not in prompt
    assert "max_rows" not in prompt
    assert "GROUP BY" in prompt


def test_reason_model_receives_previous_tool_error() -> None:
    model = FakeReasonModel("上一次字段名不准确，这次使用 schema 中的完整字段名重新查询。")

    reason = _generate_tool_reason(
        reason_model=model,
        user_question="比较各队排名",
        tool_name="query_data",
        tool_args={"sql": 'SELECT "Current FIFA rank" FROM "teams"'},
        previous_tool_result="Binder Error: column not found",
    )

    assert "重新查询" in reason
    assert "Binder Error: column not found" in model.calls[0][1].content


def test_latest_tool_result_uses_most_recent_message() -> None:
    messages = [
        ToolMessage(content="first", tool_call_id="one"),
        AIMessage(content=""),
        ToolMessage(content="latest", tool_call_id="two"),
    ]

    assert _latest_tool_result(messages) == "latest"


def test_tool_result_error_detection_and_duration_format() -> None:
    assert _tool_result_indicates_error("Python 沙箱分析失败：boom") is True
    assert _tool_result_indicates_error("图表已生成：销售趋势") is False
    assert _tool_result_success('{"ok": true, "stderr": "mkdir failed but recovered"}') is True
    assert _tool_result_success('{"ok": false, "error": {"message": "boom"}}') is False
    assert _tool_result_success("Python 沙箱分析失败：boom") is False
    assert _format_duration_ms(120) == "120ms"
    assert _format_duration_ms(1234) == "1.23s"
    assert _format_duration_ms(65_500) == "1m 5.5s"
