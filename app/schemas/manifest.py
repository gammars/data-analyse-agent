from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class DataSourceManifest(ManifestModel):
    name: str | None = None
    url: str | None = None
    license: str | None = None
    description: str | None = None


class CleaningStepManifest(ManifestModel):
    operation: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    applied_at: str
    applied_by: str = "tool"
    before_rows: int | None = None
    after_rows: int | None = None


class ColumnManifest(ManifestModel):
    name: str
    original_dtype: str
    current_dtype: str
    nullable: bool
    missing_count: int
    unique_count: int
    cleaning_rules: list[dict[str, Any]] = Field(default_factory=list)


class ForeignKeyManifest(ManifestModel):
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]
    name: str | None = None


class IndexManifest(ManifestModel):
    name: str
    columns: list[str]
    unique: bool = False


class TableManifest(ManifestModel):
    table_name: str
    source_file: str
    source_sheet: str | None = None
    raw_path: str
    processed_path: str
    original_row_count: int
    processed_row_count: int
    columns: list[ColumnManifest]
    cleaning_status: str = "not_started"
    cleaning_steps: list[CleaningStepManifest] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyManifest] = Field(default_factory=list)
    indexes: list[IndexManifest] = Field(default_factory=list)


class DatasetManifest(ManifestModel):
    manifest_version: int = 1
    dataset_id: str
    name: str
    created_at: str
    updated_at: str
    database_path: str = "dataset.sqlite3"
    active_layer: str = "processed"
    processing_status: str = "not_started"
    source: DataSourceManifest = Field(default_factory=DataSourceManifest)
    tables: list[TableManifest]
