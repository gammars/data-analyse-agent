from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.state import AgentState

MAX_GRAPH_TOOL_CALLS = 20


def should_continue(state: AgentState) -> str:
    if state.get("error"):
        return "finish"

    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    tool_calls = getattr(last_message, "tool_calls", None)
    total_tool_calls = sum(
        len(getattr(message, "tool_calls", []) or [])
        for message in messages
        if isinstance(message, AIMessage)
    )

    if total_tool_calls >= MAX_GRAPH_TOOL_CALLS:
        return "finish"

    if tool_calls:
        return "tools"

    return "finish"


def make_agent_node(model: BaseChatModel, tools: Sequence[BaseTool]) -> Callable[[AgentState], dict]:
    model_with_tools = model.bind_tools(tools)

    def agent_node(state: AgentState) -> dict:
        response = model_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    return agent_node


def finish_node(state: AgentState) -> dict[str, Any]:
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None

    if isinstance(last_message, AIMessage):
        return {"final_answer": last_message.content}

    return {"final_answer": ""}


def build_agent_graph(agent_node: Callable[[AgentState], dict], tools: Sequence[BaseTool]):
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(list(tools)))
    graph.add_node("finish", finish_node)

    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tools": "tools",
            "finish": "finish",
        },
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("finish", END)

    return graph.compile()
