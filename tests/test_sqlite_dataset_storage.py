import sqlite3
from contextlib import closing
from io import BytesIO

import pandas as pd

from app.schemas.manifest import CleaningStepManifest
from app.services.dataset_service import DatasetService


def _csv(content: str) -> bytes:
    return content.encode("utf-8")


def _sqlite_tables(database_path) -> set[str]:
    with closing(sqlite3.connect(database_path)) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }


def _sqlite_views(database_path) -> set[str]:
    with closing(sqlite3.connect(database_path)) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
            ).fetchall()
        }


def test_upload_materializes_multitable_sqlite_and_survives_reload(tmp_path) -> None:
    dataset_dir = tmp_path / "datasets"
    service = DatasetService(dataset_dir=dataset_dir)

    record = service.save_dataset_files(
        [
            ("orders.csv", _csv("order_id,customer_id,amount\no1,c1,10.5\no2,c2,20\n")),
            ("customers.csv", _csv("customer_id,name\nc1,Alice\nc2,Bob\n")),
        ]
    )

    database_path = service.get_database_path(record.dataset_id)
    dataset_path = dataset_dir / record.dataset_id
    assert database_path.name == "dataset.sqlite3"
    assert database_path.exists()
    assert _sqlite_tables(database_path) == {"orders", "customers"}
    assert (dataset_path / "raw" / "orders.csv").read_bytes() == _csv(
        "order_id,customer_id,amount\no1,c1,10.5\no2,c2,20\n"
    )
    assert (dataset_path / "processed" / "orders.csv").exists()

    manifest = service.get_manifest(record.dataset_id)
    assert manifest["active_layer"] == "processed"
    assert {table["table_name"] for table in manifest["tables"]} == {
        "orders",
        "customers",
    }
    orders_manifest = next(
        table for table in manifest["tables"] if table["table_name"] == "orders"
    )
    assert orders_manifest["raw_path"] == "raw/orders.csv"
    assert orders_manifest["processed_path"] == "processed/orders.csv"
    assert orders_manifest["original_row_count"] == 2
    assert orders_manifest["processed_row_count"] == 2
    assert orders_manifest["cleaning_steps"] == []

    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
        assert connection.execute("SELECT SUM(amount) FROM orders").fetchone()[0] == 30.5

    reloaded = DatasetService(dataset_dir=dataset_dir)
    summary = reloaded.get_summary(record.dataset_id)
    assert summary["database"] == {
        "engine": "sqlite",
        "filename": "dataset.sqlite3",
        "path": str(database_path),
        "ready": True,
    }


def test_append_and_delete_table_rebuild_sqlite(tmp_path) -> None:
    service = DatasetService(dataset_dir=tmp_path / "datasets")
    record = service.save_dataset_files(
        [
            ("orders.csv", _csv("order_id,amount\no1,10\n")),
            ("customers.csv", _csv("customer_id,name\nc1,Alice\n")),
        ]
    )

    service.append_tables(
        record.dataset_id,
        [("payments.csv", _csv("order_id,payment\no1,10\n"))],
    )
    database_path = service.get_database_path(record.dataset_id)
    assert _sqlite_tables(database_path) == {"orders", "customers", "payments"}

    service.delete_table(record.dataset_id, "payments")
    assert _sqlite_tables(database_path) == {"orders", "customers"}
    manifest = service.get_manifest(record.dataset_id)
    assert {table["table_name"] for table in manifest["tables"]} == {
        "orders",
        "customers",
    }
    assert not (
        tmp_path
        / "datasets"
        / record.dataset_id
        / "processed"
        / "payments.csv"
    ).exists()


def test_single_table_database_exposes_data_table_view(tmp_path) -> None:
    service = DatasetService(dataset_dir=tmp_path / "datasets")
    record = service.save_dataset_files(
        [("sales.csv", _csv("category,amount\nA,10\nB,20\n"))]
    )

    database_path = service.get_database_path(record.dataset_id)
    assert _sqlite_tables(database_path) == {"sales"}
    assert _sqlite_views(database_path) == {"data_table"}


def test_excel_sheets_are_materialized_as_processed_csv_tables(tmp_path) -> None:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame({"order_id": [1, 2]}).to_excel(
            writer,
            sheet_name="orders",
            index=False,
        )
        pd.DataFrame({"customer_id": [10, 20]}).to_excel(
            writer,
            sheet_name="customers",
            index=False,
        )

    service = DatasetService(dataset_dir=tmp_path / "datasets")
    record = service.save_dataset("book.xlsx", output.getvalue())
    dataset_path = tmp_path / "datasets" / record.dataset_id

    assert (dataset_path / "raw" / "book.xlsx").exists()
    assert (dataset_path / "processed" / "orders.csv").exists()
    assert (dataset_path / "processed" / "customers.csv").exists()
    manifest = service.get_manifest(record.dataset_id)
    assert {table["source_sheet"] for table in manifest["tables"]} == {
        "orders",
        "customers",
    }


def test_legacy_dataset_layout_is_migrated(tmp_path) -> None:
    dataset_dir = tmp_path / "datasets"
    dataset_path = dataset_dir / "legacy-id"
    dataset_path.mkdir(parents=True)
    legacy_file = dataset_path / "sales.csv"
    legacy_file.write_bytes(_csv("category,amount\nA,10\n"))
    (dataset_dir / "metadata.json").write_text(
        """[
          {
            "dataset_id": "legacy-id",
            "filename": "sales.csv",
            "schema": "",
            "created_at": "2026-01-01T00:00:00+00:00",
            "database_path": "DATABASE_PATH",
            "tables": [
              {
                "table_name": "sales",
                "filename": "sales.csv",
                "path": "SOURCE_PATH",
                "suffix": ".csv",
                "sheet_name": null,
                "created_at": "2026-01-01T00:00:00+00:00"
              }
            ]
          }
        ]""".replace("DATABASE_PATH", str(dataset_path / "dataset.sqlite3").replace("\\", "\\\\"))
        .replace("SOURCE_PATH", str(legacy_file).replace("\\", "\\\\")),
        encoding="utf-8",
    )

    service = DatasetService(dataset_dir=dataset_dir)
    manifest = service.get_manifest("legacy-id")

    assert not legacy_file.exists()
    assert (dataset_path / "raw" / "sales.csv").exists()
    assert (dataset_path / "processed" / "sales.csv").exists()
    assert (dataset_path / "manifest.json").exists()
    assert manifest["tables"][0]["raw_path"] == "raw/sales.csv"


def test_manifest_refresh_preserves_tool_configuration(tmp_path) -> None:
    service = DatasetService(dataset_dir=tmp_path / "datasets")
    record = service.save_dataset(
        "sales.csv",
        _csv("id,amount\n1,10\n2,20\n"),
    )
    manifest = service.manifests.load(record.manifest_path)
    assert manifest is not None
    manifest.source.url = "https://example.com/dataset"
    manifest.processing_status = "processed"
    manifest.tables[0].primary_key = ["id"]
    manifest.tables[0].cleaning_status = "applied"
    manifest.tables[0].cleaning_steps.append(
        CleaningStepManifest(
            operation="trim_strings",
            applied_at="2026-01-01T00:00:00+00:00",
            before_rows=2,
            after_rows=2,
        )
    )
    service.manifests.write(record.manifest_path, manifest)

    service.rename_dataset(record.dataset_id, "renamed dataset")
    refreshed = service.get_manifest(record.dataset_id)

    assert refreshed["name"] == "renamed dataset"
    assert refreshed["source"]["url"] == "https://example.com/dataset"
    assert refreshed["processing_status"] == "processed"
    assert refreshed["tables"][0]["primary_key"] == ["id"]
    assert refreshed["tables"][0]["cleaning_steps"][0]["operation"] == "trim_strings"
