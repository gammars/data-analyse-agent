import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class SQLiteBuildResult:
    database_path: Path
    table_count: int
    row_counts: dict[str, int]


class SQLiteStorageService:
    """Materialize a dataset's DataFrames into one SQLite database file."""

    def rebuild(
        self,
        database_path: Path,
        tables: list[tuple[str, pd.DataFrame]],
    ) -> SQLiteBuildResult:
        if not tables:
            raise ValueError("SQLite 数据库至少需要一张数据表")

        table_names = [name for name, _ in tables]
        if len(table_names) != len(set(table_names)):
            raise ValueError("SQLite 数据表名称不能重复")

        database_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = database_path.with_name(
            f".{database_path.name}.{uuid.uuid4().hex}.tmp"
        )
        row_counts: dict[str, int] = {}

        try:
            with closing(sqlite3.connect(temporary_path)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA synchronous = OFF")
                connection.execute("PRAGMA temp_store = MEMORY")

                for table_name, dataframe in tables:
                    dataframe.to_sql(
                        table_name,
                        connection,
                        if_exists="fail",
                        index=False,
                        chunksize=1000,
                    )
                    actual_rows = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {self._quote_identifier(table_name)}"
                        ).fetchone()[0]
                    )
                    expected_rows = int(len(dataframe))
                    if actual_rows != expected_rows:
                        raise RuntimeError(
                            f"SQLite 表 {table_name} 行数校验失败："
                            f"期望 {expected_rows}，实际 {actual_rows}"
                        )
                    row_counts[table_name] = actual_rows

                if len(tables) == 1 and tables[0][0] != "data_table":
                    source_table = self._quote_identifier(tables[0][0])
                    connection.execute(
                        f'CREATE VIEW "data_table" AS SELECT * FROM {source_table}'
                    )

                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"SQLite 完整性检查失败：{integrity}")
                connection.commit()

            os.replace(temporary_path, database_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        return SQLiteBuildResult(
            database_path=database_path,
            table_count=len(tables),
            row_counts=row_counts,
        )

    def rebuild_with_schema(
        self,
        database_path: Path,
        tables: list[tuple[str, pd.DataFrame]],
        schema_sql: str,
        indexes_sql: str,
    ) -> SQLiteBuildResult:
        if not tables:
            raise ValueError("SQLite 数据库至少需要一张数据表")
        table_names = [name for name, _ in tables]
        if len(table_names) != len(set(table_names)):
            raise ValueError("SQLite 数据表名称不能重复")

        database_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = database_path.with_name(
            f".{database_path.name}.{uuid.uuid4().hex}.tmp"
        )
        row_counts: dict[str, int] = {}

        try:
            with closing(sqlite3.connect(temporary_path)) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("PRAGMA synchronous = OFF")
                connection.execute("PRAGMA temp_store = MEMORY")
                connection.executescript(schema_sql)

                for table_name, dataframe in tables:
                    dataframe.to_sql(
                        table_name,
                        connection,
                        if_exists="append",
                        index=False,
                        chunksize=1000,
                    )
                    actual_rows = int(
                        connection.execute(
                            f"SELECT COUNT(*) FROM {self._quote_identifier(table_name)}"
                        ).fetchone()[0]
                    )
                    expected_rows = int(len(dataframe))
                    if actual_rows != expected_rows:
                        raise RuntimeError(
                            f"SQLite 表 {table_name} 行数校验失败："
                            f"期望 {expected_rows}，实际 {actual_rows}"
                        )
                    row_counts[table_name] = actual_rows

                connection.commit()
                if indexes_sql.strip():
                    connection.executescript(indexes_sql)
                    connection.commit()

                if len(tables) == 1 and tables[0][0] != "data_table":
                    source_table = self._quote_identifier(tables[0][0])
                    connection.execute(
                        f'CREATE VIEW "data_table" AS SELECT * FROM {source_table}'
                    )
                    connection.commit()

                connection.execute("PRAGMA foreign_keys = ON")
                foreign_key_errors = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if foreign_key_errors:
                    raise RuntimeError(
                        "SQLite 外键完整性检查失败："
                        f"{foreign_key_errors[:10]}"
                    )
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(f"SQLite 完整性检查失败：{integrity}")

            os.replace(temporary_path, database_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        return SQLiteBuildResult(
            database_path=database_path,
            table_count=len(tables),
            row_counts=row_counts,
        )

    def _quote_identifier(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'


sqlite_storage_service = SQLiteStorageService()
