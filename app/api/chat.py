import logging
import os
import traceback
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.runtime import ask_data_agent, stream_data_agent_events
from app.services.analysis_service import AnalysisService
from app.services.chart_service import ChartService
from app.services.conversation_service import conversation_service
from app.services.context_service import context_service
from app.services.dataset_service import dataset_service
from app.services.sql_service import SQLService


router = APIRouter()
sql_service = SQLService(dataset_service)
chart_service = ChartService()
analysis_service = AnalysisService(dataset_service)
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    dataset_id: str | None = Field(None, description="数据集 ID")
    conversation_id: str | None = Field(None, description="对话 ID")
    message: str = Field(..., description="用户自然语言问题")


@router.post("/chat")
def chat(req: ChatRequest) -> dict:
    try:
        conversation, dataset_id = _resolve_conversation(req)
        conversation_id = conversation["conversation_id"]
        conversation, compacted = _compact_conversation_if_needed(conversation)
        history_messages = context_service.build_history_messages(conversation)
        conversation_service.append_message(
            conversation_id,
            {
                "role": "user",
                "type": "text",
                "content": req.message,
            },
        )
        result = ask_data_agent(
            dataset_service=dataset_service,
            sql_service=sql_service,
            chart_service=chart_service,
            analysis_service=analysis_service,
            dataset_id=dataset_id,
            message=req.message,
            history_messages=history_messages,
        )
        saved_messages = [
            {
                "role": "tool",
                "type": "tool_end",
                "name": tool_call.get("name"),
                "args": tool_call.get("args", {}),
                "result": tool_call.get("result", ""),
            }
            for tool_call in result.get("tool_calls", [])
        ]
        if result.get("answer"):
            saved_messages.append(
                {
                    "role": "assistant",
                    "type": "text",
                    "content": result["answer"],
                }
            )
        conversation_service.append_messages(conversation_id, saved_messages)
        return result | {"conversation_id": conversation_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Agent chat failed")
        detail = {
            "message": f"Agent 调用失败：{exc}",
            "error_type": exc.__class__.__name__,
        }
        if os.getenv("APP_ENV", "development") == "development":
            detail["traceback"] = traceback.format_exc()

        raise HTTPException(status_code=500, detail=detail) from exc


@router.post("/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    def event_stream():
        assistant_text = ""
        saved_messages = []
        conversation_id = ""

        def flush_assistant_text() -> None:
            nonlocal assistant_text
            if not assistant_text.strip():
                assistant_text = ""
                return
            saved_messages.append(
                {
                    "role": "assistant",
                    "type": "text",
                    "content": assistant_text,
                }
            )
            assistant_text = ""

        try:
            conversation, dataset_id = _resolve_conversation(req)
            conversation_id = conversation["conversation_id"]
            if context_service.should_compact_now(conversation):
                yield _format_sse(
                    {
                        "type": "context_compacting",
                        "content": "上下文较长，正在压缩早期对话，请稍候...",
                        **context_service.get_context_stats(conversation),
                    }
                )
            conversation, compacted = _compact_conversation_if_needed(conversation)
            history_messages = context_service.build_history_messages(conversation)
            yield _format_sse(
                {
                    "type": "context",
                    "compacted": compacted,
                    **context_service.get_context_stats(conversation),
                }
            )
            conversation_service.append_message(
                conversation_id,
                {
                    "role": "user",
                    "type": "text",
                    "content": req.message,
                },
            )
            yield _format_sse(
                {
                    "type": "conversation",
                    "conversation_id": conversation_id,
                    "dataset_id": dataset_id,
                }
            )
            for event in stream_data_agent_events(
                dataset_service=dataset_service,
                sql_service=sql_service,
                chart_service=chart_service,
                analysis_service=analysis_service,
                dataset_id=dataset_id,
                message=req.message,
                history_messages=history_messages,
            ):
                if event.get("type") == "tool_start":
                    flush_assistant_text()
                    saved_messages.append(
                        {
                            "role": "tool",
                            "type": "tool_start",
                            "name": event.get("name"),
                            "args": event.get("args", {}),
                        }
                    )
                elif event.get("type") == "tool_reason":
                    flush_assistant_text()
                    saved_messages.append(
                        {
                            "role": "assistant",
                            "type": "text",
                            "content": event.get("content", ""),
                        }
                    )
                elif event.get("type") == "tool_end":
                    saved_messages.append(
                        {
                            "role": "tool",
                            "type": "tool_end",
                            "name": event.get("name"),
                            "args": event.get("args", {}),
                            "result": event.get("result", ""),
                        }
                    )
                elif event.get("type") == "chart":
                    saved_messages.append(
                        {
                            "role": "chart",
                            "type": "chart",
                            "chart_id": event.get("chart_id"),
                            "chart_type": event.get("chart_type"),
                            "title": event.get("title"),
                            "chart_url": event.get("chart_url"),
                        }
                    )
                elif event.get("type") == "text_delta":
                    assistant_text += event.get("content", "")
                yield _format_sse(event)

            flush_assistant_text()
            conversation_service.append_messages(conversation_id, saved_messages)
        except Exception as exc:
            logger.exception("Agent chat stream failed")
            detail = {
                "message": f"Agent 调用失败：{exc}",
                "error_type": exc.__class__.__name__,
            }
            if os.getenv("APP_ENV", "development") == "development":
                detail["traceback"] = traceback.format_exc()
            yield _format_sse({"type": "error", "detail": detail})
            yield _format_sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event: dict) -> str:
    event_type = event.get("type", "message")
    payload = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _resolve_conversation(req: ChatRequest) -> tuple[dict, str]:
    if req.conversation_id:
        conversation = conversation_service.get_conversation(req.conversation_id)
        return conversation, conversation["dataset_id"]

    if not req.dataset_id:
        raise ValueError("缺少 dataset_id 或 conversation_id")

    dataset_service.get_summary(req.dataset_id)
    conversation = conversation_service.create_conversation(dataset_id=req.dataset_id)
    return conversation, req.dataset_id


def _compact_conversation_if_needed(conversation: dict) -> tuple[dict, bool]:
    compacted_conversation, compacted = context_service.compact_if_needed(conversation)
    if compacted:
        conversation_service.save_conversation(compacted_conversation)
    return compacted_conversation, compacted
