# app/site/routes.py
from __future__ import annotations

import os, re, unicodedata
from datetime import datetime
from pathlib import Path

from flask import (
    render_template, request, redirect, url_for, flash, current_app, jsonify
)
from werkzeug.utils import secure_filename
from urllib.parse import quote

from app.extensions import db
from app.models import Tenant, User, Prospect  # Prospects para CRM
from . import site_bp  # blueprint criado em app/site/__init__.py

# helper que monta e envia o e-mail de confirmação (plataforma: ACS/SMTP)
from app.auth.routes import _send_confirmation_email
from app.services.mailer import send_platform_mail_html

from sqlalchemy import and_, or_, exists, not_
from sqlalchemy.orm import joinedload

from app.models import Vehicle, VehicleShare, Reservation


# -------------------- Utils locais --------------------
def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    value = re.sub(r"-{2,}", "-", value)
    return value or "empresa"


def _branding_dir(slug: str) -> Path:
    # static/uploads/branding/<slug>/
    base = Path(current_app.root_path).parent / "static" / "uploads" / "branding" / slug
    base.mkdir(parents=True, exist_ok=True)
    return base


def _save_logo(file_storage, slug: str) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    fn = secure_filename(file_storage.filename)
    ext = (os.path.splitext(fn)[1] or "").lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        raise ValueError("Formato de logo não suportado.")
    name = "logo" + ext
    dest = _branding_dir(slug) / name
    file_storage.save(dest)
    # caminho relativo a /static
    return f"uploads/branding/{slug}/{name}".replace("\\", "/")

@site_bp.get("/")
@site_bp.get("/home")
def home():
    return render_template("site/home.html")

@site_bp.get("/lang/<code>")
def set_lang(code):
    from flask import current_app
    langs = current_app.config["LANGUAGES"]
    if code in langs:
        session["lang"] = code
    # redireciona para onde o usuário estava
    dest = request.referrer or url_for("site.home")
    return redirect(dest)



# -------------------- LANDING --------------------
@site_bp.route("/landing", methods=["GET"], strict_slashes=False)
def landing():
    # hide_chrome -> esconde topbar/sidebar só nesta página
    return render_template("site/landing.html", hide_chrome=True)


# -------------------- SIGNUP --------------------
@site_bp.get("/signup")
def signup_form():
    return render_template("site/signup.html", hide_chrome=True)


@site_bp.post("/signup")
def signup_submit():
    name  = (request.form.get("company_name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    pwd   = request.form.get("password") or ""
    pwd2  = request.form.get("password2") or ""
    terms = bool(request.form.get("terms"))

    if not name or not email or not pwd:
        flash("Preencha nome, e-mail e senha.", "warning")
        return redirect(url_for("site.signup_form"))
    if pwd != pwd2:
        flash("As senhas não conferem.", "warning")
        return redirect(url_for("site.signup_form"))
    if not terms:
        flash("É necessário aceitar os Termos de Uso e a Política de Privacidade.", "warning")
        return redirect(url_for("site.signup_form"))

    # slug único
    base = slugify(name)
    slug = base
    i = 2
    while Tenant.query.filter_by(slug=slug).first():
        slug = f"{base}-{i}"
        i += 1

    # === cria tenant com defaults do Login Hero ===
    t = Tenant(
        name=name,
        slug=slug,
        terms_accepted_at=datetime.utcnow(),
        # defaults do hero (pode editar depois em Configurações)
        login_hero_enabled=True,
        login_hero_kicker="4uFLEET • MULTIFROTA",
        login_hero_title="Carros, motos, vans, barcos e mais — tudo pronto para alugar.",
        login_hero_desc=(
            "Centralize reservas, tarifas e manutenção em um único painel. "
            "Aumente a ocupação da sua frota com automações e relatórios em tempo real."
        ),
        # caminho padrão (se tiver CDN/Blob, ajuste aqui)
        login_hero_image=f"https://st4ufleetprd.blob.core.windows.net/branding/{slug}/login-hero.jpg",
    )
    db.session.add(t)
    db.session.flush()  # t.id disponível

    # salva logo (opcional)
    try:
        logo_rel = _save_logo(request.files.get("logo_file"), slug)
        if logo_rel:
            t.logo_path = logo_rel
    except ValueError as e:
        db.session.rollback()
        flash(str(e), "warning")
        return redirect(url_for("site.signup_form"))

    # usuário admin
    if User.query.filter_by(email=email).first():
        db.session.rollback()
        flash("Já existe um usuário com esse e-mail.", "warning")
        return redirect(url_for("site.signup_form"))

    u = User(tenant_id=t.id, email=email, is_admin=True)
    u.set_password(pwd)
    db.session.add(u)
    db.session.commit()

    # === dispara e-mail de confirmação imediatamente ===
    try:
        # _send_confirmation_email pode retornar True/False/None.
        sent = _send_confirmation_email(t, u)
        status = "SENT" if (sent is True or sent is None) else "NOT_SENT"
        current_app.logger.info(
            "signup: confirmation email %s tenant=%s user=%s",
            status, t.slug, u.email
        )
        if sent is False:
            # plataforma ficou em MOCK: avisa de forma amigável sem quebrar o fluxo
            flash("Ambiente criado. O e-mail de confirmação será enviado em instantes.", "info")
    except Exception as e:
        # não quebra o fluxo do signup; apenas registra
        current_app.logger.exception(
            "signup: confirmation email FAILED tenant=%s user=%s error=%s",
            t.slug, u.email, e
        )

    flash("Ambiente criado! Verifique seu e-mail para confirmar e depois faça login.", "success")
    return redirect(url_for("auth.login", tenant_slug=slug))


# -------------------- Páginas estáticas --------------------
@site_bp.get("/terms")
def terms():
    return render_template("site/terms.html", hide_chrome=True)

@site_bp.get("/privacy")
def privacy():
    return render_template("site/privacy.html", hide_chrome=True)


# --- CTA da landing: capta nome/telefone/email e envia e-mail com link de signup
@site_bp.post("/pre-signup")
def pre_signup():
    """
    Capta lead da landing (Pré-Signup), grava no CRM (Prospect) e envia e-mail com link /signup.
    Também notifica opcionalmente a equipe comercial (config PLATFORM_SALES_EMAIL).
    """
    name  = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    email = (request.form.get("email") or "").strip().lower()

    # validação simples
    if not name or not email or "@" not in email:
        return jsonify(ok=False, error="Informe nome e um e-mail válido."), 400

    # 1) Persistência no CRM (Prospects)
    try:
        p = Prospect(
            name=name,
            email=email,
            phone=phone,
            source="landing_pre_signup",
            status="new",
        )
        db.session.add(p)
        db.session.commit()
        current_app.logger.info("[pre-signup] prospect saved id=%s email=%s", p.id, p.email)
    except Exception:
        current_app.logger.exception("Falha ao salvar Prospect (pre-signup)")
        # segue para e-mail mesmo assim

    # 2) Link de signup com campos pré-preenchidos
    signup_url = url_for("site.signup_form", _external=True) + \
                 f"?name={quote(name)}&email={quote(email)}&phone={quote(phone)}"

    # 3) E-mail de boas-vindas (sempre via plataforma)
    subject = "Bem-vindo à 4uFleet — crie sua locadora em minutos"
    html = render_template(
        "emails/welcome_presignup.html",
        name=name,
        signup_url=signup_url,
        phone=phone,
        brand=current_app.config.get("PUBLIC_BRAND", "4uFleet"),
    )
    text_alt = (
        f"Olá {name}!\n\nObrigado pelo interesse.\n"
        f"Crie sua locadora agora: {signup_url}\n\n"
        "Leva poucos minutos. Qualquer dúvida, responda este e-mail."
    )

    try:
        send_platform_mail_html(subject=subject, html=html, to=email, text_alt=text_alt)
        current_app.logger.info("[pre-signup] lead mailed=%s phone=%s", email, phone)
    except Exception:
        current_app.logger.exception("Falha ao enviar e-mail de pre-signup")
        return jsonify(ok=False, error="Não foi possível enviar o e-mail agora."), 500

    # 4) Notificação opcional ao time comercial
    sales_to = current_app.config.get("PLATFORM_SALES_EMAIL")
    if sales_to:
        try:
            sales_html = f"""
            <h3>Novo pré-cadastro (Landing)</h3>
            <p><b>Nome:</b> {name}<br>
               <b>E-mail:</b> {email}<br>
               <b>Telefone:</b> {phone or '-'}<br>
               <b>Fonte:</b> landing_pre_signup</p>
            """
            send_platform_mail_html(
                subject="Novo lead — Pré-signup (Landing)",
                html=sales_html,
                to=sales_to,
                text_alt=f"Novo lead: {name} | {email} | {phone or '-'} | fonte: landing_pre_signup"
            )
        except Exception:
            current_app.logger.exception("Falha ao notificar equipe comercial (pre-signup)")

    return jsonify(ok=True)


# ====== PASSO 5: Busca pública com veículos compartilhados ======

def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None

def get_available_vehicles_for_tenant(
    tenant_id: int,
    start_dt: datetime | None,
    end_dt: datetime | None,
    min_seats: int | None = None,
    category_id: int | None = None
):
    """
    Retorna uma query com veículos disponíveis para o tenant:
    - Veículos do próprio tenant
    - Veículos compartilhados ativamente com o tenant (VehicleShare.active = TRUE)
    Aplica filtro de disponibilidade via subconsulta em Reservation (agenda única por vehicle_id).
    """
    # 1) Conjunto-base: veículos do tenant OU compartilhados com ele
    #    (sem duplicar veículos; agenda é única por vehicle_id)
    q_own = db.session.query(Vehicle.id).filter(Vehicle.tenant_id == tenant_id)

    q_shared = (
        db.session.query(Vehicle.id)
        .join(VehicleShare, VehicleShare.vehicle_id == Vehicle.id)
        .filter(
            VehicleShare.shared_with_tenant_id == tenant_id,
            VehicleShare.active.is_(True),
        )
    )

    # unify (ids)
    sub_ids = q_own.union(q_shared).subquery()

    # 2) Query principal sobre Vehicle usando os IDs unificados
    q = (
        db.session.query(Vehicle)
        .options(joinedload(Vehicle.category))
        .filter(Vehicle.id.in_(db.session.query(sub_ids.c.id)))
        .filter(Vehicle.active.is_(True))
    )

    # 3) Filtros opcionais (assentos, categoria)
    if min_seats:
        # ajuste o campo conforme seu model (ex.: Vehicle.seats)
        q = q.filter(getattr(Vehicle, "seats", None) >= min_seats)
    if category_id:
        q = q.filter(getattr(Vehicle, "category_id", None) == category_id)

    # 4) Filtro de disponibilidade por data (quando informado)
    #    Remove veículos que tenham alguma reserva que COLIDA com [start_dt, end_dt]
    #    Overlap: (res.start <= end) AND (res.end >= start)
    if start_dt and end_dt and end_dt >= start_dt:
        overlap_exists = (
            db.session.query(Reservation.id)
            .filter(Reservation.vehicle_id == Vehicle.id)
            .filter(
                and_(
                    Reservation.start_date <= end_dt,
                    Reservation.end_date >= start_dt,
                )
            )
            # opcional: filtre só status que bloqueiam (ajuste para seus valores)
            # .filter(Reservation.status.in_(["confirmed", "paid", "booked"]))
        ).exists()

        q = q.filter(not_(overlap_exists))

    # 5) Ordenação amigável
    q = q.order_by(Vehicle.tenant_id, Vehicle.name.asc())

    return q

# --------- ROTA PÚBLICA EXEMPLO (use se não tiver uma) ---------
@site_bp.route("/search", methods=["GET"])
def site_search():
    # captura parâmetros
    pickup_str = request.args.get("pickup")      # ex.: 2025-09-30
    dropoff_str = request.args.get("dropoff")    # ex.: 2025-10-05
    seats_str = request.args.get("seats")        # ex.: "7"
    category_str = request.args.get("category")  # opcional

    start_dt = _parse_date(pickup_str)
    end_dt = _parse_date(dropoff_str)
    min_seats = int(seats_str) if seats_str and seats_str.isdigit() else None
    category_id = int(category_str) if category_str and category_str.isdigit() else None

    # tenant atual do site público
    tenant = g.tenant  # já usado no seu app; assume que o middleware define g.tenant
    if not tenant:
        abort(404)

    vehicles_q = get_available_vehicles_for_tenant(
        tenant_id=tenant.id,
        start_dt=start_dt,
        end_dt=end_dt,
        min_seats=min_seats,
        category_id=category_id
    )

    vehicles = vehicles_q.all()

    # Renderize com seu template público de catálogo/lista (ajuste o nome se necessário)
    return render_template(
        "site/search_results.html",
        vehicles=vehicles,
        pickup=pickup_str,
        dropoff=dropoff_str,
        seats=min_seats,
        category_id=category_id,
        tenant=tenant
    )
