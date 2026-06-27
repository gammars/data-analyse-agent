from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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

    def get_context_stats(self, conversation: dict[str, Any]) -> dict[str, Any]:
        estimated_tokens = self.estimate_conversation_tokens(conversation)
        threshold_tokens = int(self.context_limit_tokens * self.compact_threshold)
        return {
            "estimated_tokens": estimated_tokens,
            "context_limit_tokens": self.context_limit_tokens,
            "compact_threshold": self.compact_threshold,
            "threshold_tokens": threshold_tokens,
            "usage_ratio": round(estimated_tokens / self.context_limit_tokens, 4),
            "should_compact": estimated_tokens >= threshold_tokens,
            "summary_tokens": self.estimate_text_tokens(conversation.get("context_summary", "")),
            "message_count": len(conversation.get("messages", [])),
        }

    def should_compact_now(self, conversation: dict[str, Any]) -> bool:
        stats = self.get_context_stats(conversation)
        return stats["should_compact"] and len(conversation.get("messages", [])) > RECENT_MESSAGE_COUNT

    def compact_if_needed(self, conversation: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        stats = self.get_context_stats(conversation)
        messages = conversation.get("messages", [])
        if not stats["should_compact"] or len(messages) <= RECENT_MESSAGE_COUNT:
            return conversation, False

        old_messages = messages[:-RECENT_MESSAGE_COUNT]
        recent_messages = messages[-RECENT_MESSAGE_COUNT:]
        existing_summary = conversation.get("context_summary", "")
        conversation["context_summary"] = self._summarize_messages(existing_summary, old_messages)
        conversation["messages"] = recent_messages
        return conversation, True

    def build_history_messages(self, conversation: dict[str, Any]) -> list:
        history = []
        summary = conversation.get("context_summary")
        if summary:
            history.append(
                SystemMessage(
                    content=(
                        "以下是较早对话的压缩摘要，用于延续上下文。"
                        "摘要可能省略无关工具日志，但保留用户目标、关键结论、图表和重要字段。\n\n"
                        f"{summary}"
                    )
                )
            )

        for message in conversation.get("messages", [])[-RECENT_MESSAGE_COUNT:]:
            role = message.get("role")
            content = self._message_to_context_text(message)
            if not content:
                continue
            if role == "user":
                history.append(HumanMessage(content=content))
            elif role == "assistant":
                history.append(AIMessage(content=content))
            elif role in {"tool", "chart"}:
                history.append(SystemMessage(content=content))

        return history

    def estimate_conversation_tokens(self, conversation: dict[str, Any]) -> int:
        total = self.estimate_text_tokens(conversation.get("context_summary", ""))
        for message in conversation.get("messages", []):
            total += self.estimate_text_tokens(self._message_to_context_text(message))
        return total

    def estimate_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _summarize_messages(self, existing_summary: str, messages: list[dict[str, Any]]) -> str:
        source = "\n\n".join(self._message_to_context_text(message) for message in messages)
        prompt = (
            "请把下面的数据分析对话压缩成可继续对话的上下文摘要。"
            "保留：用户目标、绑定数据集相关信息、字段名、SQL/工具关键结果、图表结论、未完成事项。"
            "省略重复工具日志和无关客套。用中文，控制在 800 字以内。\n\n"
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
                f"args={message.get('args', {})}\n"
                f"result={message.get('result', '')}"
            )
        if role == "chart":
            return (
                f"chart:{message.get('title', '')}\n"
                f"type={message.get('chart_type', '')}; url={message.get('chart_url', '')}"
            )
        return ""


context_service = ContextService()
