import re

import duckdb
import pandas as pd

from app.services.dataset_service import DatasetService


FORBIDDEN_SQL_PATTERNS = [
    r"\bdrop\b",
    r"\bdelete\b",
    r"\bupdate\b",
    r"\binsert\b",
    r"\balter\b",
    r"\bcreate\b",
    r"\bcopy\b",
    r"\battach\b",
    r"\binstall\b",
    r"\bload\b",
    r"\bpragma\b",
    r"\bcall\b",
    r"\bexport\b",
    r"\bimport\b",
    r"\bread_csv\w*\s*\(",
    r"\bread_json\w*\s*\(",
    r"\bread_parquet\w*\s*\(",
    r"\bread_text\w*\s*\(",
    r"\b(from|join)\s+'",
]


class SQLService:
    """Run safe read-only SQL over uploaded DataFrames."""

    def __init__(self, dataset_service: DatasetService) -> None:
        self.dataset_service = dataset_service

    def query(self, dataset_id: str, sql: str, max_rows: int = 100) -> pd.DataFrame:
        self._validate_sql(sql)

        safe_max_rows = min(max(max_rows, 1), 1000)

        with duckdb.connect(database=":memory:") as conn:
            tables = self.dataset_service.iter_tables(dataset_id)
            if len(tables) > 1 and re.search(r"\bdata_table\b", sql, flags=re.IGNORECASE):
                table_names = ", ".join(table.table_name for table in tables)
                raise ValueError(
                    "当前数据集包含多张表，不存在 data_table 通用表名；"
                    f"请使用 schema 中的具体 SQL表名。可用表：{table_names}"
                )

            registered_names = set()
            first_dataframe = None

            for table in tables:
                dataframe = self.dataset_service.get_table_dataframe(dataset_id, table.table_name)
                if first_dataframe is None:
                    first_dataframe = dataframe
                registered_names.add(table.table_name)
                conn.register(table.table_name, dataframe)

            if len(tables) == 1 and "data_table" not in registered_names and first_dataframe is not None:
                conn.register("data_table", first_dataframe)

            result = conn.execute(sql).fetchdf()

        if len(result) > safe_max_rows:
            return result.head(safe_max_rows)
        return result

    def _validate_sql(self, sql: str) -> None:
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
