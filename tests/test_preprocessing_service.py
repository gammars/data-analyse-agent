import json

import pandas as pd

from app.agent.tools import build_tools
from app.schemas.preprocessing import CleaningOperation
from app.services.analysis_service import AnalysisService
from app.services.chart_service import ChartService
from app.services.dataset_service import DatasetService
from app.services.preprocessing_service import PreprocessingService
from app.services.sql_service import SQLService


DIRTY_CSV = (
    "id,name,amount,flag,event_date,empty\n"
    "1, Alice ,10,yes,2024-01-01,\n"
    "1, Alice ,10,yes,2024-01-01,\n"
    "2,Bob,,no,2024-01-02,\n"
    ",,,,,\n"
).encode("utf-8")


def _operations() -> list[CleaningOperation]:
    return [
        CleaningOperation(operation="drop_duplicate_rows"),
        CleaningOperation(operation="drop_empty_rows"),
        CleaningOperation(operation="drop_empty_columns"),
        CleaningOperation(operation="trim_strings", column="name"),
        CleaningOperation(
            operation="handle_missing",
            column="amount",
            strategy="fill_median",
        ),
        CleaningOperation(
            operation="convert_type",
            column="amount",
            target_type="integer",
        ),
        CleaningOperation(
            operation="convert_type",
            column="flag",
            target_type="boolean",
        ),
        CleaningOperation(
            operation="convert_type",
            column="event_date",
            target_type="datetime",
        ),
    ]


def test_suggest_apply_and_reset_cleaning(tmp_path) -> None:
    dataset_dir = tmp_path / "datasets"
    datasets = DatasetService(dataset_dir=dataset_dir)
    record = datasets.save_dataset("dirty.csv", DIRTY_CSV)
    preprocessing = PreprocessingService(datasets)
    raw_path = dataset_dir / record.dataset_id / "raw" / "dirty.csv"
    processed_path = dataset_dir / record.dataset_id / "processed" / "dirty.csv"
    raw_before = raw_path.read_bytes()
    processed_before = processed_path.read_bytes()

    suggestions = preprocessing.suggest_cleaning(record.dataset_id, "dirty")

    assert suggestions["mode"] == "suggest_only"
    assert suggestions["tables"][0]["duplicate_rows"] == 1
    assert suggestions["tables"][0]["empty_rows"] == 1
    assert suggestions["tables"][0]["empty_columns"] == ["empty"]
    assert raw_path.read_bytes() == raw_before
    assert processed_path.read_bytes() == processed_before

    result = preprocessing.apply_cleaning(
        record.dataset_id,
        "dirty",
        _operations(),
    )

    assert result["before_rows"] == 4
    assert result["after_rows"] == 2
    assert result["raw_unchanged"] is True
    assert raw_path.read_bytes() == raw_before
    cleaned = datasets.get_table_dataframe(record.dataset_id, "dirty")
    assert cleaned["name"].tolist() == ["Alice", "Bob"]
    assert "empty" not in cleaned.columns
    assert str(cleaned["amount"].dtype) == "Int64"
    assert str(cleaned["flag"].dtype) == "boolean"
    assert pd.api.types.is_datetime64_any_dtype(cleaned["event_date"])

    sql_result = SQLService(datasets).query(
        record.dataset_id,
        "SELECT name, amount, flag FROM data_table ORDER BY id",
    )
    assert sql_result.to_dict("records") == [
        {"name": "Alice", "amount": 10, "flag": 1},
        {"name": "Bob", "amount": 10, "flag": 0},
    ]
    manifest = datasets.get_manifest(record.dataset_id)
    table_manifest = manifest["tables"][0]
    assert table_manifest["cleaning_status"] == "applied"
    assert len(table_manifest["cleaning_steps"]) == len(_operations())
    assert table_manifest["processed_row_count"] == 2

    reloaded = DatasetService(dataset_dir=dataset_dir)
    reloaded_data = reloaded.get_table_dataframe(record.dataset_id, "dirty")
    assert str(reloaded_data["amount"].dtype) == "Int64"
    assert str(reloaded_data["flag"].dtype) == "boolean"
    assert pd.api.types.is_datetime64_any_dtype(reloaded_data["event_date"])

    reset = PreprocessingService(reloaded).reset_cleaning(record.dataset_id, "dirty")
    assert reset["reset_tables"] == ["dirty"]
    assert raw_path.read_bytes() == raw_before
    restored = reloaded.get_table_dataframe(record.dataset_id, "dirty")
    assert len(restored) == 4
    assert "empty" in restored.columns
    reset_manifest = reloaded.get_manifest(record.dataset_id)
    assert reset_manifest["processing_status"] == "not_started"
    assert reset_manifest["tables"][0]["cleaning_steps"] == []


def test_cleaning_tools_are_registered_and_invokable(tmp_path) -> None:
    datasets = DatasetService(dataset_dir=tmp_path / "datasets")
    record = datasets.save_dataset("dirty.csv", DIRTY_CSV)
    tools = build_tools(
        SQLService(datasets),
        ChartService(tmp_path / "charts"),
        AnalysisService(datasets),
    )
    tools_by_name = {tool.name: tool for tool in tools}

    assert {"suggest_cleaning", "apply_cleaning", "reset_cleaning"} <= set(tools_by_name)
    suggestion = json.loads(
        tools_by_name["suggest_cleaning"].invoke(
            {"dataset_id": record.dataset_id, "table_name": "dirty"}
        )
    )
    assert suggestion["mode"] == "suggest_only"

    applied = json.loads(
        tools_by_name["apply_cleaning"].invoke(
            {
                "dataset_id": record.dataset_id,
                "table_name": "dirty",
                "operations": [
                    {"operation": "trim_strings", "column": "name"},
                ],
            }
        )
    )
    assert applied["sqlite_rebuilt"] is True

    reset = json.loads(
        tools_by_name["reset_cleaning"].invoke(
            {"dataset_id": record.dataset_id, "table_name": "dirty"}
        )
    )
    assert reset["raw_unchanged"] is True
