# app/utils.py
from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from flask import current_app, url_for

# -------------------------------------------------------------
# Helpers gerais
# -------------------------------------------------------------
def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return re.sub(r'-+', '-', text).strip('-')


def parse_datetime(date_str: str, time_str: str) -> datetime:
    # Expects 'YYYY-MM-DD' and 'HH:MM'
    return datetime.fromisoformat(f"{date_str} {time_str}")


def absolute_url_for(endpoint: str, **values) -> str:
    """
    Gera URL ABSOLUTA. Se EXTERNAL_BASE_URL (no .env/config) estiver
    definido, monta a URL usando essa base; caso contrário, usa
    url_for(..., _external=True) (localhost/dev).

    Exemplo:
        absolute_url_for("auth.verify_email", tenant_slug="locadora1", token="XYZ")
    """
    # tenta em config primeiro; se não tiver, busca no ambiente
    base = (current_app.config.get("EXTERNAL_BASE_URL") if current_app else None) or os.getenv("EXTERNAL_BASE_URL") or ""
    base = base.strip()

    # Caminho “local” (sem domínio) a partir do endpoint
    path_url = url_for(endpoint, _external=False, **values)

    # Se por algum motivo path_url já vier absoluto, retorna como está
    parsed = urlparse(path_url)
    if parsed.scheme and parsed.netloc:
        return path_url

    if base:
        # garante que teremos exatamente uma / entre base e path
        return urljoin(base.rstrip("/") + "/", path_url.lstrip("/"))

    # fallback: usa o domínio do próprio servidor (localhost em dev)
    return url_for(endpoint, _external=True, **values)

# app/utils.py

from flask import url_for

def imgsrc(path: str | None) -> str:
    """
    Resolve URLs de imagem para uso no <img src="...">.

    - URLs absolutas ('https://', 'http://', ou '//') são retornadas como estão.
    - Corrige 'https:/' -> 'https://', 'http:/' -> 'http://' se aparecer.
    - Caminhos começando com 'static/' ou relativos viram url_for('static', filename=...).
    - Se vier vazio, usa placeholder.
    """
    placeholder = url_for('static', filename='img/placeholder-car.jpg')

    if not path:
        return placeholder

    s = str(path).strip()

    # Correções de esquemas malformados vindos do storage
    if s.startswith('https:/') and not s.startswith('https://'):
        s = s.replace('https:/', 'https://', 1)
        return s
    if s.startswith('http:/') and not s.startswith('http://'):
        s = s.replace('http:/', 'http://', 1)
        return s

    # URLs absolutas (inclui protocolo omitido com '//')
    if s.startswith('https://') or s.startswith('http://') or s.startswith('//'):
        return s

    # Se alguém salvou já com prefixo '/static/', normalize
    if s.startswith('/static/'):
        return s  # já é caminho absoluto do app

    # Se veio 'static/...', transforme corretamente
    if s.startswith('static/'):
        return url_for('static', filename=s[len('static/'):])

    # Qualquer outro relativo cai em /static/<relativo>
    return url_for('static', filename=s)
# app/utils.py
from flask import url_for

def imgsrc(value: str | None) -> str:
    """Normaliza caminhos de imagem:
    - URL absoluta (http/https) => retorna como está
    - Caminho começando por /static/ => retorna como está
    - Qualquer outro caminho => trata como arquivo dentro de /static
    """
    if not value:
        return url_for('static', filename='img/placeholder_car.jpg')

    v = str(value).strip()

    # URL absoluta: devolve direto
    if v.startswith('http://') or v.startswith('https://'):
        return v

    # Se já vier com /static/... mantém
    if v.startswith('/static/'):
        return v

    # Remove barras à esquerda para evitar // ao concatenar
    v = v.lstrip('/')

    # Se o valor já vier com "static/...", garante uma única / no início
    if v.startswith('static/'):
        return '/' + v

    # Caso padrão: caminho relativo dentro do diretório static
    return url_for('static', filename=v)
# app/utils.py
from flask import url_for

ABS_PREFIXES = ("http://", "https://", "data:")

def imgsrc(path: str | None) -> str:
    if not path:
        return ""
    p = str(path).strip()
    if p.startswith(ABS_PREFIXES):
        return p
    if p.startswith("/static/"):
        return p
    return url_for("static", filename=p.lstrip("/"))

# -------------------------------------------------------------
# Key Vault helpers (como você já tinha)
# -------------------------------------------------------------
# app/utils/keyvault.py (mantido aqui por compatibilidade)
from functools import lru_cache
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Carrega .env em dev (não quebra se não existir)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


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
