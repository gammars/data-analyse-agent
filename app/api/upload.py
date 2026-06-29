from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.schemas.relationships import (
    ReviseRelationshipConfigRequest,
    SaveRelationshipConfigRequest,
)
from app.services.dataset_service import dataset_service
from app.services.relationship_service import relationship_service


router = APIRouter()


class RenameDatasetRequest(BaseModel):
    name: str


@router.get("/datasets")
def list_datasets() -> dict:
    return {"datasets": dataset_service.list_datasets()}


@router.post("/upload")
async def upload_file(request: Request) -> dict:
    files = await _read_upload_files(request)

    try:
        record = dataset_service.save_dataset_files(files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return dataset_service.get_summary(record.dataset_id)


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str) -> dict:
    try:
        return dataset_service.get_summary(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/manifest")
def get_dataset_manifest(dataset_id: str) -> dict:
    try:
        return dataset_service.get_manifest(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/relationships")
def get_dataset_relationships(dataset_id: str) -> dict:
    try:
        return relationship_service.get_configuration(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/relationships/suggestions")
def suggest_dataset_relationships(
    dataset_id: str,
    refresh_llm: bool = False,
) -> dict:
    try:
        return relationship_service.suggest(
            dataset_id,
            include_llm=True,
            refresh_llm=refresh_llm,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/datasets/{dataset_id}/relationships/validation")
def validate_dataset_relationships(dataset_id: str) -> dict:
    try:
        return relationship_service.validate(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/datasets/{dataset_id}/relationships")
def save_dataset_relationships(
    dataset_id: str,
    payload: SaveRelationshipConfigRequest,
) -> dict:
    try:
        return relationship_service.save(
            dataset_id,
            payload.tables,
            payload.confirmed,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/datasets/{dataset_id}/relationships/revise")
def revise_dataset_relationships(
    dataset_id: str,
    payload: ReviseRelationshipConfigRequest,
) -> dict:
    try:
        return relationship_service.revise(dataset_id, payload.tables)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/datasets/{dataset_id}")
def rename_dataset(dataset_id: str, payload: RenameDatasetRequest) -> dict:
    try:
        record = dataset_service.rename_dataset(dataset_id, payload.name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return dataset_service.get_summary(record.dataset_id)


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict:
    try:
        dataset_service.delete_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"deleted": True, "dataset_id": dataset_id}


@router.post("/datasets/{dataset_id}/tables")
async def append_dataset_table(dataset_id: str, request: Request) -> dict:
    files = await _read_upload_files(request)

    try:
        record = dataset_service.append_tables(dataset_id, files)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return dataset_service.get_summary(record.dataset_id)


@router.delete("/datasets/{dataset_id}/tables/{table_name}")
def delete_dataset_table(dataset_id: str, table_name: str) -> dict:
    try:
        record = dataset_service.delete_table(dataset_id, table_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return dataset_service.get_summary(record.dataset_id)


async def _read_upload_files(request: Request) -> list[tuple[str, bytes]]:
    form = await request.form()
    uploads = []
    for field_name in ("files", "file"):
        uploads.extend(form.getlist(field_name))

    files: list[tuple[str, bytes]] = []
    for upload in uploads:
        if not hasattr(upload, "filename") or not hasattr(upload, "read"):
            continue
        files.append((upload.filename or "", await upload.read()))

    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的数据文件")
    return files
