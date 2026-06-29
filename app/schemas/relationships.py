from pydantic import BaseModel, Field

from app.schemas.manifest import ForeignKeyManifest, IndexManifest


class TableRelationshipConfig(BaseModel):
    table_name: str
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyManifest] = Field(default_factory=list)
    indexes: list[IndexManifest] = Field(default_factory=list)


class SaveRelationshipConfigRequest(BaseModel):
    confirmed: bool = Field(
        ...,
        description="必须由用户明确确认为 true，系统不会自动应用候选关系",
    )
    tables: list[TableRelationshipConfig]


class ReviseRelationshipConfigRequest(BaseModel):
    tables: list[TableRelationshipConfig]


class LLMTableRelationshipRecommendation(BaseModel):
    table_name: str
    primary_key: list[str] = Field(default_factory=list)
    primary_key_reason: str = ""
    indexes: list[str] = Field(default_factory=list)
    index_reason: str = ""


class LLMForeignKeyRecommendation(BaseModel):
    candidate_id: str
    reason: str = ""


class LLMRelationshipAdvice(BaseModel):
    summary: str
    table_recommendations: list[LLMTableRelationshipRecommendation] = Field(
        default_factory=list
    )
    foreign_key_recommendations: list[LLMForeignKeyRecommendation] = Field(
        default_factory=list
    )
    warnings: list[str] = Field(default_factory=list)
