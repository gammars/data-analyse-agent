import json
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable

from app.agent.graph import build_agent_graph, make_agent_node
from app.agent.models import build_chat_model
from app.agent.planner import ExecutionPlan, PlanStep, build_execution_plan
from app.agent.prompts import build_system_message
from app.agent.tool_policy import MAX_PLAN_STEPS
from app.agent.tools import build_tools
from app.services.analysis_service import AnalysisService
from app.services.chart_service import ChartService
from app.services.dataset_service import DatasetService
from app.services.sql_service import SQLService


MAX_TOOL_ROUNDS = 20
MAX_STEP_TOOL_ROUNDS = 6


def ask_data_agent(
    dataset_service: DatasetService,
    sql_service: SQLService,
    chart_service: ChartService,
    analysis_service: AnalysisService,
    dataset_id: str,
    message: str,
    history_messages: list | None = None,
) -> dict:
    schema_text = dataset_service.get_schema(dataset_id)
    tools = build_tools(sql_service, chart_service, analysis_service)
    model = build_chat_model()
    agent_node = make_agent_node(model, tools)
    graph = build_agent_graph(agent_node, tools)

    result = graph.invoke(
        {
            "messages": [
                build_system_message(schema_text),
                *(history_messages or []),
                HumanMessage(content=f"dataset_id={dataset_id}\n用户问题：{message}"),
            ],
            "dataset_id": dataset_id,
            "user_question": message,
            "dataset_schema": schema_text,
        },
        config={"recursion_limit": 50},
    )

    return {
        "answer": result.get("final_answer", ""),
        "tool_calls": _collect_tool_calls(result.get("messages", [])),
    }


def stream_data_agent_events(
    dataset_service: DatasetService,
    sql_service: SQLService,
    chart_service: ChartService,
    analysis_service: AnalysisService,
    dataset_id: str,
    message: str,
    history_messages: list | None = None,
):
    schema_text = dataset_service.get_schema(dataset_id)
    tools = build_tools(sql_service, chart_service, analysis_service)
    tools_by_name = {tool.name: tool for tool in tools}
    reason_model = build_chat_model()
    messages = [
        build_system_message(schema_text),
        *(history_messages or []),
        HumanMessage(content=f"dataset_id={dataset_id}\n用户问题：{message}"),
    ]

    yield {
        "type": "status",
        "content": "Agent 已接收问题，正在思考。",
    }

    yield {
        "type": "thinking",
        "content": "正在生成执行计划...",
    }
    plan = build_execution_plan(
        planner_model=reason_model,
        user_question=message,
        schema_text=schema_text,
    )
    yield {
        "type": "plan",
        "plan": plan.model_dump(),
    }

    for step_index, step in enumerate(plan.steps[:MAX_PLAN_STEPS], start=1):
        selected_tools = [
            tools_by_name[tool_name]
            for tool_name in step.allowed_tools
            if tool_name in tools_by_name
        ]
        if not selected_tools:
            continue

        yield {
            "type": "plan_step_start",
            "plan_id": plan.plan_id,
            "step_id": step.step_id,
            "step_index": step_index,
            "total_steps": len(plan.steps),
            "intent": step.intent,
            "goal": step.goal,
            "allowed_tools": step.allowed_tools,
            "retry_limit": step.retry_limit,
        }

        messages.append(HumanMessage(content=_build_step_instruction(plan, step, step_index)))
        model_with_tools = reason_model.bind_tools(selected_tools)
        step_success = yield from _execute_plan_step(
            model_with_tools=model_with_tools,
            reason_model=reason_model,
            messages=messages,
            tools_by_name={tool.name: tool for tool in selected_tools},
            user_question=message,
            step=step,
        )

        yield {
            "type": "plan_step_end",
            "plan_id": plan.plan_id,
            "step_id": step.step_id,
            "success": step_success,
        }

    yield {
        "type": "thinking",
        "content": "计划步骤已完成，正在汇总结论...",
    }
    messages.append(
        HumanMessage(
            content=(
                "请基于以上计划步骤和工具结果，用中文汇总回答用户的原始问题。"
                "突出关键结论、图表和必要的限制说明；不要再调用工具。"
            )
        )
    )
    yield from _stream_model_message(reason_model, messages)
    yield {"type": "done"}


def _execute_plan_step(
    model_with_tools: Runnable,
    reason_model: Runnable,
    messages: list,
    tools_by_name: dict,
    user_question: str,
    step: PlanStep,
) -> bool:
    failed_attempts = 0
    step_success = False

    for round_index in range(MAX_STEP_TOOL_ROUNDS):
        yield {
            "type": "thinking",
            "content": f"正在执行计划步骤：{step.goal}",
        }
        response = yield from _stream_model_message(model_with_tools, messages)
        messages.append(response)

        if not response.tool_calls:
            break

        if round_index == MAX_STEP_TOOL_ROUNDS - 1:
            yield {
                "type": "text_delta",
                "content": "\n\n当前计划步骤已达到工具调用轮数上限，我将基于已有结果继续后续步骤。",
            }
            break

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args", {})
            tool = tools_by_name.get(tool_name)

            if not _message_content_to_text(response.content).strip():
                yield {
                    "type": "tool_reason",
                    "content": _generate_tool_reason(
                        reason_model=reason_model,
                        user_question=user_question,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        previous_tool_result=_latest_tool_result(messages[:-1]),
                    ),
                    "name": tool_name,
                    "args": tool_args,
                }

            yield {
                "type": "tool_start",
                "name": tool_name,
                "args": tool_args,
            }

            started_at = time.perf_counter()
            tool_success = True
            if tool is None:
                result = f"工具不存在：{tool_name}"
                tool_success = False
            else:
                try:
                    result = tool.invoke(tool_args)
                except Exception as exc:
                    result = f"工具执行失败：{exc}"
                    tool_success = False

            duration_ms = int((time.perf_counter() - started_at) * 1000)
            tool_success = tool_success and _tool_result_success(result)
            step_success = step_success or tool_success

            yield {
                "type": "tool_end",
                "name": tool_name,
                "args": tool_args,
                "result": result,
                "duration_ms": duration_ms,
                "duration_label": _format_duration_ms(duration_ms),
                "success": tool_success,
            }

            for chart_event in _try_build_chart_events(result):
                yield chart_event

            messages.append(
                ToolMessage(
                    content=result,
                    tool_call_id=tool_call["id"],
                )
            )

            if not tool_success:
                failed_attempts += 1
                if failed_attempts > step.retry_limit:
                    messages.append(
                        HumanMessage(
                            content=(
                                f"计划步骤 {step.step_id} 已超过重试次数 "
                                f"({step.retry_limit})。请停止重试该步骤，"
                                "基于已有结果继续后续步骤或在最终回答中说明失败原因。"
                            )
                        )
                    )
                    return False

            yield {
                "type": "thinking",
                "content": "工具结果已返回，模型正在思考下一步...",
            }

    return step_success


def _build_step_instruction(plan: ExecutionPlan, step: PlanStep, step_index: int) -> str:
    allowed_tools = "、".join(step.allowed_tools)
    return (
        f"现在执行计划 {plan.plan_id} 的第 {step_index}/{len(plan.steps)} 步。\n"
        f"用户原始目标：{plan.user_goal}\n"
        f"当前步骤意图：{step.intent}\n"
        f"当前步骤目标：{step.goal}\n"
        f"成功标准：{step.success_criteria or '完成当前步骤目标'}\n"
        f"本步骤只能调用这些工具：{allowed_tools}。\n"
        f"优先工具：{step.preferred_tool or '无'}。\n"
        f"本步骤失败重试上限：{step.retry_limit}。\n"
        "如果本步骤需要工具，必须只从允许工具中选择；不要尝试调用本步骤未列出的工具。"
        "如果信息不足以安全执行，请直接说明需要用户补充什么。"
    )


def _stream_model_message(model_with_tools: Runnable, messages: list) -> AIMessage:
    response = None

    for chunk in model_with_tools.stream(messages):
        response = chunk if response is None else response + chunk
        content = getattr(chunk, "content", "")
        if content:
            yield {
                "type": "text_delta",
                "content": content,
            }

    if response is None:
        return AIMessage(content="")

    return AIMessage(
        content=response.content,
        tool_calls=getattr(response, "tool_calls", []),
        additional_kwargs=getattr(response, "additional_kwargs", {}),
        response_metadata=getattr(response, "response_metadata", {}),
    )


def _try_build_chart_events(tool_result: str) -> list[dict]:
    try:
        payload = json.loads(tool_result)
    except json.JSONDecodeError:
        return []

    events = []
    candidates = []
    if isinstance(payload.get("figures"), list):
        candidates.extend(payload["figures"])
    nested_result = payload.get("result")
    if isinstance(nested_result, dict) and isinstance(nested_result.get("figures"), list):
        candidates.extend(nested_result["figures"])
    if payload.get("chart_url") and payload.get("chart_id"):
        candidates.append(payload)

    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        chart_url = candidate.get("chart_url")
        chart_id = candidate.get("chart_id")
        if not chart_url or not chart_id or chart_id in seen:
            continue
        seen.add(chart_id)
        events.append(
            {
                "type": "chart",
                "chart_id": chart_id,
                "chart_type": candidate.get("chart_type") or "python",
                "title": candidate.get("title"),
                "chart_url": chart_url,
            }
        )
    return events


def _tool_result_indicates_error(result: object) -> bool:
    text = _message_content_to_text(result).strip()
    if not text:
        return False
    error_markers = (
        "失败",
        "错误",
        "不存在",
        "未生成",
        "not found",
        "error",
        "failed",
        "traceback",
    )
    lowered = text.lower()
    return any(marker in lowered for marker in error_markers)


def _tool_result_success(result: object) -> bool:
    text = _message_content_to_text(result).strip()
    if not text:
        return True

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return not _tool_result_indicates_error(text)

    if not isinstance(payload, dict):
        return True

    if "ok" in payload:
        return bool(payload["ok"])
    if payload.get("error"):
        return False
    return True


def _format_duration_ms(duration_ms: int) -> str:
    safe_duration = max(int(duration_ms), 0)
    if safe_duration < 1000:
        return f"{safe_duration}ms"
    seconds = safe_duration / 1000
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes = int(seconds // 60)
    remaining_seconds = seconds - minutes * 60
    return f"{minutes}m {remaining_seconds:.1f}s"


def _build_tool_reason(tool_name: str, tool_args: dict) -> str:
    if tool_name == "query_data":
        sql = str(tool_args.get("sql") or "").strip()
        if sql:
            return "我需要调用 query_data，因为这个问题需要基于当前数据执行精确 SQL 查询。"
        return "我需要调用 query_data，因为需要从当前数据集中查询出准确结果。"

    if tool_name == "generate_chart":
        chart_type = tool_args.get("chart_type") or "图表"
        return f"我需要调用 generate_chart，因为用户需要可视化结果，我将生成 {chart_type} 图表。"

    if tool_name == "python_analysis":
        goal = str(tool_args.get("analysis_goal") or "").strip()
        if goal:
            return f"我需要调用 python_analysis，在 Docker 沙箱中执行 Python 来完成复杂分析：{goal}"
        return "我需要调用 python_analysis，因为当前问题需要普通 SQL 之外的复杂统计分析。"

    if tool_name == "profile_data":
        return "我需要调用 profile_data，因为用户需要整体数据质量和结构概览。"

    cleaning_reasons = {
        "suggest_cleaning": "我先检查 processed 数据中的质量问题并生成建议，不会修改数据。",
        "apply_cleaning": "用户已确认清洗方案，我将更新 processed 数据并重建 SQLite。",
        "reset_cleaning": "用户要求撤销清洗，我将从 raw 原件恢复 processed 数据。",
    }
    if tool_name in cleaning_reasons:
        return cleaning_reasons[tool_name]

    analysis_tools = {
        "missing_value_analysis": "缺失值情况",
        "descriptive_statistics": "描述性统计",
        "correlation_analysis": "字段相关性",
        "outlier_detection": "异常值情况",
    }
    if tool_name in analysis_tools:
        return f"我需要调用 {tool_name}，因为用户问题需要分析{analysis_tools[tool_name]}。"

    return f"我需要调用 {tool_name}，因为当前问题需要这个工具返回数据结果。"


def _generate_tool_reason(
    reason_model: Runnable,
    user_question: str,
    tool_name: str,
    tool_args: dict,
    previous_tool_result: str,
) -> str:
    visible_args = {
        key: value
        for key, value in tool_args.items()
        if key not in {"dataset_id", "max_rows"}
    }
    previous_context = previous_tool_result[-1600:] if previous_tool_result else "无，这是首次工具调用。"
    prompt = (
        f"用户问题：{user_question}\n\n"
        f"即将调用的工具：{tool_name}\n"
        f"工具参数：{json.dumps(visible_args, ensure_ascii=False)}\n\n"
        f"上一次工具结果或错误：{previous_context}\n\n"
        "请说明这一步具体要获取、核对、修正或分析什么，以及它如何推进用户问题。"
    )

    try:
        response = reason_model.invoke(
            [
                SystemMessage(
                    content=(
                        "你负责在数据分析 Agent 执行工具前，向用户解释本轮操作原因。"
                        "只输出一句简短自然的中文，不使用 Markdown，不泄露 dataset_id，"
                        "不要只说‘基于当前数据执行精确查询’，不要泛泛复述工具名称。"
                        "如果上一次调用出错，要明确说明本轮正在修正什么。"
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        reason = _message_content_to_text(getattr(response, "content", ""))
        reason = " ".join(reason.split()).strip()
        if reason:
            return reason[:240]
    except Exception:
        pass

    return _build_tool_reason(tool_name, tool_args)


def _latest_tool_result(messages: list) -> str:
    for item in reversed(messages):
        if isinstance(item, ToolMessage):
            return _message_content_to_text(item.content)
    return ""


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


def _collect_tool_calls(messages: list) -> list[dict]:
    tool_results_by_id = {
        message.tool_call_id: message.content
        for message in messages
        if isinstance(message, ToolMessage)
    }

    tool_calls = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue

        for tool_call in message.tool_calls:
            tool_calls.append(
                {
                    "name": tool_call["name"],
                    "args": tool_call["args"],
                    "result": tool_results_by_id.get(tool_call["id"], ""),
                }
            )

    return tool_calls
