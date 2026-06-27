import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONVERSATION_DIR = Path("app/storage/conversations")


class ConversationService:
    """Persist chat conversations as local JSON files."""

    def __init__(self, conversation_dir: Path = CONVERSATION_DIR) -> None:
        self.conversation_dir = conversation_dir
        self.conversation_dir.mkdir(parents=True, exist_ok=True)

    def create_conversation(self, dataset_id: str, title: str | None = None) -> dict[str, Any]:
        conversation_id = str(uuid.uuid4())
        now = self._now()
        conversation = {
            "conversation_id": conversation_id,
            "title": title or "新对话",
            "dataset_id": dataset_id,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self._write(conversation)
        return conversation

    def list_conversations(self) -> list[dict[str, Any]]:
        conversations = []
        for path in self.conversation_dir.glob("*.json"):
            try:
                conversation = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            conversations.append(self._summary(conversation))

        return sorted(conversations, key=lambda item: item["updated_at"], reverse=True)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        path = self._path(conversation_id)
        if not path.exists():
            raise KeyError(f"对话不存在：{conversation_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def update_dataset(self, conversation_id: str, dataset_id: str) -> dict[str, Any]:
        conversation = self.get_conversation(conversation_id)
        conversation["dataset_id"] = dataset_id
        conversation["updated_at"] = self._now()
        self._write(conversation)
        return conversation

    def delete_conversation(self, conversation_id: str) -> None:
        path = self._path(conversation_id)
        if not path.exists():
            raise KeyError(f"对话不存在：{conversation_id}")
        path.unlink()

    def save_conversation(self, conversation: dict[str, Any]) -> None:
        conversation["updated_at"] = self._now()
        self._write(conversation)

    def append_message(self, conversation_id: str, message: dict[str, Any]) -> dict[str, Any]:
        conversation = self.get_conversation(conversation_id)
        saved_message = {
            "message_id": str(uuid.uuid4()),
            "created_at": self._now(),
            **message,
        }
        conversation.setdefault("messages", []).append(saved_message)
        conversation["updated_at"] = saved_message["created_at"]

        if conversation.get("title") == "新对话" and message.get("role") == "user":
            conversation["title"] = str(message.get("content", "新对话"))[:40] or "新对话"

        self._write(conversation)
        return saved_message

    def append_messages(self, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            self.append_message(conversation_id, message)

    def _summary(self, conversation: dict[str, Any]) -> dict[str, Any]:
        return {
            "conversation_id": conversation["conversation_id"],
            "title": conversation.get("title", "新对话"),
            "dataset_id": conversation.get("dataset_id", ""),
            "created_at": conversation.get("created_at", ""),
            "updated_at": conversation.get("updated_at", ""),
            "message_count": len(conversation.get("messages", [])),
        }

    def _write(self, conversation: dict[str, Any]) -> None:
        self._path(conversation["conversation_id"]).write_text(
            json.dumps(conversation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _path(self, conversation_id: str) -> Path:
        return self.conversation_dir / f"{conversation_id}.json"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()


conversation_service = ConversationService()
