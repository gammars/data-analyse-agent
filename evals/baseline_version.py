from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.agent.planner import _planner_system_prompt
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.scope_router import _scope_system_prompt
from app.agent.tool_policy import MAX_PLAN_STEPS
from app.agent.runtime import MAX_STEP_TOOL_ROUNDS, MAX_TOOL_ROUNDS


BASELINE_CONFIG_PATH = Path(__file__).parent / "configs" / "baseline_v1.json"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_baseline_config(path: Path = BASELINE_CONFIG_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def current_prompt_hashes() -> dict[str, str]:
    return {
        "planner": sha256_text(_planner_system_prompt()),
        "scope_router": sha256_text(_scope_system_prompt()),
        "system_tool": sha256_text(SYSTEM_PROMPT),
    }


def validate_frozen_baseline(path: Path = BASELINE_CONFIG_PATH) -> list[str]:
    config = load_baseline_config(path)
    errors: list[str] = []

    expected_hashes = {
        name: item["sha256"]
        for name, item in config["prompt_versions"].items()
    }
    actual_hashes = current_prompt_hashes()
    for name, expected in expected_hashes.items():
        actual = actual_hashes[name]
        if actual != expected:
            errors.append(
                f"{name} prompt changed: expected {expected}, got {actual}; "
                "create a new prompt and baseline version instead of overwriting baseline-v1"
            )

    execution = config["execution"]
    if execution["max_plan_steps"] != MAX_PLAN_STEPS:
        errors.append("max_plan_steps no longer matches the frozen runtime")
    if execution["max_step_tool_rounds"] != MAX_STEP_TOOL_ROUNDS:
        errors.append("max_step_tool_rounds no longer matches the frozen runtime")
    if execution["legacy_non_stream_max_tool_rounds"] != MAX_TOOL_ROUNDS:
        errors.append("legacy_non_stream_max_tool_rounds no longer matches the frozen runtime")

    return errors


def assert_frozen_baseline(path: Path = BASELINE_CONFIG_PATH) -> None:
    errors = validate_frozen_baseline(path)
    if errors:
        raise RuntimeError("\n".join(errors))

