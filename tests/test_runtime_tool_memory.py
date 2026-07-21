from langchain_core.messages import AIMessage

from app.agent.planner import ExecutionPlan, PlanStep
from app.agent.runtime import _build_step_instruction, _execute_plan_step
from app.services.tool_memory_service import ToolMemoryService


SCHEMA = "CREATE TABLE orders (order_id TEXT, order_purchase_timestamp TEXT);"


class SequencedModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = list(responses)

    def stream(self, messages: list):
        del messages
        yield self.responses.pop(0)


class SequencedTool:
    def __init__(self, results: list[str]) -> None:
        self.results = list(results)

    def invoke(self, args: dict) -> str:
        del args
        return self.results.pop(0)


def test_step_instruction_includes_dataset_memory_context() -> None:
    step = PlanStep(
        step_id="step_1",
        intent="query",
        goal="统计每日订单量",
        allowed_tools=["query_data"],
        success_criteria="返回每日订单数量",
    )
    plan = ExecutionPlan(
        mode="single_step",
        primary_intent="query",
        user_goal="按日期统计订单",
        steps=[step],
    )
    memory_context = (
        "本数据集历史工具经验（仅在相关时参考）：\n"
        "1. 字段 order_date 不存在，应使用 order_purchase_timestamp。"
    )

    instruction = _build_step_instruction(
        plan,
        step,
        1,
        memory_context=memory_context,
    )

    assert memory_context in instruction
    assert "本步骤只能调用这些工具：query_data" in instruction


def test_retry_success_writes_memory_and_emits_event(tmp_path) -> None:
    failed_call = {
        "name": "query_data",
        "args": {
            "sql": "SELECT order_date, COUNT(*) FROM orders GROUP BY order_date"
        },
        "id": "call-failed",
        "type": "tool_call",
    }
    successful_call = {
        "name": "query_data",
        "args": {
            "sql": (
                "SELECT date(order_purchase_timestamp), COUNT(*) FROM orders "
                "GROUP BY date(order_purchase_timestamp)"
            )
        },
        "id": "call-success",
        "type": "tool_call",
    }
    model = SequencedModel(
        [
            AIMessage(content="先执行查询。", tool_calls=[failed_call]),
            AIMessage(content="修正字段后重试。", tool_calls=[successful_call]),
            AIMessage(content="步骤完成。"),
        ]
    )
    tool = SequencedTool(
        [
            "SQL 查询执行失败：no such column: order_date",
            "查询成功，共 3 行。",
        ]
    )
    step = PlanStep(
        step_id="step_1",
        intent="query",
        goal="统计每日订单量",
        allowed_tools=["query_data"],
    )
    service = ToolMemoryService(tmp_path)
    messages = []

    events = list(
        _execute_plan_step(
            model_with_tools=model,
            reason_model=model,
            messages=messages,
            tools_by_name={"query_data": tool},
            user_question="统计每日订单量",
            step=step,
            dataset_id="dataset-1",
            schema_text=SCHEMA,
            memory_service=service,
        )
    )

    memory_events = [event for event in events if event["type"] == "memory_write"]
    assert len(memory_events) == 1
    assert "order_date" in memory_events[0]["memory"]["lesson"]
    assert len(service.load_memories("dataset-1")) == 1
    assert "失败反思" in messages[1].content


def test_unrepaired_failure_is_not_persisted(tmp_path) -> None:
    failed_call = {
        "name": "query_data",
        "args": {"sql": "SELECT missing_column FROM orders"},
        "id": "call-failed",
        "type": "tool_call",
    }
    model = SequencedModel(
        [
            AIMessage(content="执行查询。", tool_calls=[failed_call]),
            AIMessage(content="无法继续。"),
        ]
    )
    tool = SequencedTool(["SQL 查询执行失败：no such column: missing_column"])
    step = PlanStep(
        step_id="step_1",
        intent="query",
        goal="查询字段",
        allowed_tools=["query_data"],
    )
    service = ToolMemoryService(tmp_path)

    events = list(
        _execute_plan_step(
            model_with_tools=model,
            reason_model=model,
            messages=[],
            tools_by_name={"query_data": tool},
            user_question="查询字段",
            step=step,
            dataset_id="dataset-1",
            schema_text=SCHEMA,
            memory_service=service,
        )
    )

    assert not any(event["type"] == "memory_write" for event in events)
    assert service.load_memories("dataset-1") == []
