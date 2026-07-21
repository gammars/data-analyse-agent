from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.artifacts import build_tool_artifacts
from app.agent.planner import build_fallback_plan
from app.agent.scope_router import classify_scope_by_rules
from app.services.dataset_service import DatasetService
from app.services.python_sandbox_service import PythonSandboxService
from app.services.sql_service import SQLService


CASES_DIR = PROJECT_ROOT / "evals" / "cases"
REPORTS_DIR = PROJECT_ROOT / "evals" / "reports"


@dataclass
class EvalResult:
    id: str
    suite: str
    passed: bool
    skipped: bool = False
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local evaluation suites.")
    parser.add_argument(
        "--run-docker",
        action="store_true",
        help="Also run a Docker-backed Python sandbox smoke eval.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="data-agent-evals-") as tmp:
        dataset_service = DatasetService(dataset_dir=Path(tmp) / "datasets")
        ecommerce_dataset_id = _create_ecommerce_fixture(dataset_service)
        sql_service = SQLService(dataset_service)
        schema_texts = {
            "ecommerce_small": dataset_service.get_schema(ecommerce_dataset_id),
            "finance_small": (
                "数据表数量：1\n"
                "表 stock_prices: date, ticker, open, close, high, low, volume, return"
            ),
        }
        dataset_ids = {"ecommerce_small": ecommerce_dataset_id}

        results: list[EvalResult] = []
        results.extend(_run_scope_eval(schema_texts))
        results.extend(_run_planner_eval("planner", "planner_eval.jsonl"))
        results.extend(_run_planner_eval("tool_routing", "tool_routing_eval.jsonl"))
        results.extend(_run_sql_eval(sql_service, dataset_ids))
        results.extend(_run_python_eval(args.run_docker, Path(tmp)))

    report = _build_report(results)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _print_report(report)
    return 0 if report["summary"]["failed"] == 0 else 1


def _run_scope_eval(schema_texts: dict[str, str]) -> list[EvalResult]:
    results = []
    for case in _read_jsonl("scope_router_eval.jsonl"):
        expected = case["expected"]
        schema_text = schema_texts.get(case["dataset"], "")
        decision = classify_scope_by_rules(case["question"], schema_text)
        if decision is None:
            actual = {"scope": "in_scope", "should_plan": True}
        else:
            actual = {
                "scope": decision.scope,
                "should_plan": bool(decision.should_plan),
                "intent": decision.intent,
                "reason": decision.reason,
            }
        passed = (
            actual["scope"] == expected["scope"]
            and actual["should_plan"] == expected["should_plan"]
        )
        results.append(
            EvalResult(
                id=case["id"],
                suite="scope",
                passed=passed,
                message="" if passed else f"expected={expected}, actual={actual}",
                details={"question": case["question"], "expected": expected, "actual": actual},
            )
        )
    return results


def _run_planner_eval(suite: str, filename: str) -> list[EvalResult]:
    results = []
    for case in _read_jsonl(filename):
        expected = case["expected"]
        plan = build_fallback_plan(case["question"])
        intents = [step.intent for step in plan.steps]
        tools = [tool for step in plan.steps for tool in step.allowed_tools]
        checks = [
            len(plan.steps) <= expected.get("max_steps", 5),
            _contains_all(intents, expected.get("must_include_intents", [])),
            _contains_all(tools, expected.get("must_use_tools", [])),
            _contains_none(tools, expected.get("must_not_use_tools", [])),
        ]
        if expected.get("primary_intent"):
            checks.append(plan.primary_intent == expected["primary_intent"])
        passed = all(checks)
        actual = {
            "primary_intent": plan.primary_intent,
            "intents": intents,
            "tools": tools,
            "step_count": len(plan.steps),
        }
        results.append(
            EvalResult(
                id=case["id"],
                suite=suite,
                passed=passed,
                message="" if passed else f"expected={expected}, actual={actual}",
                details={"question": case["question"], "expected": expected, "actual": actual},
            )
        )
    return results


def _run_sql_eval(sql_service: SQLService, dataset_ids: dict[str, str]) -> list[EvalResult]:
    results = []
    for case in _read_jsonl("sql_correctness_eval.jsonl"):
        expected = case["expected"]
        dataset_id = dataset_ids[case["dataset"]]
        try:
            dataframe = sql_service.query(dataset_id, case["reference_sql"], max_rows=100)
        except Exception as exc:
            if expected.get("should_error"):
                passed = expected.get("error_contains", "") in str(exc)
                results.append(
                    EvalResult(
                        id=case["id"],
                        suite="sql",
                        passed=passed,
                        message="" if passed else f"unexpected error: {exc}",
                        details={"question": case["question"], "error": str(exc)},
                    )
                )
                continue
            results.append(
                EvalResult(
                    id=case["id"],
                    suite="sql",
                    passed=False,
                    message=f"query failed: {exc}",
                    details={"question": case["question"], "sql": case["reference_sql"]},
                )
            )
            continue

        if expected.get("should_error"):
            results.append(
                EvalResult(
                    id=case["id"],
                    suite="sql",
                    passed=False,
                    message="expected SQL to be rejected, but it executed",
                    details={"question": case["question"], "rows": _records(dataframe)},
                )
            )
            continue

        records = _records(dataframe)
        checks = [
            _contains_all(list(dataframe.columns), expected.get("required_columns", [])),
            len(records) >= expected.get("min_rows", 0),
        ]
        if "rows" in expected:
            checks.append(records == expected["rows"])
        passed = all(checks)
        results.append(
            EvalResult(
                id=case["id"],
                suite="sql",
                passed=passed,
                message="" if passed else f"expected={expected}, actual_rows={records}",
                details={
                    "question": case["question"],
                    "sql": case["reference_sql"],
                    "actual_rows": records,
                },
            )
        )
    return results


def _run_python_eval(run_docker: bool, tmp_dir: Path) -> list[EvalResult]:
    results = []
    sandbox = PythonSandboxService(
        runs_dir=tmp_dir / "python_runs",
        chart_dir=tmp_dir / "charts",
    )
    for case in _read_jsonl("python_analysis_eval.jsonl"):
        expected = case["expected"]
        if expected.get("static_reject"):
            try:
                sandbox._validate_python_code(case["python_code"])
            except Exception as exc:
                passed = expected.get("error_contains", "") in str(exc)
                results.append(
                    EvalResult(
                        id=case["id"],
                        suite="python",
                        passed=passed,
                        message="" if passed else f"unexpected validation error: {exc}",
                        details={"question": case["question"], "error": str(exc)},
                    )
                )
                continue
            results.append(
                EvalResult(
                    id=case["id"],
                    suite="python",
                    passed=False,
                    message="expected code to be rejected, but it passed static validation",
                    details={"question": case["question"]},
                )
            )
            continue

        plan = build_fallback_plan(case["question"])
        tools = [tool for step in plan.steps for tool in step.allowed_tools]
        artifact_result = _fake_python_artifact()
        artifact = build_tool_artifacts(
            step_id="step_1",
            tool_name="python_analysis",
            tool_args={"analysis_goal": case["question"]},
            result=json.dumps(artifact_result, ensure_ascii=False),
            success=True,
            duration_ms=321,
        )[0]
        checks = [
            expected.get("must_use_tool") in tools,
            artifact.type == expected.get("artifact_type"),
            bool(artifact.preview.get("figures")) == expected.get("must_generate_chart", False),
            _contains_all(list(artifact.content.keys()), expected.get("result_schema", [])),
        ]
        results.append(
            EvalResult(
                id=case["id"],
                suite="python",
                passed=all(checks),
                message="" if all(checks) else f"tools={tools}, artifact={artifact.model_dump()}",
                details={"question": case["question"], "tools": tools, "artifact": artifact.model_dump()},
            )
        )

    results.append(_run_docker_smoke_eval(tmp_dir) if run_docker else _skipped_docker_eval())
    return results


def _run_docker_smoke_eval(tmp_dir: Path) -> EvalResult:
    code = """
import json
from pathlib import Path

import pandas as pd

data = json.loads(Path("/workspace/input/data.json").read_text(encoding="utf-8"))
df = pd.DataFrame(data)
Path("/workspace/output/result.json").write_text(
    json.dumps(
        {
            "summary": "docker sandbox eval ok",
            "metrics": {"total_amount": int(df["amount"].sum())},
            "figures": [],
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
""".strip()
    try:
        result = PythonSandboxService(
            runs_dir=tmp_dir / "python_runs_docker",
            chart_dir=tmp_dir / "charts_docker",
        ).run_analysis(
            dataframe=pd.DataFrame([{"category": "A", "amount": 10}, {"category": "B", "amount": 20}]),
            python_code=code,
            analysis_goal="Docker 沙箱冒烟评测",
        )
    except Exception as exc:
        return EvalResult(
            id="py_docker_001",
            suite="python",
            passed=False,
            message=f"Docker sandbox smoke eval failed: {exc}",
            details={"error": str(exc)},
        )
    return EvalResult(
        id="py_docker_001",
        suite="python",
        passed=result.result.get("metrics", {}).get("total_amount") == 30,
        details={"run_id": result.run_id, "result": result.result},
    )


def _skipped_docker_eval() -> EvalResult:
    return EvalResult(
        id="py_docker_001",
        suite="python",
        passed=True,
        skipped=True,
        message="Docker smoke eval skipped. Use --run-docker to execute it.",
    )


def _create_ecommerce_fixture(dataset_service: DatasetService) -> str:
    files = [
        (
            "orders.csv",
            "\n".join(
                [
                    "order_id,customer_id,order_status,order_purchase_timestamp,order_delivered_customer_date",
                    "o1,c1,delivered,2024-01-05,2024-01-10",
                    "o2,c2,delivered,2024-01-18,2024-01-25",
                    "o3,c1,shipped,2024-02-02,",
                    "o4,c3,delivered,2024-02-20,2024-02-28",
                    "o5,c4,delivered,2024-03-03,2024-03-08",
                    "o6,c5,delivered,2024-03-15,2024-03-20",
                ]
            ),
        ),
        (
            "order_items.csv",
            "\n".join(
                [
                    "order_id,product_id,price,freight_value",
                    "o1,p1,100,12",
                    "o2,p2,200,30",
                    "o3,p1,120,15",
                    "o4,p3,80,9",
                    "o5,p4,300,45",
                    "o6,p2,150,28",
                ]
            ),
        ),
        (
            "payments.csv",
            "\n".join(
                [
                    "order_id,payment_type,payment_value",
                    "o1,credit_card,112",
                    "o2,boleto,230",
                    "o3,credit_card,135",
                    "o4,voucher,89",
                    "o5,credit_card,345",
                    "o6,boleto,178",
                ]
            ),
        ),
        (
            "products.csv",
            "\n".join(
                [
                    "product_id,product_category_name",
                    "p1,electronics",
                    "p2,books",
                    "p3,home",
                    "p4,electronics",
                ]
            ),
        ),
        (
            "customers.csv",
            "\n".join(
                [
                    "customer_id,customer_state",
                    "c1,SP",
                    "c2,RJ",
                    "c3,SP",
                    "c4,MG",
                    "c5,RJ",
                ]
            ),
        ),
        (
            "reviews.csv",
            "\n".join(
                [
                    "order_id,review_score",
                    "o1,5",
                    "o2,4",
                    "o3,3",
                    "o4,5",
                    "o5,2",
                    "o6,4",
                ]
            ),
        ),
    ]
    record = dataset_service.save_dataset_files(
        [(filename, content.encode("utf-8")) for filename, content in files]
    )
    return record.dataset_id


def _fake_python_artifact() -> dict[str, Any]:
    return {
        "ok": True,
        "run_id": "eval-run",
        "input_rows": 6,
        "result": {
            "summary": "价格和运费之间存在正相关，已生成相关性热力图。",
            "metrics": {"price_freight_corr": 0.82},
            "figures": [
                {
                    "chart_id": "eval-chart",
                    "chart_url": "/charts/eval-chart.png",
                    "chart_type": "heatmap",
                    "title": "价格与运费相关性热力图",
                }
            ],
        },
        "figures": [
            {
                "chart_id": "eval-chart",
                "chart_url": "/charts/eval-chart.png",
                "chart_type": "heatmap",
                "title": "价格与运费相关性热力图",
            }
        ],
    }


def _build_report(results: list[EvalResult]) -> dict[str, Any]:
    suites: dict[str, dict[str, Any]] = {}
    for result in results:
        suite = suites.setdefault(
            result.suite,
            {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "cases": []},
        )
        suite["total"] += 1
        if result.skipped:
            suite["skipped"] += 1
        elif result.passed:
            suite["passed"] += 1
        else:
            suite["failed"] += 1
        suite["cases"].append(
            {
                "id": result.id,
                "passed": result.passed,
                "skipped": result.skipped,
                "message": result.message,
                "details": result.details,
            }
        )

    summary = {
        "total": len(results),
        "passed": sum(1 for item in results if item.passed and not item.skipped),
        "failed": sum(1 for item in results if not item.passed and not item.skipped),
        "skipped": sum(1 for item in results if item.skipped),
    }
    summary["pass_rate"] = round(
        summary["passed"] / max(summary["total"] - summary["skipped"], 1),
        4,
    )
    return {"summary": summary, "suites": suites}


def _print_report(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(
        "Eval summary: "
        f"passed={summary['passed']} failed={summary['failed']} "
        f"skipped={summary['skipped']} pass_rate={summary['pass_rate']:.2%}"
    )
    for suite_name, suite in report["suites"].items():
        print(
            f"- {suite_name}: passed={suite['passed']} "
            f"failed={suite['failed']} skipped={suite['skipped']} total={suite['total']}"
        )
        for case in suite["cases"]:
            if not case["passed"] and not case["skipped"]:
                print(f"  FAIL {case['id']}: {case['message']}")
    print(f"Report written to {REPORTS_DIR / 'latest.json'}")


def _read_jsonl(filename: str) -> list[dict[str, Any]]:
    path = CASES_DIR / filename
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            cases.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} is not valid JSONL") from exc
    return cases


def _records(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(dataframe.to_json(orient="records", force_ascii=False))


def _contains_all(actual: list[str], expected: list[str]) -> bool:
    return all(item in actual for item in expected)


def _contains_none(actual: list[str], forbidden: list[str]) -> bool:
    return all(item not in actual for item in forbidden)


if __name__ == "__main__":
    raise SystemExit(main())
