# app/auth/routes.py
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
from app.utils import absolute_url_for  # usa EXTERNAL_BASE_URL se houver
from . import auth_bp

from werkzeug.security import generate_password_hash
from email.message import EmailMessage
import smtplib


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
# Tokens de CONFIRMAÇÃO DE E-MAIL (boas-vindas/ativação)
# -----------------------------------------------------------------------------#
def _email_serializer() -> URLSafeTimedSerializer:
    salt = current_app.config.get("SECURITY_EMAIL_SALT", "email-confirm")
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def make_confirm_token(user: User) -> str:
    data = {"uid": user.id, "tenant": user.tenant.slug}
    return _email_serializer().dumps(data)


def load_confirm_token(token: str, max_age_seconds: int = 60 * 60 * 24 * 3) -> dict:
    return _email_serializer().loads(token, max_age=max_age_seconds)


def _send_confirmation_email(tenant: Tenant, user: User) -> bool:
    """
    Envia o e-mail de boas-vindas/ativação **pela PLATAFORMA**.
    Retorna True se o envio foi realizado (ACS/SMTP); False se ficou em modo MOCK.
    Lança exceção apenas em erro real de envio.
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

    # Retorna o bool do mailer (True= enviado; False = mock / não configurado)
    return bool(
        send_platform_mail_html(
            subject=subject,
            html=html,
            to=user.email,
            text_alt=text_alt,
        )
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
        if getattr(tenant, "is_blocked", False):
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

            # registra ultimo login e ultima atividade do tenant
            user.last_login_at = datetime.utcnow()
            if hasattr(user, "tenant") and user.tenant:
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
    email = _get_email_from_request()
    if not email:
        return jsonify(ok=False, error="Informe o e-mail."), 400

    tenant = g.tenant
    user = User.query.filter_by(email=email, tenant_id=tenant.id).first()
    if not user:
        return jsonify(ok=False, error="Usuário não encontrado."), 404
    if getattr(user, "email_confirmed_at", None):
        return jsonify(ok=True, already=True)

    try:
        sent = _send_confirmation_email(tenant, user)  # agora retorna bool
        if not sent:
            # Sem ACS/SMTP configurado → modo mock: avisa o front
            return jsonify(ok=False, error="Serviço de e-mail não configurado."), 500
        return jsonify(ok=True)
    except Exception as e:
        current_app.logger.exception("Falha ao reenviar confirmação")
        return jsonify(ok=False, error=str(e)), 500


# -----------------------------------------------------------------------------#
# Tokens de RESET DE SENHA
# -----------------------------------------------------------------------------#
def _reset_serializer():
    secret = current_app.config.get("SECRET_KEY")
    salt = current_app.config.get("SECURITY_PASSWORD_SALT", "password-reset-salt")
    return URLSafeTimedSerializer(secret_key=secret, salt=salt)


def _send_email(to_email: str, subject: str, html: str):
    """
    Envio SMTP simples usando configs no current_app.config:
      MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD,
      MAIL_USE_TLS (bool), MAIL_USE_SSL (bool), MAIL_DEFAULT_SENDER
    """
    server = current_app.config.get("MAIL_SERVER")
    port = int(current_app.config.get("MAIL_PORT", 587))
    username = current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD")
    use_tls = bool(current_app.config.get("MAIL_USE_TLS", True))
    use_ssl = bool(current_app.config.get("MAIL_USE_SSL", False))
    sender = current_app.config.get("MAIL_DEFAULT_SENDER") or username

    if not all([server, port, username, password, sender]):
        current_app.logger.warning("SMTP não configurado completamente; email não foi enviado.")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content("Para redefinir sua senha, abra o link em um cliente que suporte HTML.")
    msg.add_alternative(html, subtype="html")

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(server, port) as s:
                s.login(username, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(server, port) as s:
                if use_tls:
                    s.starttls()
                s.login(username, password)
                s.send_message(msg)
        return True
    except Exception as e:
        current_app.logger.exception(f"Falha ao enviar email: {e}")
        return False


# -----------------------------------------------------------------------------#
# ESQUECI A SENHA (FORGOT)
# -----------------------------------------------------------------------------#
@auth_bp.route("/forgot", methods=["GET", "POST"], endpoint="forgot_password")
def forgot_password():
    tenant = g.tenant

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email:
            flash("Informe seu e-mail.", "warning")
            return render_template("auth/forgot.html", tenant=tenant)

        user = User.query.filter(
            User.email.ilike(email),
            User.tenant_id == tenant.id
        ).first()

        # Mesmo se não existir, não revelar — sempre responder sucesso
        if user:
            token = _reset_serializer().dumps({"email": user.email, "tid": tenant.id})
            reset_url = url_for("auth.reset_password", tenant_slug=tenant.slug, token=token, _external=True)
            # OBS: verifique o caminho do seu template de e-mail: "email/" ou "emails/"
            html = render_template("email/reset_password.html", tenant=tenant, user=user, reset_url=reset_url)
            _send_email(
                to_email=user.email,
                subject=f"Redefinição de senha — {tenant.name or '4uFleet'}",
                html=html
            )

        flash("Se encontramos sua conta, enviamos um e-mail com o link para redefinir a senha.", "info")
        return redirect(url_for("auth.login", tenant_slug=tenant.slug))

    return render_template("auth/forgot.html", tenant=tenant)


# -----------------------------------------------------------------------------#
# REDEFINIR SENHA (RESET)
# -----------------------------------------------------------------------------#
@auth_bp.route("/reset/<token>", methods=["GET", "POST"], endpoint="reset_password")
def reset_password(token):
    tenant = g.tenant

    # Validar token
    try:
        data = _reset_serializer().loads(token, max_age=3600)  # 1 hora de validade
        email = data.get("email")
        tid = data.get("tid")
    except SignatureExpired:
        flash("Link expirado. Solicite uma nova redefinição.", "warning")
        return redirect(url_for("auth.forgot_password", tenant_slug=tenant.slug))
    except BadSignature:
        flash("Link inválido. Solicite uma nova redefinição.", "danger")
        return redirect(url_for("auth.forgot_password", tenant_slug=tenant.slug))

    if tid != tenant.id:
        flash("Token não corresponde ao tenant.", "danger")
        return redirect(url_for("auth.forgot_password", tenant_slug=tenant.slug))

    user = User.query.filter(
        User.email.ilike(email),
        User.tenant_id == tenant.id
    ).first_or_404()

    if request.method == "POST":
        pwd = request.form.get("password") or ""
        pwd2 = request.form.get("password2") or ""
        if len(pwd) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "warning")
            return render_template("auth/reset_password.html", tenant=tenant)
        if pwd != pwd2:
            flash("As senhas não conferem.", "warning")
            return render_template("auth/reset_password.html", tenant=tenant)

        user.password_hash = generate_password_hash(pwd)
        db.session.commit()
        flash("Senha redefinida com sucesso. Faça login.", "success")
        return redirect(url_for("auth.login", tenant_slug=tenant.slug))

    return render_template("auth/reset_password.html", tenant=tenant)
