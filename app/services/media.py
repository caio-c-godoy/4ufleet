# app/services/media.py
from __future__ import annotations

import os
import uuid
import mimetypes
from pathlib import Path
from typing import Optional

from flask import current_app
from werkzeug.utils import secure_filename

# Extensões aceitas
_ALLOWED_EXT = {"jpg", "jpeg", "png", "webp", "gif"}

def _choose_ext(filename: str, fallback: str = "jpg") -> str:
    name = (filename or "").lower()
    if "." in name:
        ext = name.rsplit(".", 1)[-1]
        if ext in _ALLOWED_EXT:
            return ext
    return fallback

def _guess_content_type(filename: str, default: str = "application/octet-stream") -> str:
    # usa o mimetype do sistema
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or default

# --------------------------
# Backend: LOCAL (filesystem)
# --------------------------
def _save_local(file_storage, tenant_slug: str) -> str:
    """Salva em /static/uploads/vehicles/<tenant_slug>/... e retorna a URL web (/static/...)."""
    # raiz do projeto = app.root_path/..
    root = Path(current_app.root_path).parent
    rel_dir = Path("static") / "uploads" / "vehicles" / tenant_slug
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    ext = _choose_ext(file_storage.filename)
    filename = f"{uuid.uuid4().hex}.{ext}"
    abs_path = abs_dir / filename

    # salva
    file_storage.save(abs_path.as_posix())

    # caminho web (servido pelo Flask/AppService)
    web_path = f"/{rel_dir.as_posix()}/{filename}"
    # garante uma única barra no início
    if not web_path.startswith("/"):
        web_path = "/" + web_path
    return web_path

# --------------------------
# Backend: AZURE BLOB
# --------------------------
def _conn_str_account_name(conn_str: str) -> Optional[str]:
    # extrai AccountName=... da connection string
    try:
        parts = dict(
            kv.split("=", 1) for kv in conn_str.split(";") if "=" in kv
        )
        return parts.get("AccountName")
    except Exception:
        return None

def _save_azure_blob(file_storage, tenant_slug: str) -> str:
    """
    Envia para Azure Blob Storage e retorna URL pública do blob.
    Requer variáveis:
      - AZURE_STORAGE_CONNECTION_STRING  (ou AZURE_BLOB_CONNECTION_STRING)
      - AZURE_STORAGE_CONTAINER          (ou AZURE_BLOB_CONTAINER)
    Opcional:
      - AZURE_STORAGE_BASE_URL (ex: https://minhaconta.blob.core.windows.net)
    """
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
    except Exception as e:
        raise RuntimeError(
            "Dependência ausente: instale 'azure-storage-blob' no seu ambiente."
        ) from e

    conn_str = (
        os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        or os.getenv("AZURE_BLOB_CONNECTION_STRING")
    )
    container = (
        os.getenv("AZURE_STORAGE_CONTAINER")
        or os.getenv("AZURE_BLOB_CONTAINER")
    )
    if not conn_str or not container:
        raise RuntimeError(
            "Faltam variáveis para Azure Blob: "
            "AZURE_STORAGE_CONNECTION_STRING e AZURE_STORAGE_CONTAINER."
        )

    ext = _choose_ext(file_storage.filename)
    if ext not in _ALLOWED_EXT:
        raise ValueError("Formato de imagem não suportado.")

    filename = f"{uuid.uuid4().hex}.{ext}"
    blob_path = f"uploads/vehicles/{tenant_slug}/{filename}"

    # content-type
    ctype = getattr(file_storage, "mimetype", None) or _guess_content_type(filename, "image/jpeg")
    content_settings = ContentSettings(content_type=ctype)

    bsc = BlobServiceClient.from_connection_string(conn_str)
    cc = bsc.get_container_client(container)

    # cria o container se não existir (idempotente)
    try:
        cc.create_container()  # se já existir, lança exceção
    except Exception:
        pass  # ignora se já existe

    # upload (overwrite=True para permitir reenvio com mesmo nome se ocorrer)
    file_storage.stream.seek(0)
    cc.upload_blob(
        name=blob_path,
        data=file_storage.stream,
        overwrite=True,
        content_settings=content_settings,
    )

    # monta URL pública
    base_url = os.getenv("AZURE_STORAGE_BASE_URL")
    if not base_url:
        # tenta deduzir: https://<account>.blob.core.windows.net
        account = _conn_str_account_name(conn_str) or ""
        base_url = f"https://{account}.blob.core.windows.net"
    public_url = f"{base_url.strip().rstrip('/')}/{container}/{blob_path}"
    return public_url

# --------------------------
# Seleção de backend e API pública
# --------------------------
def _want_azure() -> bool:
    # ativa Azure se variáveis mínimas existirem
    has_conn = bool(os.getenv("AZURE_STORAGE_CONNECTION_STRING") or os.getenv("AZURE_BLOB_CONNECTION_STRING"))
    has_cont = bool(os.getenv("AZURE_STORAGE_CONTAINER") or os.getenv("AZURE_BLOB_CONTAINER"))
    return has_conn and has_cont

def save_vehicle_image_from_request(file_storage, tenant_slug: str) -> str:
    """
    Salva a imagem do veículo e retorna uma URL (web) utilizável no template.
    - Se Azure Blob estiver configurado via env, envia para o Blob e retorna a URL.
    - Caso contrário, salva localmente em /static/uploads/vehicles/<tenant>/... e retorna /static/...
    """
    if not file_storage or not getattr(file_storage, "filename", ""):
        raise ValueError("Nenhum arquivo recebido.")

    # sanitiza nome original (apenas por segurança; usamos UUID para o nome final)
    _ = secure_filename(file_storage.filename)

    if _want_azure():
        return _save_azure_blob(file_storage, tenant_slug)

    # fallback local
    return _save_local(file_storage, tenant_slug)
