from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    dataset_id: str
    user_question: str
    dataset_schema: str
    intermediate_results: list[dict[str, Any]]
    chart_ids: list[str]
    report_ids: list[str]
    final_answer: str
    error: str
