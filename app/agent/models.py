import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


def build_chat_model() -> ChatOpenAI:
    load_dotenv()

    return ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL") or None,
        temperature=0.1,
        streaming=True,
    )
