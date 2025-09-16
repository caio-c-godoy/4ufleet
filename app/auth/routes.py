# app/auth/routes.py
#atualizado
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from flask import (
    render_template, request, redirect, url_for, flash,
    g, abort, current_app, jsonify
)
from flask_login import (
    login_user, logout_user, login_required, current_user
)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.extensions import db
from app.models import Tenant, User
from app.services.mailer import send_platform_mail_html  # plataforma SEMPRE para confirmação
from app.utils import absolute_url_for  # <- usa EXTERNAL_BASE_URL se houver
from . import auth_bp


# -----------------------------------------------------------------------------#
# Helpers de tenant no blueprint
# -----------------------------------------------------------------------------#
@auth_bp.url_value_preprocessor
def pull_tenant(endpoint, values):
    if values is None:
        return
    g.tenant_slug = values.pop("tenant_slug", None)


@auth_bp.url_defaults
def add_tenant_slug(endpoint, values):
    if "tenant_slug" in values or not getattr(g, "tenant_slug", None):
        return
    values["tenant_slug"] = g.tenant_slug


_ALLOW_WITHOUT_TENANT = set()  # nenhuma rota global aqui


@auth_bp.before_request
def load_tenant():
    endpoint = (request.endpoint or "").split(".")[-1]
    if endpoint in _ALLOW_WITHOUT_TENANT:
        g.tenant = None
        return
    slug = getattr(g, "tenant_slug", None)
    if not slug:
        abort(404)
    g.tenant = Tenant.query.filter_by(slug=slug).first_or_404()


# -----------------------------------------------------------------------------#
# Tokens de confirmação de e-mail
# -----------------------------------------------------------------------------#
def _serializer() -> URLSafeTimedSerializer:
    salt = current_app.config.get("SECURITY_EMAIL_SALT", "email-confirm")
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def make_confirm_token(user: User) -> str:
    data = {"uid": user.id, "tenant": user.tenant.slug}
    return _serializer().dumps(data)


def load_confirm_token(token: str, max_age_seconds: int = 60 * 60 * 24 * 3) -> dict:
    return _serializer().loads(token, max_age=max_age_seconds)


def _send_confirmation_email(tenant: Tenant, user: User) -> None:
    """
    Envia e-mail de boas-vindas/ativação SEMPRE pelo SMTP da PLATAFORMA (.env).
    O link é gerado com EXTERNAL_BASE_URL (se existir).
    """
    token = make_confirm_token(user)
    confirm_url = absolute_url_for(
        "auth.verify_email",
        tenant_slug=tenant.slug,
        token=token,
    )

    subject = f"{tenant.name or tenant.slug} — confirme seu e-mail"
    html = render_template(
        "emails/welcome_confirm.html",
        tenant=tenant,
        user=user,
        confirm_url=confirm_url,
    )
    text_alt = f"Bem-vindo(a)! Confirme seu e-mail para ativar seu acesso: {confirm_url}"

    # plataforma (não depende do SMTP do tenant)
    send_platform_mail_html(
        subject=subject,
        html=html,
        to=user.email,
        text_alt=text_alt,
    )


# -----------------------------------------------------------------------------#
# Utils
# -----------------------------------------------------------------------------#
def _get_email_from_request() -> str:
    """Pega 'email' de form, JSON ou query string, normalizando."""
    payload_json = request.get_json(silent=True) or {}
    email = (
        request.form.get("email")
        or payload_json.get("email")
        or request.args.get("email")
    )
    return (email or "").strip().lower()


# -----------------------------------------------------------------------------#
# LOGIN / LOGOUT
# -----------------------------------------------------------------------------#
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login do usuário do tenant (exige e-mail confirmado)."""
    tenant: Optional[Tenant] = g.get("tenant")

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        next_url = request.args.get("next") or url_for("admin.dashboard")

        if not tenant:
            flash("Tenant inválido.", "danger")
            return redirect("/")

        # se o tenant estiver bloqueado, não deixa logar
        if tenant.is_blocked:
            flash("Este ambiente está temporariamente bloqueado. Fale com o suporte.", "warning")
            return render_template("auth/login.html", tenant=tenant)

        user = User.query.filter_by(email=email, tenant_id=tenant.id).first()
        if user and user.check_password(password):
            if not getattr(user, "email_confirmed_at", None):
                current_app.logger.info("login: pending confirmation tenant=%s email=%s", tenant.slug, email)
                flash("Confirme seu e-mail para continuar. Você pode reenviar o link.", "warning")
                return render_template(
                    "auth/login.html",
                    pending_confirmation=True,
                    email=email,
                    tenant=tenant,
                )

            # ✅ registra ultimo login e ultima atividade do tenant
            user.last_login_at = datetime.utcnow()
            user.tenant.last_activity_at = datetime.utcnow()
            db.session.commit()

            login_user(user)
            current_app.logger.info("login: success tenant=%s user_id=%s email=%s", tenant.slug, user.id, email)
            flash("Bem-vindo!", "success")
            return redirect(url_for("admin.dashboard", tenant_slug=tenant.slug))

        current_app.logger.info("login: invalid creds tenant=%s email=%s", tenant.slug if tenant else "?", email)
        flash("Credenciais inválidas.", "danger")

    return render_template("auth/login.html", tenant=tenant)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Você saiu da sessão.", "info")
    return redirect(url_for("auth.login"))


# -----------------------------------------------------------------------------#
# CONFIRMAÇÃO DE E-MAIL
# -----------------------------------------------------------------------------#
@auth_bp.get("/verify")
def verify_email():
    token = request.args.get("token", "")
    if not token:
        flash("Link inválido.", "danger")
        return redirect(url_for("auth.login"))

    try:
        data = load_confirm_token(token)
        uid = int(data.get("uid"))
        slug = str(data.get("tenant"))
    except (BadSignature, SignatureExpired, Exception):
        flash("Link de confirmação inválido ou expirado.", "danger")
        return redirect(url_for("auth.login"))

    if slug != g.tenant.slug:
        flash("Este link não pertence a este ambiente.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(id=uid, tenant_id=g.tenant.id).first()
    if not user:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("auth.login"))

    if not getattr(user, "email_confirmed_at", None):
        user.email_confirmed_at = datetime.now(timezone.utc)
        db.session.add(user)
        db.session.commit()

    return render_template("auth/verified.html", tenant=g.tenant)


@auth_bp.post("/resend-confirmation")
def resend_confirmation():
    """
    Reenvia o e-mail de confirmação — também pela PLATAFORMA (.env).
    Aceita email via form, JSON ou query string.
    """
    email = _get_email_from_request()

    current_app.logger.info(
        "resend-confirmation: tenant=%s email_in='%s' form=%s json=%s args=%s",
        g.tenant.slug if hasattr(g, "tenant") and g.tenant else "?",
        email,
        dict(request.form),
        (request.get_json(silent=True) or {}),
        dict(request.args),
    )

    if not email:
        return jsonify(ok=False, error="Informe o e-mail."), 400

    tenant = g.tenant
    user = User.query.filter_by(email=email, tenant_id=tenant.id).first()
    if not user:
        # Mantém contrato atual (404) para não “quebrar” seu front.
        return jsonify(ok=False, error="Usuário não encontrado."), 404

    if getattr(user, "email_confirmed_at", None):
        return jsonify(ok=True, already=True)

    try:
        _send_confirmation_email(tenant, user)
        current_app.logger.info("resend-confirmation: sent tenant=%s email=%s", tenant.slug, email)
        return jsonify(ok=True)
    except Exception as e:
        current_app.logger.exception("Falha ao reenviar confirmação")
        return jsonify(ok=False, error=str(e)), 500
