from fastapi.testclient import TestClient

from app.api import diagnostics
from app.main import app


class FakeSandboxService:
    def health_check(self) -> dict:
        return {
            "ok": True,
            "image": "sandbox:test",
            "checks": [
                {"name": "docker_cli", "ok": True, "message": "ok", "fix": ""},
            ],
        }


def test_sandbox_health_api(monkeypatch) -> None:
    monkeypatch.setattr(diagnostics, "PythonSandboxService", FakeSandboxService)
    client = TestClient(app)

    response = client.get("/api/sandbox/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["image"] == "sandbox:test"
