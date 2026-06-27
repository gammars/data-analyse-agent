import sqlite3

import pytest

from app.services.dataset_service import DatasetService
from app.services.sql_service import SQLService


def _csv(content: str) -> bytes:
    return content.encode("utf-8")


def test_single_table_alias_row_limit_and_stddev(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset_files(
        [("sales.csv", _csv("category,amount\nA,10\nB,20\nC,30\n"))]
    )
    sql = SQLService(datasets)

    limited = sql.query(
        record.dataset_id,
        "SELECT category, amount FROM data_table ORDER BY amount",
        max_rows=2,
    )
    stats = sql.query(
        record.dataset_id,
        "SELECT ROUND(STDDEV(amount), 2) AS sample_stddev FROM data_table",
    )

    assert limited.to_dict("records") == [
        {"category": "A", "amount": 10},
        {"category": "B", "amount": 20},
    ]
    assert stats.iloc[0]["sample_stddev"] == 10.0


def test_multitable_join_runs_against_sqlite(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset_files(
        [
            ("orders.csv", _csv("order_id,customer_id,amount\no1,c1,10\no2,c2,20\n")),
            ("customers.csv", _csv("customer_id,name\nc1,Alice\nc2,Bob\n")),
        ]
    )
    sql = SQLService(datasets)

    result = sql.query(
        record.dataset_id,
        'SELECT c."name", o."amount" '
        'FROM "orders" AS o JOIN "customers" AS c '
        'ON o."customer_id" = c."customer_id" ORDER BY o."amount" DESC',
    )

    assert result.to_dict("records") == [
        {"name": "Bob", "amount": 20},
        {"name": "Alice", "amount": 10},
    ]


def test_sql_validation_and_authorizer_reject_writes(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset_files([("items.csv", _csv("id\n1\n"))])
    sql = SQLService(datasets)

    with pytest.raises(ValueError, match="只允许 SELECT / WITH"):
        sql.query(record.dataset_id, "DELETE FROM items")
    with pytest.raises(ValueError, match="不允许执行的操作"):
        sql.query(record.dataset_id, "WITH x AS (SELECT 1) DELETE FROM items")

    assert sql._authorize(sqlite3.SQLITE_DELETE, None, None, None, None) == sqlite3.SQLITE_DENY
    remaining = sql.query(record.dataset_id, "SELECT COUNT(*) AS count FROM items")
    assert remaining.iloc[0]["count"] == 1


def test_long_running_query_is_interrupted(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset_files([("items.csv", _csv("id\n1\n"))])
    sql = SQLService(datasets, query_timeout_seconds=0.1)

    with pytest.raises(TimeoutError, match="自动中止"):
        sql.query(
            record.dataset_id,
            "WITH RECURSIVE counter(value) AS ("
            "SELECT 1 UNION ALL SELECT value + 1 FROM counter WHERE value < 100000000"
            ") SELECT SUM(value) FROM counter",
        )
