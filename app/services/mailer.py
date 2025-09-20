# app/services/mailer.py
from __future__ import annotations

import json, os, smtplib, ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Optional
from flask import current_app, has_app_context

# ---------------- ACS (sem EmailContent/ACSEmailMessage) ----------------
_ACS_IMPORT_ERR = ""
try:
    from azure.communication.email import EmailClient
    from azure.core.exceptions import HttpResponseError
    _ACS_AVAILABLE = True
except Exception as e:
    _ACS_AVAILABLE = False
    _ACS_IMPORT_ERR = repr(e)

def _log(level: str, msg: str, *args):
    if has_app_context():
        lg = current_app.logger
        getattr(lg, {"info":"info","warning":"warning","error":"error"}.get(level,"debug"))(msg, *args)

def _getenv(key: str, default=None):
    if has_app_context():
        v = current_app.config.get(key)
        if v not in (None, ""):
            return v
    return os.getenv(key, default)

def _as_bool(v) -> bool:
    return str(v).strip().lower() in ("1","true","yes","on")

# ---------------- Cofre DEV (inalterado) ----------------
_DEV_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dev_secrets"))
os.makedirs(_DEV_DIR, exist_ok=True)
def _path(alias: str) -> str:
    return os.path.join(_DEV_DIR, f"{alias.replace('/','_')}.json")

def save_tenant_mail_creds(*, tenant, host, port, user, password, use_tls, use_ssl, provider="custom-smtp") -> str:
    alias = tenant.mail_secret_id or f"mail/{tenant.slug}"
    with open(_path(alias), "w", encoding="utf-8") as f:
        json.dump({
            "host": host, "port": int(port or 0), "user": user, "password": password,
            "use_tls": bool(use_tls), "use_ssl": bool(use_ssl), "provider": provider or "custom-smtp",
        }, f)
    return alias

def get_tenant_mail_creds(tenant) -> dict | None:
    alias = getattr(tenant, "mail_secret_id", None)
    if not alias: return None
    p = _path(alias)
    if not os.path.isfile(p): return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

# ---------------- Plataforma SMTP (inalterado) ----------------
def get_platform_mail_creds() -> dict | None:
    host = _getenv("PLATFORM_SMTP_HOST")
    if not host: return None
    port = int(_getenv("PLATFORM_SMTP_PORT", 0) or 0)
    user = _getenv("PLATFORM_SMTP_USER") or ""
    password = (_getenv("PLATFORM_SMTP_PASS") or "").replace(" ", "")
    use_tls = _as_bool(_getenv("PLATFORM_SMTP_TLS", "1"))
    use_ssl = _as_bool(_getenv("PLATFORM_SMTP_SSL", "0"))
    return {
        "host": host, "port": port, "user": user, "password": password,
        "use_tls": bool(use_tls and not use_ssl), "use_ssl": bool(use_ssl), "provider": "platform-smtp",
    }

# ---------------- ACS helpers ----------------
_ACS_CONN: Optional[str] = None
_EMAIL_FROM: Optional[str] = None
_ACS_CLIENT: Optional["EmailClient"] = None

def _acs_enabled() -> bool:
    global _ACS_CONN, _EMAIL_FROM
    if not _ACS_AVAILABLE: return False
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

def _normalize_emails(to: str) -> list[dict]:
    return [{"address": e.strip()} for e in (to or "").split(",") if e.strip()]

def _send_via_acs(*, subject: str, html: str, text: str, to: str, reply_to: str | None = None) -> str:
    """
    Envia por ACS (SDK 1.0.x): payload em dict.
    """
    client = _acs_client()
    message = {
        "senderAddress": _EMAIL_FROM,
        "content": {
            "subject": subject or "",
            "plainText": text or "",
            "html": html or "",
        },
        "recipients": {"to": _normalize_emails(to)},
    }
    if reply_to:
        message["replyTo"] = [{"address": reply_to}]
    try:
        poller = client.begin_send(message)  # type: ignore
        result = poller.result()
        msg_id = getattr(result, "message_id", "") or getattr(result, "messageId", "") or ""
        _log("info", "[PLATFORM EMAIL/ACS] To=%s Subject=%s MsgId=%s", to, subject, msg_id)
        return msg_id
    except HttpResponseError as e:  # type: ignore
        raise RuntimeError(f"Falha ao enviar e-mail (ACS): {getattr(e, 'message', str(e))}") from e
    except Exception as e:
        raise RuntimeError(f"Erro inesperado ao enviar e-mail (ACS): {e}") from e

# ---------------- SMTP baixo nível ----------------
def _stamp_headers(msg: EmailMessage, from_domain_hint: str | None = None):
    # Cabeçalhos que ajudam reputação
    if "Date" not in msg:
        msg["Date"] = formatdate(localtime=True)
    if "Message-ID" not in msg:
        msg["Message-ID"] = make_msgid(domain=from_domain_hint)

def _smtp_send(cfg: dict, msg: EmailMessage):
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 0)
    user = (cfg.get("user") or "").strip()
    password = (cfg.get("password") or "").strip()
    use_tls = bool(cfg.get("use_tls"))
    use_ssl = bool(cfg.get("use_ssl"))
    if not host: raise RuntimeError("SMTP host vazio.")
    # aplica Date e Message-ID se faltar
    from_domain = None
    try:
        from_domain = (msg.get("From") or "").split("<")[-1].split(">")[0].split("@")[-1].strip() or None
    except Exception:
        from_domain = None
    _stamp_headers(msg, from_domain)

    if use_ssl:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port or 465, context=ctx, timeout=25) as s:
            if user and password: s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port or 587, timeout=25) as s:
            s.ehlo()
            if use_tls:
                s.starttls(context=ssl.create_default_context()); s.ehlo()
            if user and password: s.login(user, password)
            s.send_message(msg)

# ---------------- Plataforma pública (ACS -> SMTP -> MOCK) ----------------
def send_platform_mail_html(*, subject: str, html: str, to: str, text_alt: str = "") -> bool:
    if _acs_enabled():
        try:
            _send_via_acs(
                subject=subject, html=html, text=text_alt, to=to,
                reply_to=_getenv("PLATFORM_REPLY_TO", "support@4ufleet.com")
            )
            return True
        except Exception as e:
            _log("error", "Falha no envio via ACS; caindo para SMTP. Err=%s", e)

    cfg = get_platform_mail_creds()
    if not cfg:
        _log("warning", "[EMAIL MOCK] (platform) To=%s Subject=%s (sem ACS e sem PLATFORM_SMTP_HOST)", to, subject)
        return False

    from_name  = _getenv("PLATFORM_MAIL_FROM_NAME", _getenv("APP_NAME", "Car Rental SaaS"))
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

# ---------------- Utilitários/tenant/compat ----------------
def send_test_mail(*, cfg: dict, subject: str, body: str, from_name: str, from_email: str, to_email: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.set_content(body)
    _smtp_send(cfg, msg)

def send_tenant_mail_html(*, tenant, subject: str, html: str, to: str, text_alt: str = "") -> bool:
    cfg = get_tenant_mail_creds(tenant)
    if not cfg:
        _log("info", "[EMAIL MOCK] (tenant=%s) To=%s Subject=%s", getattr(tenant, 'slug','?'), to, subject)
        return False

    from_name  = getattr(tenant, "mail_from_name", None) or getattr(tenant, "name", None) or "Locadora"
    from_email = getattr(tenant, "mail_from_email", None) or cfg.get("user") or _getenv("EMAIL_FROM") or "no-reply@example.com"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to
    if text_alt:
        msg.set_content(text_alt)
    msg.add_alternative(html, subtype="html")

    _smtp_send(cfg, msg)
    _log("info", "[TENANT EMAIL/SMTP] tenant=%s To=%s Subject=%s", getattr(tenant,'slug','?'), to, subject)
    return True

def send_mail_auto(*, tenant, subject: str, html: str, to: str, text_alt: str = "") -> bool:
    try:
        if send_tenant_mail_html(tenant=tenant, subject=subject, html=html, to=to, text_alt=text_alt):
            return True
    except Exception as e:
        _log("error","Falha SMTP do tenant; tentando plataforma. Err=%s", e)
    try:
        return bool(send_platform_mail_html(subject=subject, html=html, to=to, text_alt=text_alt))
    except Exception as e:
        _log("error","Falha no envio via plataforma. Err=%s", e)
    return False

def send_email_for_tenant(tenant, recipients, subject: str, html: str, text_alt: str = "") -> bool:
    to = ", ".join(r for r in recipients if r) if isinstance(recipients,(list,tuple,set)) else recipients
    return send_mail_auto(tenant=tenant, subject=subject, html=html, to=to, text_alt=text_alt)
