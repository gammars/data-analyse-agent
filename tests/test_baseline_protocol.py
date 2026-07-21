import hashlib
import json
from pathlib import Path

from evals.baseline_version import load_baseline_config, validate_frozen_baseline


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_frozen_baseline_matches_current_prompts_and_runtime() -> None:
    assert validate_frozen_baseline() == []


def test_baseline_configuration_has_required_research_contract() -> None:
    config = load_baseline_config()

    assert config["system_version"] == "baseline-v1"
    assert config["task_scope"] == {
        "primary_task": "deterministic_multi_table_data_analysis",
        "open_ended_report_evaluation": False,
        "required_oracle": True,
    }
    assert config["execution"]["max_plan_steps"] == 5
    assert config["execution"]["max_step_tool_rounds"] == 6
    assert config["model"] == {
        "provider_protocol": "openai_compatible",
        "model": "LongCat-2.0",
        "base_url": "https://api.longcat.chat/openai/v1",
        "temperature": 0.1,
        "streaming": True,
    }
    assert config["metrics"] == [
        "answer_accuracy",
        "execution_success_rate",
        "tool_selection_accuracy",
        "average_tool_calls",
        "average_latency_ms",
        "token_usage",
        "estimated_cost",
    ]


def test_dirty_worktree_snapshot_is_present_and_hash_locked() -> None:
    config = load_baseline_config()
    snapshot = REPO_ROOT / config["git"]["snapshot_path"]

    assert snapshot.exists()
    assert snapshot.stat().st_size == config["git"]["snapshot_size_bytes"]
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == config["git"][
        "snapshot_sha256"
    ]


def test_protocol_contains_no_subjective_acceptance_phrases() -> None:
    protocol = (REPO_ROOT / "research" / "experiment_protocol.md").read_text(
        encoding="utf-8"
    )
    forbidden = ("效果很好", "回答正确", "比较智能")

    # The phrases may appear only in the explicit prohibition sentence.
    stripped = protocol.replace(
        "结果报告只使用数值和可复核分类，不使用“效果很好”“基本正确”“分析合理”“比较智能”\n"
        "等主观描述替代指标。",
        "",
    )
    assert all(phrase not in stripped for phrase in forbidden)


def test_baseline_config_is_valid_json() -> None:
    config_path = REPO_ROOT / "evals" / "configs" / "baseline_v1.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["immutability_policy"]["overwrite_frozen_config"] is False

