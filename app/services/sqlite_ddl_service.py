from dataclasses import dataclass
from typing import Any

import pandas as pd
from pandas.api import types as pandas_types


@dataclass(frozen=True)
class SQLiteDDL:
    schema_sql: str
    indexes_sql: str


class SQLiteDDLService:
    """Generate reproducible SQLite DDL from tables and relationship config."""

    def generate(
        self,
        tables: list[tuple[str, pd.DataFrame]],
        configs: dict[str, Any] | None = None,
    ) -> SQLiteDDL:
        if not tables:
            raise ValueError("至少需要一张数据表才能生成 SQLite DDL")

        configs = configs or {}
        table_names = {name for name, _ in tables}
        schema_statements = []
        index_statements = []
        index_names: set[str] = set()

        for table_name, dataframe in tables:
            config = configs.get(table_name)
            primary_key = list(getattr(config, "primary_key", []) or [])
            foreign_keys = list(getattr(config, "foreign_keys", []) or [])
            indexes = list(getattr(config, "indexes", []) or [])
            columns = [str(column) for column in dataframe.columns]
            self._require_columns(table_name, columns, primary_key, "主键")

            definitions = []
            for column in dataframe.columns:
                column_name = str(column)
                nullable = " NOT NULL" if column_name in primary_key else ""
                definitions.append(
                    f"  {self.quote(column_name)} "
                    f"{self._sqlite_type(dataframe[column].dtype)}{nullable}"
                )

            if primary_key:
                definitions.append(
                    "  PRIMARY KEY ("
                    + ", ".join(self.quote(column) for column in primary_key)
                    + ")"
                )

            for position, foreign_key in enumerate(foreign_keys, start=1):
                source_columns = list(foreign_key.columns)
                target_columns = list(foreign_key.referenced_columns)
                self._require_columns(table_name, columns, source_columns, "外键")
                if foreign_key.referenced_table not in table_names:
                    raise ValueError(
                        f"外键引用的数据表不存在：{foreign_key.referenced_table}"
                    )
                if len(source_columns) != len(target_columns) or not source_columns:
                    raise ValueError(f"表 {table_name} 的外键字段数量不匹配")
                constraint_name = foreign_key.name or f"fk_{table_name}_{position}"
                definitions.append(
                    f"  CONSTRAINT {self.quote(constraint_name)} FOREIGN KEY ("
                    + ", ".join(self.quote(column) for column in source_columns)
                    + f") REFERENCES {self.quote(foreign_key.referenced_table)} ("
                    + ", ".join(self.quote(column) for column in target_columns)
                    + ")"
                )

            schema_statements.append(
                f"CREATE TABLE {self.quote(table_name)} (\n"
                + ",\n".join(definitions)
                + "\n);"
            )

            for index in indexes:
                self._require_columns(table_name, columns, index.columns, "索引")
                if not index.columns:
                    raise ValueError(f"表 {table_name} 的索引 {index.name} 没有字段")
                if index.name in index_names:
                    raise ValueError(f"SQLite 索引名称必须全局唯一：{index.name}")
                index_names.add(index.name)
                unique = "UNIQUE " if index.unique else ""
                index_statements.append(
                    f"CREATE {unique}INDEX {self.quote(index.name)} "
                    f"ON {self.quote(table_name)} ("
                    + ", ".join(self.quote(column) for column in index.columns)
                    + ");"
                )

        schema_header = "-- Generated from manifest.json. Do not edit by hand.\n"
        index_header = "-- Generated from manifest.json. Do not edit by hand.\n"
        return SQLiteDDL(
            schema_sql=schema_header + "\n\n".join(schema_statements) + "\n",
            indexes_sql=(
                index_header + "\n".join(index_statements) + "\n"
                if index_statements
                else index_header + "-- No indexes configured.\n"
            ),
        )

    def _sqlite_type(self, dtype: Any) -> str:
        if pandas_types.is_bool_dtype(dtype) or pandas_types.is_integer_dtype(dtype):
            return "INTEGER"
        if pandas_types.is_float_dtype(dtype):
            return "REAL"
        if pandas_types.is_datetime64_any_dtype(dtype):
            return "TIMESTAMP"
        if pandas_types.is_timedelta64_dtype(dtype):
            return "REAL"
        return "TEXT"

    def _require_columns(
        self,
        table_name: str,
        available: list[str],
        requested: list[str],
        label: str,
    ) -> None:
        missing = set(requested) - set(available)
        if missing:
            raise ValueError(
                f"表 {table_name} 的{label}字段不存在：{', '.join(sorted(missing))}"
            )

    def quote(self, identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'


sqlite_ddl_service = SQLiteDDLService()
