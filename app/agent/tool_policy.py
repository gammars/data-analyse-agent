from __future__ import annotations

from collections.abc import Iterable


MAX_PLAN_STEPS = 5

INTENT_TOOL_POLICY: dict[str, list[str]] = {
    "query": ["query_data"],
    "chart": ["generate_chart"],
    "quality": [
        "profile_data",
        "missing_value_analysis",
        "descriptive_statistics",
        "correlation_analysis",
        "outlier_detection",
    ],
    "advanced": ["python_analysis"],
    "cleaning": ["suggest_cleaning", "apply_cleaning", "reset_cleaning"],
    "mixed": [],
}

TOOL_RETRY_LIMITS: dict[str, int] = {
    "query_data": 2,
    "generate_chart": 2,
    "python_analysis": 3,
    "profile_data": 0,
    "missing_value_analysis": 0,
    "descriptive_statistics": 0,
    "correlation_analysis": 0,
    "outlier_detection": 0,
    "suggest_cleaning": 0,
    "apply_cleaning": 0,
    "reset_cleaning": 0,
}


def allowed_tools_for_intent(intent: str) -> list[str]:
    return list(INTENT_TOOL_POLICY.get(intent, []))


def retry_limit_for_tools(tool_names: Iterable[str]) -> int:
    return max((TOOL_RETRY_LIMITS.get(name, 0) for name in tool_names), default=0)


def sanitize_allowed_tools(intent: str, tool_names: Iterable[str]) -> list[str]:
    intent_tools = set(allowed_tools_for_intent(intent))
    if intent == "mixed":
        intent_tools = {
            tool_name
            for tools in INTENT_TOOL_POLICY.values()
            for tool_name in tools
        }
    cleaned = []
    for tool_name in tool_names:
        if tool_name in intent_tools and tool_name not in cleaned:
            cleaned.append(tool_name)
    return cleaned
