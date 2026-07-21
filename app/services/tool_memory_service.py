from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.services.dataset_service import DATASET_DIR


SQL_TOOL_NAMES = {"query_data", "generate_chart", "python_analysis"}
MAX_DATASET_MEMORIES = 100
DEFAULT_CONTEXT_LIMIT = 5
_STORE_LOCK = threading.RLock()

FailureType = Literal[
    "sql_column_mismatch",
    "sql_table_mismatch",
    "sql_ambiguous_column",
    "sql_function_mismatch",
    "sql_join_error",
    "unknown",
]


class ToolFailureReflection(BaseModel):
    tool_name: str
    failure_type: FailureType
    failed_sql: str | None = None
    failed_identifier: str | None = None
    error_message: str
    retry_hint: str
    should_retry: bool = True
    should_remember_after_success: bool = True


class PendingToolFailure(BaseModel):
    tool_name: str
    tool_args: dict = Field(default_factory=dict)
    result: str
    reflection: ToolFailureReflection
    created_at_round: int


class ToolMemory(BaseModel):
    memory_id: str
    dataset_id: str
    schema_fingerprint: str
    tool_name: str
    memory_type: str
    failed_pattern: dict = Field(default_factory=dict)
    confirmed_fix: dict = Field(default_factory=dict)
    lesson: str
    created_at: str
    updated_at: str
    hit_count: int = 0


class ToolMemoryStore(BaseModel):
    version: int = 1
    memories: list[ToolMemory] = Field(default_factory=list)


class ToolMemoryService:
    """Persist confirmed SQL repair lessons for one dataset and schema version."""

    _ERROR_PATTERNS: tuple[tuple[re.Pattern[str], FailureType], ...] = (
        (
            re.compile(r"no such column:\s*([^\n,;]+)", re.IGNORECASE),
            "sql_column_mismatch",
        ),
        (
            re.compile(r"no such table:\s*([^\n,;]+)", re.IGNORECASE),
            "sql_table_mismatch",
        ),
        (
            re.compile(r"ambiguous column name:\s*([^\n,;]+)", re.IGNORECASE),
            "sql_ambiguous_column",
        ),
        (
            re.compile(r"no such function:\s*([^\n,;]+)", re.IGNORECASE),
            "sql_function_mismatch",
        ),
    )

    _SQL_KEYWORDS = {
        "all",
        "and",
        "as",
        "asc",
        "avg",
        "between",
        "by",
        "case",
        "cast",
        "count",
        "date",
        "desc",
        "distinct",
        "else",
        "end",
        "from",
        "group",
        "having",
        "in",
        "inner",
        "is",
        "join",
        "left",
        "like",
        "limit",
        "max",
        "min",
        "not",
        "null",
        "on",
        "or",
        "order",
        "outer",
        "right",
        "select",
        "sum",
        "then",
        "when",
        "where",
        "with",
    }

    def __init__(
        self,
        dataset_dir: Path = DATASET_DIR,
        max_memories: int = MAX_DATASET_MEMORIES,
    ) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.max_memories = max(int(max_memories), 1)

    @staticmethod
    def schema_fingerprint(schema_text: str) -> str:
        normalized = " ".join(str(schema_text or "").split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def reflect_failure(
        self,
        tool_name: str,
        tool_args: dict,
        result: object,
    ) -> ToolFailureReflection | None:
        if tool_name not in SQL_TOOL_NAMES:
            return None

        failed_sql = str(tool_args.get("sql") or "").strip()
        if not failed_sql:
            return None

        error_text = self._result_text(result)
        for pattern, failure_type in self._ERROR_PATTERNS:
            match = pattern.search(error_text)
            if not match:
                continue
            identifier = self._normalize_identifier(match.group(1))
            return ToolFailureReflection(
                tool_name=tool_name,
                failure_type=failure_type,
                failed_sql=failed_sql,
                failed_identifier=identifier,
                error_message=match.group(0).strip(),
                retry_hint=self._retry_hint(failure_type, identifier),
            )
        return None

    def build_prompt_context(
        self,
        dataset_id: str,
        schema_text: str,
        tool_names: list[str],
        user_question: str,
        limit: int = DEFAULT_CONTEXT_LIMIT,
    ) -> tuple[str, list[ToolMemory]]:
        current_fingerprint = self.schema_fingerprint(schema_text)
        allowed_sql_tools = SQL_TOOL_NAMES.intersection(tool_names)
        if not allowed_sql_tools:
            return "", []

        with _STORE_LOCK:
            store = self._load_store(dataset_id)
            candidates = [
                memory
                for memory in store.memories
                if memory.dataset_id == dataset_id
                and memory.schema_fingerprint == current_fingerprint
                and memory.tool_name in SQL_TOOL_NAMES
            ]
            candidates.sort(
                key=lambda memory: self._memory_score(
                    memory,
                    user_question,
                    allowed_sql_tools,
                ),
                reverse=True,
            )
            selected = candidates[: max(min(int(limit), DEFAULT_CONTEXT_LIMIT), 0)]
            if not selected:
                return "", []

            selected_ids = {memory.memory_id for memory in selected}
            for memory in store.memories:
                if memory.memory_id in selected_ids:
                    memory.hit_count += 1
            self._save_store(dataset_id, store)

        lessons = "\n".join(
            f"{index}. {memory.lesson}" for index, memory in enumerate(selected, start=1)
        )
        prompt = (
            "本数据集历史工具经验（仅在相关时参考）：\n"
            f"{lessons}\n\n"
            "请优先使用 schema 中存在的真实字段和表；不要臆造字段名。"
        )
        return prompt, selected

    def record_success_after_failure(
        self,
        dataset_id: str,
        schema_text: str,
        pending_failure: PendingToolFailure,
        successful_tool_name: str,
        successful_tool_args: dict,
    ) -> ToolMemory | None:
        reflection = pending_failure.reflection
        successful_sql = str(successful_tool_args.get("sql") or "").strip()
        if (
            not reflection.should_remember_after_success
            or successful_tool_name not in SQL_TOOL_NAMES
            or not successful_sql
        ):
            return None

        correct_identifiers = self._extract_successful_identifiers(
            successful_sql,
            reflection.failed_identifier,
        )
        lesson = self._build_lesson(reflection, correct_identifiers)
        now = self._now()
        memory = ToolMemory(
            memory_id=f"mem_{uuid.uuid4().hex}",
            dataset_id=dataset_id,
            schema_fingerprint=self.schema_fingerprint(schema_text),
            tool_name=successful_tool_name,
            memory_type=reflection.failure_type,
            failed_pattern={
                "failed_identifier": reflection.failed_identifier,
                "failed_sql": reflection.failed_sql,
                "error": reflection.error_message,
            },
            confirmed_fix={
                "correct_identifiers": correct_identifiers,
                "successful_sql": successful_sql,
            },
            lesson=lesson,
            created_at=now,
            updated_at=now,
        )

        with _STORE_LOCK:
            store = self._load_store(dataset_id)
            duplicate = next(
                (
                    item
                    for item in store.memories
                    if self._dedupe_key(item) == self._dedupe_key(memory)
                ),
                None,
            )
            if duplicate is not None:
                duplicate.confirmed_fix = memory.confirmed_fix
                duplicate.updated_at = now
                duplicate.hit_count += 1
                saved_memory = duplicate
            else:
                store.memories.append(memory)
                saved_memory = memory

            store.memories = self._prune(store.memories)
            self._save_store(dataset_id, store)
        return saved_memory

    def load_memories(self, dataset_id: str) -> list[ToolMemory]:
        with _STORE_LOCK:
            return self._load_store(dataset_id).memories

    def _store_path(self, dataset_id: str) -> Path:
        if not str(dataset_id).strip():
            raise ValueError("dataset_id 不能为空")
        root = self.dataset_dir.resolve()
        dataset_path = (root / dataset_id).resolve()
        try:
            dataset_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("dataset_id 不能指向数据集目录之外") from exc
        return dataset_path / "tool_memory.json"

    def _load_store(self, dataset_id: str) -> ToolMemoryStore:
        path = self._store_path(dataset_id)
        if not path.exists():
            return ToolMemoryStore()
        try:
            return ToolMemoryStore.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return ToolMemoryStore()

    def _save_store(self, dataset_id: str, store: ToolMemoryStore) -> None:
        path = self._store_path(dataset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".json.tmp")
        temporary_path.write_text(
            store.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(path)

    def _prune(self, memories: list[ToolMemory]) -> list[ToolMemory]:
        if len(memories) <= self.max_memories:
            return memories
        ranked = sorted(
            memories,
            key=lambda memory: (memory.hit_count, memory.updated_at),
            reverse=True,
        )
        return ranked[: self.max_memories]

    @classmethod
    def _retry_hint(cls, failure_type: FailureType, identifier: str) -> str:
        hints = {
            "sql_column_mismatch": (
                f"字段 {identifier} 不存在。请重新查看 schema 中的真实字段名；"
                "如果信息位于另一张表，请使用已确认的关联键 JOIN。"
            ),
            "sql_table_mismatch": (
                f"表 {identifier} 不存在。请只使用 schema 中列出的真实表名，并核对别名。"
            ),
            "sql_ambiguous_column": (
                f"字段 {identifier} 在当前 SQL 中存在歧义。请使用 表名.字段名 明确限定来源。"
            ),
            "sql_function_mismatch": (
                f"函数 {identifier} 不受当前 SQLite 支持。请改用 SQLite 内置函数或等价表达式。"
            ),
        }
        return hints.get(failure_type, "请根据当前 schema 和错误信息修正 SQL 后再试。")

    @classmethod
    def _build_lesson(
        cls,
        reflection: ToolFailureReflection,
        correct_identifiers: list[str],
    ) -> str:
        identifier = reflection.failed_identifier or "该标识符"
        references = "、".join(correct_identifiers[:6])
        suffix = f"；成功写法使用了 {references}" if references else ""
        lessons = {
            "sql_column_mismatch": f"字段 {identifier} 在当前 schema 中不存在{suffix}。",
            "sql_table_mismatch": f"表 {identifier} 在当前 schema 中不存在{suffix}。",
            "sql_ambiguous_column": f"字段 {identifier} 需要使用表名或别名限定来源{suffix}。",
            "sql_function_mismatch": f"SQLite 不支持函数 {identifier}，应使用兼容表达式{suffix}。",
        }
        return lessons.get(reflection.failure_type, f"先前 SQL 使用 {identifier} 失败{suffix}。")

    @classmethod
    def _extract_successful_identifiers(
        cls,
        sql: str,
        failed_identifier: str | None,
    ) -> list[str]:
        candidates = re.findall(
            r'"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|\b([A-Za-z_][A-Za-z0-9_$.]*)\b',
            sql,
        )
        failed_lower = (failed_identifier or "").lower()
        identifiers: list[str] = []
        for groups in candidates:
            value = next((item for item in groups if item), "").strip()
            lowered = value.lower()
            if not value or lowered in cls._SQL_KEYWORDS or lowered == failed_lower:
                continue
            if value not in identifiers:
                identifiers.append(value)
            if len(identifiers) >= 8:
                break
        return identifiers

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        return value.strip().strip('"`[]').rstrip(".:").strip()

    @staticmethod
    def _result_text(result: object) -> str:
        if isinstance(result, str):
            try:
                payload = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                return result
            return ToolMemoryService._flatten_text(payload)
        return ToolMemoryService._flatten_text(result)

    @staticmethod
    def _flatten_text(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return "\n".join(
                ToolMemoryService._flatten_text(item)
                for item in value.values()
                if item is not None
            )
        if isinstance(value, list):
            return "\n".join(ToolMemoryService._flatten_text(item) for item in value)
        try:
            return str(value or "")
        except (TypeError, ValueError):
            return ""

    @staticmethod
    def _dedupe_key(memory: ToolMemory) -> tuple[str, str, str, str]:
        identifier = str(memory.failed_pattern.get("failed_identifier") or "").lower()
        lesson = " ".join(memory.lesson.lower().split())
        return memory.schema_fingerprint, memory.memory_type, identifier, lesson

    @staticmethod
    def _memory_score(
        memory: ToolMemory,
        user_question: str,
        allowed_tools: set[str],
    ) -> tuple[int, int, str]:
        question = user_question.lower()
        identifier = str(memory.failed_pattern.get("failed_identifier") or "").lower()
        score = 2 if memory.tool_name in allowed_tools else 0
        if identifier and identifier in question:
            score += 3
        lesson_terms = [term for term in re.split(r"\W+", memory.lesson.lower()) if len(term) >= 2]
        score += min(sum(term in question for term in lesson_terms), 3)
        return score, memory.hit_count, memory.updated_at

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


tool_memory_service = ToolMemoryService()
