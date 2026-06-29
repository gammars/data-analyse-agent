import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from app.schemas.manifest import (
    ColumnManifest,
    DataSourceManifest,
    DatasetManifest,
    TableManifest,
)
from app.services.manifest_service import MANIFEST_FILENAME, ManifestService, manifest_service
from app.services.sqlite_ddl_service import SQLiteDDLService, sqlite_ddl_service
from app.services.sqlite_schema_service import SQLiteSchemaService, sqlite_schema_service
from app.services.sqlite_storage_service import SQLiteStorageService, sqlite_storage_service


DATASET_DIR = Path("app/storage/datasets")
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls"}
MAX_UPLOAD_SIZE_BYTES = None
DEFAULT_SINGLE_TABLE_ALIAS = "data_table"
SQLITE_FILENAME = "dataset.sqlite3"
RAW_DIRNAME = "raw"
PROCESSED_DIRNAME = "processed"
SCHEMA_FILENAME = "schema.sql"
INDEXES_FILENAME = "indexes.sql"


def get_upload_size_limit_bytes() -> int | None:
    load_dotenv()
    limit_mb = os.getenv("DATA_ANALYSE_MAX_UPLOAD_MB", "").strip()
    if not limit_mb:
        return MAX_UPLOAD_SIZE_BYTES

    try:
        parsed_limit = float(limit_mb)
    except ValueError:
        return MAX_UPLOAD_SIZE_BYTES

    if parsed_limit <= 0:
        return None
    return int(parsed_limit * 1024 * 1024)


def quote_sql_identifier(identifier: object) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def sanitize_table_name(name: str) -> str:
    value = re.sub(r"\W+", "_", name.strip(), flags=re.UNICODE).strip("_")
    if not value:
        return DEFAULT_SINGLE_TABLE_ALIAS
    if value[0].isdigit():
        value = f"t_{value}"
    return value[:64]


def unique_table_name(base_name: str, existing_names: set[str]) -> str:
    candidate = sanitize_table_name(base_name)
    if candidate not in existing_names:
        return candidate

    index = 2
    while f"{candidate}_{index}" in existing_names:
        index += 1
    return f"{candidate}_{index}"


@dataclass
class TableRecord:
    table_name: str
    filename: str
    path: Path
    suffix: str
    created_at: str
    sheet_name: str | None = None
    processed_path: Path | None = None
    processed_dtypes: dict[str, str] = field(default_factory=dict)
    dataframe: pd.DataFrame | None = None


@dataclass
class DatasetRecord:
    dataset_id: str
    filename: str
    schema: str
    created_at: str
    tables: dict[str, TableRecord]
    database_path: Path
    manifest_path: Path


class DatasetService:
    """Save uploaded datasets and expose single-table or multi-table metadata."""

    def __init__(
        self,
        dataset_dir: Path = DATASET_DIR,
        sqlite_storage: SQLiteStorageService = sqlite_storage_service,
        manifests: ManifestService = manifest_service,
        sqlite_ddl: SQLiteDDLService = sqlite_ddl_service,
        sqlite_schema: SQLiteSchemaService = sqlite_schema_service,
    ) -> None:
        self.dataset_dir = dataset_dir
        self.metadata_path = dataset_dir / "metadata.json"
        self.sqlite_storage = sqlite_storage
        self.manifests = manifests
        self.sqlite_ddl = sqlite_ddl
        self.sqlite_schema = sqlite_schema
        self.datasets: dict[str, DatasetRecord] = {}
        self._load_metadata()
        self._index_existing_files()
        self._migrate_dataset_structures()

    def save_dataset(self, filename: str, content: bytes) -> DatasetRecord:
        return self.save_dataset_files([(filename, content)])

    def save_dataset_files(self, files: list[tuple[str, bytes]]) -> DatasetRecord:
        if not files:
            raise ValueError("请至少上传一个数据文件")

        for filename, content in files:
            suffix = Path(filename).suffix.lower()
            self._validate_upload(filename=filename, suffix=suffix, content=content)

        dataset_id = str(uuid.uuid4())
        dataset_path = self.dataset_dir / dataset_id
        dataset_path.mkdir(parents=True, exist_ok=True)
        self._ensure_layer_directories(dataset_path)
        dataset_name = self._build_dataset_name([filename for filename, _ in files])

        record = DatasetRecord(
            dataset_id=dataset_id,
            filename=dataset_name,
            schema="",
            created_at=self._now(),
            tables={},
            database_path=dataset_path / SQLITE_FILENAME,
            manifest_path=dataset_path / MANIFEST_FILENAME,
        )
        try:
            existing_names: set[str] = set()
            for filename, content in files:
                suffix = Path(filename).suffix.lower()
                tables = self._materialize_uploaded_tables(
                    dataset_path=dataset_path,
                    filename=filename,
                    content=content,
                    suffix=suffix,
                    existing_names=existing_names,
                )
                record.tables.update(tables)
                existing_names.update(tables)

            self._rebuild_sqlite_database(record)
            record.schema = self._build_schema(record)
            self.datasets[dataset_id] = record
            self._write_manifest(record, relationship_status="pending")
            self._save_metadata()
            return record
        except Exception:
            self.datasets.pop(dataset_id, None)
            if dataset_path.exists():
                shutil.rmtree(dataset_path)
            raise

    def append_table(self, dataset_id: str, filename: str, content: bytes) -> DatasetRecord:
        return self.append_tables(dataset_id, [(filename, content)])

    def append_tables(self, dataset_id: str, files: list[tuple[str, bytes]]) -> DatasetRecord:
        record = self._get_record(dataset_id)
        if not files:
            raise ValueError("请至少上传一个数据文件")

        for filename, content in files:
            suffix = Path(filename).suffix.lower()
            self._validate_upload(filename=filename, suffix=suffix, content=content)

        dataset_path = self.dataset_dir / dataset_id
        dataset_path.mkdir(parents=True, exist_ok=True)
        self._ensure_layer_directories(dataset_path)
        existing_names = set(record.tables)
        new_tables: dict[str, TableRecord] = {}
        try:
            for filename, content in files:
                suffix = Path(filename).suffix.lower()
                tables = self._materialize_uploaded_tables(
                    dataset_path=dataset_path,
                    filename=filename,
                    content=content,
                    suffix=suffix,
                    existing_names=existing_names,
                )
                new_tables.update(tables)
                existing_names.update(tables)

            candidate_tables = {**record.tables, **new_tables}
            self._rebuild_sqlite_database(record, candidate_tables)
        except Exception:
            self._delete_table_source_files(new_tables, record.tables)
            raise

        record.tables.update(new_tables)
        record.schema = self._build_schema(record)
        self._write_manifest(record, relationship_status="pending")
        self._save_metadata()
        return record

    def rename_dataset(self, dataset_id: str, name: str) -> DatasetRecord:
        record = self._get_record(dataset_id)
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("数据集名称不能为空")
        record.filename = clean_name
        record.schema = self._build_schema(record)
        self._write_manifest(record)
        self._save_metadata()
        return record

    def delete_dataset(self, dataset_id: str) -> None:
        record = self._get_record(dataset_id)
        dataset_path = self.dataset_dir / dataset_id

        if dataset_path.exists() and dataset_path.is_dir():
            shutil.rmtree(dataset_path)
        else:
            for table in record.tables.values():
                if table.path.exists() and self._is_inside_dataset_dir(table.path):
                    table.path.unlink()
                if (
                    table.processed_path
                    and table.processed_path.exists()
                    and self._is_inside_dataset_dir(table.processed_path)
                ):
                    table.processed_path.unlink()

        del self.datasets[dataset_id]
        self._save_metadata()

    def delete_table(self, dataset_id: str, table_name: str) -> DatasetRecord:
        record = self._get_record(dataset_id)
        if table_name not in record.tables:
            raise KeyError(f"数据表不存在：{table_name}")
        if len(record.tables) <= 1:
            raise ValueError("数据集至少需要保留一张表；如需删除全部数据，请删除整个数据集")

        table = record.tables[table_name]
        remaining_tables = {
            name: item for name, item in record.tables.items() if name != table_name
        }
        self._rebuild_sqlite_database(record, remaining_tables)
        record.tables = remaining_tables
        if not any(other.path == table.path for other in remaining_tables.values()):
            if table.path.exists() and self._is_inside_dataset_dir(table.path):
                table.path.unlink()
        if (
            table.processed_path
            and table.processed_path.exists()
            and self._is_inside_dataset_dir(table.processed_path)
        ):
            table.processed_path.unlink()

        record.schema = self._build_schema(record)
        self._write_manifest(record, relationship_status="pending")
        self._save_metadata()
        return record

    def list_datasets(self) -> list[dict[str, Any]]:
        return [
            self.get_summary(dataset_id)
            for dataset_id in sorted(
                self.datasets,
                key=lambda key: self.datasets[key].created_at,
                reverse=True,
            )
        ]

    def get_dataframe(self, dataset_id: str) -> pd.DataFrame:
        table = self._get_first_table(dataset_id)
        return self.get_table_dataframe(dataset_id, table.table_name)

    def get_table_dataframe(self, dataset_id: str, table_name: str) -> pd.DataFrame:
        record = self._get_record(dataset_id)
        try:
            table = record.tables[table_name]
        except KeyError as exc:
            raise KeyError(f"数据表不存在：{table_name}") from exc

        if table.dataframe is None:
            table.dataframe = self._read_table_dataframe(table)
        return table.dataframe

    def get_raw_table_dataframe(self, dataset_id: str, table_name: str) -> pd.DataFrame:
        record = self._get_record(dataset_id)
        try:
            table = record.tables[table_name]
        except KeyError as exc:
            raise KeyError(f"数据表不存在：{table_name}") from exc
        return self._read_dataframe(table.path, table.suffix, table.sheet_name)

    def replace_processed_tables(
        self,
        dataset_id: str,
        updates: dict[str, pd.DataFrame],
    ) -> DatasetRecord:
        if not updates:
            raise ValueError("请至少提供一张需要更新的处理后数据表")

        record = self._get_record(dataset_id)
        self._ensure_sqlite_database(record)
        unknown_tables = set(updates) - set(record.tables)
        if unknown_tables:
            raise KeyError(f"数据表不存在：{', '.join(sorted(unknown_tables))}")
        if any(not isinstance(dataframe, pd.DataFrame) for dataframe in updates.values()):
            raise TypeError("处理后数据必须是 Pandas DataFrame")

        dataset_path = record.manifest_path.parent
        staging_path = dataset_path / f".cleaning-{uuid.uuid4().hex}"
        backup_path = staging_path / "backup"
        staging_path.mkdir(parents=True, exist_ok=False)
        backup_path.mkdir()

        staged_processed: dict[str, Path] = {}
        candidate_dataframes: list[tuple[str, pd.DataFrame]] = []
        staged_database = staging_path / SQLITE_FILENAME
        try:
            for table_name, table in record.tables.items():
                dataframe = updates.get(table_name)
                if dataframe is None:
                    dataframe = self.get_table_dataframe(dataset_id, table_name)
                candidate_dataframes.append((table_name, dataframe))
                if table_name in updates:
                    staged_path = staging_path / f"{table_name}.csv"
                    self._write_processed_dataframe(staged_path, dataframe)
                    staged_processed[table_name] = staged_path

            ddl = self._generate_sqlite_ddl(record, candidate_dataframes)
            self.sqlite_storage.rebuild_with_schema(
                staged_database,
                candidate_dataframes,
                ddl.schema_sql,
                ddl.indexes_sql,
            )
            (staging_path / SCHEMA_FILENAME).write_text(
                ddl.schema_sql,
                encoding="utf-8",
            )
            (staging_path / INDEXES_FILENAME).write_text(
                ddl.indexes_sql,
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(staging_path)
            raise

        previous_schema = record.schema
        previous_dataframes = {
            table_name: record.tables[table_name].dataframe for table_name in updates
        }
        previous_dtypes = {
            table_name: dict(record.tables[table_name].processed_dtypes)
            for table_name in updates
        }
        previous_manifest = (
            record.manifest_path.read_bytes() if record.manifest_path.exists() else None
        )
        processed_backups: dict[Path, Path | None] = {}
        artifact_backups: dict[Path, Path | None] = {}

        try:
            for table_name, staged_path in staged_processed.items():
                target_path = record.tables[table_name].processed_path
                if target_path is None:
                    raise RuntimeError(f"数据表缺少 processed 路径：{table_name}")
                backup = backup_path / target_path.name if target_path.exists() else None
                if backup:
                    os.replace(target_path, backup)
                processed_backups[target_path] = backup
                os.replace(staged_path, target_path)

            for staged_path, target_path in (
                (staged_database, record.database_path),
                (
                    staging_path / SCHEMA_FILENAME,
                    dataset_path / SCHEMA_FILENAME,
                ),
                (
                    staging_path / INDEXES_FILENAME,
                    dataset_path / INDEXES_FILENAME,
                ),
            ):
                backup = backup_path / target_path.name if target_path.exists() else None
                if backup:
                    os.replace(target_path, backup)
                artifact_backups[target_path] = backup
                os.replace(staged_path, target_path)

            for table_name, dataframe in updates.items():
                record.tables[table_name].dataframe = dataframe
                record.tables[table_name].processed_dtypes = {
                    str(column): str(dataframe[column].dtype)
                    for column in dataframe.columns
                }
            record.schema = self._build_schema(record)
            self._write_manifest(record)
            self._save_metadata()
            return record
        except Exception:
            for target_path, backup in artifact_backups.items():
                if target_path.exists():
                    target_path.unlink()
                if backup and backup.exists():
                    os.replace(backup, target_path)

            for target_path, backup in processed_backups.items():
                if target_path.exists():
                    target_path.unlink()
                if backup and backup.exists():
                    os.replace(backup, target_path)

            record.schema = previous_schema
            for table_name, dataframe in previous_dataframes.items():
                record.tables[table_name].dataframe = dataframe
                record.tables[table_name].processed_dtypes = previous_dtypes[table_name]
            if previous_manifest is not None:
                record.manifest_path.write_bytes(previous_manifest)
            raise
        finally:
            if staging_path.exists():
                shutil.rmtree(staging_path)

    def iter_tables(self, dataset_id: str) -> list[TableRecord]:
        return list(self._get_record(dataset_id).tables.values())

    def get_schema(self, dataset_id: str) -> str:
        record = self._get_record(dataset_id)
        self._ensure_sqlite_database(record)
        if not record.schema:
            record.schema = self._build_schema(record)
        return record.schema

    def get_database_path(self, dataset_id: str) -> Path:
        record = self._get_record(dataset_id)
        self._ensure_sqlite_database(record)
        return record.database_path

    def rebuild_database(self, dataset_id: str) -> Path:
        record = self._get_record(dataset_id)
        self._rebuild_sqlite_database(record)
        record.schema = self._build_schema(record)
        self._save_metadata()
        return record.database_path

    def refresh_schema(self, dataset_id: str) -> str:
        record = self._get_record(dataset_id)
        record.schema = self._build_schema(record)
        self._save_metadata()
        return record.schema

    def get_manifest(self, dataset_id: str) -> dict[str, Any]:
        record = self._get_record(dataset_id)
        self._ensure_dataset_structure(record)
        manifest = self.manifests.load(record.manifest_path)
        if manifest is None:
            raise RuntimeError(f"数据集 Manifest 不存在：{dataset_id}")
        return manifest.model_dump(mode="json")

    def get_relationship_status(self, dataset_id: str) -> str:
        record = self._get_record(dataset_id)
        self._ensure_dataset_structure(record)
        manifest = self.manifests.load(record.manifest_path)
        if manifest is None:
            raise RuntimeError(f"数据集 Manifest 不存在：{dataset_id}")
        return manifest.relationship_status

    def require_relationship_configuration(self, dataset_id: str) -> None:
        if self.get_relationship_status(dataset_id) != "confirmed":
            raise ValueError("当前数据集尚未完成关系配置，请先确认主键、外键和索引设置")

    def get_summary(self, dataset_id: str) -> dict[str, Any]:
        record = self._get_record(dataset_id)
        self._ensure_sqlite_database(record)
        first_table = self._get_first_table(dataset_id)
        first_dataframe = self.get_table_dataframe(dataset_id, first_table.table_name)
        table_summaries = [self._table_summary(record.dataset_id, table) for table in record.tables.values()]

        return {
            "dataset_id": record.dataset_id,
            "filename": record.filename,
            "created_at": record.created_at,
            "table_count": len(record.tables),
            "database": {
                "engine": "sqlite",
                "filename": record.database_path.name,
                "path": str(record.database_path),
                "ready": record.database_path.exists(),
            },
            "storage": {
                "raw_dir": str(record.manifest_path.parent / RAW_DIRNAME),
                "processed_dir": str(record.manifest_path.parent / PROCESSED_DIRNAME),
                "manifest_path": str(record.manifest_path),
                "manifest_ready": record.manifest_path.exists(),
            },
            "relationship_configuration": self._relationship_summary(record),
            "row_count": int(len(first_dataframe)),
            "column_count": int(len(first_dataframe.columns)),
            "columns": self._column_summaries(first_dataframe),
            "sample_rows": first_dataframe.head(5).where(pd.notna(first_dataframe), None).to_dict("records"),
            "tables": table_summaries,
            "schema": self.get_schema(dataset_id),
        }

    def _get_record(self, dataset_id: str) -> DatasetRecord:
        try:
            return self.datasets[dataset_id]
        except KeyError as exc:
            raise KeyError(f"数据集不存在：{dataset_id}") from exc

    def _get_first_table(self, dataset_id: str) -> TableRecord:
        record = self._get_record(dataset_id)
        try:
            return next(iter(record.tables.values()))
        except StopIteration as exc:
            raise ValueError(f"数据集没有可用的数据表：{dataset_id}") from exc

    def _build_dataset_name(self, filenames: list[str]) -> str:
        names = [Path(filename).name for filename in filenames if filename]
        if len(names) == 1:
            return names[0]
        first_stem = Path(names[0]).stem if names else "多文件数据集"
        return f"{first_stem} 等 {len(names)} 个文件"

    def _materialize_uploaded_tables(
        self,
        dataset_path: Path,
        filename: str,
        content: bytes,
        suffix: str,
        existing_names: set[str],
    ) -> dict[str, TableRecord]:
        now = self._now()
        source_stem = sanitize_table_name(Path(filename).stem)
        raw_dir = dataset_path / RAW_DIRNAME
        processed_dir = dataset_path / PROCESSED_DIRNAME

        if suffix == ".csv":
            table_name = unique_table_name(source_stem, existing_names)
            save_path = raw_dir / f"{table_name}{suffix}"
            processed_path = processed_dir / f"{table_name}.csv"
            save_path.write_bytes(content)
            dataframe = self._read_dataframe(save_path, suffix)
            self._write_processed_dataframe(processed_path, dataframe)
            return {
                table_name: TableRecord(
                    table_name=table_name,
                    filename=Path(filename).name,
                    path=save_path,
                    suffix=suffix,
                    created_at=now,
                    processed_path=processed_path,
                    dataframe=dataframe,
                )
            }

        workbook_name = unique_table_name(source_stem, {path.stem for path in raw_dir.glob("*")})
        save_path = raw_dir / f"{workbook_name}{suffix}"
        save_path.write_bytes(content)
        sheets = pd.read_excel(save_path, sheet_name=None)

        tables: dict[str, TableRecord] = {}
        names = set(existing_names)
        for sheet_name, dataframe in sheets.items():
            table_name = unique_table_name(str(sheet_name), names)
            names.add(table_name)
            processed_path = processed_dir / f"{table_name}.csv"
            self._write_processed_dataframe(processed_path, dataframe)
            tables[table_name] = TableRecord(
                table_name=table_name,
                filename=Path(filename).name,
                path=save_path,
                suffix=suffix,
                sheet_name=str(sheet_name),
                created_at=now,
                processed_path=processed_path,
                dataframe=dataframe,
            )
        return tables

    def _load_metadata(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            return

        try:
            records = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return

        for item in records:
            dataset_id = item.get("dataset_id")
            if not dataset_id:
                continue

            if item.get("tables"):
                record = self._load_multitable_record(item)
            else:
                record = self._load_legacy_record(item)

            if record:
                self.datasets[dataset_id] = record

    def _load_multitable_record(self, item: dict[str, Any]) -> DatasetRecord | None:
        tables: dict[str, TableRecord] = {}
        for table_item in item.get("tables", []):
            table_name = table_item.get("table_name")
            path = Path(table_item.get("path", ""))
            suffix = table_item.get("suffix") or path.suffix.lower()
            if not table_name or not path.exists() or suffix not in ALLOWED_SUFFIXES:
                continue
            tables[table_name] = TableRecord(
                table_name=table_name,
                filename=table_item.get("filename", path.name),
                path=path,
                suffix=suffix,
                sheet_name=table_item.get("sheet_name"),
                created_at=table_item.get("created_at", item.get("created_at", self._now())),
                processed_path=(
                    Path(table_item["processed_path"])
                    if table_item.get("processed_path")
                    else None
                ),
                processed_dtypes=dict(table_item.get("processed_dtypes") or {}),
            )

        if not tables:
            return None

        return DatasetRecord(
            dataset_id=item["dataset_id"],
            filename=item.get("filename", item["dataset_id"]),
            schema=item.get("schema", ""),
            created_at=item.get("created_at", self._now()),
            tables=tables,
            database_path=Path(
                item.get("database_path")
                or self.dataset_dir / item["dataset_id"] / SQLITE_FILENAME
            ),
            manifest_path=Path(
                item.get("manifest_path")
                or self.dataset_dir / item["dataset_id"] / MANIFEST_FILENAME
            ),
        )

    def _load_legacy_record(self, item: dict[str, Any]) -> DatasetRecord | None:
        path = Path(item.get("path", ""))
        suffix = item.get("suffix") or path.suffix.lower()
        if not item.get("dataset_id") or not path.exists() or suffix not in ALLOWED_SUFFIXES:
            return None

        table_name = sanitize_table_name(path.stem)
        table = TableRecord(
            table_name=table_name,
            filename=item.get("filename", path.name),
            path=path,
            suffix=suffix,
            created_at=item.get("created_at", self._now()),
            processed_path=(
                Path(item["processed_path"])
                if item.get("processed_path")
                else None
            ),
            processed_dtypes=dict(item.get("processed_dtypes") or {}),
        )
        return DatasetRecord(
            dataset_id=item["dataset_id"],
            filename=item.get("filename", path.name),
            schema="",
            created_at=item.get("created_at", self._now()),
            tables={table_name: table},
            database_path=Path(
                item.get("database_path")
                or self.dataset_dir / item["dataset_id"] / SQLITE_FILENAME
            ),
            manifest_path=Path(
                item.get("manifest_path")
                or self.dataset_dir / item["dataset_id"] / MANIFEST_FILENAME
            ),
        )

    def _save_metadata(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "dataset_id": record.dataset_id,
                "filename": record.filename,
                "schema": record.schema,
                "created_at": record.created_at,
                "database_path": str(record.database_path),
                "manifest_path": str(record.manifest_path),
                "tables": [
                    {
                        "table_name": table.table_name,
                        "filename": table.filename,
                        "path": str(table.path),
                        "suffix": table.suffix,
                        "sheet_name": table.sheet_name,
                        "processed_path": (
                            str(table.processed_path) if table.processed_path else None
                        ),
                        "processed_dtypes": table.processed_dtypes,
                        "created_at": table.created_at,
                    }
                    for table in record.tables.values()
                ],
            }
            for record in self.datasets.values()
        ]
        self.metadata_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _index_existing_files(self) -> None:
        known_paths = {
            table.path.resolve()
            for record in self.datasets.values()
            for table in record.tables.values()
        }
        changed = False

        for path in self.dataset_dir.iterdir():
            suffix = path.suffix.lower()
            if not path.is_file() or suffix not in ALLOWED_SUFFIXES:
                continue
            if path.resolve() in known_paths:
                continue

            dataset_id = path.stem
            table_name = sanitize_table_name(path.stem)
            table = TableRecord(
                table_name=table_name,
                filename=path.name,
                path=path,
                suffix=suffix,
                created_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            )
            record = DatasetRecord(
                dataset_id=dataset_id,
                filename=path.name,
                schema="",
                created_at=table.created_at,
                tables={table_name: table},
                database_path=self.dataset_dir / dataset_id / SQLITE_FILENAME,
                manifest_path=self.dataset_dir / dataset_id / MANIFEST_FILENAME,
            )
            try:
                table.dataframe = self._read_table_dataframe(table)
                record.schema = self._build_schema(record)
            except Exception:
                continue

            self.datasets[dataset_id] = record
            changed = True

        if changed:
            self._save_metadata()

    def _validate_upload(self, filename: str, suffix: str, content: bytes) -> None:
        if not filename:
            raise ValueError("上传文件缺少文件名")
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError("暂不支持该文件类型，仅支持 CSV、XLSX、XLS")
        if not content:
            raise ValueError("上传文件为空")
        upload_limit = get_upload_size_limit_bytes()
        if upload_limit is not None and len(content) > upload_limit:
            limit_mb = upload_limit / 1024 / 1024
            raise ValueError(f"上传文件超过 {limit_mb:g}MB 限制")

    def _read_dataframe(self, save_path: Path, suffix: str, sheet_name: str | None = None) -> pd.DataFrame:
        if suffix == ".csv":
            try:
                return pd.read_csv(save_path, encoding="utf-8-sig")
            except UnicodeDecodeError:
                return pd.read_csv(save_path, encoding="gbk")

        if sheet_name is None:
            return pd.read_excel(save_path)
        return pd.read_excel(save_path, sheet_name=sheet_name)

    def _read_table_dataframe(self, table: TableRecord) -> pd.DataFrame:
        if table.processed_path and table.processed_path.exists():
            dataframe = self._read_dataframe(table.processed_path, ".csv")
            return self._restore_dataframe_dtypes(dataframe, table.processed_dtypes)
        return self._read_dataframe(table.path, table.suffix, table.sheet_name)

    def _write_processed_dataframe(self, path: Path, dataframe: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        dataframe.to_csv(path, index=False, encoding="utf-8-sig")

    def _restore_dataframe_dtypes(
        self,
        dataframe: pd.DataFrame,
        dtypes: dict[str, str],
    ) -> pd.DataFrame:
        if not dtypes:
            return dataframe
        restored = dataframe.copy()
        for column, dtype in dtypes.items():
            if column not in restored.columns:
                continue
            try:
                if dtype.startswith("datetime64"):
                    restored[column] = pd.to_datetime(restored[column], errors="coerce")
                elif dtype in {"boolean", "bool"}:
                    restored[column] = restored[column].astype("boolean")
                elif dtype.startswith(("Int", "UInt")):
                    restored[column] = pd.to_numeric(restored[column], errors="coerce").astype(dtype)
                elif dtype.startswith(("Float", "float")):
                    restored[column] = pd.to_numeric(restored[column], errors="coerce").astype(dtype)
                elif dtype == "string":
                    restored[column] = restored[column].astype("string")
            except (TypeError, ValueError):
                continue
        return restored

    def _build_schema(self, record: DatasetRecord) -> str:
        if record.database_path.exists():
            return self.sqlite_schema.build_schema_text(
                database_path=record.database_path,
                dataset_id=record.dataset_id,
                dataset_name=record.filename,
            )

        lines = [
            f"数据集 ID：{record.dataset_id}",
            f"数据集文件：{record.filename}",
            f"数据表数量：{len(record.tables)}",
            f"SQLite 数据库：{record.database_path.name}",
            "",
            "SQL 说明：",
            "- 多表查询时，请使用下方每张表的 SQL表名。",
            f"- 如果数据集只有 1 张表，也可以使用兼容别名 {DEFAULT_SINGLE_TABLE_ALIAS}。",
        ]

        for table in record.tables.values():
            if table.dataframe is None:
                table.dataframe = self._read_table_dataframe(table)
            dataframe = table.dataframe
            source = table.filename
            if table.sheet_name:
                source = f"{source} / sheet: {table.sheet_name}"
            lines.extend(
                [
                    "",
                    f"## 表：{table.table_name}",
                    f"- SQL表名：{quote_sql_identifier(table.table_name)}",
                    f"- 来源：{source}",
                    f"- 行数：{len(dataframe)}",
                    f"- 列数：{len(dataframe.columns)}",
                    "- 字段信息：",
                ]
            )

            for column in dataframe.columns:
                missing_count = int(dataframe[column].isna().sum())
                lines.append(
                    "  - "
                    f"字段名：{column}; "
                    f"SQL引用：{quote_sql_identifier(column)}; "
                    f"类型：{dataframe[column].dtype}; "
                    f"缺失值：{missing_count} 个"
                )

            lines.append("- 样例数据：")
            lines.append(dataframe.head(5).to_markdown(index=False))

        return "\n".join(lines)

    def _table_summary(self, dataset_id: str, table: TableRecord) -> dict[str, Any]:
        dataframe = self.get_table_dataframe(dataset_id, table.table_name)
        return {
            "table_name": table.table_name,
            "sql_name": quote_sql_identifier(table.table_name),
            "filename": table.filename,
            "sheet_name": table.sheet_name,
            "raw_path": str(table.path),
            "processed_path": (
                str(table.processed_path) if table.processed_path else None
            ),
            "row_count": int(len(dataframe)),
            "column_count": int(len(dataframe.columns)),
            "columns": self._column_summaries(dataframe),
            "sample_rows": dataframe.head(5).where(pd.notna(dataframe), None).to_dict("records"),
        }

    def _column_summaries(self, dataframe: pd.DataFrame) -> list[dict[str, Any]]:
        return [
            {
                "name": str(column),
                "dtype": str(dataframe[column].dtype),
                "missing_count": int(dataframe[column].isna().sum()),
            }
            for column in dataframe.columns
        ]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_sqlite_database(self, record: DatasetRecord) -> None:
        self._ensure_dataset_structure(record)
        if record.database_path.exists():
            return
        self._rebuild_sqlite_database(record)
        record.schema = self._build_schema(record)
        self._save_metadata()

    def _rebuild_sqlite_database(
        self,
        record: DatasetRecord,
        tables: dict[str, TableRecord] | None = None,
    ) -> None:
        selected_tables = tables if tables is not None else record.tables
        dataframes = []
        for table in selected_tables.values():
            if table.dataframe is None:
                table.dataframe = self._read_table_dataframe(table)
            dataframes.append((table.table_name, table.dataframe))
        ddl = self._generate_sqlite_ddl(record, dataframes)
        self.sqlite_storage.rebuild_with_schema(
            record.database_path,
            dataframes,
            ddl.schema_sql,
            ddl.indexes_sql,
        )
        self._write_text_atomic(
            record.database_path.parent / SCHEMA_FILENAME,
            ddl.schema_sql,
        )
        self._write_text_atomic(
            record.database_path.parent / INDEXES_FILENAME,
            ddl.indexes_sql,
        )

    def _generate_sqlite_ddl(
        self,
        record: DatasetRecord,
        dataframes: list[tuple[str, pd.DataFrame]],
    ):
        manifest = self.manifests.load(record.manifest_path)
        configs = (
            {table.table_name: table for table in manifest.tables}
            if manifest
            else {}
        )
        return self.sqlite_ddl.generate(dataframes, configs)

    def _write_text_atomic(self, path: Path, content: str) -> None:
        temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary_path.write_text(content, encoding="utf-8")
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _delete_table_source_files(
        self,
        new_tables: dict[str, TableRecord],
        existing_tables: dict[str, TableRecord],
    ) -> None:
        existing_paths = {table.path.resolve() for table in existing_tables.values()}
        for path in {table.path for table in new_tables.values()}:
            if (
                path.exists()
                and path.resolve() not in existing_paths
                and self._is_inside_dataset_dir(path)
            ):
                path.unlink()

        for path in {
            table.processed_path
            for table in new_tables.values()
            if table.processed_path is not None
        }:
            if path.exists() and self._is_inside_dataset_dir(path):
                path.unlink()

    def _ensure_layer_directories(self, dataset_path: Path) -> None:
        (dataset_path / RAW_DIRNAME).mkdir(parents=True, exist_ok=True)
        (dataset_path / PROCESSED_DIRNAME).mkdir(parents=True, exist_ok=True)

    def _migrate_dataset_structures(self) -> None:
        changed = False
        for record in self.datasets.values():
            changed = self._ensure_dataset_structure(record) or changed
        if changed:
            self._save_metadata()

    def _ensure_dataset_structure(self, record: DatasetRecord) -> bool:
        dataset_path = record.database_path.parent
        raw_dir = dataset_path / RAW_DIRNAME
        processed_dir = dataset_path / PROCESSED_DIRNAME
        self._ensure_layer_directories(dataset_path)
        changed = False
        existing_manifest = self.manifests.load(record.manifest_path)
        manifest_tables = {
            table.table_name: table for table in existing_manifest.tables
        } if existing_manifest else {}

        for table in record.tables.values():
            if not table.processed_dtypes and table.table_name in manifest_tables:
                table.processed_dtypes = {
                    column.name: column.current_dtype
                    for column in manifest_tables[table.table_name].columns
                }
                changed = True
            raw_target = raw_dir / table.path.name
            if table.path.resolve() != raw_target.resolve():
                source_path = table.path
                if not raw_target.exists() and source_path.exists():
                    if self._is_inside_dataset_dir(source_path):
                        source_path.replace(raw_target)
                    else:
                        shutil.copy2(source_path, raw_target)
                if not raw_target.exists():
                    raise FileNotFoundError(f"原始数据文件不存在：{source_path}")
                table.path = raw_target
                changed = True

            processed_target = processed_dir / f"{table.table_name}.csv"
            if (
                table.processed_path
                and table.processed_path.exists()
                and table.processed_path.resolve() != processed_target.resolve()
                and not processed_target.exists()
            ):
                table.processed_path.replace(processed_target)

            if not processed_target.exists():
                dataframe = (
                    table.dataframe
                    if table.dataframe is not None
                    else self._read_dataframe(table.path, table.suffix, table.sheet_name)
                )
                self._write_processed_dataframe(processed_target, dataframe)
                changed = True

            if table.processed_path != processed_target:
                table.processed_path = processed_target
                changed = True

        if not record.manifest_path.exists() or changed:
            self._write_manifest(record)
            changed = True
        schema_path = dataset_path / SCHEMA_FILENAME
        indexes_path = dataset_path / INDEXES_FILENAME
        if not schema_path.exists() or not indexes_path.exists():
            self._rebuild_sqlite_database(record)
            record.schema = self._build_schema(record)
            changed = True
        return changed

    def _write_manifest(
        self,
        record: DatasetRecord,
        relationship_status: str | None = None,
    ) -> None:
        existing = self.manifests.load(record.manifest_path)
        existing_tables = {
            table.table_name: table for table in existing.tables
        } if existing else {}
        dataset_path = record.manifest_path.parent
        table_manifests = []

        for table in record.tables.values():
            current_dataframe = self._read_table_dataframe(table)
            table.processed_dtypes = {
                str(column): str(current_dataframe[column].dtype)
                for column in current_dataframe.columns
            }
            previous = existing_tables.get(table.table_name)
            previous_columns = {
                column.name: column for column in previous.columns
            } if previous else {}

            original_dtypes: dict[str, str] = {}
            if previous:
                original_row_count = previous.original_row_count
            else:
                original_dataframe = self._read_dataframe(
                    table.path,
                    table.suffix,
                    table.sheet_name,
                )
                original_row_count = int(len(original_dataframe))
                original_dtypes = {
                    str(column): str(original_dataframe[column].dtype)
                    for column in original_dataframe.columns
                }

            columns = []
            for column in current_dataframe.columns:
                column_name = str(column)
                previous_column = previous_columns.get(column_name)
                original_dtype = (
                    previous_column.original_dtype
                    if previous_column
                    else original_dtypes.get(
                        column_name,
                        str(current_dataframe[column].dtype),
                    )
                )
                columns.append(
                    ColumnManifest(
                        name=column_name,
                        original_dtype=original_dtype,
                        current_dtype=str(current_dataframe[column].dtype),
                        nullable=bool(current_dataframe[column].isna().any()),
                        missing_count=int(current_dataframe[column].isna().sum()),
                        unique_count=int(current_dataframe[column].nunique(dropna=True)),
                        cleaning_rules=(
                            previous_column.cleaning_rules if previous_column else []
                        ),
                    )
                )

            table_manifests.append(
                TableManifest(
                    table_name=table.table_name,
                    source_file=table.filename,
                    source_sheet=table.sheet_name,
                    raw_path=self._relative_dataset_path(table.path, dataset_path),
                    processed_path=self._relative_dataset_path(
                        table.processed_path,
                        dataset_path,
                    ),
                    original_row_count=original_row_count,
                    processed_row_count=int(len(current_dataframe)),
                    columns=columns,
                    cleaning_status=(
                        previous.cleaning_status if previous else "not_started"
                    ),
                    cleaning_steps=(previous.cleaning_steps if previous else []),
                    primary_key=(previous.primary_key if previous else []),
                    foreign_keys=(previous.foreign_keys if previous else []),
                    indexes=(previous.indexes if previous else []),
                )
            )

        manifest = DatasetManifest(
            dataset_id=record.dataset_id,
            name=record.filename,
            created_at=record.created_at,
            updated_at=self._now(),
            database_path=self._relative_dataset_path(record.database_path, dataset_path),
            active_layer="processed",
            processing_status=(
                existing.processing_status if existing else "not_started"
            ),
            relationship_status=(
                relationship_status
                if relationship_status is not None
                else existing.relationship_status if existing else "confirmed"
            ),
            relationship_confirmed_at=(
                None
                if relationship_status == "pending"
                else existing.relationship_confirmed_at if existing else None
            ),
            source=(existing.source if existing else DataSourceManifest(name=record.filename)),
            tables=table_manifests,
        )
        self.manifests.write(record.manifest_path, manifest)

    def _relationship_summary(self, record: DatasetRecord) -> dict[str, Any]:
        manifest = self.manifests.load(record.manifest_path)
        if manifest is None:
            return {"status": "pending", "confirmed_at": None}
        return {
            "status": manifest.relationship_status,
            "confirmed_at": manifest.relationship_confirmed_at,
        }

    def _relative_dataset_path(self, path: Path | None, dataset_path: Path) -> str:
        if path is None:
            return ""
        try:
            return path.resolve().relative_to(dataset_path.resolve()).as_posix()
        except ValueError:
            return str(path)

    def _is_inside_dataset_dir(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.dataset_dir.resolve())
        except ValueError:
            return False
        return True


dataset_service = DatasetService()
