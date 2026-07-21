import json
import uuid
from typing import Any

from pydantic import BaseModel, Field


MAX_SUMMARY_CHARS = 700
MAX_TEXT_PREVIEW_CHARS = 1800
MAX_JSON_PREVIEW_CHARS = 3000
MAX_METRIC_ITEMS = 20
MAX_FIGURE_ITEMS = 6


class AnalysisArtifact(BaseModel):
    """Compact, reusable record derived from a tool result."""

    artifact_id: str = Field(default_factory=lambda: f"artifact-{uuid.uuid4().hex[:12]}")
    step_id: str | None = None
    type: str
    title: str
    summary: str
    source_tool: str
    success: bool = True
    preview: Any = None
    content: Any = None


def build_tool_artifacts(
    *,
    step_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any],
    result: object,
    success: bool,
    duration_ms: int | None = None,
) -> list[AnalysisArtifact]:
    text = _to_text(result).strip()
    payload = _try_load_json(text)

    if not success:
        return [
            AnalysisArtifact(
                step_id=step_id,
                type="error",
                title=f"{tool_name} 执行失败",
                summary=_extract_error_summary(payload, text),
                source_tool=tool_name,
                success=False,
                preview=_preview_error(payload, text, duration_ms),
            )
        ]

    if tool_name == "python_analysis":
        return [_python_analysis_artifact(step_id, tool_name, tool_args, payload, text, duration_ms)]

    if tool_name == "generate_chart":
        return [_chart_artifact(step_id, tool_name, tool_args, payload, text, duration_ms)]

    if tool_name == "query_data":
        return [_table_artifact(step_id, tool_name, tool_args, text, duration_ms)]

    return [_generic_artifact(step_id, tool_name, tool_args, payload, text, duration_ms)]


def artifact_context_text(artifact: dict[str, Any]) -> str:
    title = _truncate(str(artifact.get("title") or "分析产物"), 120)
    summary = _truncate(str(artifact.get("summary") or ""), MAX_SUMMARY_CHARS)
    preview = artifact.get("preview")
    preview_text = _truncate(_safe_json_dumps(preview), MAX_JSON_PREVIEW_CHARS) if preview else ""
    lines = [
        f"artifact:{artifact.get('artifact_id', '')}",
        f"type={artifact.get('type', '')}; source_tool={artifact.get('source_tool', '')}; success={artifact.get('success', True)}",
        f"title={title}",
        f"summary={summary}",
    ]
    if preview_text:
        lines.append(f"preview={preview_text}")
    return "\n".join(lines)


def tool_result_context_preview(tool_name: str, result: object) -> str:
    text = _to_text(result).strip()
    payload = _try_load_json(text)
    if isinstance(payload, dict):
        if tool_name == "python_analysis":
            nested_result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            figures = _collect_figures(payload)
            preview = {
                "ok": payload.get("ok"),
                "run_id": payload.get("run_id"),
                "input_rows": payload.get("input_rows"),
                "summary": _truncate(
                    str(nested_result.get("summary") or nested_result.get("conclusion") or payload.get("message") or ""),
                    MAX_SUMMARY_CHARS,
                ),
                "metrics": _limit_mapping(nested_result.get("metrics")),
                "figures": figures[:MAX_FIGURE_ITEMS],
                "warnings": payload.get("warnings", [])[:5] if isinstance(payload.get("warnings"), list) else [],
            }
            return _safe_json_dumps(preview)

        keys = {
            key: payload.get(key)
            for key in ("ok", "message", "chart_id", "chart_type", "chart_url", "title", "error")
            if key in payload
        }
        if keys:
            return _safe_json_dumps(keys)

        return _truncate(_safe_json_dumps(payload), MAX_JSON_PREVIEW_CHARS)

    return _truncate(text, MAX_TEXT_PREVIEW_CHARS)


def sanitize_tool_args_for_context(args: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in args.items():
        if key in {"dataset_id", "python_code"}:
            continue
        if isinstance(value, str):
            limit = 1200 if key == "sql" else 400
            sanitized[key] = _truncate(value, limit)
        else:
            sanitized[key] = value
    return sanitized


def _python_analysis_artifact(
    step_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any],
    payload: Any,
    text: str,
    duration_ms: int | None,
) -> AnalysisArtifact:
    data = payload if isinstance(payload, dict) else {}
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    summary = (
        result.get("summary")
        or result.get("conclusion")
        or data.get("message")
        or tool_args.get("analysis_goal")
        or "Python 沙箱分析已完成。"
    )
    figures = _collect_figures(data)
    preview = {
        "run_id": data.get("run_id"),
        "input_rows": data.get("input_rows"),
        "duration_ms": duration_ms,
        "metrics": _limit_mapping(result.get("metrics")),
        "figures": figures[:MAX_FIGURE_ITEMS],
        "warnings": data.get("warnings", [])[:5] if isinstance(data.get("warnings"), list) else [],
    }
    content = {
        "summary": _truncate(str(summary), MAX_SUMMARY_CHARS),
        "metrics": _limit_mapping(result.get("metrics")),
        "figures": figures[:MAX_FIGURE_ITEMS],
    }
    return AnalysisArtifact(
        step_id=step_id,
        type="python_result",
        title=_truncate(str(tool_args.get("analysis_goal") or "Python 分析结果"), 120),
        summary=_truncate(str(summary), MAX_SUMMARY_CHARS),
        source_tool=tool_name,
        success=True,
        preview=preview,
        content=content if data else _truncate(text, MAX_TEXT_PREVIEW_CHARS),
    )


def _chart_artifact(
    step_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any],
    payload: Any,
    text: str,
    duration_ms: int | None,
) -> AnalysisArtifact:
    data = payload if isinstance(payload, dict) else {}
    title = str(data.get("title") or tool_args.get("title") or "生成图表")
    summary = str(data.get("message") or f"图表已生成：{title}")
    preview = {
        "chart_id": data.get("chart_id"),
        "chart_type": data.get("chart_type") or tool_args.get("chart_type"),
        "chart_url": data.get("chart_url"),
        "duration_ms": duration_ms,
    }
    return AnalysisArtifact(
        step_id=step_id,
        type="chart",
        title=_truncate(title, 120),
        summary=_truncate(summary, MAX_SUMMARY_CHARS),
        source_tool=tool_name,
        success=True,
        preview=preview,
        content=preview if data else _truncate(text, MAX_TEXT_PREVIEW_CHARS),
    )


def _table_artifact(
    step_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any],
    text: str,
    duration_ms: int | None,
) -> AnalysisArtifact:
    sql = str(tool_args.get("sql") or "").strip()
    row_count = _estimate_markdown_table_rows(text)
    summary = "SQL 查询已返回结果。"
    if row_count is not None:
        summary = f"SQL 查询已返回约 {row_count} 行预览结果。"
    preview = {
        "sql": _truncate(sql, 1200) if sql else None,
        "estimated_preview_rows": row_count,
        "duration_ms": duration_ms,
        "text_preview": _truncate(text, MAX_TEXT_PREVIEW_CHARS),
    }
    return AnalysisArtifact(
        step_id=step_id,
        type="table",
        title="SQL 查询结果",
        summary=summary,
        source_tool=tool_name,
        success=True,
        preview=preview,
        content={"text_preview": _truncate(text, MAX_TEXT_PREVIEW_CHARS)},
    )


def _generic_artifact(
    step_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any],
    payload: Any,
    text: str,
    duration_ms: int | None,
) -> AnalysisArtifact:
    artifact_type = "json" if isinstance(payload, (dict, list)) else "text"
    summary = _generic_summary(payload, text, tool_name)
    preview = {
        "duration_ms": duration_ms,
        "result": _truncate(_safe_json_dumps(payload), MAX_JSON_PREVIEW_CHARS)
        if isinstance(payload, (dict, list))
        else _truncate(text, MAX_TEXT_PREVIEW_CHARS),
    }
    return AnalysisArtifact(
        step_id=step_id,
        type=artifact_type,
        title=f"{tool_name} 输出",
        summary=summary,
        source_tool=tool_name,
        success=True,
        preview=preview,
        content=preview,
    )


def _generic_summary(payload: Any, text: str, tool_name: str) -> str:
    if isinstance(payload, dict):
        for key in ("summary", "message", "conclusion"):
            if payload.get(key):
                return _truncate(str(payload[key]), MAX_SUMMARY_CHARS)
        if payload.get("error"):
            return _truncate(str(payload["error"]), MAX_SUMMARY_CHARS)
    return _truncate(text or f"{tool_name} 已返回结果。", MAX_SUMMARY_CHARS)


def _extract_error_summary(payload: Any, text: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return _truncate(str(error.get("message") or error.get("type") or error), MAX_SUMMARY_CHARS)
        if error:
            return _truncate(str(error), MAX_SUMMARY_CHARS)
        if payload.get("message"):
            return _truncate(str(payload["message"]), MAX_SUMMARY_CHARS)
    return _truncate(text or "工具执行失败。", MAX_SUMMARY_CHARS)


def _preview_error(payload: Any, text: str, duration_ms: int | None) -> dict[str, Any]:
    preview: dict[str, Any] = {"duration_ms": duration_ms}
    if isinstance(payload, dict):
        preview["error"] = payload.get("error") or payload.get("message") or payload
    else:
        preview["error"] = _truncate(text, MAX_TEXT_PREVIEW_CHARS)
    return preview


def _collect_figures(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(payload.get("figures"), list):
        candidates.extend(payload["figures"])
    nested = payload.get("result")
    if isinstance(nested, dict) and isinstance(nested.get("figures"), list):
        candidates.extend(nested["figures"])
    if payload.get("chart_id") and payload.get("chart_url"):
        candidates.append(payload)

    figures: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        chart_id = item.get("chart_id")
        chart_url = item.get("chart_url")
        if not chart_id or not chart_url or chart_id in seen:
            continue
        seen.add(chart_id)
        figures.append(
            {
                "chart_id": chart_id,
                "chart_url": chart_url,
                "chart_type": item.get("chart_type") or "python",
                "title": item.get("title"),
            }
        )
    return figures


def _limit_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    limited: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= MAX_METRIC_ITEMS:
            break
        limited[str(key)] = item
    return limited


def _estimate_markdown_table_rows(text: str) -> int | None:
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(rows) < 3:
        return None
    return max(len(rows) - 2, 0)


def _try_load_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _to_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(value or "")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...（已截断）"
