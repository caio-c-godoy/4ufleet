# app/services/mailer.py
from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from flask import current_app, has_app_context

# --- Azure Communication Services (usado se variáveis estiverem setadas) ---
try:
    from azure.communication.email import (
        EmailClient,
        EmailContent,
        EmailMessage as ACSEmailMessage,
        EmailAddress,
    )
    from azure.core.exceptions import HttpResponseError
    _ACS_AVAILABLE = True
except Exception:
    _ACS_AVAILABLE = False

# =============================================================================
# Logging / utils
# =============================================================================
def _log(level: str, msg: str, *args):
    """Log seguro (não quebra fora de app context)."""
    if has_app_context():
        logger = current_app.logger
        if level == "info":
            logger.info(msg, *args)
        elif level == "warning":
            logger.warning(msg, *args)
        elif level == "error":
            logger.error(msg, *args)
        else:
            logger.debug(msg, *args)

def _getenv(key: str, default=None):
    """
    Busca configuração primeiro em app.config (se houver app context) e,
    se não existir/estiver vazia, usa os.getenv.
    """
    if has_app_context():
        val = current_app.config.get(key)
        if val not in (None, ""):
            return val
    return os.getenv(key, default)

def _as_bool(v) -> bool:
    """Converte valores diversos para booleano (1/true/on)."""
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")

# =============================================================================
# Cofre DEV (arquivo) para credenciais por tenant  (mantido)
# =============================================================================
_DEV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dev_secrets"))
os.makedirs(_DEV_DIR, exist_ok=True)

def _path(alias: str) -> str:
    safe = alias.replace("/", "_")
    return os.path.join(_DEV_DIR, f"{safe}.json")

def save_tenant_mail_creds(
    *, tenant, host: str, port: int, user: str, password: str,
    use_tls: bool, use_ssl: bool, provider: str = "custom-smtp"
) -> str:
    alias = tenant.mail_secret_id or f"mail/{tenant.slug}"
    data = {
        "host": host,
        "port": int(port or 0),
        "user": user,
        "password": password,
        "use_tls": bool(use_tls),
        "use_ssl": bool(use_ssl),
        "provider": provider or "custom-smtp",
    }
    with open(_path(alias), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return alias

def get_tenant_mail_creds(tenant) -> dict | None:
    alias = getattr(tenant, "mail_secret_id", None)
    if not alias:
        return None
    p = _path(alias)
    if not os.path.isfile(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

# =============================================================================
# Plataforma: SMTP (mantido) + ACS (novo)
# =============================================================================
def get_platform_mail_creds() -> dict | None:
    """
    Lê SMTP da plataforma usando PLATFORM_*.
    Retorna None se faltar host (modo MOCK).
    (mantido por compat)
    """
    host = _getenv("PLATFORM_SMTP_HOST")
    if not host:
        return None

    port = int(_getenv("PLATFORM_SMTP_PORT", 0) or 0)
    user = _getenv("PLATFORM_SMTP_USER") or ""
    password = (_getenv("PLATFORM_SMTP_PASS") or "")
    password = password.replace(" ", "")  # Gmail App Password às vezes vem com espaços

    use_tls = _as_bool(_getenv("PLATFORM_SMTP_TLS", "1"))
    use_ssl = _as_bool(_getenv("PLATFORM_SMTP_SSL", "0"))

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "use_tls": bool(use_tls and not use_ssl),
        "use_ssl": bool(use_ssl),
        "provider": "platform-smtp",
    }

# -------------- ACS helpers (novo) --------------
_ACS_CONN: Optional[str] = None
_EMAIL_FROM: Optional[str] = None
_ACS_CLIENT: Optional["EmailClient"] = None

def _acs_enabled() -> bool:
    global _ACS_CONN, _EMAIL_FROM
    if not _ACS_AVAILABLE:
        return False
    if _ACS_CONN is None:
        _ACS_CONN = (_getenv("ACS_EMAIL_CONNECTION_STRING") or "").strip()
    if _EMAIL_FROM is None:
        _EMAIL_FROM = (_getenv("EMAIL_FROM") or "").strip()
    return bool(_ACS_CONN and _EMAIL_FROM)

def _acs_client() -> "EmailClient":
    global _ACS_CLIENT
    if _ACS_CLIENT is None:
        if not _acs_enabled():
            raise RuntimeError("ACS não configurado (ACS_EMAIL_CONNECTION_STRING/EMAIL_FROM).")
        _ACS_CLIENT = EmailClient.from_connection_string(_ACS_CONN)  # type: ignore
    return _ACS_CLIENT

def _normalize_recipients(to: str) -> list[EmailAddress]:
    # aceita "a@x.com" ou "a@x.com,b@y.com"
    emails = [e.strip() for e in (to or "").split(",") if e.strip()]
    return [EmailAddress(email=e) for e in emails]  # type: ignore

def _send_via_acs(*, subject: str, html: str, text: str, to: str, reply_to: str | None = None) -> str:
    """
    Envia via Azure Communication Services e retorna message_id.
    Lança exceção em erro.
    """
    client = _acs_client()

    content = EmailContent(subject=subject)
    content.html = html or ""
    content.plain_text = text or ""

    msg = ACSEmailMessage(  # type: ignore
        sender=_EMAIL_FROM,
        content=content,
        recipients={"to": _normalize_recipients(to)},
        reply_to=([EmailAddress(email=reply_to)] if reply_to else None),  # type: ignore
    )
    try:
        poller = client.begin_send(msg)
        result = poller.result()
        msg_id = getattr(result, "message_id", "") or ""
        _log("info", "[PLATFORM EMAIL/ACS] To=%s Subject=%s MsgId=%s", to, subject, msg_id)
        return msg_id
    except HttpResponseError as e:  # type: ignore
        raise RuntimeError(f"Falha ao enviar e-mail (ACS): {getattr(e, 'message', str(e))}") from e
    except Exception as e:
        raise RuntimeError(f"Erro inesperado ao enviar e-mail (ACS): {e}") from e

# =============================================================================
# Envio SMTP baixo nível (mantido)
# =============================================================================
def _smtp_send(cfg: dict, msg: EmailMessage):
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 0)
    user = (cfg.get("user") or "").strip()
    password = (cfg.get("password") or "").strip()
    use_tls = bool(cfg.get("use_tls"))
    use_ssl = bool(cfg.get("use_ssl"))

    if not host:
        raise RuntimeError("SMTP host vazio.")

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port or 465, context=context, timeout=25) as s:
            if user and password:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port or 587, timeout=25) as s:
            s.ehlo()
            if use_tls:
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
            if user and password:
                s.login(user, password)
            s.send_message(msg)

# =============================================================================
# Plataforma: função pública (compat) — ACS -> SMTP -> MOCK
# =============================================================================
def send_platform_mail_html(*, subject: str, html: str, to: str, text_alt: str = "") -> bool:
    """
    Envia e-mail pela 'plataforma'.
    Prioridade:
      1) ACS (se configurado com ACS_EMAIL_CONNECTION_STRING + EMAIL_FROM)
      2) SMTP da plataforma (PLATFORM_*)
      3) MOCK (sem envio)
    Retorna True se enviou; False se ficou em MOCK.
    """
    # 1) ACS
    if _acs_enabled():
        try:
            _send_via_acs(
                subject=subject,
                html=html,
                text=text_alt,
                to=to,
                reply_to=_getenv("PLATFORM_REPLY_TO", "support@4ufleet.com"),
            )
            return True
        except Exception as e:
            _log("error", "Falha no envio via ACS; tentando SMTP da plataforma. Err=%s", e)

    # 2) SMTP plataforma (mantido)
    cfg = get_platform_mail_creds()
    if not cfg:
        _log("warning", "[EMAIL MOCK] (platform) To=%s Subject=%s (sem ACS e sem PLATFORM_SMTP_HOST)", to, subject)
        return False

    from_name = _getenv("PLATFORM_MAIL_FROM_NAME", _getenv("APP_NAME", "Car Rental SaaS"))
    # Preferimos o EMAIL_FROM quando configurado, senão user do SMTP, senão fallback
    from_email = _getenv("EMAIL_FROM") or _getenv("PLATFORM_MAIL_FROM_EMAIL") or cfg.get("user") or "no-reply@example.com"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to
    if text_alt:
        msg.set_content(text_alt)
    msg.add_alternative(html, subtype="html")

    _smtp_send(cfg, msg)
    _log("info", "[PLATFORM EMAIL/SMTP] To=%s Subject=%s", to, subject)
    return True

# =============================================================================
# Utilitários de teste (mantido)
# =============================================================================
def send_test_mail(
    *, cfg: dict, subject: str, body: str,
    from_name: str, from_email: str, to_email: str
):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.set_content(body)
    _smtp_send(cfg, msg)

# =============================================================================
# Envio por TENANT (mantido: sempre SMTP do tenant)
# =============================================================================
def send_tenant_mail_html(
    *, tenant, subject: str, html: str, to: str, text_alt: str = ""
) -> bool:
    """
    Envia e-mail HTML usando as credenciais do tenant (sempre SMTP do tenant).
    Retorna True se enviou; False se entrou em modo MOCK (sem alias/creds).
    Lança exceção em erro real de envio.
    """
    cfg = get_tenant_mail_creds(tenant)
    if not cfg:
        _log("info", "[EMAIL MOCK] (tenant=%s) To=%s Subject=%s", getattr(tenant, 'slug', '?'), to, subject)
        return False

    from_name = getattr(tenant, "mail_from_name", None) or getattr(tenant, "name", None) or "Locadora"
    from_email = getattr(tenant, "mail_from_email", None) or cfg.get("user") or _getenv("EMAIL_FROM") or "no-reply@example.com"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to
    if text_alt:
        msg.set_content(text_alt)
    msg.add_alternative(html, subtype="html")

    _smtp_send(cfg, msg)
    _log("info", "[TENANT EMAIL/SMTP] tenant=%s To=%s Subject=%s", getattr(tenant, 'slug', '?'), to, subject)
    return True

# =============================================================================
# Roteador automático — Tenant → Plataforma(ACS/SMTP) → MOCK
# =============================================================================
def send_mail_auto(
    *, tenant, subject: str, html: str, to: str, text_alt: str = ""
) -> bool:
    """
    Tenta enviar pelo SMTP do tenant; se não houver ou falhar, cai para
    a plataforma (ACS se disponível; caso contrário, SMTP).
    Retorna True se enviou por qualquer um; False se nenhum estava configurado (MOCK).
    """
    try:
        ok = send_tenant_mail_html(
            tenant=tenant, subject=subject, html=html, to=to, text_alt=text_alt
        )
        if ok:
            return True
    except Exception as e:
        _log("error", "Falha no envio via SMTP do tenant; tentando plataforma. Err=%s", e)

    try:
        ok2 = send_platform_mail_html(
            subject=subject, html=html, to=to, text_alt=text_alt
        )
        return bool(ok2)
    except Exception as e:
        _log("error", "Falha no envio via plataforma. Err=%s", e)

    return False

# =============================================================================
# Compat com chamadas antigas (mantido)
# =============================================================================
def send_email_for_tenant(
    tenant, recipients, subject: str, html: str, text_alt: str = ""
) -> bool:
    """
    Wrapper compatível com a assinatura antiga.
    Aceita list/tuple/set de destinatários. Tenta Tenant → Plataforma.
    """
    if isinstance(recipients, (list, tuple, set)):
        to = ", ".join(r for r in recipients if r)
    else:
        to = recipients
    return send_mail_auto(
        tenant=tenant, subject=subject, html=html, to=to, text_alt=text_alt
    )
