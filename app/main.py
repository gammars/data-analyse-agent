from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.upload import router as upload_router


app = FastAPI(title="LangChain Data Analysis Agent")

app.include_router(upload_router, prefix="/api", tags=["datasets"])
app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.mount("/charts", StaticFiles(directory="app/storage/charts"), name="charts")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
