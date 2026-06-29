from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.services.conversation_service import ConversationService
from app.services.context_service import ContextService


def make_messages(count: int, content_size: int = 40) -> list[dict]:
    return [
        {
            "message_id": f"message-{index}",
            "role": "user" if index % 2 == 0 else "assistant",
            "type": "text",
            "content": f"message {index} " + ("x" * content_size),
        }
        for index in range(count)
    ]


def test_compaction_preserves_full_history_and_records_boundary(monkeypatch) -> None:
    service = ContextService(context_limit_tokens=100, compact_threshold=0.5)
    conversation = {"messages": make_messages(20)}
    original_messages = list(conversation["messages"])
    summarized = []

    def fake_summary(existing_summary: str, messages: list[dict]) -> str:
        summarized.append((existing_summary, messages))
        return "compressed history"

    monkeypatch.setattr(service, "_summarize_messages", fake_summary)

    compacted_conversation, compacted = service.compact_if_needed(conversation)

    assert compacted is True
    assert compacted_conversation["messages"] == original_messages
    assert compacted_conversation["context_summary"] == "compressed history"
    assert compacted_conversation["context_summary_message_count"] == 8
    assert compacted_conversation["context_summary_through_message_id"] == "message-7"
    assert summarized == [("", original_messages[:8])]


def test_model_history_uses_summary_and_only_messages_after_boundary() -> None:
    service = ContextService()
    messages = make_messages(20)
    conversation = {
        "messages": messages,
        "context_summary": "compressed history",
        "context_summary_message_count": 8,
        "context_summary_through_message_id": "message-7",
    }

    history = service.build_history_messages(conversation)

    assert len(history) == 13
    assert isinstance(history[0], SystemMessage)
    assert "compressed history" in history[0].content
    assert isinstance(history[1], HumanMessage)
    assert "message 8" in history[1].content
    assert isinstance(history[-1], AIMessage)
    assert "message 19" in history[-1].content


def test_repeated_compaction_summarizes_only_new_uncompressed_messages(monkeypatch) -> None:
    service = ContextService(context_limit_tokens=100, compact_threshold=0.5)
    messages = make_messages(20)
    conversation = {
        "messages": messages,
        "context_summary": "first summary",
        "context_summary_message_count": 8,
        "context_summary_through_message_id": "message-7",
    }
    conversation["messages"].extend(make_messages(8, content_size=80))
    for index, message in enumerate(conversation["messages"][20:], start=20):
        message["message_id"] = f"message-{index}"
    calls = []

    def fake_summary(existing_summary: str, messages_to_summarize: list[dict]) -> str:
        calls.append((existing_summary, messages_to_summarize))
        return "second summary"

    monkeypatch.setattr(service, "_summarize_messages", fake_summary)

    _, compacted = service.compact_if_needed(conversation)

    assert compacted is True
    assert len(conversation["messages"]) == 28
    assert conversation["context_summary_message_count"] == 16
    assert conversation["context_summary_through_message_id"] == "message-15"
    assert calls == [("first summary", messages[8:16])]


def test_legacy_summary_treats_all_remaining_messages_as_active() -> None:
    service = ContextService()
    conversation = {
        "messages": make_messages(5),
        "context_summary": "summary created by the old destructive compactor",
    }

    stats = service.get_context_stats(conversation)
    history = service.build_history_messages(conversation)

    assert stats["active_message_count"] == 5
    assert stats["summarized_message_count"] == 0
    assert len(history) == 6


def test_new_conversation_initializes_context_boundary(tmp_path) -> None:
    conversation_service = ConversationService(tmp_path / "conversations")

    conversation = conversation_service.create_conversation("dataset-1")

    assert conversation["context_summary"] == ""
    assert conversation["context_summary_message_count"] == 0
    assert conversation["context_summary_through_message_id"] is None
    assert conversation["messages"] == []


def test_context_stats_prefers_model_token_counter(monkeypatch) -> None:
    service = ContextService(context_limit_tokens=100, compact_threshold=0.5)
    conversation = {"messages": make_messages(4), "context_summary": "old summary"}

    class FakeModel:
        def get_num_tokens(self, text: str) -> int:
            return 11

        def get_num_tokens_from_messages(self, messages) -> int:
            return 77

    monkeypatch.setattr(service, "_get_token_counter_model", lambda: FakeModel())

    stats = service.get_context_stats(conversation)

    assert stats["estimated_tokens"] == 77
    assert stats["summary_tokens"] == 11
    assert stats["token_source"] == "model"
    assert stats["token_source_label"] == "模型 tokenizer"
