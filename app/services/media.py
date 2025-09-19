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
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or default

# --------------------------
# LOCAL (filesystem)
# --------------------------
def _save_local(file_storage, tenant_slug: str) -> str:
    """Salva em /static/uploads/vehicles/<tenant_slug>/... e retorna a URL web (/static/...)."""
    root = Path(current_app.root_path).parent
    rel_dir = Path("static") / "uploads" / "vehicles" / tenant_slug
    abs_dir = root / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    ext = _choose_ext(file_storage.filename)
    filename = f"{uuid.uuid4().hex}.{ext}"
    abs_path = abs_dir / filename

    file_storage.save(abs_path.as_posix())

    web_path = f"/{rel_dir.as_posix()}/{filename}"
    if not web_path.startswith("/"):
        web_path = "/" + web_path
    return web_path

# --------------------------
# AZURE BLOB
# --------------------------
def _conn_str_account_name(conn_str: str) -> Optional[str]:
    try:
        parts = dict(kv.split("=", 1) for kv in conn_str.split(";") if "=" in kv)
        return parts.get("AccountName")
    except Exception:
        return None

def _valid_conn_str(conn_str: str) -> bool:
    if not conn_str:
        return False
    # precisa conter chaves básicas
    return all(k in conn_str for k in ("AccountName=", "AccountKey=", "EndpointSuffix="))

def _save_azure_blob(file_storage, tenant_slug: str) -> str:
    from azure.storage.blob import BlobServiceClient, ContentSettings

    conn_str = (
        os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        or os.getenv("AZURE_BLOB_CONNECTION_STRING")
        or ""
    )
    container = (
        os.getenv("AZURE_STORAGE_CONTAINER")
        or os.getenv("AZURE_BLOB_CONTAINER")
        or ""
    )

    if not _valid_conn_str(conn_str) or not container:
        # parâmetros ruins → trate como “sem Azure”
        raise RuntimeError("Azure Blob mal configurado.")

    ext = _choose_ext(file_storage.filename)
    if ext not in _ALLOWED_EXT:
        raise ValueError("Formato de imagem não suportado.")

    filename = f"{uuid.uuid4().hex}.{ext}"
    blob_path = f"uploads/vehicles/{tenant_slug}/{filename}"

    ctype = getattr(file_storage, "mimetype", None) or _guess_content_type(filename, "image/jpeg")
    content_settings = ContentSettings(content_type=ctype)

    bsc = BlobServiceClient.from_connection_string(conn_str)
    cc = bsc.get_container_client(container)
    try:
        cc.create_container()
    except Exception:
        pass

    file_storage.stream.seek(0)
    cc.upload_blob(
        name=blob_path,
        data=file_storage.stream,
        overwrite=True,
        content_settings=content_settings,
    )

    base_url = os.getenv("AZURE_STORAGE_BASE_URL")
    if not base_url:
        account = _conn_str_account_name(conn_str) or ""
        base_url = f"https://{account}.blob.core.windows.net"
    return f"{base_url.strip().rstrip('/')}/{container}/{blob_path}"

# --------------------------
# API pública
# --------------------------
def save_vehicle_image_from_request(file_storage, tenant_slug: str) -> str:
    """
    Salva a imagem do veículo e **NÃO** levanta exceção:
    - Tenta Azure (se configurado corretamente). Se falhar, faz fallback para local.
    - Retorna sempre uma URL utilizável (externa ou /static/...).
    """
    if not file_storage or not getattr(file_storage, "filename", ""):
        return ""  # nada enviado

    # sanitiza (o nome final é UUID de qualquer forma)
    _ = secure_filename(file_storage.filename)

    try:
        # tenta Azure primeiro; se der qualquer pau, cai no local
        return _save_azure_blob(file_storage, tenant_slug)
    except Exception:
        # fallback local silencioso
        try:
            return _save_local(file_storage, tenant_slug)
        except Exception:
            return ""  # último recurso
