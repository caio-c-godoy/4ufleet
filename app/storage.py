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
