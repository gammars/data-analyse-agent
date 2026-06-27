from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.dataset_service import dataset_service


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
