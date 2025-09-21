# app/storage.py
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4
from typing import Optional

from flask import url_for
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

AZURE_AVAILABLE = False
try:
    from azure.identity import DefaultAzureCredential  # type: ignore
    from azure.storage.blob import BlobServiceClient, ContentSettings  # type: ignore
    from azure.core.exceptions import ResourceExistsError  # type: ignore
    AZURE_AVAILABLE = True
except Exception:
    DefaultAzureCredential = None  # type: ignore
    BlobServiceClient = None  # type: ignore
    ContentSettings = None  # type: ignore
    ResourceExistsError = Exception  # type: ignore


class MediaStorage:
    def save(self, file: FileStorage, *, folder: str = "vehicles") -> str:
        raise NotImplementedError


class LocalStorage(MediaStorage):
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path(__file__).resolve().parents[1] / "static" / "uploads"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, file: FileStorage, *, folder: str = "vehicles") -> str:
        filename = secure_filename(file.filename or f"file-{uuid4().hex}")
        name = f"{uuid4().hex}-{filename}"
        dest_dir = self.base_dir / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / name
        file.save(path)
        rel_from_static = path.relative_to(self.base_dir.parent)  # uploads/vehicles/...
        return url_for("static", filename=str(rel_from_static).replace("\\", "/"))


class AzureBlobStorage(MediaStorage):
    """
    Suporta dois modos:
      A) Connection String:
         - AZURE_STORAGE_CONNECTION_STRING
         - AZURE_STORAGE_CONTAINER
      B) Account URL + Managed Identity/CLI:
         - AZURE_BLOB_ACCOUNT_URL (https://<account>.blob.core.windows.net)
         - AZURE_BLOB_CONTAINER
    Opcional:
         - AZURE_BLOB_PREFIX (ex.: 'uploads/'; default 'uploads/')
    Observação: para URL pública funcionar sem SAS, o container precisa estar com
    nível de acesso 'Blob' (leitura pública de blobs).
    """
    def __init__(self):
        if not AZURE_AVAILABLE:
            raise RuntimeError("Dependências do Azure não encontradas (azure-identity, azure-storage-blob).")

        prefix = (os.getenv("AZURE_BLOB_PREFIX", "uploads/") or "").strip("/")
        self.prefix = f"{prefix}/" if prefix else ""

        # --- Caminho A: Connection String ---
        conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "").strip()
        if conn_str:
            self.container = os.getenv("AZURE_STORAGE_CONTAINER", "").strip() or os.getenv("AZURE_BLOB_CONTAINER", "").strip()
            if not self.container:
                raise RuntimeError("Defina AZURE_STORAGE_CONTAINER (ou AZURE_BLOB_CONTAINER).")
            self.blob = BlobServiceClient.from_connection_string(conn_str)
            # Para montar a URL pública mais tarde:
            self._account_url = self.blob.url.rstrip("/")
        else:
            # --- Caminho B: Account URL + Credencial ---
            account_url = os.getenv("AZURE_BLOB_ACCOUNT_URL", "").strip()
            self.container = os.getenv("AZURE_BLOB_CONTAINER", "").strip() or os.getenv("AZURE_STORAGE_CONTAINER", "").strip()
            if not account_url:
                raise RuntimeError("Defina AZURE_BLOB_ACCOUNT_URL ou AZURE_STORAGE_CONNECTION_STRING.")
            if not self.container:
                raise RuntimeError("Defina AZURE_BLOB_CONTAINER (ou AZURE_STORAGE_CONTAINER).")
            cred = DefaultAzureCredential()
            self.blob = BlobServiceClient(account_url=account_url, credential=cred)
            self._account_url = account_url.rstrip("/")

        # Garante container (se o RBAC permitir). Se já existe, ignora.
        try:
            self.blob.create_container(self.container, public_access="blob")
        except ResourceExistsError:
            pass

    def save(self, file: FileStorage, *, folder: str = "vehicles") -> str:
        filename = secure_filename(file.filename or f"file-{uuid4().hex}")
        name = f"{uuid4().hex}-{filename}"
        blob_path = f"{self.prefix}{folder}/{name}"

        content_type = file.mimetype or "application/octet-stream"
        content = ContentSettings(content_type=content_type)

        client = self.blob.get_blob_client(self.container, blob_path)
        client.upload_blob(file.stream, overwrite=True, content_settings=content)

        # URL pública (sem SAS) se o container tiver acesso de leitura 'Blob'
        # A URL do client já é completa, podemos usá-la diretamente:
        return client.url


def get_media_storage() -> MediaStorage:
    backend = (os.getenv("MEDIA_BACKEND") or "local").lower()
    if backend == "azure":
        return AzureBlobStorage()
    return LocalStorage(base_dir=os.getenv("MEDIA_ROOT"))


def save_media(file: FileStorage, *, folder: str = "vehicles") -> str:
    storage = get_media_storage()
    return storage.save(file, folder=folder)

# ===== Airports served (per-tenant) stored under instance/uploads/tenant_settings/<slug>/airports.json
from pathlib import Path
import json

def _tenant_settings_dir(instance_path: str, tenant_slug: str) -> Path:
    """
    Returns instance/uploads/tenant_settings/<tenant_slug> creating it when needed.
    """
    p = Path(instance_path) / "uploads" / "tenant_settings" / (tenant_slug or "default")
    p.mkdir(parents=True, exist_ok=True)
    return p

def load_tenant_airports(instance_path: str, tenant_slug: str) -> list[str]:
    """
    Reads the allowed airports (list of strings like 'Miami International Airport (MIA) - Miami'
    OR just IATA codes like 'MIA') from the JSON file. Returns [] if absent.
    """
    try:
        settings_dir = _tenant_settings_dir(instance_path, tenant_slug)
        f = settings_dir / "airports.json"
        if not f.exists():
            return []
        data = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(data, list):
            # normalize to strings only
            return [str(x).strip() for x in data if x]
        return []
    except Exception:
        return []

def save_tenant_airports(instance_path: str, tenant_slug: str, airports: list[str]) -> bool:
    """
    Persists the list of airports for the tenant. Overwrites the whole file.
    """
    try:
        settings_dir = _tenant_settings_dir(instance_path, tenant_slug)
        f = settings_dir / "airports.json"
        # keep as a simple JSON array of strings
        clean = [str(x).strip() for x in (airports or []) if str(x).strip()]
        f.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
