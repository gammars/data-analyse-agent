from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.conversation_service import conversation_service
from app.services.context_service import context_service
from app.services.dataset_service import dataset_service


router = APIRouter()


class CreateConversationRequest(BaseModel):
    dataset_id: str = Field(..., description="绑定的数据集 ID")
    title: str | None = Field(None, description="对话标题")


class UpdateConversationDatasetRequest(BaseModel):
    dataset_id: str = Field(..., description="新的数据集 ID")


@router.get("/conversations")
def list_conversations() -> dict:
    conversations = []
    for item in conversation_service.list_conversations():
        try:
            full = conversation_service.get_conversation(item["conversation_id"])
            item["context"] = context_service.get_context_stats(full)
        except KeyError:
            pass
        conversations.append(item)
    return {"conversations": conversations}


@router.post("/conversations")
def create_conversation(req: CreateConversationRequest) -> dict:
    try:
        dataset_service.get_summary(req.dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return conversation_service.create_conversation(dataset_id=req.dataset_id, title=req.title)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    try:
        conversation = conversation_service.get_conversation(conversation_id)
        conversation["context"] = context_service.get_context_stats(conversation)
        return conversation
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/conversations/{conversation_id}/dataset")
def update_conversation_dataset(conversation_id: str, req: UpdateConversationDatasetRequest) -> dict:
    try:
        dataset_service.get_summary(req.dataset_id)
        return conversation_service.update_dataset(conversation_id, req.dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    try:
        conversation_service.delete_conversation(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"deleted": True, "conversation_id": conversation_id}
