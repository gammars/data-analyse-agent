from __future__ import annotations

import json
import re
import uuid
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.agent.tool_policy import (
    MAX_PLAN_STEPS,
    allowed_tools_for_intent,
    retry_limit_for_tools,
    sanitize_allowed_tools,
)


Intent = Literal["query", "chart", "quality", "advanced", "cleaning", "mixed"]
PlanMode = Literal["single_step", "multi_step"]


class PlanStep(BaseModel):
    step_id: str = Field(..., description="稳定步骤 ID，例如 step_1")
    intent: Intent
    goal: str
    allowed_tools: list[str] = Field(default_factory=list)
    preferred_tool: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    retry_limit: int = 0

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _coerce_allowed_tools(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    @model_validator(mode="after")
    def _apply_tool_policy(self) -> "PlanStep":
        allowed_tools = sanitize_allowed_tools(self.intent, self.allowed_tools)
        if not allowed_tools:
            allowed_tools = allowed_tools_for_intent(self.intent)
        self.allowed_tools = allowed_tools
        if self.preferred_tool not in self.allowed_tools:
            self.preferred_tool = self.allowed_tools[0] if self.allowed_tools else None
        self.retry_limit = retry_limit_for_tools(self.allowed_tools)
        return self


class ExecutionPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    mode: PlanMode
    primary_intent: Intent
    user_goal: str
    steps: list[PlanStep]
    final_response_requirements: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _limit_steps(self) -> "ExecutionPlan":
        self.steps = self.steps[:MAX_PLAN_STEPS]
        if len(self.steps) <= 1:
            self.mode = "single_step"
        else:
            self.mode = "multi_step"
        if len({step.intent for step in self.steps}) > 1:
            self.primary_intent = "mixed"
        return self


def build_execution_plan(
    planner_model: Runnable,
    user_question: str,
    schema_text: str,
) -> ExecutionPlan:
    fallback_plan = build_fallback_plan(user_question)
    try:
        response = planner_model.invoke(
            [
                SystemMessage(content=_planner_system_prompt()),
                HumanMessage(content=_planner_user_prompt(user_question, schema_text)),
            ]
        )
        payload = _extract_json_object(_message_content_to_text(response.content))
        plan = ExecutionPlan.model_validate(payload)
        if not plan.steps:
            return fallback_plan
        return _enforce_cleaning_safety(plan, user_question)
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return fallback_plan
    except Exception:
        return fallback_plan


def build_fallback_plan(user_question: str) -> ExecutionPlan:
    detected_intents = _detect_intents(user_question)
    if not detected_intents:
        detected_intents = ["query"]

    steps = []
    for index, intent in enumerate(detected_intents[:MAX_PLAN_STEPS], start=1):
        allowed_tools = _fallback_allowed_tools(intent, user_question)
        steps.append(
            PlanStep(
                step_id=f"step_{index}",
                intent=intent,
                goal=_fallback_goal(intent, user_question),
                allowed_tools=allowed_tools,
                preferred_tool=allowed_tools[0] if allowed_tools else None,
                depends_on=[] if index == 1 else [f"step_{index - 1}"],
                success_criteria=_fallback_success_criteria(intent),
            )
        )

    return ExecutionPlan(
        mode="multi_step" if len(steps) > 1 else "single_step",
        primary_intent="mixed" if len(steps) > 1 else steps[0].intent,
        user_goal=user_question,
        steps=steps,
        final_response_requirements=["用中文总结每一步结果，并给出直接结论。"],
    )


def _enforce_cleaning_safety(plan: ExecutionPlan, user_question: str) -> ExecutionPlan:
    text = user_question.lower()
    for step in plan.steps:
        if step.intent != "cleaning":
            continue
        if "恢复" in text or "撤销" in text or "reset" in text:
            step.allowed_tools = ["reset_cleaning"]
        elif any(keyword in text for keyword in ("确认", "执行", "应用", "apply")):
            step.allowed_tools = ["apply_cleaning"]
        else:
            step.allowed_tools = ["suggest_cleaning"]
        step.preferred_tool = step.allowed_tools[0]
        step.retry_limit = retry_limit_for_tools(step.allowed_tools)
    return plan


def _planner_system_prompt() -> str:
    return (
        "你是数据分析 Agent 的 Planner。你的任务是把用户问题拆成最多 5 个可执行 step，"
        "每个 step 只能属于这些 intent：query、chart、quality、advanced、cleaning、mixed。\n"
        "必须严格输出 JSON 对象，不要输出 Markdown。\n\n"
        "工具选择规则：\n"
        "- query: 只能用 query_data，适合简单统计、筛选、排序、分组。\n"
        "- chart: 只能用 generate_chart，适合基础柱状图、折线图、饼图、散点图。\n"
        "- quality: 可用 profile_data、missing_value_analysis、descriptive_statistics、"
        "correlation_analysis、outlier_detection，适合数据质量、缺失值、描述统计、简单相关性、异常值。\n"
        "- advanced: 只能用 python_analysis，适合聚类、建模、时间序列、相关性热力图、复杂异常检测、多步骤统计。\n"
        "- cleaning: 可用 suggest_cleaning、apply_cleaning、reset_cleaning；用户没有明确确认时只能建议，不能执行清洗。\n"
        "- mixed: 只作为 primary_intent 或复杂步骤的标记；能拆开就拆成具体 intent step。\n\n"
        "输出 schema：\n"
        "{"
        '"mode":"single_step|multi_step",'
        '"primary_intent":"query|chart|quality|advanced|cleaning|mixed",'
        '"user_goal":"...",'
        '"steps":[{'
        '"step_id":"step_1",'
        '"intent":"query|chart|quality|advanced|cleaning|mixed",'
        '"goal":"...",'
        '"allowed_tools":["..."],'
        '"preferred_tool":"...",'
        '"depends_on":[],'
        '"success_criteria":"..."'
        "}],"
        '"final_response_requirements":["..."]'
        "}"
    )


def _planner_user_prompt(user_question: str, schema_text: str) -> str:
    return (
        f"用户问题：\n{user_question}\n\n"
        f"当前数据集 schema：\n{schema_text}\n\n"
        "请生成执行计划。不要超过 5 个 step。"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", stripped)
        if not match:
            raise
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise TypeError("planner output must be a JSON object")
    return payload


def _detect_intents(user_question: str) -> list[Intent]:
    text = user_question.lower()
    intents: list[Intent] = []
    keyword_groups: list[tuple[Intent, tuple[str, ...]]] = [
        ("cleaning", ("清洗", "去重", "填充", "恢复原始", "撤销清洗", "应用清洗", "执行清洗")),
        ("advanced", ("聚类", "建模", "预测", "时间序列", "热力图", "复杂", "机器学习", "多步骤")),
        ("chart", ("画图", "图表", "可视化", "趋势图", "柱状图", "折线图", "饼图", "散点图")),
        ("quality", ("数据质量", "缺失", "描述性统计", "字段类型", "概览", "相关性", "异常值", "离群")),
        ("query", ("统计", "查询", "多少", "排名", "top", "平均", "总计", "分组", "筛选")),
    ]
    for intent, keywords in keyword_groups:
        if any(keyword in text for keyword in keywords):
            intents.append(intent)
    if "advanced" in intents and "quality" in intents and "热力图" in text:
        intents.remove("quality")
    return intents


def _fallback_allowed_tools(intent: Intent, user_question: str) -> list[str]:
    if intent != "cleaning":
        return allowed_tools_for_intent(intent)
    text = user_question.lower()
    if "恢复" in text or "撤销" in text or "reset" in text:
        return ["reset_cleaning"]
    if any(keyword in text for keyword in ("确认", "执行", "应用", "apply")):
        return ["apply_cleaning"]
    return ["suggest_cleaning"]


def _fallback_goal(intent: Intent, user_question: str) -> str:
    goals = {
        "query": "使用 SQL 查询回答用户的统计或筛选问题。",
        "chart": "生成用户需要的基础可视化图表。",
        "quality": "分析数据质量、缺失值、描述统计、相关性或异常值情况。",
        "advanced": "使用 Python 沙箱完成普通 SQL 难以覆盖的复杂分析。",
        "cleaning": "根据用户意图生成清洗建议、执行确认过的清洗或恢复原始数据。",
        "mixed": "拆解并完成用户的复合分析目标。",
    }
    return f"{goals[intent]} 用户目标：{user_question}"


def _fallback_success_criteria(intent: Intent) -> str:
    criteria = {
        "query": "得到准确查询结果并解释关键结论。",
        "chart": "生成图表并解释图表表达的结论。",
        "quality": "返回对应的数据质量或统计分析结果。",
        "advanced": "返回 Python 分析 result.json、必要指标和图表。",
        "cleaning": "完成安全的清洗建议或用户确认过的清洗动作。",
        "mixed": "完成复合目标中的当前步骤。",
    }
    return criteria[intent]


def _message_content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content or "")
