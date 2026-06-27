import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable

from app.agent.graph import build_agent_graph, make_agent_node
from app.agent.models import build_chat_model
from app.agent.prompts import build_system_message
from app.agent.tools import build_tools
from app.services.analysis_service import AnalysisService
from app.services.chart_service import ChartService
from app.services.dataset_service import DatasetService
from app.services.sql_service import SQLService


MAX_TOOL_ROUNDS = 20


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
    model_with_tools = reason_model.bind_tools(tools)
    messages = [
        build_system_message(schema_text),
        *(history_messages or []),
        HumanMessage(content=f"dataset_id={dataset_id}\n用户问题：{message}"),
    ]

    yield {
        "type": "status",
        "content": "Agent 已接收问题，正在思考。",
    }

    for round_index in range(MAX_TOOL_ROUNDS):
        yield {
            "type": "thinking",
            "content": "模型正在思考下一步...",
        }
        response = yield from _stream_model_message(model_with_tools, messages)
        messages.append(response)

        if not response.tool_calls:
            break

        if round_index == MAX_TOOL_ROUNDS - 1:
            yield {
                "type": "text_delta",
                "content": "\n\n已达到工具调用轮数上限，我将基于当前结果停止继续调用工具。请换一种更明确的问题再试。",
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
                        user_question=message,
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

            if tool is None:
                result = f"工具不存在：{tool_name}"
            else:
                try:
                    result = tool.invoke(tool_args)
                except Exception as exc:
                    result = f"工具执行失败：{exc}"

            yield {
                "type": "tool_end",
                "name": tool_name,
                "args": tool_args,
                "result": result,
            }

            chart_event = _try_build_chart_event(result)
            if chart_event:
                yield chart_event

            messages.append(
                ToolMessage(
                    content=result,
                    tool_call_id=tool_call["id"],
                )
            )

            yield {
                "type": "thinking",
                "content": "工具结果已返回，模型正在思考下一步...",
            }

    yield {"type": "done"}


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


def _try_build_chart_event(tool_result: str) -> dict | None:
    try:
        payload = json.loads(tool_result)
    except json.JSONDecodeError:
        return None

    chart_url = payload.get("chart_url")
    chart_id = payload.get("chart_id")
    if not chart_url or not chart_id:
        return None

    return {
        "type": "chart",
        "chart_id": chart_id,
        "chart_type": payload.get("chart_type"),
        "title": payload.get("title"),
        "chart_url": chart_url,
    }


def _build_tool_reason(tool_name: str, tool_args: dict) -> str:
    if tool_name == "query_data":
        sql = str(tool_args.get("sql") or "").strip()
        if sql:
            return "我需要调用 query_data，因为这个问题需要基于当前数据执行精确 SQL 查询。"
        return "我需要调用 query_data，因为需要从当前数据集中查询出准确结果。"

    if tool_name == "generate_chart":
        chart_type = tool_args.get("chart_type") or "图表"
        return f"我需要调用 generate_chart，因为用户需要可视化结果，我将生成 {chart_type} 图表。"

    if tool_name == "profile_data":
        return "我需要调用 profile_data，因为用户需要整体数据质量和结构概览。"

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
