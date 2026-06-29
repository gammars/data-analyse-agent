import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from pandas.api import types as pandas_types

from app.agent.models import build_chat_model
from app.schemas.manifest import DatasetManifest, ForeignKeyManifest, IndexManifest
from app.schemas.relationships import LLMRelationshipAdvice, TableRelationshipConfig
from app.services.dataset_service import (
    INDEXES_FILENAME,
    SCHEMA_FILENAME,
    DatasetService,
    dataset_service,
)
from app.services.sqlite_ddl_service import SQLiteDDLService, sqlite_ddl_service
from app.services.sqlite_storage_service import SQLiteStorageService, sqlite_storage_service


class RelationshipService:
    """Suggest, validate, persist, and materialize relational metadata."""

    def __init__(
        self,
        datasets: DatasetService = dataset_service,
        sqlite_ddl: SQLiteDDLService = sqlite_ddl_service,
        sqlite_storage: SQLiteStorageService = sqlite_storage_service,
    ) -> None:
        self.datasets = datasets
        self.sqlite_ddl = sqlite_ddl
        self.sqlite_storage = sqlite_storage

    def get_configuration(self, dataset_id: str) -> dict[str, Any]:
        manifest = self._load_manifest(dataset_id)
        return {
            "dataset_id": dataset_id,
            "status": manifest.relationship_status,
            "confirmed_at": manifest.relationship_confirmed_at,
            "tables": [self._config_dict(table) for table in manifest.tables],
            "validation": self.validate(dataset_id),
        }

    def suggest(
        self,
        dataset_id: str,
        include_llm: bool = False,
        refresh_llm: bool = False,
    ) -> dict[str, Any]:
        manifest = self._load_manifest(dataset_id)
        frames = self._load_frames(dataset_id)
        primary_key_candidates: dict[str, list[dict[str, Any]]] = {}
        table_suggestions = []

        for table in manifest.tables:
            dataframe = frames[table.table_name]
            candidates = self._primary_key_candidates(dataframe)
            self._include_current_primary_key(candidates, table.primary_key)
            primary_key_candidates[table.table_name] = candidates
            table_suggestions.append(
                {
                    "table_name": table.table_name,
                    "primary_key_candidates": candidates,
                    "index_candidates": [],
                }
            )

        foreign_key_candidates = self._foreign_key_candidates(
            frames,
            manifest,
            primary_key_candidates,
        )
        indexes_by_table = self._index_candidates(
            frames,
            manifest,
            foreign_key_candidates,
        )
        for table in table_suggestions:
            table["index_candidates"] = indexes_by_table[table["table_name"]]

        result = {
            "dataset_id": dataset_id,
            "relationship_status": manifest.relationship_status,
            "current": [self._config_dict(table) for table in manifest.tables],
            "tables": table_suggestions,
            "foreign_key_candidates": foreign_key_candidates,
            "validation": self.validate(dataset_id),
        }
        if include_llm:
            advice = self._get_llm_advice(
                dataset_id,
                manifest,
                result,
                refresh=refresh_llm,
            )
            recommended_configuration = self._apply_advice_safety(
                dataset_id,
                result,
                advice,
            )
            result["llm_advice"] = advice
            result["recommended_configuration"] = recommended_configuration
            self._mark_llm_recommendations(result, advice)
        else:
            result["llm_advice"] = {
                "status": "disabled",
                "source": "deterministic_candidates",
                "summary": "测试或内部调用未启用 LLM 建议。",
                "table_recommendations": [],
                "foreign_key_recommendations": [],
                "warnings": [],
            }
        return result

    def revise(
        self,
        dataset_id: str,
        configs: list[TableRelationshipConfig],
    ) -> dict[str, Any]:
        validation = self.validate(dataset_id, configs)
        manifest = self._load_manifest(dataset_id)
        suggestions = self.suggest(dataset_id, include_llm=False)
        correction_context = {
            "previous_configuration": [
                config.model_dump(mode="json") for config in configs
            ],
            "validation_errors": validation["errors"],
        }
        advice = self._get_llm_advice(
            dataset_id,
            manifest,
            suggestions,
            refresh=True,
            correction_context=correction_context,
        )
        recommended_configuration = self._apply_advice_safety(
            dataset_id,
            suggestions,
            advice,
        )
        suggestions["llm_advice"] = advice
        suggestions["recommended_configuration"] = recommended_configuration
        suggestions["revision"] = {
            "triggered": True,
            "previous_validation": validation,
        }
        self._mark_llm_recommendations(suggestions, advice)
        return suggestions

    def validate(
        self,
        dataset_id: str,
        configs: list[TableRelationshipConfig] | None = None,
    ) -> dict[str, Any]:
        manifest = self._load_manifest(dataset_id)
        frames = self._load_frames(dataset_id)
        config_map = self._config_map(manifest, configs)
        errors: list[str] = []
        warnings: list[str] = []
        table_results = []
        all_index_names: set[str] = set()

        for table_name, dataframe in frames.items():
            config = config_map[table_name]
            table_errors: list[str] = []
            columns = {str(column) for column in dataframe.columns}
            primary_key = list(config.primary_key)

            missing_primary = set(primary_key) - columns
            if missing_primary:
                table_errors.append(
                    f"主键字段不存在：{', '.join(sorted(missing_primary))}"
                )
            elif primary_key:
                null_rows = int(dataframe[primary_key].isna().any(axis=1).sum())
                duplicate_rows = int(dataframe.duplicated(primary_key, keep=False).sum())
                if null_rows:
                    table_errors.append(f"主键包含 {null_rows} 行空值")
                if duplicate_rows:
                    table_errors.append(f"主键包含 {duplicate_rows} 行重复值")

            foreign_key_results = []
            seen_foreign_keys: set[tuple] = set()
            for foreign_key in config.foreign_keys:
                key = (
                    tuple(foreign_key.columns),
                    foreign_key.referenced_table,
                    tuple(foreign_key.referenced_columns),
                )
                if key in seen_foreign_keys:
                    table_errors.append("存在重复的外键配置")
                    continue
                seen_foreign_keys.add(key)
                result = self._validate_foreign_key(
                    table_name,
                    dataframe,
                    foreign_key,
                    frames,
                    config_map,
                )
                foreign_key_results.append(result)
                table_errors.extend(result["errors"])

            seen_index_columns: set[tuple[str, ...]] = set()
            for index in config.indexes:
                if not index.name.strip():
                    table_errors.append("索引名称不能为空")
                elif index.name in all_index_names:
                    table_errors.append(f"索引名称重复：{index.name}")
                all_index_names.add(index.name)
                missing = set(index.columns) - columns
                if missing:
                    table_errors.append(
                        f"索引 {index.name} 的字段不存在：{', '.join(sorted(missing))}"
                    )
                if not index.columns:
                    table_errors.append(f"索引 {index.name} 没有字段")
                signature = tuple(index.columns)
                if signature in seen_index_columns:
                    warnings.append(f"表 {table_name} 存在字段相同的重复索引")
                seen_index_columns.add(signature)

            errors.extend(f"表 {table_name}：{message}" for message in table_errors)
            table_results.append(
                {
                    "table_name": table_name,
                    "valid": not table_errors,
                    "primary_key": primary_key,
                    "foreign_keys": foreign_key_results,
                    "errors": table_errors,
                }
            )

        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "tables": table_results,
        }

    def save(
        self,
        dataset_id: str,
        configs: list[TableRelationshipConfig],
        confirmed: bool,
    ) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("关系配置必须由用户明确确认后才能保存")

        manifest = self._load_manifest(dataset_id)
        config_map = self._config_map(manifest, configs, require_all=True)
        validation = self.validate(dataset_id, configs)
        if not validation["valid"]:
            raise ValueError("关系配置完整性验证失败：" + "；".join(validation["errors"]))

        candidate = manifest.model_copy(deep=True)
        for table in candidate.tables:
            config = config_map[table.table_name]
            table.primary_key = list(config.primary_key)
            table.foreign_keys = list(config.foreign_keys)
            table.indexes = list(config.indexes)
        candidate.updated_at = datetime.now(timezone.utc).isoformat()
        candidate.relationship_status = "confirmed"
        candidate.relationship_confirmed_at = candidate.updated_at

        frames = list(self._load_frames(dataset_id).items())
        candidate_configs = {table.table_name: table for table in candidate.tables}
        ddl = self.sqlite_ddl.generate(frames, candidate_configs)
        database_path = self.datasets.get_database_path(dataset_id)
        dataset_path = database_path.parent
        staging_path = dataset_path / f".relationships-{uuid.uuid4().hex}"
        staging_path.mkdir(parents=True, exist_ok=False)

        staged_database = staging_path / database_path.name
        staged_manifest = staging_path / "manifest.json"
        staged_schema = staging_path / SCHEMA_FILENAME
        staged_indexes = staging_path / INDEXES_FILENAME
        try:
            self.sqlite_storage.rebuild_with_schema(
                staged_database,
                frames,
                ddl.schema_sql,
                ddl.indexes_sql,
            )
            self.datasets.manifests.write(staged_manifest, candidate)
            staged_schema.write_text(ddl.schema_sql, encoding="utf-8")
            staged_indexes.write_text(ddl.indexes_sql, encoding="utf-8")
            self._replace_artifacts(
                dataset_path,
                [staged_database, staged_manifest, staged_schema, staged_indexes],
            )
        finally:
            if staging_path.exists():
                shutil.rmtree(staging_path)

        schema = self.datasets.refresh_schema(dataset_id)
        return {
            "dataset_id": dataset_id,
            "saved": True,
            "relationship_status": candidate.relationship_status,
            "confirmed_at": candidate.relationship_confirmed_at,
            "validation": validation,
            "schema_sql": ddl.schema_sql,
            "indexes_sql": ddl.indexes_sql,
            "agent_schema": schema,
            "tables": [self._config_dict(table) for table in candidate.tables],
        }

    def _primary_key_candidates(self, dataframe: pd.DataFrame) -> list[dict[str, Any]]:
        row_count = len(dataframe)
        if row_count == 0:
            return []
        candidates = []
        id_columns = []
        for column in dataframe.columns:
            name = str(column)
            series = dataframe[column]
            if self._is_id_column(name):
                id_columns.append(name)
            if not series.isna().any() and int(series.nunique(dropna=False)) == row_count:
                candidates.append(
                    {
                        "columns": [name],
                        "score": 0.98 if self._is_id_column(name) else 0.78,
                        "reasons": ["字段无空值且值完全唯一"],
                    }
                )

        for left, right in combinations(id_columns[:8], 2):
            columns = [left, right]
            if dataframe[columns].isna().any(axis=None):
                continue
            if len(dataframe.drop_duplicates(columns)) == row_count:
                candidates.append(
                    {
                        "columns": columns,
                        "score": 0.88,
                        "reasons": ["两个标识字段组合后无空值且完全唯一"],
                    }
                )

        candidates.sort(key=lambda item: (-item["score"], len(item["columns"])))
        return candidates[:8]

    def _foreign_key_candidates(
        self,
        frames: dict[str, pd.DataFrame],
        manifest: DatasetManifest,
        primary_candidates: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[tuple] = set()
        current = {
            (
                table.table_name,
                tuple(foreign_key.columns),
                foreign_key.referenced_table,
                tuple(foreign_key.referenced_columns),
            )
            for table in manifest.tables
            for foreign_key in table.foreign_keys
        }

        for target_table, target_candidates in primary_candidates.items():
            target_frame = frames[target_table]
            for target_candidate in target_candidates:
                if len(target_candidate["columns"]) != 1:
                    continue
                target_column = target_candidate["columns"][0]
                target_values = set(target_frame[target_column].dropna().tolist())
                if not target_values:
                    continue
                for source_table, source_frame in frames.items():
                    if source_table == target_table:
                        continue
                    for source_column in map(str, source_frame.columns):
                        signature = (
                            source_table,
                            (source_column,),
                            target_table,
                            (target_column,),
                        )
                        is_current = signature in current
                        if not is_current and not self._names_can_reference(
                            source_column,
                            target_column,
                            target_table,
                        ):
                            continue
                        if not self._compatible_dtypes(
                            source_frame[source_column].dtype,
                            target_frame[target_column].dtype,
                        ):
                            continue
                        source_values = source_frame[source_column].dropna()
                        if source_values.empty:
                            continue
                        orphan_mask = ~source_values.isin(target_values)
                        orphan_count = int(orphan_mask.sum())
                        match_ratio = 1 - orphan_count / len(source_values)
                        if match_ratio < 0.9 and not is_current:
                            continue
                        if signature in seen:
                            continue
                        seen.add(signature)
                        candidates.append(
                            {
                                "candidate_id": self._foreign_key_candidate_id(
                                    source_table,
                                    [source_column],
                                    target_table,
                                    [target_column],
                                ),
                                "table_name": source_table,
                                "columns": [source_column],
                                "referenced_table": target_table,
                                "referenced_columns": [target_column],
                                "name": f"fk_{source_table}_{source_column}",
                                "score": round(min(0.99, 0.7 + match_ratio * 0.29), 3),
                                "match_ratio": round(match_ratio, 4),
                                "orphan_count": orphan_count,
                                "reasons": [
                                    "字段名称相似",
                                    f"非空值匹配率 {match_ratio:.1%}",
                                ],
                                "current": is_current,
                            }
                        )

        for table in manifest.tables:
            for foreign_key in table.foreign_keys:
                signature = (
                    table.table_name,
                    tuple(foreign_key.columns),
                    foreign_key.referenced_table,
                    tuple(foreign_key.referenced_columns),
                )
                if signature in seen:
                    continue
                seen.add(signature)
                candidates.append(
                    {
                        "candidate_id": self._foreign_key_candidate_id(
                            table.table_name,
                            list(foreign_key.columns),
                            foreign_key.referenced_table,
                            list(foreign_key.referenced_columns),
                        ),
                        "table_name": table.table_name,
                        "columns": list(foreign_key.columns),
                        "referenced_table": foreign_key.referenced_table,
                        "referenced_columns": list(foreign_key.referenced_columns),
                        "name": foreign_key.name or f"fk_{table.table_name}_{len(candidates) + 1}",
                        "score": 1.0,
                        "match_ratio": 1.0,
                        "orphan_count": 0,
                        "reasons": ["当前已保存的外键"],
                        "current": True,
                    }
                )

        candidates.sort(key=lambda item: (-item["current"], -item["score"]))
        return candidates

    def _index_candidates(
        self,
        frames: dict[str, pd.DataFrame],
        manifest: DatasetManifest,
        foreign_keys: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {name: [] for name in frames}
        current_by_table = {
            table.table_name: {tuple(index.columns): index for index in table.indexes}
            for table in manifest.tables
        }
        foreign_key_columns = {
            (item["table_name"], tuple(item["columns"])) for item in foreign_keys
        }

        for table_name, dataframe in frames.items():
            seen = set()
            for column in map(str, dataframe.columns):
                columns = (column,)
                current = current_by_table[table_name].get(columns)
                is_fk = (table_name, columns) in foreign_key_columns
                lowered = column.lower()
                keyword = any(
                    token in lowered
                    for token in (
                        "_id",
                        "date",
                        "time",
                        "status",
                        "type",
                        "category",
                        "state",
                        "city",
                        "country",
                    )
                )
                if not (current or is_fk or keyword):
                    continue
                name = current.name if current else self._index_name(table_name, column)
                result[table_name].append(
                    {
                        "name": name,
                        "columns": [column],
                        "unique": bool(current.unique) if current else False,
                        "score": 0.96 if is_fk else 0.72,
                        "reasons": [
                            "外键查询和 JOIN 常用字段" if is_fk else "常见筛选或分组字段"
                        ],
                        "current": current is not None,
                    }
                )
                seen.add(columns)

            for columns, index in current_by_table[table_name].items():
                if columns in seen:
                    continue
                result[table_name].append(
                    {
                        "name": index.name,
                        "columns": list(columns),
                        "unique": index.unique,
                        "score": 1.0,
                        "reasons": ["当前已保存的索引"],
                        "current": True,
                    }
                )
            result[table_name] = result[table_name][:10]
        return result

    def _validate_foreign_key(
        self,
        table_name: str,
        dataframe: pd.DataFrame,
        foreign_key: ForeignKeyManifest,
        frames: dict[str, pd.DataFrame],
        configs: dict[str, TableRelationshipConfig],
    ) -> dict[str, Any]:
        errors = []
        source_columns = list(foreign_key.columns)
        target_columns = list(foreign_key.referenced_columns)
        if not source_columns or len(source_columns) != len(target_columns):
            errors.append("外键字段与被引用字段数量不一致")
            return {"foreign_key": foreign_key.model_dump(), "orphan_count": None, "errors": errors}

        missing_source = set(source_columns) - set(map(str, dataframe.columns))
        if missing_source:
            errors.append(f"外键字段不存在：{', '.join(sorted(missing_source))}")
        target_frame = frames.get(foreign_key.referenced_table)
        if target_frame is None:
            errors.append(f"被引用表不存在：{foreign_key.referenced_table}")
        else:
            missing_target = set(target_columns) - set(map(str, target_frame.columns))
            if missing_target:
                errors.append(f"被引用字段不存在：{', '.join(sorted(missing_target))}")
            target_config = configs[foreign_key.referenced_table]
            if target_columns != list(target_config.primary_key):
                errors.append("被引用字段必须是目标表已配置的主键")

        orphan_count = None
        if not errors and target_frame is not None:
            for source_column, target_column in zip(
                source_columns,
                target_columns,
                strict=True,
            ):
                if not self._compatible_dtypes(
                    dataframe[source_column].dtype,
                    target_frame[target_column].dtype,
                ):
                    errors.append(
                        f"字段类型不兼容：{source_column} 与 "
                        f"{foreign_key.referenced_table}.{target_column}"
                    )
            if not errors:
                source_rows = dataframe[source_columns].dropna()
                target_values = set(target_frame[target_columns].dropna().itertuples(index=False, name=None))
                orphan_count = sum(
                    tuple(row) not in target_values
                    for row in source_rows.itertuples(index=False, name=None)
                )
                if orphan_count:
                    errors.append(f"外键存在 {orphan_count} 行孤立值")

        return {
            "foreign_key": foreign_key.model_dump(mode="json"),
            "orphan_count": orphan_count,
            "errors": errors,
        }

    def _config_map(
        self,
        manifest: DatasetManifest,
        configs: list[TableRelationshipConfig] | None,
        require_all: bool = False,
    ) -> dict[str, TableRelationshipConfig]:
        result = {
            table.table_name: TableRelationshipConfig(
                table_name=table.table_name,
                primary_key=table.primary_key,
                foreign_keys=table.foreign_keys,
                indexes=table.indexes,
            )
            for table in manifest.tables
        }
        if configs is None:
            return result

        supplied_names = [config.table_name for config in configs]
        if len(supplied_names) != len(set(supplied_names)):
            raise ValueError("关系配置中包含重复的数据表")
        unknown = set(supplied_names) - set(result)
        if unknown:
            raise ValueError(f"关系配置包含未知数据表：{', '.join(sorted(unknown))}")
        if require_all and set(supplied_names) != set(result):
            missing = set(result) - set(supplied_names)
            raise ValueError(f"关系配置缺少数据表：{', '.join(sorted(missing))}")
        result.update({config.table_name: config for config in configs})
        return result

    def _load_manifest(self, dataset_id: str) -> DatasetManifest:
        database_path = self.datasets.get_database_path(dataset_id)
        manifest = self.datasets.manifests.load(database_path.parent / "manifest.json")
        if manifest is None:
            raise RuntimeError(f"数据集 Manifest 不存在：{dataset_id}")
        return manifest

    def _load_frames(self, dataset_id: str) -> dict[str, pd.DataFrame]:
        return {
            table.table_name: self.datasets.get_table_dataframe(dataset_id, table.table_name)
            for table in self.datasets.iter_tables(dataset_id)
        }

    def _get_llm_advice(
        self,
        dataset_id: str,
        manifest: DatasetManifest,
        suggestions: dict[str, Any],
        refresh: bool,
        correction_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._llm_candidate_context(manifest, suggestions)
        context["prompt_version"] = 2
        if correction_context:
            context["correction"] = correction_context
        serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
        signature = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        cache_path = (
            self.datasets.get_database_path(dataset_id).parent
            / "relationship_advice.json"
        )

        if not refresh and correction_context is None and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("candidate_signature") == signature:
                    return cached["advice"]
            except (json.JSONDecodeError, KeyError, OSError):
                pass

        prompt = (
            "请根据数据表字段统计和候选关系，给出关系数据库设计建议。\n"
            "你只能选择输入中提供的主键候选、外键 candidate_id 和索引名称，"
            "不能编造字段、表或候选。没有合适选项时使用空数组。\n"
            "主键应代表稳定业务实体；外键应有明确语义和高匹配率；"
            "正式外键的 orphan_count 必须为 0，而且被引用字段必须与最终推荐的目标表主键完全一致；"
            "不要因为字段同名就同时推荐正向和反向关系；"
            "索引只推荐常用 JOIN、筛选、分组或排序字段，避免为小表或低价值字段滥建索引。\n"
            "严格返回一个 JSON 对象，不要使用 Markdown，格式为：\n"
            '{"summary":"总体建议","table_recommendations":['
            '{"table_name":"表名","primary_key":["字段"],'
            '"primary_key_reason":"理由","indexes":["索引名"],'
            '"index_reason":"理由"}],"foreign_key_recommendations":['
            '{"candidate_id":"候选ID","reason":"理由"}],"warnings":["风险"]}\n\n'
            + (
                "上一次配置未通过完整性验证。请针对 correction.validation_errors 修正，"
                "不要重复产生相同错误。\n"
                if correction_context
                else ""
            )
            + f"候选信息：{serialized}"
        )

        try:
            model = build_chat_model()
            response = model.invoke(
                [
                    SystemMessage(
                        content=(
                            "你是关系数据库建模顾问。你的建议必须保守、可解释，"
                            "并严格受候选集合约束。"
                        )
                    ),
                    HumanMessage(content=prompt),
                ]
            )
            parsed = self._parse_llm_json(self._message_text(response.content))
            advice_model = LLMRelationshipAdvice.model_validate(parsed)
            advice = self._sanitize_llm_advice(advice_model, context)
            advice["status"] = "success"
            advice["source"] = "llm"
            if correction_context is None:
                self._write_json_atomic(
                    cache_path,
                    {"candidate_signature": signature, "advice": advice},
                )
            return advice
        except Exception as exc:
            return {
                "status": "unavailable",
                "source": "deterministic_candidates",
                "summary": "LLM 关系建议暂时不可用，请根据统计候选手动确认或重试。",
                "table_recommendations": [],
                "foreign_key_recommendations": [],
                "warnings": [str(exc)[:300]],
            }

    def _llm_candidate_context(
        self,
        manifest: DatasetManifest,
        suggestions: dict[str, Any],
    ) -> dict[str, Any]:
        table_manifest = {table.table_name: table for table in manifest.tables}
        tables = []
        for table in suggestions["tables"]:
            manifest_table = table_manifest[table["table_name"]]
            tables.append(
                {
                    "table_name": table["table_name"],
                    "row_count": manifest_table.processed_row_count,
                    "columns": [
                        {
                            "name": column.name,
                            "dtype": column.current_dtype,
                            "nullable": column.nullable,
                            "unique_count": column.unique_count,
                        }
                        for column in manifest_table.columns
                    ],
                    "primary_key_candidates": [
                        {
                            "columns": item["columns"],
                            "score": item["score"],
                            "reasons": item["reasons"],
                        }
                        for item in table["primary_key_candidates"]
                    ],
                    "index_candidates": [
                        {
                            "name": item["name"],
                            "columns": item["columns"],
                            "score": item["score"],
                            "reasons": item["reasons"],
                        }
                        for item in table["index_candidates"]
                    ],
                }
            )
        return {
            "dataset_name": manifest.name,
            "tables": tables,
            "foreign_key_candidates": [
                {
                    "candidate_id": item["candidate_id"],
                    "table_name": item["table_name"],
                    "columns": item["columns"],
                    "referenced_table": item["referenced_table"],
                    "referenced_columns": item["referenced_columns"],
                    "match_ratio": item["match_ratio"],
                    "orphan_count": item["orphan_count"],
                    "reasons": item["reasons"],
                }
                for item in suggestions["foreign_key_candidates"]
            ],
        }

    def _sanitize_llm_advice(
        self,
        advice: LLMRelationshipAdvice,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        tables = {table["table_name"]: table for table in context["tables"]}
        foreign_key_ids = {
            item["candidate_id"] for item in context["foreign_key_candidates"]
        }
        warnings = list(advice.warnings)
        table_recommendations = []

        for recommendation in advice.table_recommendations:
            table = tables.get(recommendation.table_name)
            if table is None:
                warnings.append(f"LLM 返回了未知表：{recommendation.table_name}")
                continue
            allowed_primary_keys = {
                tuple(item["columns"]) for item in table["primary_key_candidates"]
            }
            primary_key = list(recommendation.primary_key)
            if primary_key and tuple(primary_key) not in allowed_primary_keys:
                warnings.append(
                    f"已忽略表 {recommendation.table_name} 的非候选主键建议"
                )
                primary_key = []
            allowed_indexes = {
                item["name"] for item in table["index_candidates"]
            }
            indexes = [
                name for name in recommendation.indexes if name in allowed_indexes
            ]
            table_recommendations.append(
                {
                    "table_name": recommendation.table_name,
                    "primary_key": primary_key,
                    "primary_key_reason": recommendation.primary_key_reason,
                    "indexes": indexes,
                    "index_reason": recommendation.index_reason,
                }
            )

        foreign_key_recommendations = [
            recommendation.model_dump(mode="json")
            for recommendation in advice.foreign_key_recommendations
            if recommendation.candidate_id in foreign_key_ids
        ]
        return {
            "summary": advice.summary,
            "table_recommendations": table_recommendations,
            "foreign_key_recommendations": foreign_key_recommendations,
            "warnings": warnings,
        }

    def _mark_llm_recommendations(
        self,
        suggestions: dict[str, Any],
        advice: dict[str, Any],
    ) -> None:
        table_advice = {
            item["table_name"]: item
            for item in advice.get("table_recommendations", [])
        }
        for table in suggestions["tables"]:
            recommendation = table_advice.get(table["table_name"], {})
            recommended_primary = recommendation.get("primary_key", [])
            recommended_indexes = set(recommendation.get("indexes", []))
            for candidate in table["primary_key_candidates"]:
                candidate["llm_recommended"] = (
                    candidate["columns"] == recommended_primary
                )
            for candidate in table["index_candidates"]:
                candidate["llm_recommended"] = candidate["name"] in recommended_indexes

        recommended_foreign_keys = {
            item["candidate_id"]
            for item in advice.get("foreign_key_recommendations", [])
        }
        for candidate in suggestions["foreign_key_candidates"]:
            candidate["llm_recommended"] = (
                candidate["candidate_id"] in recommended_foreign_keys
            )

    def _apply_advice_safety(
        self,
        dataset_id: str,
        suggestions: dict[str, Any],
        advice: dict[str, Any],
    ) -> list[dict[str, Any]]:
        table_advice = {
            item["table_name"]: item
            for item in advice.get("table_recommendations", [])
        }
        primary_keys = {
            table["table_name"]: list(
                table_advice.get(table["table_name"], {}).get("primary_key", [])
            )
            for table in suggestions["tables"]
        }
        indexes_by_table = {
            table["table_name"]: {
                item["name"]: item for item in table["index_candidates"]
            }
            for table in suggestions["tables"]
        }
        foreign_candidates = {
            item["candidate_id"]: item
            for item in suggestions["foreign_key_candidates"]
        }
        recommended_foreign_keys = {
            item["candidate_id"]: item
            for item in advice.get("foreign_key_recommendations", [])
        }
        accepted_foreign_key_ids: set[str] = set()
        foreign_keys_by_table: dict[str, list[dict[str, Any]]] = {
            table["table_name"]: [] for table in suggestions["tables"]
        }
        warnings = list(advice.get("warnings", []))

        for candidate_id, recommendation in recommended_foreign_keys.items():
            candidate = foreign_candidates.get(candidate_id)
            if candidate is None:
                continue
            if candidate["orphan_count"] > 0:
                warnings.append(
                    f"已自动取消外键 {candidate_id}：存在 "
                    f"{candidate['orphan_count']} 行孤立值。"
                )
                continue
            target_primary_key = primary_keys.get(candidate["referenced_table"], [])
            if candidate["referenced_columns"] != target_primary_key:
                warnings.append(
                    f"已自动取消外键 {candidate_id}：被引用字段不是目标表推荐主键。"
                )
                continue
            accepted_foreign_key_ids.add(candidate_id)
            foreign_keys_by_table[candidate["table_name"]].append(
                {
                    "name": candidate["name"],
                    "columns": candidate["columns"],
                    "referenced_table": candidate["referenced_table"],
                    "referenced_columns": candidate["referenced_columns"],
                }
            )

        advice["foreign_key_recommendations"] = [
            recommendation
            for recommendation in advice.get("foreign_key_recommendations", [])
            if recommendation["candidate_id"] in accepted_foreign_key_ids
        ]
        advice["warnings"] = list(dict.fromkeys(warnings))

        configuration = []
        for table in suggestions["tables"]:
            table_name = table["table_name"]
            recommendation = table_advice.get(table_name, {})
            selected_indexes = []
            for index_name in recommendation.get("indexes", []):
                candidate = indexes_by_table[table_name].get(index_name)
                if candidate:
                    selected_indexes.append(
                        {
                            "name": candidate["name"],
                            "columns": candidate["columns"],
                            "unique": candidate["unique"],
                        }
                    )
            configuration.append(
                {
                    "table_name": table_name,
                    "primary_key": primary_keys[table_name],
                    "foreign_keys": foreign_keys_by_table[table_name],
                    "indexes": selected_indexes,
                }
            )

        validated_configs = [
            TableRelationshipConfig.model_validate(item) for item in configuration
        ]
        validation = self.validate(dataset_id, validated_configs)
        if not validation["valid"]:
            advice["warnings"].append(
                "修正建议仍存在约束冲突，系统已取消全部外键，仅保留可验证的主键和索引。"
            )
            for item in configuration:
                item["foreign_keys"] = []
        return configuration

    def _parse_llm_json(self, content: str) -> dict[str, Any]:
        clean = content.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"\s*```$", "", clean)
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end < start:
            raise ValueError("LLM 未返回有效 JSON")
        return json.loads(clean[start : end + 1])

    def _message_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _replace_artifacts(self, dataset_path: Path, staged_paths: list[Path]) -> None:
        backup_path = dataset_path / f".relationships-backup-{uuid.uuid4().hex}"
        backup_path.mkdir()
        replaced: list[tuple[Path, Path | None]] = []
        try:
            for staged_path in staged_paths:
                target = dataset_path / staged_path.name
                backup = backup_path / target.name if target.exists() else None
                if backup:
                    os.replace(target, backup)
                replaced.append((target, backup))
                os.replace(staged_path, target)
        except Exception:
            for target, backup in reversed(replaced):
                if target.exists():
                    target.unlink()
                if backup and backup.exists():
                    os.replace(backup, target)
            raise
        finally:
            if backup_path.exists():
                shutil.rmtree(backup_path)

    def _include_current_primary_key(
        self,
        candidates: list[dict[str, Any]],
        primary_key: list[str],
    ) -> None:
        if not primary_key:
            return
        for candidate in candidates:
            if candidate["columns"] == primary_key:
                candidate["current"] = True
                return
        candidates.insert(
            0,
            {
                "columns": primary_key,
                "score": 1.0,
                "reasons": ["当前已保存的主键"],
                "current": True,
            },
        )

    def _config_dict(self, table: Any) -> dict[str, Any]:
        return {
            "table_name": table.table_name,
            "primary_key": list(table.primary_key),
            "foreign_keys": [item.model_dump(mode="json") for item in table.foreign_keys],
            "indexes": [item.model_dump(mode="json") for item in table.indexes],
        }

    def _compatible_dtypes(self, left: Any, right: Any) -> bool:
        if pandas_types.is_numeric_dtype(left) and pandas_types.is_numeric_dtype(right):
            return True
        if pandas_types.is_datetime64_any_dtype(left) and pandas_types.is_datetime64_any_dtype(right):
            return True
        return str(left) == str(right) or (
            pandas_types.is_string_dtype(left) and pandas_types.is_string_dtype(right)
        )

    def _is_id_column(self, name: str) -> bool:
        lowered = name.lower()
        return lowered == "id" or lowered.endswith("_id") or lowered.endswith("id")

    def _names_can_reference(
        self,
        source: str,
        target: str,
        target_table: str,
    ) -> bool:
        source_normalized = re.sub(r"[^a-z0-9]", "", source.lower())
        target_normalized = re.sub(r"[^a-z0-9]", "", target.lower())
        if source_normalized == target_normalized:
            return True
        singular_table = target_table.lower().rstrip("s")
        table_normalized = re.sub(r"[^a-z0-9]", "", singular_table)
        return (
            target_normalized == "id"
            and source_normalized == f"{table_normalized}id"
        )

    def _index_name(self, table_name: str, column: str) -> str:
        clean = re.sub(r"\W+", "_", f"idx_{table_name}_{column}", flags=re.UNICODE)
        return clean.strip("_")[:120]

    def _foreign_key_candidate_id(
        self,
        table_name: str,
        columns: list[str],
        referenced_table: str,
        referenced_columns: list[str],
    ) -> str:
        return (
            f"{table_name}({','.join(columns)})->"
            f"{referenced_table}({','.join(referenced_columns)})"
        )


relationship_service = RelationshipService()
