from app.services.conversation_service import ConversationService


def test_conversation_summary_counts_only_user_questions_as_turns(tmp_path) -> None:
    service = ConversationService(tmp_path / "conversations")
    conversation = service.create_conversation("dataset-1")
    conversation_id = conversation["conversation_id"]

    service.append_messages(
        conversation_id,
        [
            {"role": "user", "type": "text", "content": "第一个问题"},
            {"role": "assistant", "type": "text", "content": "先说明思路"},
            {"role": "tool", "type": "tool_start", "name": "query_data", "args": {}},
            {"role": "tool", "type": "tool_end", "name": "query_data", "args": {}, "result": "ok"},
            {"role": "assistant", "type": "text", "content": "第一个回答"},
            {"role": "user", "type": "text", "content": "第二个问题"},
            {"role": "assistant", "type": "text", "content": "第二个回答"},
        ],
    )

    summary = service.list_conversations()[0]

    assert summary["message_count"] == 7
    assert summary["turn_count"] == 2
