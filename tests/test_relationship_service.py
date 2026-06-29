import json
import sqlite3
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage

from app.schemas.manifest import ForeignKeyManifest, IndexManifest
from app.schemas.relationships import TableRelationshipConfig
from app.services.dataset_service import DatasetService
from app.services.relationship_service import RelationshipService
from app.services.sqlite_schema_service import SQLiteSchemaService


def _csv(content: str) -> bytes:
    return content.encode("utf-8")


def _valid_configs() -> list[TableRelationshipConfig]:
    return [
        TableRelationshipConfig(
            table_name="customers",
            primary_key=["customer_id"],
            indexes=[IndexManifest(name="idx_customers_city", columns=["city"])],
        ),
        TableRelationshipConfig(
            table_name="orders",
            primary_key=["order_id"],
            foreign_keys=[
                ForeignKeyManifest(
                    name="fk_orders_customer",
                    columns=["customer_id"],
                    referenced_table="customers",
                    referenced_columns=["customer_id"],
                )
            ],
            indexes=[IndexManifest(name="idx_orders_customer", columns=["customer_id"])],
        ),
    ]


def test_suggest_save_and_rebuild_relational_sqlite(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset_files(
        [
            ("customers.csv", _csv("customer_id,city\nc1,Beijing\nc2,Shanghai\n")),
            (
                "orders.csv",
                _csv("order_id,customer_id,amount\no1,c1,10\no2,c1,20\no3,c2,30\n"),
            ),
        ]
    )
    relationships = RelationshipService(datasets=datasets)

    suggestions = relationships.suggest(record.dataset_id)
    customer_suggestion = next(
        item for item in suggestions["tables"] if item["table_name"] == "customers"
    )
    assert any(
        candidate["columns"] == ["customer_id"]
        for candidate in customer_suggestion["primary_key_candidates"]
    )
    assert any(
        candidate["table_name"] == "orders"
        and candidate["columns"] == ["customer_id"]
        and candidate["referenced_table"] == "customers"
        for candidate in suggestions["foreign_key_candidates"]
    )

    with pytest.raises(ValueError, match="明确确认"):
        relationships.save(record.dataset_id, _valid_configs(), confirmed=False)

    result = relationships.save(record.dataset_id, _valid_configs(), confirmed=True)
    assert result["saved"] is True
    assert result["validation"]["valid"] is True

    dataset_path = datasets.get_database_path(record.dataset_id).parent
    schema_sql = (dataset_path / "schema.sql").read_text(encoding="utf-8")
    indexes_sql = (dataset_path / "indexes.sql").read_text(encoding="utf-8")
    assert 'PRIMARY KEY ("customer_id")' in schema_sql
    assert 'CONSTRAINT "fk_orders_customer" FOREIGN KEY ("customer_id")' in schema_sql
    assert 'CREATE INDEX "idx_orders_customer"' in indexes_sql

    inspector = SQLiteSchemaService()
    inspected = inspector.inspect(datasets.get_database_path(record.dataset_id))
    tables = {table["table_name"]: table for table in inspected["tables"]}
    assert next(
        column for column in tables["customers"]["columns"] if column["name"] == "customer_id"
    )["primary_key_position"] == 1
    assert tables["orders"]["foreign_keys"][0]["referenced_table"] == "customers"
    assert tables["orders"]["foreign_keys"][0]["columns"] == ["customer_id"]
    assert {index["name"] for index in tables["orders"]["indexes"]} == {
        "idx_orders_customer"
    }
    assert "外键关系" in datasets.get_schema(record.dataset_id)
    assert "idx_orders_customer" in datasets.get_schema(record.dataset_id)

    datasets.rebuild_database(record.dataset_id)
    rebuilt = inspector.inspect(datasets.get_database_path(record.dataset_id))
    rebuilt_orders = next(table for table in rebuilt["tables"] if table["table_name"] == "orders")
    assert rebuilt_orders["foreign_keys"]
    assert rebuilt_orders["indexes"]


def test_relationship_validation_rejects_duplicate_keys_and_orphans(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset_files(
        [
            ("customers.csv", _csv("customer_id,name\nc1,Alice\nc1,Alicia\n")),
            ("orders.csv", _csv("order_id,customer_id\no1,c9\n")),
        ]
    )
    relationships = RelationshipService(datasets=datasets)
    configs = _valid_configs()

    validation = relationships.validate(record.dataset_id, configs)
    assert validation["valid"] is False
    assert any("主键包含" in error for error in validation["errors"])
    assert any("孤立值" in error for error in validation["errors"])
    with pytest.raises(ValueError, match="完整性验证失败"):
        relationships.save(record.dataset_id, configs, confirmed=True)

    database_path = datasets.get_database_path(record.dataset_id)
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("PRAGMA foreign_key_list(orders)").fetchall() == []


def test_relationship_configuration_is_required_after_upload_and_append(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset(
        "customers.csv",
        _csv("customer_id,name\nc1,Alice\nc2,Bob\n"),
    )
    relationships = RelationshipService(datasets=datasets)

    assert datasets.get_relationship_status(record.dataset_id) == "pending"
    with pytest.raises(ValueError, match="尚未完成关系配置"):
        datasets.require_relationship_configuration(record.dataset_id)

    relationships.save(
        record.dataset_id,
        [TableRelationshipConfig(table_name="customers")],
        confirmed=True,
    )
    assert datasets.get_relationship_status(record.dataset_id) == "confirmed"
    datasets.require_relationship_configuration(record.dataset_id)

    datasets.append_table(
        record.dataset_id,
        "orders.csv",
        _csv("order_id,customer_id\no1,c1\n"),
    )
    assert datasets.get_relationship_status(record.dataset_id) == "pending"


def test_llm_advice_can_only_select_existing_candidates(tmp_path, monkeypatch) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset_files(
        [
            ("customers.csv", _csv("customer_id,city\nc1,Beijing\nc2,Shanghai\n")),
            ("orders.csv", _csv("order_id,customer_id\no1,c1\no2,c2\n")),
        ]
    )
    relationships = RelationshipService(datasets=datasets)

    class FakeModel:
        calls = 0

        def invoke(self, messages):
            self.calls += 1
            return AIMessage(
                content=json.dumps(
                    {
                        "summary": "订单表通过 customer_id 关联客户表。",
                        "table_recommendations": [
                            {
                                "table_name": "customers",
                                "primary_key": ["customer_id"],
                                "primary_key_reason": "客户编号唯一且非空",
                                "indexes": ["not_an_existing_index"],
                                "index_reason": "无",
                            },
                            {
                                "table_name": "orders",
                                "primary_key": ["order_id"],
                                "primary_key_reason": "订单编号唯一且非空",
                                "indexes": ["idx_orders_customer_id"],
                                "index_reason": "用于客户订单 JOIN",
                            },
                        ],
                        "foreign_key_recommendations": [
                            {
                                "candidate_id": (
                                    "orders(customer_id)->customers(customer_id)"
                                ),
                                "reason": "值匹配且业务语义一致",
                            },
                            {
                                "candidate_id": "invented(a)->missing(b)",
                                "reason": "无效候选",
                            },
                        ],
                        "warnings": [],
                    },
                    ensure_ascii=False,
                )
            )

    fake_model = FakeModel()
    monkeypatch.setattr(
        "app.services.relationship_service.build_chat_model",
        lambda: fake_model,
    )

    suggestions = relationships.suggest(record.dataset_id, include_llm=True)
    assert suggestions["llm_advice"]["status"] == "success"
    orders = next(item for item in suggestions["tables"] if item["table_name"] == "orders")
    assert any(
        item["columns"] == ["order_id"] and item["llm_recommended"]
        for item in orders["primary_key_candidates"]
    )
    assert any(
        item["name"] == "idx_orders_customer_id" and item["llm_recommended"]
        for item in orders["index_candidates"]
    )
    recommended_foreign_key = next(
        item
        for item in suggestions["foreign_key_candidates"]
        if item["candidate_id"] == "orders(customer_id)->customers(customer_id)"
    )
    assert recommended_foreign_key["llm_recommended"] is True
    customers_advice = next(
        item
        for item in suggestions["llm_advice"]["table_recommendations"]
        if item["table_name"] == "customers"
    )
    assert customers_advice["indexes"] == []
    assert len(suggestions["llm_advice"]["foreign_key_recommendations"]) == 1

    relationships.suggest(record.dataset_id, include_llm=True)
    assert fake_model.calls == 1
