# app/storage.py
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4
from typing import Optional

from flask import url_for
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# Imports do Azure (opcionais em dev)
AZURE_AVAILABLE = False
try:
    from azure.identity import DefaultAzureCredential  # type: ignore
    from azure.storage.blob import BlobServiceClient, ContentSettings  # type: ignore
    from azure.core.exceptions import ResourceExistsError  # type: ignore
    AZURE_AVAILABLE = True
except Exception:  # libs não instaladas em dev
    DefaultAzureCredential = None  # type: ignore
    BlobServiceClient = None  # type: ignore
    ContentSettings = None  # type: ignore
    ResourceExistsError = Exception  # type: ignore


class MediaStorage:
    """Interface simples para salvar uploads e devolver URL pública."""
    def save(self, file: FileStorage, *, folder: str = "vehicles") -> str:
        raise NotImplementedError


class LocalStorage(MediaStorage):
    """
    Salva em disco local (dev) dentro de static/uploads/.
    Retorna URL via /static/uploads/...
    """
    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            # <repo_root>/static/uploads
            self.base_dir = Path(__file__).resolve().parents[1] / "static" / "uploads"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, file: FileStorage, *, folder: str = "vehicles") -> str:
        filename = secure_filename(file.filename or f"file-{uuid4().hex}")
        name = f"{uuid4().hex}-{filename}"
        dest_dir = self.base_dir / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / name
        file.save(path)

        # path relativo a /static
        rel_from_static = path.relative_to(self.base_dir.parent)  # uploads/vehicles/...
        return url_for("static", filename=str(rel_from_static).replace("\\", "/"))


class AzureBlobStorage(MediaStorage):
    """
    Envia para Azure Blob Storage (prod).
    Espera variáveis:
      - AZURE_BLOB_ACCOUNT_URL (ex: https://minhaconta.blob.core.windows.net)
      - AZURE_BLOB_CONTAINER   (ex: media)
      - AZURE_BLOB_PREFIX      (opcional, ex: uploads/)
    Requer Managed Identity com 'Storage Blob Data Contributor' no Storage Account.
    """
    def __init__(self, account_url: str, container: str, prefix: str = "uploads/"):
        if not AZURE_AVAILABLE:
            raise RuntimeError("Dependências do Azure não encontradas (azure-identity, azure-storage-blob).")

        self.account_url = account_url.rstrip("/")
        self.container = container
        self.prefix = (prefix or "").strip("/")
        if self.prefix:
            self.prefix += "/"

        cred = DefaultAzureCredential()  # usa MI no App Service, e DevTools/CLI em dev
        self.blob = BlobServiceClient(account_url=self.account_url, credential=cred)

        # Garante container (se política permitir)
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

        self.blob.get_blob_client(self.container, blob_path).upload_blob(
            file.stream, overwrite=True, content_settings=content
        )
        # URL pública (container com acesso de leitura tipo "Blob")
        return f"{self.account_url}/{self.container}/{blob_path}"


def get_media_storage() -> MediaStorage:
    """
    Decide backend por env:
      MEDIA_BACKEND=azure -> AzureBlobStorage
      MEDIA_BACKEND=local (default) -> LocalStorage
    """
    backend = (os.getenv("MEDIA_BACKEND") or "local").lower()

    if backend == "azure":
        account_url = os.getenv("AZURE_BLOB_ACCOUNT_URL", "").strip()
        container = os.getenv("AZURE_BLOB_CONTAINER", "media").strip()
        prefix = os.getenv("AZURE_BLOB_PREFIX", "uploads/").strip()
        if not account_url:
            raise RuntimeError("AZURE_BLOB_ACCOUNT_URL não configurado.")
        return AzureBlobStorage(account_url=account_url, container=container, prefix=prefix)

    # local
    base_dir = os.getenv("MEDIA_ROOT")  # opcional
    return LocalStorage(base_dir=base_dir)


def save_media(file: FileStorage, *, folder: str = "vehicles") -> str:
    """
    Helper único que sua rota chamará para persistir e receber a URL final.
    """
    storage = get_media_storage()
    return storage.save(file, folder=folder)
