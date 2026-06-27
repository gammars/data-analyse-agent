import os
import uuid
from pathlib import Path

from app.schemas.manifest import DatasetManifest


MANIFEST_FILENAME = "manifest.json"


class ManifestService:
    """Read and atomically persist per-dataset manifests."""

    def load(self, manifest_path: Path) -> DatasetManifest | None:
        if not manifest_path.exists():
            return None
        return DatasetManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )

    def write(self, manifest_path: Path, manifest: DatasetManifest) -> None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = manifest_path.with_name(
            f".{manifest_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_path.write_text(
                manifest.model_dump_json(indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_path, manifest_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()


manifest_service = ManifestService()
