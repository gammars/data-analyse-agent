from __future__ import annotations

import json
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


Scope = Literal["in_scope", "out_of_scope", "needs_clarification", "general_help"]

GENERAL_HELP_KEYWORDS = (
    "怎么用",
    "如何使用",
    "使用方法",
    "工具原理",
    "项目说明",
    "docker",
    "conda",
    "python_analysis",
    "planner",
    "plan",
    "沙箱",
    "执行计划",
    "工具",
    "上传数据",
    "构建镜像",
)

OUT_OF_SCOPE_KEYWORDS = (
    "股市",
    "股票",
    "a股",
    "美股",
    "基金",
    "期货",
    "币圈",
    "比特币",
    "汇率",
    "房价",
    "天气",
    "医疗",
    "法律",
    "合同",
    "政策",
    "利率",
    "宏观经济",
    "明年经济",
    "世界杯",
    "新闻",
)

DOMAIN_SCHEMA_KEYWORDS = {
    "finance": (
        "stock",
        "ticker",
        "symbol",
        "open",
        "close",
        "high",
        "low",
        "volume",
        "return",
        "收益率",
        "股票",
        "股价",
        "指数",
        "开盘",
        "收盘",
        "成交量",
    ),
    "weather": ("weather", "temperature", "humidity", "天气", "气温", "降雨", "湿度"),
    "medical": ("patient", "diagnosis", "medicine", "医疗", "病人", "诊断", "药品"),
    "legal": ("contract", "law", "legal", "合同", "法律", "条款"),
}


class ScopeDecision(BaseModel):
    scope: Scope
    intent: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str
    should_plan: bool | None = None
    response: str = ""

    @field_validator("intent", mode="before")
    @classmethod
    def _coerce_intent(cls, value: object) -> str:
        return str(value or "")

    @model_validator(mode="after")
    def _normalize(self) -> "ScopeDecision":
        self.should_plan = self.scope == "in_scope"
        if self.scope != "in_scope" and not self.response.strip():
            self.response = _default_response(self.scope, self.reason)
        return self


def classify_scope(
    scope_model: Runnable,
    user_question: str,
    schema_text: str,
) -> ScopeDecision:
    rule_decision = classify_scope_by_rules(user_question, schema_text)
    if rule_decision is not None:
        return rule_decision

    try:
        response = scope_model.invoke(
            [
                SystemMessage(content=_scope_system_prompt()),
                HumanMessage(content=_scope_user_prompt(user_question, schema_text)),
            ]
        )
        payload = _extract_json_object(_message_content_to_text(response.content))
        return ScopeDecision.model_validate(payload)
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return _fallback_in_scope_decision(user_question)
    except Exception:
        return _fallback_in_scope_decision(user_question)


def classify_scope_by_rules(user_question: str, schema_text: str) -> ScopeDecision | None:
    normalized_question = user_question.lower()
    normalized_schema = schema_text.lower()

    if _contains_any(normalized_question, GENERAL_HELP_KEYWORDS):
        return ScopeDecision(
            scope="general_help",
            intent="system_help",
            confidence=0.88,
            reason="用户在询问系统、工具、Docker、Planner 或项目使用方式，不需要分析当前数据集。",
            response=(
                "这个问题属于系统使用或工具原理说明，不需要进入数据分析计划，也不会调用数据工具。"
                "我会直接基于项目当前能力进行说明。"
            ),
        )

    if _contains_any(normalized_question, OUT_OF_SCOPE_KEYWORDS):
        domain = _guess_external_domain(normalized_question)
        if not _schema_supports_domain(normalized_schema, domain):
            return ScopeDecision(
                scope="out_of_scope",
                intent=f"external_{domain}",
                confidence=0.92,
                reason="用户问题需要当前数据集之外的外部领域数据，当前 schema 无法支持该分析。",
                response=_out_of_scope_response(domain),
            )

    return None


def _scope_system_prompt() -> str:
    return (
        "你是数据分析 Agent 的 ScopeRouter。你只判断用户问题是否应该使用当前数据集和工具。"
        "不要生成执行计划。\n\n"
        "scope 只能是：in_scope、out_of_scope、needs_clarification、general_help。\n"
        "- in_scope：用户问题可以由当前数据集支持，后续进入 Planner。\n"
        "- out_of_scope：用户问题和当前数据集无关，或需要外部实时/专业数据，不能调用工具。\n"
        "- needs_clarification：问题可能相关，但缺少关键字段、对象或条件，需要先追问。\n"
        "- general_help：用户问系统怎么用、工具原理、Docker、项目说明等，不需要数据工具。\n\n"
        "重要规则：不要为了调用工具而牵强改写用户问题。"
        "如果用户问股市、天气、医疗、法律、宏观经济、实时新闻等，而 schema 没有对应数据，必须 out_of_scope。\n\n"
        "必须严格输出 JSON 对象："
        "{"
        '"scope":"in_scope|out_of_scope|needs_clarification|general_help",'
        '"intent":"简短意图",'
        '"confidence":0.0,'
        '"reason":"判断原因",'
        '"should_plan":true,'
        '"response":"如果不进入 Planner，给用户的中文回答；in_scope 可为空"'
        "}"
    )


def _scope_user_prompt(user_question: str, schema_text: str) -> str:
    return (
        f"用户问题：\n{user_question}\n\n"
        f"当前数据集 schema：\n{schema_text}\n\n"
        "请判断是否应该进入 Planner。"
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
        raise TypeError("scope router output must be a JSON object")
    return payload


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _guess_external_domain(text: str) -> str:
    if _contains_any(text, ("股市", "股票", "a股", "美股", "基金", "期货", "币圈", "比特币", "汇率", "利率", "宏观经济", "明年经济")):
        return "finance"
    if _contains_any(text, ("天气",)):
        return "weather"
    if _contains_any(text, ("医疗", "病", "药")):
        return "medical"
    if _contains_any(text, ("法律", "合同", "条款")):
        return "legal"
    return "external"


def _schema_supports_domain(schema_text: str, domain: str) -> bool:
    keywords = DOMAIN_SCHEMA_KEYWORDS.get(domain, ())
    return bool(keywords and _contains_any(schema_text, keywords))


def _out_of_scope_response(domain: str) -> str:
    if domain == "finance":
        return (
            "这个问题超出了当前数据集的能力范围。当前数据集不能用于预测股市、基金、汇率或宏观走势，"
            "我不会调用数据工具做牵强分析。如果你上传股票指数、行情、财务、估值或宏观经济数据，"
            "我可以帮你做趋势、相关性、波动率和风险分析，但不能提供投资建议。"
        )
    if domain == "weather":
        return "这个问题需要外部天气数据或实时天气服务，当前数据集无法支持，因此不会调用数据工具。"
    if domain == "medical":
        return "这个问题涉及医疗判断，当前数据集和工具无法支持可靠结论。请咨询专业医生。"
    if domain == "legal":
        return "这个问题涉及法律判断，当前数据集和工具无法支持可靠结论。请咨询专业法律人士。"
    return "这个问题和当前数据集无关，或需要外部数据支持，因此不会调用数据分析工具。"


def _default_response(scope: Scope, reason: str) -> str:
    if scope == "general_help":
        return "这个问题属于系统使用或工具说明，不需要调用数据工具。"
    if scope == "needs_clarification":
        return f"这个问题需要先补充信息后才能分析：{reason}"
    if scope == "out_of_scope":
        return f"这个问题当前数据集无法支持，因此不会调用数据工具：{reason}"
    return ""


def _fallback_in_scope_decision(user_question: str) -> ScopeDecision:
    return ScopeDecision(
        scope="in_scope",
        intent="data_analysis",
        confidence=0.5,
        reason="ScopeRouter 未能稳定分类，默认交给 Planner 处理当前数据分析请求。",
        response="",
    )


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
