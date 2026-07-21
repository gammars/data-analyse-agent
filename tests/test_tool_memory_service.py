import json

from app.services.tool_memory_service import PendingToolFailure, ToolMemoryService


SCHEMA = """
CREATE TABLE orders (
    order_id TEXT,
    customer_id TEXT,
    order_purchase_timestamp TEXT
);
"""


def _pending(service: ToolMemoryService, result: str) -> PendingToolFailure:
    args = {
        "sql": "SELECT order_date, COUNT(*) FROM orders GROUP BY order_date",
    }
    reflection = service.reflect_failure("query_data", args, result)
    assert reflection is not None
    return PendingToolFailure(
        tool_name="query_data",
        tool_args=args,
        result=result,
        reflection=reflection,
        created_at_round=0,
    )


def test_sqlite_errors_are_parsed_into_reflections(tmp_path) -> None:
    service = ToolMemoryService(tmp_path)
    cases = [
        ("no such column: order_date", "sql_column_mismatch", "order_date"),
        ("no such table: order_items", "sql_table_mismatch", "order_items"),
        ("ambiguous column name: customer_id", "sql_ambiguous_column", "customer_id"),
        ("no such function: datediff", "sql_function_mismatch", "datediff"),
    ]

    for error, expected_type, expected_identifier in cases:
        reflection = service.reflect_failure(
            "query_data",
            {"sql": "SELECT broken FROM orders"},
            f"SQL 查询执行失败：{error}\n请检查 SQL。",
        )
        assert reflection is not None
        assert reflection.failure_type == expected_type
        assert reflection.failed_identifier == expected_identifier
        assert reflection.retry_hint


def test_successful_retry_persists_and_injects_confirmed_memory(tmp_path) -> None:
    service = ToolMemoryService(tmp_path)
    dataset_id = "dataset-1"
    memory = service.record_success_after_failure(
        dataset_id=dataset_id,
        schema_text=SCHEMA,
        pending_failure=_pending(service, "no such column: order_date"),
        successful_tool_name="query_data",
        successful_tool_args={
            "sql": (
                "SELECT date(order_purchase_timestamp) AS order_date, COUNT(*) "
                "FROM orders GROUP BY date(order_purchase_timestamp)"
            )
        },
    )

    assert memory is not None
    assert "order_date" in memory.lesson
    store_path = tmp_path / dataset_id / "tool_memory.json"
    assert store_path.exists()
    assert json.loads(store_path.read_text(encoding="utf-8"))["version"] == 1

    prompt, selected = service.build_prompt_context(
        dataset_id=dataset_id,
        schema_text=SCHEMA,
        tool_names=["query_data"],
        user_question="按订单日期统计订单数量",
    )
    assert len(selected) == 1
    assert memory.lesson in prompt
    assert "不要臆造字段名" in prompt


def test_schema_change_invalidates_old_memory(tmp_path) -> None:
    service = ToolMemoryService(tmp_path)
    dataset_id = "dataset-1"
    service.record_success_after_failure(
        dataset_id=dataset_id,
        schema_text=SCHEMA,
        pending_failure=_pending(service, "no such column: order_date"),
        successful_tool_name="query_data",
        successful_tool_args={"sql": "SELECT order_purchase_timestamp FROM orders"},
    )

    prompt, selected = service.build_prompt_context(
        dataset_id=dataset_id,
        schema_text=f"{SCHEMA}\nALTER TABLE orders ADD COLUMN order_date TEXT;",
        tool_names=["query_data"],
        user_question="按订单日期统计",
    )
    assert prompt == ""
    assert selected == []


def test_duplicate_memory_is_updated_instead_of_appended(tmp_path) -> None:
    service = ToolMemoryService(tmp_path)
    dataset_id = "dataset-1"
    pending = _pending(service, "no such column: order_date")
    success_args = {"sql": "SELECT order_purchase_timestamp FROM orders"}

    first = service.record_success_after_failure(
        dataset_id, SCHEMA, pending, "query_data", success_args
    )
    second = service.record_success_after_failure(
        dataset_id, SCHEMA, pending, "query_data", success_args
    )

    memories = service.load_memories(dataset_id)
    assert first is not None and second is not None
    assert len(memories) == 1
    assert memories[0].memory_id == first.memory_id
    assert memories[0].hit_count == 1


def test_non_sql_or_unknown_errors_are_not_reflected(tmp_path) -> None:
    service = ToolMemoryService(tmp_path)
    assert service.reflect_failure("profile_data", {}, "no such column: x") is None
    assert service.reflect_failure("query_data", {"sql": "SELECT 1"}, "timeout") is None


def test_python_analysis_nested_json_error_is_reflected(tmp_path) -> None:
    service = ToolMemoryService(tmp_path)
    result = json.dumps(
        {
            "ok": False,
            "error": {
                "type": "OperationalError",
                "message": "Python 沙箱分析失败：no such column: order_date\n请检查 SQL。",
            },
        },
        ensure_ascii=False,
    )

    reflection = service.reflect_failure(
        "python_analysis",
        {"sql": "SELECT order_date FROM orders"},
        result,
    )
    assert reflection is not None
    assert reflection.failed_identifier == "order_date"


def test_sql_memory_is_shared_across_sql_tools(tmp_path) -> None:
    service = ToolMemoryService(tmp_path)
    dataset_id = "dataset-1"
    service.record_success_after_failure(
        dataset_id=dataset_id,
        schema_text=SCHEMA,
        pending_failure=_pending(service, "no such column: order_date"),
        successful_tool_name="query_data",
        successful_tool_args={"sql": "SELECT order_purchase_timestamp FROM orders"},
    )

    prompt, selected = service.build_prompt_context(
        dataset_id=dataset_id,
        schema_text=SCHEMA,
        tool_names=["generate_chart"],
        user_question="画每日订单量趋势",
    )
    assert len(selected) == 1
    assert "order_date" in prompt


def test_memory_store_is_pruned_to_configured_limit(tmp_path) -> None:
    service = ToolMemoryService(tmp_path, max_memories=2)
    dataset_id = "dataset-1"
    for index in range(3):
        failed_identifier = f"missing_{index}"
        args = {"sql": f"SELECT {failed_identifier} FROM orders"}
        reflection = service.reflect_failure(
            "query_data",
            args,
            f"no such column: {failed_identifier}",
        )
        assert reflection is not None
        service.record_success_after_failure(
            dataset_id=dataset_id,
            schema_text=SCHEMA,
            pending_failure=PendingToolFailure(
                tool_name="query_data",
                tool_args=args,
                result=f"no such column: {failed_identifier}",
                reflection=reflection,
                created_at_round=0,
            ),
            successful_tool_name="query_data",
            successful_tool_args={
                "sql": f"SELECT order_purchase_timestamp AS fixed_{index} FROM orders"
            },
        )

    assert len(service.load_memories(dataset_id)) == 2
