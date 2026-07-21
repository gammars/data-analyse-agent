from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.agent.artifacts import (
    artifact_context_text,
    sanitize_tool_args_for_context,
    tool_result_context_preview,
)
from app.agent.models import build_chat_model


DEFAULT_CONTEXT_LIMIT_TOKENS = 24000
DEFAULT_COMPACT_THRESHOLD = 0.8
RECENT_MESSAGE_COUNT = 12


class ContextService:
    """Estimate and compact conversation context before model calls."""

    def __init__(
        self,
        context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS,
        compact_threshold: float = DEFAULT_COMPACT_THRESHOLD,
    ) -> None:
        self.context_limit_tokens = context_limit_tokens
        self.compact_threshold = compact_threshold
        self._token_counter_model = None
        self._token_counter_failed = False

    def get_context_stats(self, conversation: dict[str, Any]) -> dict[str, Any]:
        history_messages = self.build_history_messages(conversation)
        estimated_tokens, token_source = self.count_history_tokens(history_messages)
        threshold_tokens = int(self.context_limit_tokens * self.compact_threshold)
        messages = conversation.get("messages", [])
        active_messages = self._messages_after_summary(conversation)
        summary_tokens, summary_token_source = self.count_text_tokens(
            conversation.get("context_summary", "")
        )
        return {
            "estimated_tokens": estimated_tokens,
            "context_limit_tokens": self.context_limit_tokens,
            "compact_threshold": self.compact_threshold,
            "threshold_tokens": threshold_tokens,
            "usage_ratio": round(estimated_tokens / self.context_limit_tokens, 4),
            "should_compact": (
                estimated_tokens >= threshold_tokens
                and len(active_messages) > RECENT_MESSAGE_COUNT
            ),
            "summary_tokens": summary_tokens,
            "token_source": token_source,
            "token_source_label": self._token_source_label(token_source),
            "summary_token_source": summary_token_source,
            "summary_token_source_label": self._token_source_label(summary_token_source),
            "message_count": len(messages),
            "active_message_count": len(active_messages),
            "summarized_message_count": len(messages) - len(active_messages),
        }

    def should_compact_now(self, conversation: dict[str, Any]) -> bool:
        return self.get_context_stats(conversation)["should_compact"]

    def compact_if_needed(self, conversation: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        stats = self.get_context_stats(conversation)
        messages = conversation.get("messages", [])
        active_messages = self._messages_after_summary(conversation)
        if not stats["should_compact"]:
            return conversation, False

        old_messages = active_messages[:-RECENT_MESSAGE_COUNT]
        existing_summary = conversation.get("context_summary", "")
        conversation["context_summary"] = self._summarize_messages(existing_summary, old_messages)
        summarized_count = len(messages) - RECENT_MESSAGE_COUNT
        conversation["context_summary_message_count"] = summarized_count
        conversation["context_summary_through_message_id"] = old_messages[-1].get("message_id")
        return conversation, True

    def build_history_messages(self, conversation: dict[str, Any]) -> list:
        history = []
        summary = conversation.get("context_summary")
        if summary:
            history.append(
                SystemMessage(
                    content=(
                        "以下是较早对话的压缩摘要，用于延续上下文。"
                        "摘要可能省略无关工具日志和原始大结果，但保留用户目标、关键结论、图表、分析产物和重要字段。\n\n"
                        f"{summary}"
                    )
                )
            )

        for message in self._messages_after_summary(conversation):
            role = message.get("role")
            content = self._message_to_context_text(message)
            if not content:
                continue
            if role == "user":
                history.append(HumanMessage(content=content))
            elif role == "assistant":
                history.append(AIMessage(content=content))
            elif role in {"tool", "chart", "plan", "scope", "artifact"}:
                history.append(SystemMessage(content=content))

        return history

    def estimate_conversation_tokens(self, conversation: dict[str, Any]) -> int:
        history_messages = self.build_history_messages(conversation)
        total, _ = self.count_history_tokens(history_messages)
        return total

    def _messages_after_summary(self, conversation: dict[str, Any]) -> list[dict[str, Any]]:
        """Return messages not represented by the persisted context summary.

        Full messages remain append-only for UI history. The message-id cursor is the
        authoritative boundary; the count is retained as a fallback for imported or
        legacy messages without ids. Legacy conversations with a summary but no cursor
        already had their older messages deleted, so every remaining message is active.
        """
        messages = conversation.get("messages", [])
        cursor = conversation.get("context_summary_through_message_id")
        if cursor:
            for index, message in enumerate(messages):
                if message.get("message_id") == cursor:
                    return messages[index + 1 :]

        summarized_count = conversation.get("context_summary_message_count")
        if isinstance(summarized_count, int) and 0 <= summarized_count <= len(messages):
            return messages[summarized_count:]

        return messages

    def estimate_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // 4)

    def count_text_tokens(self, text: str) -> tuple[int, str]:
        if not text:
            return 0, "empty"

        model = self._get_token_counter_model()
        if model is not None:
            try:
                return model.get_num_tokens(text), "model"
            except Exception:
                pass

        return self.estimate_text_tokens(text), "estimated"

    def count_history_tokens(self, history_messages: list[BaseMessage]) -> tuple[int, str]:
        if not history_messages:
            return 0, "empty"

        model = self._get_token_counter_model()
        if model is not None:
            try:
                return model.get_num_tokens_from_messages(history_messages), "model"
            except Exception:
                try:
                    serialized = self._serialize_history_messages(history_messages)
                    return model.get_num_tokens(serialized), "model_text"
                except Exception:
                    pass

        total = 0
        for message in history_messages:
            content = message.content if isinstance(message.content, str) else str(message.content)
            total += self.estimate_text_tokens(content)
        return total, "estimated"

    def _summarize_messages(self, existing_summary: str, messages: list[dict[str, Any]]) -> str:
        source = "\n\n".join(self._message_to_context_text(message) for message in messages)
        prompt = (
            "请把下面的数据分析对话压缩成可继续对话的上下文摘要。"
            "保留：用户目标、绑定数据集相关信息、字段名、SQL/工具关键结果、图表结论、分析产物摘要、未完成事项。"
            "省略重复工具日志、原始大 JSON、完整代码和无关客套。用中文，控制在 800 字以内。\n\n"
            f"已有摘要：\n{existing_summary or '无'}\n\n"
            f"需要压缩的新内容：\n{source}"
        )

        try:
            response = build_chat_model().invoke([HumanMessage(content=prompt)])
            return str(response.content)
        except Exception:
            return self._fallback_summary(existing_summary, messages)

    def _fallback_summary(self, existing_summary: str, messages: list[dict[str, Any]]) -> str:
        snippets = [existing_summary] if existing_summary else []
        for message in messages[-8:]:
            text = self._message_to_context_text(message)
            if text:
                snippets.append(text[:500])
        return "\n\n".join(snippets)[-3000:]

    def _message_to_context_text(self, message: dict[str, Any]) -> str:
        role = message.get("role", "")
        message_type = message.get("type", "")
        if role in {"user", "assistant"}:
            return f"{role}: {message.get('content', '')}"
        if role == "tool":
            return (
                f"tool:{message.get('name', '')}:{message_type}\n"
                f"args={sanitize_tool_args_for_context(message.get('args', {}))}\n"
                f"result_preview={tool_result_context_preview(message.get('name', ''), message.get('result', ''))}"
            )
        if role == "chart":
            return (
                f"chart:{message.get('title', '')}\n"
                f"type={message.get('chart_type', '')}; url={message.get('chart_url', '')}"
            )
        if role == "artifact":
            artifact = message.get("artifact", {})
            if isinstance(artifact, dict):
                return artifact_context_text(artifact)
            return ""
        if role == "plan":
            return f"plan:{message.get('plan', {})}"
        if role == "scope":
            return f"scope:{message.get('scope', {})}"
        return ""

    def _get_token_counter_model(self):
        if self._token_counter_failed:
            return None
        if self._token_counter_model is None:
            try:
                self._token_counter_model = build_chat_model()
            except Exception:
                self._token_counter_failed = True
                return None
        return self._token_counter_model

    def _token_source_label(self, source: str) -> str:
        if source == "model":
            return "模型 tokenizer"
        if source == "model_text":
            return "模型 tokenizer（按文本）"
        if source == "empty":
            return "无上下文"
        return "字符估算"

    def _serialize_history_messages(self, history_messages: list[BaseMessage]) -> str:
        lines: list[str] = []
        for message in history_messages:
            message_type = getattr(message, "type", message.__class__.__name__)
            content = message.content if isinstance(message.content, str) else str(message.content)
            lines.append(f"{message_type}: {content}")
        return "\n\n".join(lines)


context_service = ContextService()
