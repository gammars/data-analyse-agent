import json

from langchain_core.messages import AIMessage

from app.agent.planner import ExecutionPlan, build_execution_plan, build_fallback_plan
from app.agent.tool_policy import retry_limit_for_tools


class FakePlannerModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def invoke(self, messages: list) -> AIMessage:
        return AIMessage(content=json.dumps(self.payload, ensure_ascii=False))


def test_plan_step_sanitizes_allowed_tools_and_sets_retry_limits() -> None:
    plan = ExecutionPlan.model_validate(
        {
            "mode": "single_step",
            "primary_intent": "chart",
            "user_goal": "画销售趋势图",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "chart",
                    "goal": "生成趋势图",
                    "allowed_tools": ["query_data", "generate_chart"],
                    "preferred_tool": "query_data",
                    "depends_on": [],
                    "success_criteria": "生成折线图",
                }
            ],
            "final_response_requirements": [],
        }
    )

    assert plan.steps[0].allowed_tools == ["generate_chart"]
    assert plan.steps[0].preferred_tool == "generate_chart"
    assert plan.steps[0].retry_limit == 2


def test_python_retry_limit_is_three_and_fixed_analysis_is_zero() -> None:
    assert retry_limit_for_tools(["python_analysis"]) == 3
    assert retry_limit_for_tools(["query_data"]) == 2
    assert retry_limit_for_tools(["generate_chart"]) == 2
    assert retry_limit_for_tools(["profile_data", "missing_value_analysis"]) == 0
    assert retry_limit_for_tools(["suggest_cleaning", "apply_cleaning"]) == 0


def test_build_execution_plan_uses_model_json() -> None:
    model = FakePlannerModel(
        {
            "mode": "multi_step",
            "primary_intent": "mixed",
            "user_goal": "先画图，再做复杂分析",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "chart",
                    "goal": "画趋势图",
                    "allowed_tools": ["generate_chart"],
                    "preferred_tool": "generate_chart",
                    "depends_on": [],
                    "success_criteria": "生成图表",
                },
                {
                    "step_id": "step_2",
                    "intent": "advanced",
                    "goal": "检测复杂异常",
                    "allowed_tools": ["python_analysis"],
                    "preferred_tool": "python_analysis",
                    "depends_on": ["step_1"],
                    "success_criteria": "返回异常点",
                },
            ],
            "final_response_requirements": ["汇总图表和异常"],
        }
    )

    plan = build_execution_plan(model, "先画图，再做复杂分析", "数据表数量：1")

    assert plan.primary_intent == "mixed"
    assert [step.intent for step in plan.steps] == ["chart", "advanced"]
    assert plan.steps[1].retry_limit == 3


def test_fallback_plan_supports_mixed_intents() -> None:
    plan = build_fallback_plan("请画趋势图，并做聚类分析")

    assert plan.primary_intent == "mixed"
    assert len(plan.steps) >= 2
    assert {"chart", "advanced"} <= {step.intent for step in plan.steps}


def test_cleaning_plan_requires_explicit_confirmation_for_apply() -> None:
    model = FakePlannerModel(
        {
            "mode": "single_step",
            "primary_intent": "cleaning",
            "user_goal": "帮我清洗数据",
            "steps": [
                {
                    "step_id": "step_1",
                    "intent": "cleaning",
                    "goal": "清洗数据",
                    "allowed_tools": ["apply_cleaning"],
                    "preferred_tool": "apply_cleaning",
                    "depends_on": [],
                    "success_criteria": "完成清洗",
                }
            ],
            "final_response_requirements": [],
        }
    )

    plan = build_execution_plan(model, "帮我清洗数据", "数据表数量：1")

    assert plan.steps[0].allowed_tools == ["suggest_cleaning"]
    assert plan.steps[0].preferred_tool == "suggest_cleaning"
    assert plan.steps[0].retry_limit == 0
