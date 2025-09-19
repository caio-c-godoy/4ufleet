# app/utils.py
from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from functools import lru_cache

from flask import current_app, url_for

# -------------------------------------------------------------
# Helpers gerais
# -------------------------------------------------------------
def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def parse_datetime(date_str: str, time_str: str) -> datetime:
    # Expects 'YYYY-MM-DD' and 'HH:MM'
    return datetime.fromisoformat(f"{date_str} {time_str}")


def absolute_url_for(endpoint: str, **values) -> str:
    """
    Gera URL ABSOLUTA. Se EXTERNAL_BASE_URL estiver definida (config/.env),
    monta a URL usando essa base; senão usa url_for(..., _external=True).
    """
    base = (current_app.config.get("EXTERNAL_BASE_URL") if current_app else None) \
           or os.getenv("EXTERNAL_BASE_URL") or ""
    base = base.strip()

    path_url = url_for(endpoint, _external=False, **values)

    parsed = urlparse(path_url)
    if parsed.scheme and parsed.netloc:
        return path_url  # já veio absoluto

    if base:
        return urljoin(base.rstrip("/") + "/", path_url.lstrip("/"))

    return url_for(endpoint, _external=True, **values)


# -------------------------------------------------------------
# imgsrc: única fonte da verdade (NÃO duplique esta função)
# -------------------------------------------------------------
_ABS_PREFIXES = ("https://", "http://", "//", "data:")

# Placeholder inline para nunca dar 404 (SVG leve)
_INLINE_PLACEHOLDER = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='80'>"
    "<rect width='100%' height='100%' fill='%23f0f2f5'/>"
    "<text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' "
    "font-family='Arial' font-size='12' fill='%2399a'>sem imagem</text>"
    "</svg>"
)

def imgsrc(path: str | None) -> str:
    """
    Normaliza caminho/URL para uso em <img src="...">:
    - Corrige 'https:/' -> 'https://' e 'http:/' -> 'http://'
    - Conserta casos de '/static/https:/...' removendo o /static/ indevido
    - Mantém URLs absolutas (http/https//data)
    - Aceita domínio sem protocolo -> força https://
    - Caminhos relativos viram /static/<...>
    - Se vazio/None, retorna um placeholder inline (sem 404)
    """
    if not path:
        return _INLINE_PLACEHOLDER

    s = str(path).strip()
    if not s:
        return _INLINE_PLACEHOLDER

    # Se veio prefixado indevidamente: "/static/https:/..." ou "/static/http:/..."
    if s.startswith("/static/https:/") and not s.startswith("/static/https://"):
        s = s[len("/static/"):]  # tira o /static/
    elif s.startswith("/static/http:/") and not s.startswith("/static/http://"):
        s = s[len("/static/"):]

    # Corrige protocolos com 1 barra (erros comuns)
    if s.startswith("https:/") and not s.startswith("https://"):
        s = "https://" + s[len("https:/"):]
    elif s.startswith("http:/") and not s.startswith("http://"):
        s = "http://" + s[len("http:/"):]

    # URL absoluta (ou protocol-relative/data:)
    if s.startswith(_ABS_PREFIXES):
        return s

    # Caminhos absolutos do app
    if s.startswith("/static/") or s.startswith("/"):
        return s

    # Domínio sem protocolo (ex.: st4ufleetprd.blob.core.windows.net/foo)
    if "://" not in s and re.match(r"^[A-Za-z0-9.\-]+(:\d+)?\.[A-Za-z]{2,}(/|$)", s):
        return "https://" + s.lstrip("/")

    # "static/..."
    if s.startswith("static/"):
        return url_for("static", filename=s[len("static/"):])

    # Qualquer outro relativo -> /static/<...>
    return url_for("static", filename=s)


# -------------------------------------------------------------
# Key Vault helpers (mantidos)
# -------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

@lru_cache(maxsize=1)
def _client() -> SecretClient:
    vault_url = os.environ.get("AZURE_KEYVAULT_URL")
    if not vault_url:
        raise RuntimeError("AZURE_KEYVAULT_URL não definido no ambiente.")
    cred = DefaultAzureCredential()
    return SecretClient(vault_url=vault_url, credential=cred)

def kv_set_secret(name: str, value: str, tags: dict | None = None):
    # NUNCA faça print/log do value!
    return _client().set_secret(name=name, value=value, tags=tags or {})

def kv_get_secret(name: str) -> str:
    return _client().get_secret(name).value

def kv_secret_exists(name: str) -> bool:
    try:
        _client().get_secret(name)
        return True
    except Exception:
        return False
