import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pandas as pd


class SQLiteSchemaService:
    """Inspect the actual SQLite schema used by the Agent."""

    def inspect(self, database_path: Path) -> dict[str, Any]:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            table_names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            view_names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'view' ORDER BY name"
                ).fetchall()
            ]
            tables = [self._inspect_table(connection, table_name) for table_name in table_names]
        return {"tables": tables, "views": view_names}

    def build_schema_text(
        self,
        database_path: Path,
        dataset_id: str,
        dataset_name: str,
    ) -> str:
        schema = self.inspect(database_path)
        lines = [
            f"数据集 ID：{dataset_id}",
            f"数据集名称：{dataset_name}",
            f"SQLite 数据库：{database_path.name}",
            f"数据表数量：{len(schema['tables'])}",
            "",
            "SQL 说明：",
            "- 当前查询引擎是 SQLite，必须使用 SQLite 方言。",
            "- 多表查询必须使用下方列出的具体 SQL 表名。",
        ]
        if "data_table" in schema["views"]:
            lines.append("- 当前是单表数据集，也可以使用兼容视图 data_table。")

        for table in schema["tables"]:
            lines.extend(
                [
                    "",
                    f"## 表：{table['table_name']}",
                    f"- SQL表名：{self._quote_identifier(table['table_name'])}",
                    f"- 行数：{table['row_count']}",
                    "- 字段信息：",
                ]
            )
            for column in table["columns"]:
                flags = []
                if column["primary_key_position"]:
                    flags.append(f"主键第 {column['primary_key_position']} 列")
                if column["not_null"]:
                    flags.append("NOT NULL")
                suffix = f"; 约束：{', '.join(flags)}" if flags else ""
                lines.append(
                    "  - "
                    f"字段名：{column['name']}; "
                    f"SQL引用：{self._quote_identifier(column['name'])}; "
                    f"SQLite类型：{column['type'] or 'TEXT'}"
                    f"{suffix}"
                )

            if table["foreign_keys"]:
                lines.append("- 外键关系：")
                for foreign_key in table["foreign_keys"]:
                    source = ", ".join(
                        self._quote_identifier(column) for column in foreign_key["columns"]
                    )
                    target = ", ".join(
                        self._quote_identifier(column)
                        for column in foreign_key["referenced_columns"]
                    )
                    lines.append(
                        f"  - ({source}) -> "
                        f"{self._quote_identifier(foreign_key['referenced_table'])} ({target})"
                    )

            if table["indexes"]:
                lines.append("- 索引：")
                for index in table["indexes"]:
                    columns = ", ".join(
                        self._quote_identifier(column) for column in index["columns"]
                    )
                    unique = "唯一索引" if index["unique"] else "普通索引"
                    lines.append(f"  - {index['name']}：{unique} ({columns})")

            lines.append("- 样例数据：")
            sample = pd.DataFrame(table["sample_rows"], columns=table["column_names"])
            lines.append(sample.to_markdown(index=False) if not sample.empty else "（空表）")

        return "\n".join(lines)

    def _inspect_table(
        self,
        connection: sqlite3.Connection,
        table_name: str,
    ) -> dict[str, Any]:
        quoted_table = self._quote_identifier(table_name)
        columns = [
            {
                "position": row[0],
                "name": row[1],
                "type": row[2],
                "not_null": bool(row[3]),
                "default": row[4],
                "primary_key_position": int(row[5]),
            }
            for row in connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        ]
        foreign_keys = self._group_foreign_keys(
            connection.execute(f"PRAGMA foreign_key_list({quoted_table})").fetchall()
        )
        indexes = []
        for row in connection.execute(f"PRAGMA index_list({quoted_table})").fetchall():
            index_name = row[1]
            if index_name.startswith("sqlite_autoindex"):
                continue
            quoted_index = self._quote_identifier(index_name)
            index_columns = [
                item[2]
                for item in connection.execute(f"PRAGMA index_info({quoted_index})").fetchall()
            ]
            indexes.append(
                {
                    "name": index_name,
                    "unique": bool(row[2]),
                    "origin": row[3],
                    "partial": bool(row[4]),
                    "columns": index_columns,
                }
            )

        column_names = [column["name"] for column in columns]
        sample_rows = [
            dict(zip(column_names, row, strict=True))
            for row in connection.execute(f"SELECT * FROM {quoted_table} LIMIT 5").fetchall()
        ]
        row_count = int(
            connection.execute(f"SELECT COUNT(*) FROM {quoted_table}").fetchone()[0]
        )
        return {
            "table_name": table_name,
            "row_count": row_count,
            "column_names": column_names,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
            "sample_rows": sample_rows,
        }

    def _group_foreign_keys(self, rows: list[tuple]) -> list[dict[str, Any]]:
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            foreign_key = grouped.setdefault(
                int(row[0]),
                {
                    "id": int(row[0]),
                    "referenced_table": row[2],
                    "columns": [],
                    "referenced_columns": [],
                    "on_update": row[5],
                    "on_delete": row[6],
                },
            )
            foreign_key["columns"].append(row[3])
            foreign_key["referenced_columns"].append(row[4])
        return list(grouped.values())

    def _quote_identifier(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'


sqlite_schema_service = SQLiteSchemaService()
