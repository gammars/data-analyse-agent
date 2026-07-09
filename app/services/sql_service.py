import math
import os
import re
import sqlite3
import time
from contextlib import closing

import pandas as pd
from dotenv import load_dotenv

from app.services.dataset_service import DatasetService


FORBIDDEN_SQL_PATTERNS = [
    r"\bdrop\b",
    r"\bdelete\b",
    r"\bupdate\b",
    r"\binsert\b",
    r"\balter\b",
    r"\bcreate\b",
    r"\bvacuum\b",
    r"\battach\b",
    r"\bdetach\b",
    r"\breindex\b",
    r"\banalyze\b",
    r"\bpragma\b",
    r"\bload_extension\s*\(",
]

DEFAULT_QUERY_TIMEOUT_SECONDS = 15.0
MAX_QUERY_ROWS = 1000
DEFAULT_ANALYSIS_QUERY_ROWS = 50000

DENIED_SQLITE_ACTIONS = {
    action
    for action in (
        getattr(sqlite3, "SQLITE_ALTER_TABLE", None),
        getattr(sqlite3, "SQLITE_ANALYZE", None),
        getattr(sqlite3, "SQLITE_ATTACH", None),
        getattr(sqlite3, "SQLITE_CREATE_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_VTABLE", None),
        getattr(sqlite3, "SQLITE_DELETE", None),
        getattr(sqlite3, "SQLITE_DETACH", None),
        getattr(sqlite3, "SQLITE_DROP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_VTABLE", None),
        getattr(sqlite3, "SQLITE_INSERT", None),
        getattr(sqlite3, "SQLITE_PRAGMA", None),
        getattr(sqlite3, "SQLITE_REINDEX", None),
        getattr(sqlite3, "SQLITE_SAVEPOINT", None),
        getattr(sqlite3, "SQLITE_TRANSACTION", None),
        getattr(sqlite3, "SQLITE_UPDATE", None),
    )
    if action is not None
}


class _StdDevAggregate:
    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self.sum_squared_deviation = 0.0

    def step(self, value: object) -> None:
        if value is None:
            return
        number = float(value)
        self.count += 1
        delta = number - self.mean
        self.mean += delta / self.count
        self.sum_squared_deviation += delta * (number - self.mean)

    def finalize(self) -> float | None:
        if self.count < 2:
            return None
        return math.sqrt(self.sum_squared_deviation / (self.count - 1))


class _StdDevPopulationAggregate(_StdDevAggregate):
    def finalize(self) -> float | None:
        if self.count == 0:
            return None
        return math.sqrt(self.sum_squared_deviation / self.count)


class SQLService:
    """Run bounded, read-only SQLite queries over persisted datasets."""

    def __init__(
        self,
        dataset_service: DatasetService,
        query_timeout_seconds: float = DEFAULT_QUERY_TIMEOUT_SECONDS,
        max_query_rows: int = MAX_QUERY_ROWS,
        max_analysis_rows: int | None = None,
    ) -> None:
        self.dataset_service = dataset_service
        self.query_timeout_seconds = max(float(query_timeout_seconds), 0.1)
        self.max_query_rows = max(int(max_query_rows), 1)
        self.max_analysis_rows = self._resolve_max_analysis_rows(max_analysis_rows)

    def query(self, dataset_id: str, sql: str, max_rows: int = 100) -> pd.DataFrame:
        return self._query(
            dataset_id=dataset_id,
            sql=sql,
            max_rows=max_rows,
            row_cap=self.max_query_rows,
        )

    def query_for_analysis(
        self,
        dataset_id: str,
        sql: str,
        max_rows: int = DEFAULT_ANALYSIS_QUERY_ROWS,
    ) -> pd.DataFrame:
        return self._query(
            dataset_id=dataset_id,
            sql=sql,
            max_rows=max_rows,
            row_cap=self.max_analysis_rows,
        )

    def _query(
        self,
        dataset_id: str,
        sql: str,
        max_rows: int,
        row_cap: int,
    ) -> pd.DataFrame:
        statement = self._validate_sql(sql)
        safe_max_rows = min(max(int(max_rows), 1), row_cap)
        database_path = self.dataset_service.get_database_path(dataset_id).resolve()
        database_uri = f"{database_path.as_uri()}?mode=ro"
        deadline = time.monotonic() + self.query_timeout_seconds

        try:
            with closing(
                sqlite3.connect(database_uri, uri=True, timeout=5.0)
            ) as connection:
                connection.execute("PRAGMA query_only = ON")
                self._register_compatibility_functions(connection)
                connection.set_authorizer(self._authorize)
                connection.set_progress_handler(
                    lambda: 1 if time.monotonic() >= deadline else 0,
                    10_000,
                )

                bounded_sql = f'SELECT * FROM ({statement}) AS "__agent_query" LIMIT ?'
                cursor = connection.execute(bounded_sql, (safe_max_rows,))
                rows = cursor.fetchall()
                columns = [description[0] for description in cursor.description or []]
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise TimeoutError(
                    f"SQLite 查询超过 {self.query_timeout_seconds:g} 秒，已自动中止"
                ) from exc
            raise ValueError(f"SQLite 查询执行失败：{exc}") from exc
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"SQLite 拒绝执行该查询：{exc}") from exc

        return pd.DataFrame.from_records(rows, columns=columns)

    def _resolve_max_analysis_rows(self, max_analysis_rows: int | None) -> int:
        if max_analysis_rows is not None:
            return max(int(max_analysis_rows), 1)

        load_dotenv()
        value = (
            os.getenv("PYTHON_SANDBOX_MAX_ROWS", "").strip()
            or os.getenv("PYTHON_ANALYSIS_MAX_ROWS", "").strip()
        )
        if not value:
            return DEFAULT_ANALYSIS_QUERY_ROWS

        try:
            parsed = int(value)
        except ValueError:
            return DEFAULT_ANALYSIS_QUERY_ROWS

        return max(parsed, 1)

    def _validate_sql(self, sql: str) -> str:
        normalized = sql.strip()
        lowered = normalized.lower()

        if not normalized:
            raise ValueError("SQL 不能为空")
        if not lowered.startswith(("select", "with")):
            raise ValueError("只允许 SELECT / WITH 查询")
        if ";" in normalized.rstrip(";"):
            raise ValueError("一次只允许执行一条 SQL 查询")

        for pattern in FORBIDDEN_SQL_PATTERNS:
            if re.search(pattern, lowered):
                raise ValueError("SQL 包含不允许执行的操作")

        return normalized.rstrip(";").strip()

    def _authorize(
        self,
        action_code: int,
        parameter_one: str | None,
        parameter_two: str | None,
        database_name: str | None,
        trigger_name: str | None,
    ) -> int:
        del parameter_one, parameter_two, database_name, trigger_name
        if action_code in DENIED_SQLITE_ACTIONS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _register_compatibility_functions(self, connection: sqlite3.Connection) -> None:
        connection.create_aggregate("STDDEV", 1, _StdDevAggregate)
        connection.create_aggregate("STDDEV_SAMP", 1, _StdDevAggregate)
        connection.create_aggregate("STDDEV_POP", 1, _StdDevPopulationAggregate)
