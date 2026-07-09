from fastapi import APIRouter

from app.services.python_sandbox_service import PythonSandboxService


router = APIRouter()


@router.get("/sandbox/health")
def get_python_sandbox_health() -> dict:
    return PythonSandboxService().health_check()
