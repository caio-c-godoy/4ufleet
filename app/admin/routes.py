# app/admin/routes.py
from datetime import datetime, timedelta, date
import os
import re
import unicodedata
from uuid import uuid4
from pathlib import Path
from . import admin_bp
from sqlalchemy.exc import IntegrityError
from jinja2.sandbox import SandboxedEnvironment
import base64, io
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm.attributes import InstrumentedAttribute
from weasyprint import HTML
import json
from app.services.mailer import save_tenant_mail_creds, get_tenant_mail_creds, send_test_mail
from . import admin_bp


from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.identity import DefaultAzureCredential

from flask import (
    render_template, request, redirect, url_for, flash,
    g, abort, jsonify, current_app, send_file
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from sqlalchemy import (
    select, func, and_, or_,
    MetaData, Table, Column, Integer, String, DateTime, Boolean, Text
)

from app.extensions import db
from app.models import (
    Tenant, VehicleCategory, Rate, Vehicle, Reservation, Lead, OperatorChecklist
)

# pega o blueprint já criado em app/admin/__init__.py
from . import admin_bp

# >>> NOVO: serviço para salvar credenciais no Key Vault
from app.services.payments import save_tenant_payment_creds


# =============================================================================
# Helpers multitenant: captura <tenant_slug> e injeta automaticamente no url_for
# =============================================================================
@admin_bp.url_value_preprocessor
def _pull_tenant(endpoint, values):
    if values is None:
        return
    g.tenant_slug = values.pop("tenant_slug", None)

@admin_bp.url_defaults
def _add_tenant(endpoint, values):
    if "tenant_slug" in values:
        return
    if getattr(g, "tenant_slug", None):
        values["tenant_slug"] = g.tenant_slug

@admin_bp.before_request
def _load_tenant():
    slug = getattr(g, "tenant_slug", None)
    if not slug:
        abort(404)
    g.tenant = Tenant.query.filter_by(slug=slug).first_or_404()


# =============================================================================
# Utilidades
# =============================================================================
def simple_slugify(value: str) -> str:
    value = value or ""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-").lower()
    return value or "item"

def _res_table():
    """Tabela leve para requests públicos (se utilizada no dashboard)."""
    meta = MetaData()
    t = Table(
        "reservation_requests", meta,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer, index=True, nullable=False),
        Column("vehicle_id", Integer),
        Column("name", String(120)),
        Column("email", String(120)),
        Column("phone", String(60)),
        Column("pickup_airport", String(80)),
        Column("dropoff_airport", String(80)),
        Column("pickup_at", DateTime),
        Column("dropoff_at", DateTime),
        Column("created_at", DateTime, default=datetime.utcnow),
    )
    meta.create_all(db.engine)
    return t

def _maint_table():
    """Tabela de manutenção sem depender de model/migration."""
    meta = MetaData()
    t = Table(
        "vehicle_maintenance", meta,
        Column("id", Integer, primary_key=True),
        Column("tenant_id", Integer, index=True, nullable=False),
        Column("vehicle_id", Integer, index=True, nullable=False),
        Column("reason", Text),
        Column("started_at", DateTime, default=datetime.utcnow),
        Column("ended_at", DateTime),
        Column("active", Boolean, default=True, index=True),
    )
    meta.create_all(db.engine)
    return t

def _uploads_dir():
    static_root = os.path.abspath(os.path.join(current_app.root_path, "..", "static"))
    path = os.path.join(static_root, "uploads", "vehicles")
    os.makedirs(path, exist_ok=True)
    return path

def _save_vehicle_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    fn = secure_filename(file_storage.filename)
    base, ext = os.path.splitext(fn)
    new_name = f"{uuid.uuid4().hex}{ext.lower()}"
    full_path = os.path.join(_uploads_dir(), new_name)
    file_storage.save(full_path)
    return f"uploads/vehicles/{new_name}"

def _delete_vehicle_image(image_url):
    if not image_url:
        return
    static_root = os.path.abspath(os.path.join(current_app.root_path, "..", "static"))
    full_path = os.path.join(static_root, image_url.replace("/", os.sep))
    try:
        if os.path.isfile(full_path):
            os.remove(full_path)
    except Exception:
        pass

def _recompute_vehicle_status(vehicle):
    """Marca como 'booked' se tem reserva confirmada futura, senão 'available'."""
    now = datetime.utcnow()
    has_active = (
        Reservation.query
        .filter_by(tenant_id=g.tenant.id, vehicle_id=vehicle.id, status="confirmed")
        .filter(Reservation.dropoff_dt > now)
        .count() > 0
    )
    vehicle.status = "booked" if has_active else "available"
    db.session.commit()

# Branding paths
def _branding_dir() -> Path:
    p = Path(current_app.root_path).parent / "static" / "uploads" / "branding" / g.tenant.slug
    p.mkdir(parents=True, exist_ok=True)
    return p

def _save_logo(file_storage):
    if not file_storage or file_storage.filename == "":
        return None
    ext = (file_storage.filename.rsplit(".", 1)[-1] or "").lower()
    if ext not in ("png", "jpg", "jpeg", "webp", "svg"):
        raise ValueError("Formato de logo não suportado.")
    dest = _branding_dir() / f"logo.{ext}"
    file_storage.save(dest)
    # caminho relativo a /static
    rel = Path("uploads") / "branding" / g.tenant.slug / dest.name
    return str(rel).replace("\\", "/")


# ====== Armazenamento de imagens (Azure Blob + fallback local) ==================

def _blob_clients():
    """
    Tenta criar clients para Storage via:
    1) Connection string (AZURE_STORAGE_CONN), ou
    2) Managed Identity (DefaultAzureCredential) + AZURE_STORAGE_ACCOUNT.
    Retorna (container_client, container_name, base_url) ou (None, None, None) para fallback local.
    """
    import os
    conn = os.getenv("AZURE_STORAGE_CONN")
    account = os.getenv("AZURE_STORAGE_ACCOUNT")
    container = os.getenv("AZURE_STORAGE_CONTAINER", "vehicles")
    base_url = os.getenv("AZURE_BLOB_BASE_URL") or (f"https://{account}.blob.core.windows.net" if account else None)

    try:
        if conn:
            svc = BlobServiceClient.from_connection_string(conn)
            return svc.get_container_client(container), container, (base_url or f"https://{svc.account_name}.blob.core.windows.net")
        if account:
            cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
            svc = BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=cred)
            return svc.get_container_client(container), container, (base_url or f"https://{account}.blob.core.windows.net")
    except Exception:
        current_app.logger.exception("Falha construindo clientes do Blob.")
    return None, None, None


def _local_vehicle_dir() -> Path:
    """
    Pasta local para fallback: /static/uploads/vehicles/<tenant_slug>
    """
    root = Path(current_app.root_path).parent  # projeto/
    p = root / "static" / "uploads" / "vehicles" / g.tenant.slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_vehicle_image(file_storage) -> str:
    """
    Salva a imagem no Azure Blob (se configurado) e retorna URL pública.
    Caso contrário, salva local e retorna /static/uploads/vehicles/<tenant>/<arquivo>.
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = (Path(filename).suffix or "").lower()
    safe_name = f"{uuid4().hex}{ext}"
    blob_path = f"{g.tenant.slug}/{safe_name}"

    # Tenta Azure Blob
    cclient, container, base_url = _blob_clients()
    if cclient:
        try:
            cclient.upload_blob(
                name=blob_path,
                data=file_storage.stream,
                overwrite=True,
                content_settings=ContentSettings(content_type=file_storage.mimetype or "application/octet-stream"),
            )
            return f"{base_url}/{container}/{blob_path}"
        except Exception:
            current_app.logger.exception("Falha ao subir imagem para Blob. Fallback para disco local.")

    # Fallback local
    local_dir = _local_vehicle_dir()
    target = local_dir / safe_name
    file_storage.stream.seek(0)
    file_storage.save(target)
    # URL pública para servir pelo static
    rel_url = f"/static/uploads/vehicles/{g.tenant.slug}/{safe_name}"
    return rel_url


def _delete_vehicle_image(image_url: str) -> None:
    """
    Tenta remover a imagem anterior (Blob ou local). Não explode em caso de erro.
    """
    if not image_url:
        return
    try:
        # Se for URL do Blob
        if image_url.startswith("http") and ".blob.core.windows.net" in image_url:
            # .../<container>/<tenant>/<arquivo>
            parts = image_url.split(".blob.core.windows.net/")
            if len(parts) == 2:
                _, path = parts
                container, blob_name = path.split("/", 1)
                cclient, _, _ = _blob_clients()
                if cclient and cclient.container_name == container:
                    cclient.delete_blob(blob_name, delete_snapshots="include")
            return

        # Se for caminho /static/...
        if image_url.startswith("/static/"):
            root = Path(current_app.root_path).parent
            abs_path = root / image_url.lstrip("/")
            if abs_path.exists():
                abs_path.unlink(missing_ok=True)
    except Exception:
        current_app.logger.exception("Falha ao remover imagem antiga.")
# =============================================================================== 



# =============================================================================
# Rotas principais
# =============================================================================
@admin_bp.get("")
@admin_bp.get("/")
@login_required
def admin_root():
    return redirect(url_for("admin.dashboard"))

@admin_bp.get("/dashboard")
@login_required
def dashboard():
    """
    Renderiza o dashboard do TENANT atual.
    Passa 'totals' para o template (cards) e os gráficos buscam via /dashboard/data.
    """
    t = g.tenant

    totals = {
        "vehicles": db.session.query(Vehicle.id)
                     .filter(Vehicle.tenant_id == t.id).count(),
        "categories": db.session.query(VehicleCategory.id)
                     .filter(VehicleCategory.tenant_id == t.id).count(),
        "reservations": db.session.query(Reservation.id)
                       .filter(Reservation.tenant_id == t.id).count(),
    }

    return render_template("admin/dashboard.html", totals=totals)

@admin_bp.get("/dashboard/data")
@login_required
def dashboard_data():
    t = g.tenant
    today = datetime.utcnow().date()
    start = today - timedelta(days=6)

    currency = "USD"

    base = (Reservation.query
            .filter(Reservation.tenant_id == t.id)
            .filter(Reservation.pickup_dt != None)    # noqa: E711
            .filter(func.date(Reservation.pickup_dt) >= start))

    rev_rows = (base.filter(Reservation.status.in_(("confirmed",)))
                    .with_entities(func.date(Reservation.pickup_dt).label("d"),
                                   func.coalesce(func.sum(Reservation.total_price), 0.0))
                    .group_by("d").all())

    cnt_rows = (base.with_entities(func.date(Reservation.pickup_dt).label("d"),
                                   func.count(Reservation.id))
                    .group_by("d").all())

    def series(rows):
        m = {str(d): float(v) for d, v in rows}
        labels, data = [], []
        for i in range(7):
            d = (start + timedelta(days=i)).isoformat()
            labels.append(d); data.append(m.get(d, 0.0))
        return labels, data

    rev_labels, rev_data = series(rev_rows)
    cnt_labels, cnt_data = series(cnt_rows)

    # TOP CARS — reforça escopo por tenant em todas as tabelas
    top_cars = (db.session.query(Vehicle.model, func.count(Reservation.id))
                .join(Reservation, Reservation.vehicle_id == Vehicle.id)
                .filter(Vehicle.tenant_id == t.id)
                .filter(Reservation.tenant_id == t.id)
                .group_by(Vehicle.id, Vehicle.model)
                .order_by(func.count(Reservation.id).desc())
                .limit(5).all())

    # TOP CATEGORIES — garante tenant em todas as junções
    top_cats = (db.session.query(VehicleCategory.name, func.count(Reservation.id))
                .join(Vehicle, Vehicle.category_id == VehicleCategory.id)
                .join(Reservation, Reservation.vehicle_id == Vehicle.id)
                .filter(VehicleCategory.tenant_id == t.id)
                .filter(Vehicle.tenant_id == t.id)
                .filter(Reservation.tenant_id == t.id)
                .group_by(VehicleCategory.id, VehicleCategory.name)
                .order_by(func.count(Reservation.id).desc())
                .limit(6).all())

    payload = {
        "currency": currency,
        "revenue_week":   {"labels": rev_labels, "data": rev_data},
        "rentals_week":   {"labels": cnt_labels, "data": cnt_data},
        "top_cars":       {"labels": [m or "N/D" for m, _ in top_cars],
                           "data":   [int(c) for _, c in top_cars]},
        "top_categories": {"labels": [n or "—" for n, _ in top_cats],
                           "data":   [int(c) for _, c in top_cats]},
    }
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# =============================================================================
# Configurações do Tenant (branding, pagamentos, contrato, assinatura, usuários)
# =============================================================================
def admin_required():
    if not current_user.is_authenticated or not getattr(current_user, "is_admin", False):
        abort(403)

@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    admin_required()
    t = g.tenant

    if request.method == "POST":
        sec = (request.form.get("_section") or "").strip()

        # ---------- Branding ----------
        if sec == "branding":
            t.name = (request.form.get("company_name") or "").strip() or t.name
            t.brand_primary    = (request.form.get("brand_primary") or "").strip() or None
            t.brand_navbar_bg  = (request.form.get("brand_navbar_bg") or "").strip() or None
            t.brand_sidebar_bg = (request.form.get("brand_sidebar_bg") or "").strip() or None
            try:
                new_logo = _save_logo(request.files.get("logo_file"))
                if new_logo:
                    t.logo_path = new_logo
            except ValueError as e:
                flash(str(e), "warning")
            db.session.commit()
            flash("Branding salvo.", "success")
            return redirect(url_for("admin.settings"))

        # ---------- Pagamentos (GlobalPay) — SEGURO (Key Vault) ----------
        if sec == "payments":
            public_key    = (request.form.get("public_key") or "").strip()
            merchant_code = (request.form.get("merchant_code") or "").strip()
            token         = (request.form.get("token") or "").strip()
            endpoint      = (request.form.get("endpoint") or "").strip()

            try:
                if public_key and merchant_code:
                    alias = save_tenant_payment_creds(
                        tenant=t,
                        public_key=public_key,
                        merchant_code=merchant_code,
                        token=(token or None),
                        endpoint=(endpoint or None),
                    )
                    flash(f"Credenciais salvas no Key Vault (alias: {alias}).", "success")
                else:
                    if endpoint:
                        t.payment_endpoint = endpoint
                        db.session.add(t)
                        db.session.commit()
                        flash("Endpoint de pagamentos atualizado.", "success")
                    else:
                        flash("Informe Public Key e Merchant Code para salvar no Key Vault, ou defina apenas o Endpoint.", "warning")
            except Exception:
                current_app.logger.exception("Falha ao salvar credenciais no Key Vault")
                flash("Erro ao salvar credenciais no Key Vault. Verifique permissões e configuração.", "danger")

            return redirect(url_for("admin.settings"))

        # ---------- Template do contrato (HTML/Jinja) ----------
        if sec == "contract":
            t.contract_template_html = request.form.get("contract_template_html") or None
            db.session.commit()
            flash("Template do contrato salvo.", "success")
            return redirect(url_for("admin.settings"))

        # ---------- Posição/Tamanho da Assinatura ----------
        if sec == "signature":
            def f(name, cast=float, default=None):
                v = (request.form.get(name) or "").strip()
                try:
                    return cast(v) if v != "" else default
                except Exception:
                    return default

            t.sign_x_rel = f("sign_x_rel", float, t.sign_x_rel or 0.62)
            t.sign_y_rel = f("sign_y_rel", float, t.sign_y_rel or 0.13)
            t.sign_w_pt  = f("sign_w_pt",  int,   t.sign_w_pt  or 200)
            t.sign_h_pt  = f("sign_h_pt",  int,   t.sign_h_pt  or 80)
            t.rub_w_pt   = f("rub_w_pt",   int,   t.rub_w_pt   or 120)
            t.rub_h_pt   = f("rub_h_pt",   int,   t.rub_h_pt   or 48)
            t.rub_margin_pt = f("rub_margin_pt", int, t.rub_margin_pt or 20)
            t.rubrica_on_last = bool(request.form.get("rubrica_on_last"))
            t.audit_stamp     = bool(request.form.get("audit_stamp"))
            db.session.commit()
            flash("Configurações da assinatura salvas.", "success")
            return redirect(url_for("admin.settings"))

        # ---------- Usuários (criação rápida) ----------
        if sec == "user_new":
            email = (request.form.get("email") or "").strip().lower()
            pwd   = request.form.get("password") or ""
            admin = bool(request.form.get("is_admin"))
            if not email or not pwd:
                flash("Informe email e senha.", "warning")
                return redirect(url_for("admin.settings"))
            from app.models import User
            if User.query.filter_by(email=email).first():
                flash("Já existe um usuário com esse e-mail.", "warning")
                return redirect(url_for("admin.settings"))
            u = User(tenant_id=t.id, email=email, is_admin=admin)
            u.set_password(pwd)
            db.session.add(u)
            db.session.commit()
            flash("Usuário criado.", "success")
            return redirect(url_for("admin.settings"))

        # ---------- E-mail (SMTP por tenant) ----------
        if sec == "mail":
            # remetente exibido no e-mail
            t.mail_from_name  = (request.form.get("mail_from_name")  or t.name or "").strip() or None
            t.mail_from_email = (request.form.get("mail_from_email") or "").strip() or None

            # valores enviados no formulário
            smtp_host_in = (request.form.get("smtp_host") or "").strip()
            smtp_port_in = request.form.get("smtp_port", type=int)
            smtp_user_in = (request.form.get("smtp_user") or "").strip()
            smtp_pass_in = (request.form.get("smtp_pass") or "").strip()
            tls_posted   = ("smtp_tls" in request.form)
            ssl_posted   = ("smtp_ssl" in request.form)
            smtp_tls_in  = bool(request.form.get("smtp_tls"))
            smtp_ssl_in  = bool(request.form.get("smtp_ssl"))

            # merge com o que já está salvo no alias (se houver)
            existing = get_tenant_mail_creds(t) or {}

            host = smtp_host_in or existing.get("host")
            user = smtp_user_in or existing.get("user")
            # senha só troca se enviada
            password = smtp_pass_in or existing.get("password")
            # TLS/SSL: se o checkbox não veio (unchecked), preserva valor antigo
            use_tls = (smtp_tls_in if tls_posted else existing.get("use_tls", False))
            use_ssl = (smtp_ssl_in if ssl_posted else existing.get("use_ssl", False))
            # Porta: usa a informada, senão a antiga, senão padrão de acordo com SSL/TLS
            port = (smtp_port_in
                    or existing.get("port")
                    or (465 if use_ssl else 587))

            try:
                if not host:
                    flash("Informe o SMTP Host (ou salve uma vez completa para criar o alias).", "warning")
                else:
                    alias = save_tenant_mail_creds(
                        tenant=t,
                        host=host,
                        port=port,
                        user=user or "",
                        password=password or "",
                        use_tls=bool(use_tls and not use_ssl),
                        use_ssl=bool(use_ssl),
                        provider="custom-smtp",
                    )
                    if alias:
                        t.mail_secret_id = alias
                        t.mail_provider  = "custom-smtp"
                    db.session.commit()
                    flash("Configurações de e-mail salvas.", "success")
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Falha ao salvar credenciais de e-mail")
                flash("Não foi possível salvar as credenciais de e-mail agora.", "danger")

            return redirect(url_for("admin.settings"))

        flash("Seção inválida.", "warning")
        return redirect(url_for("admin.settings"))

    # GET ----------------------------------------------------------------------
    from app.models import User
    users = User.query.filter_by(tenant_id=g.tenant.id).order_by(User.email.asc()).all()
    mail_cfg = get_tenant_mail_creds(t) or {}

    return render_template(
        "admin/settings.html",
        t=t,
        users=users,
        gp_signup=current_app.config.get("GLOBALPAY_SIGNUP_URL", "https://tryglobalpays.com"),
        mail_cfg=mail_cfg,   # <- envia o cfg atual para preencher o formulário
    )



# =============================================================================
# Categorias
# =============================================================================
@admin_bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        seats = request.form.get("seats", type=int) or 5
        transmission = (request.form.get("transmission") or "Automatic").strip()
        large_bags = request.form.get("large_bags", type=int) or 1
        small_bags = request.form.get("small_bags", type=int) or 1
        mileage_text = (request.form.get("mileage_text") or "Unlimited mileage").strip()

        if not name:
            flash("Nome é obrigatório.", "danger")
            return redirect(url_for("admin.categories"))

        c = VehicleCategory(
            tenant_id=g.tenant.id,
            name=name,
            slug= simple_slugify(name),
            description=description,
            seats=seats,
            transmission=transmission,
            large_bags=large_bags,
            small_bags=small_bags,
            mileage_text=mileage_text,
        )
        db.session.add(c)
        db.session.commit()
        flash("Categoria criada.", "success")
        return redirect(url_for("admin.categories"))

    cats = (
        VehicleCategory.query
        .filter_by(tenant_id=g.tenant.id)
        .order_by(VehicleCategory.name.asc())
        .all()
    )
    rates = Rate.query.filter_by(tenant_id=g.tenant.id).all()
    return render_template("admin/categories.html", categories=cats, rates=rates)

# app/admin/routes.py
from sqlalchemy.exc import IntegrityError

@admin_bp.post("/categories/<int:category_id>/delete")
@login_required
def delete_category(category_id):
    from app.models import VehicleCategory, Vehicle, Rate

    cat = VehicleCategory.query.filter_by(id=category_id, tenant_id=g.tenant.id).first_or_404()

    # conta vínculos
    veh_count  = Vehicle.query.filter_by(tenant_id=g.tenant.id, category_id=cat.id).count()
    rate_count = Rate.query.filter_by(tenant_id=g.tenant.id, category_id=cat.id).count()

    if veh_count or rate_count:
        partes = []
        if veh_count:
            partes.append(f"{veh_count} veículo(s)")
        if rate_count:
            partes.append(f"{rate_count} tarifa(s)")
        msg = "Não é possível excluir esta categoria: existem " + " e ".join(partes) + \
              " vinculados. Remova/reatribua os vínculos antes de excluir."
        flash(msg, "warning")
        return redirect(url_for("admin.categories", tenant_slug=g.tenant.slug))

    try:
        db.session.delete(cat)
        db.session.commit()
        flash("Categoria excluída com sucesso.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível excluir: há registros vinculados. Remova os vínculos antes de excluir.", "danger")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao excluir categoria %s", category_id)
        flash("Ocorreu um erro ao excluir a categoria.", "danger")

    return redirect(url_for("admin.categories", tenant_slug=g.tenant.slug))


@admin_bp.get("/categories/<int:category_id>/edit.modal")
@login_required
def edit_category_modal(category_id):
    cat = VehicleCategory.query.filter_by(tenant_id=g.tenant.id, id=category_id).first_or_404()
    return render_template("admin/_category_edit_form.html", category=cat)

@admin_bp.post("/categories/<int:category_id>/edit.modal")
@login_required
def update_category_modal(category_id):
    cat = VehicleCategory.query.filter_by(tenant_id=g.tenant.id, id=category_id).first_or_404()
    name = (request.form.get("name") or "").strip()
    desc = (request.form.get("description") or "").strip() or None

    seats = request.form.get("seats", type=int)
    transmission = request.form.get("transmission")
    large_bags = request.form.get("large_bags", type=int)
    small_bags = request.form.get("small_bags", type=int)
    mileage_text = request.form.get("mileage_text")

    if not name:
        return jsonify({"ok": False, "error": "Nome é obrigatório."}), 400

    cat.name = name
    cat.slug = simple_slugify(name)
    cat.description = desc
    if seats is not None:
        cat.seats = seats
    if transmission:
        cat.transmission = transmission.strip()
    if large_bags is not None:
        cat.large_bags = large_bags
    if small_bags is not None:
        cat.small_bags = small_bags
    if mileage_text:
        cat.mileage_text = mileage_text.strip()

    db.session.commit()
    return jsonify({"ok": True})


# =============================================================================
# Tarifas
# =============================================================================
@admin_bp.get("/rates")
@login_required
def rates():
    rs = Rate.query.filter_by(tenant_id=g.tenant.id).order_by(Rate.created_at.desc()).all()
    return render_template("admin/rates.html", rates=rs)

@admin_bp.get("/rates/<int:rate_id>/edit.modal")
@login_required
def edit_rate_modal(rate_id):
    rate = Rate.query.filter_by(tenant_id=g.tenant.id, id=rate_id).first_or_404()
    return render_template("admin/_rate_edit_form.html", rate=rate)

@admin_bp.post("/rates/<int:rate_id>/edit.modal")
@login_required
def update_rate_modal(rate_id):
    rate = Rate.query.filter_by(tenant_id=g.tenant.id, id=rate_id).first_or_404()
    try:
        rate.currency = (request.form.get("currency") or rate.currency or "USD").upper()
        dr = request.form.get("daily_rate")
        if dr not in (None, ""):
            rate.daily_rate = float(dr)
        ma = request.form.get("min_age")
        if ma not in (None, ""):
            rate.min_age = int(ma)
        dep = request.form.get("deposit_amount")
        if dep not in (None, ""):
            rate.deposit_amount = float(dep)
        db.session.commit()
        return jsonify({"ok": True})
    except Exception:
        current_app.logger.exception("update_rate_modal")
        return jsonify({"error": "Falha ao salvar"}), 400

# === NOVO: criar / excluir tarifas ===========================================
@admin_bp.route("/rates/new.modal", methods=["GET", "POST"])
@login_required
def rate_new_modal():
    if request.method == "GET":
        # categorias do tenant que AINDA não têm tarifa
        ids_com_tarifa = {
            cid for (cid,) in db.session.query(Rate.category_id)
            .filter_by(tenant_id=g.tenant.id).all()
        }
        cats_all = (VehicleCategory.query
                    .filter_by(tenant_id=g.tenant.id)
                    .order_by(VehicleCategory.name.asc())
                    .all())
        categories = [c for c in cats_all if c.id not in ids_com_tarifa]
        return render_template("admin/_rate_new_form.html", categories=categories, error=None)

    # POST (salvar)
    try:
        category_id = request.form.get("category_id", type=int)
        currency = (request.form.get("currency") or "USD").upper().strip()
        daily_rate = float(request.form.get("daily_rate", "0") or 0)
        min_age = int(request.form.get("min_age", "21") or 21)
        deposit_amount = float(request.form.get("deposit_amount", "0") or 0)

        # valida categoria do tenant
        cat = VehicleCategory.query.filter_by(id=category_id, tenant_id=g.tenant.id).first()
        if not cat:
            # reabre modal com erro
            ids_com_tarifa = {cid for (cid,) in db.session.query(Rate.category_id)
                              .filter_by(tenant_id=g.tenant.id).all()}
            cats_all = (VehicleCategory.query
                        .filter_by(tenant_id=g.tenant.id)
                        .order_by(VehicleCategory.name.asc())
                        .all())
            categories = [c for c in cats_all if c.id not in ids_com_tarifa]
            html = render_template("admin/_rate_new_form.html", categories=categories,
                                   error="Categoria inválida.")
            return jsonify(ok=False, html=html)

        # evita duplicar tarifa por categoria
        exists = Rate.query.filter_by(tenant_id=g.tenant.id, category_id=category_id).first()
        if exists:
            html = render_template("admin/_rate_new_form.html", categories=[],
                                   error="Já existe uma tarifa para esta categoria.")
            return jsonify(ok=False, html=html)

        r = Rate(
            tenant_id=g.tenant.id,
            category_id=category_id,
            daily_rate=daily_rate,
            currency=currency,
            min_age=min_age,
            deposit_amount=deposit_amount,
        )
        db.session.add(r)
        db.session.commit()
        return jsonify(ok=True)
    except Exception:
        current_app.logger.exception("rate_new_modal POST")
        html = render_template("admin/_rate_new_form.html", categories=[],
                               error="Falha ao salvar tarifa.")
        return jsonify(ok=False, html=html)


@admin_bp.post("/rates/<int:rate_id>/delete")
@login_required
def rate_delete(rate_id):
    rate = Rate.query.filter_by(tenant_id=g.tenant.id, id=rate_id).first_or_404()
    db.session.delete(rate)
    db.session.commit()
    flash("Tarifa excluída.", "success")
    return redirect(url_for("admin.rates"))
# === /NOVO ====================================================================

# =============================================================================
# Veículos
# =============================================================================
# app/admin/routes.py

@admin_bp.route("/vehicles", methods=["GET", "POST"])
@login_required
def vehicles():
    # ----- CREATE (POST) -----
    if request.method == "POST":
        plate = (request.form.get("plate") or "").strip()
        brand = (request.form.get("brand") or "").strip()
        model = (request.form.get("model") or "").strip()
        year  = request.form.get("year", type=int)
        color = (request.form.get("color") or "").strip() or None
        status = (request.form.get("status") or "available").strip() or "available"

        # URL externa opcional (se o usuário colar um link)
        image_url = (request.form.get("image_url") or "").strip() or None

        # Upload de arquivo (campo "photo" do formulário)
        file = request.files.get("photo")
        if file and file.filename:
            try:
                # vamos implementar esse helper no próximo passo
                from app.services.media import save_vehicle_image_from_request
                image_url = save_vehicle_image_from_request(file, tenant_slug=g.tenant.slug)
            except Exception:
                current_app.logger.exception("Falha ao salvar imagem do veículo")
                flash("Imagem não pôde ser salva; tente novamente.", "warning")

        # categoria obrigatória
        cat_id = request.form.get("category_id", type=int)
        if not cat_id:
            flash("Selecione uma categoria para o veículo.", "warning")
            return redirect(url_for("admin.vehicles"))

        category = VehicleCategory.query.filter_by(
            id=cat_id, tenant_id=g.tenant.id
        ).first()
        if not category:
            flash("Categoria inválida para este tenant.", "warning")
            return redirect(url_for("admin.vehicles"))

        v = Vehicle(
            tenant_id=g.tenant.id,
            category_id=category.id,
            plate=plate or None,
            brand=brand,
            model=model,
            year=year,
            status=status,
            image_url=image_url,   # <- agora vem do upload (se houver) ou do link
            color=color,
        )
        db.session.add(v)
        try:
            db.session.commit()
            flash("Veículo criado com sucesso.", "success")
        except IntegrityError:
            db.session.rollback()
            flash("Erro ao salvar veículo (verifique se a categoria está selecionada e os dados são válidos).", "danger")
        return redirect(url_for("admin.vehicles"))

    # ----- LIST (GET) com paginação -----
    page = request.args.get("page", type=int) or 1
    per_page = request.args.get("per_page", type=int) or 20
    if per_page < 5:
        per_page = 5
    if per_page > 100:
        per_page = 100
    if page < 1:
        page = 1

    qv = (
        Vehicle.query
        .filter_by(tenant_id=g.tenant.id)
        .order_by(Vehicle.model.asc())
    )
    total = qv.count()
    items = qv.offset((page - 1) * per_page).limit(per_page).all()

    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max((total + per_page - 1) // per_page, 1),
        "has_prev": page > 1,
        "has_next": (page * per_page) < total,
        "prev_page": page - 1 if page > 1 else 1,
        "next_page": page + 1 if (page * per_page) < total else max((total + per_page - 1) // per_page, 1),
    }

    categories = (
        VehicleCategory.query
        .filter_by(tenant_id=g.tenant.id)
        .order_by(VehicleCategory.name.asc())
        .all()
    )

    return render_template(
        "admin/vehicles.html",
        categories=categories,
        vehicles=items,
        pagination=pagination,
    )

@admin_bp.route("/vehicles/<int:vehicle_id>/edit.modal", methods=["GET", "POST"])
@login_required
def vehicle_edit_modal(vehicle_id):
    v = Vehicle.query.filter_by(id=vehicle_id, tenant_id=g.tenant.id).first_or_404()
    if request.method == "GET":
        cats = (
            VehicleCategory.query
            .filter_by(tenant_id=g.tenant.id)
            .order_by(VehicleCategory.name.asc())
            .all()
        )
        return render_template("admin/_vehicle_edit_form.html", vehicle=v, categories=cats)

    v.brand = (request.form.get("brand") or "").strip() or None
    v.model = (request.form.get("model") or "").strip() or v.model
    v.year = request.form.get("year", type=int)
    v.plate = (request.form.get("plate") or "").strip() or None
    v.color = (request.form.get("color") or "").strip() or None
    v.category_id = request.form.get("category_id", type=int) or v.category_id
    v.status = (request.form.get("status") or v.status or "available").strip()

    file = request.files.get("photo")
    if file and file.filename:
        _delete_vehicle_image(v.image_url)
        v.image_url = _save_vehicle_image(file)

    db.session.commit()
    return jsonify(ok=True)

@admin_bp.post("/vehicles/<int:vehicle_id>/status")
@login_required
def vehicle_set_status(vehicle_id):
    target = (request.form.get("target") or "").strip()
    if target not in ("available", "unavailable"):
        abort(400)
    v = Vehicle.query.filter_by(id=vehicle_id, tenant_id=g.tenant.id).first_or_404()
    v.status = target
    db.session.commit()
    flash("Status atualizado.", "success")
    return redirect(request.referrer or url_for("admin.vehicles"))

@admin_bp.route("/vehicles/<int:vehicle_id>/maintenance.modal", methods=["GET", "POST"])
@login_required
def vehicle_maintenance_modal(vehicle_id):
    v = Vehicle.query.filter_by(id=vehicle_id, tenant_id=g.tenant.id).first_or_404()
    t = _maint_table()
    if request.method == "GET":
        return render_template("admin/_vehicle_maintenance_form.html", vehicle=v)

    reason = (request.form.get("reason") or "").strip()
    if not reason:
        return jsonify(error="Informe um motivo."), 400

    v.status = "maintenance"
    db.session.commit()

    ins = t.insert().values(
        tenant_id=g.tenant.id,
        vehicle_id=v.id,
        reason=reason,
        started_at=datetime.utcnow(),
        active=True,
    )
    db.session.execute(ins)
    db.session.commit()
    return jsonify(ok=True)

@admin_bp.route('/vehicles/<int:vehicle_id>/maintenance/complete', methods=['POST'])
@login_required
def vehicles_maintenance_complete(vehicle_id):
    v = Vehicle.query.filter_by(id=vehicle_id, tenant_id=g.tenant.id).first_or_404()
    _set_first_attr(v, ['status','situation','state'], 'available')
    _set_first_attr(v, ['maintenance_reason','manutencao_motivo','work_note','obs_manutencao'], None)
    db.session.add(v)
    db.session.commit()
    flash('Manutenção concluída. Veículo disponível para entrega.', 'success')
    return redirect(url_for('admin.vehicles_maintenance', tenant_slug=g.tenant.slug))


@admin_bp.get("/vehicles/maintenance")
@login_required
def maintenance_list():
    t = _maint_table()
    vs = (
        Vehicle.query
        .filter_by(tenant_id=g.tenant.id, status="maintenance")
        .order_by(Vehicle.model.asc())
        .all()
    )
    logs = {}
    if vs:
        ids = [v.id for v in vs]
        stmt = select(t.c.vehicle_id, t.c.reason, t.c.started_at).where(
            and_(t.c.tenant_id == g.tenant.id, t.c.active.is_(True), t.c.vehicle_id.in_(ids))
        )
        for vehicle_id, reason, started_at in db.session.execute(stmt).fetchall():
            logs[vehicle_id] = {"reason": reason, "started_at": started_at}
    return render_template("admin/maintenance.html", vehicles=vs, logs=logs)

@admin_bp.post("/vehicles/<int:vehicle_id>/maintenance/finish")
@login_required
def maintenance_finish(vehicle_id):
    v = Vehicle.query.filter_by(tenant_id=g.tenant.id, id=vehicle_id).first_or_404()
    t = _maint_table()
    upd = (
        t.update()
        .where(and_(t.c.tenant_id == g.tenant.id, t.c.vehicle_id == v.id, t.c.active.is_(True)))
        .values(active=False, ended_at=datetime.utcnow())
    )
    db.session.execute(upd)
    v.status = "available"
    db.session.commit()
    flash("Manutenção encerrada e veículo marcado como disponível.", "success")
    return redirect(url_for("admin.maintenance_list"))

@admin_bp.post("/vehicles/<int:vehicle_id>/delete")
@login_required
def vehicle_delete(vehicle_id):
    v = Vehicle.query.filter_by(id=vehicle_id, tenant_id=g.tenant.id).first_or_404()
    _delete_vehicle_image(v.image_url)
    db.session.delete(v)
    db.session.commit()
    flash("Veículo removido.", "success")
    return redirect(request.referrer or url_for("admin.vehicles"))


# =============================================================================
# Reservas
# =============================================================================
@admin_bp.get("/reservations")
@login_required
def reservations():
    status = request.args.get("status", "all").lower()
    q = (
        Reservation.query
        .filter_by(tenant_id=g.tenant.id)
        .order_by(Reservation.created_at.desc())
    )
    if status in ("pending", "pending_payment", "confirmed", "cancelled", "canceled", "under_review"):
        # aceita as duas grafias cancel(l)ed
        if status in ("cancelled", "canceled"):
            q = q.filter(Reservation.status.in_(("cancelled", "canceled")))
        else:
            q = q.filter(Reservation.status == status)
    reservations = q.all()
    return render_template("admin/reservations.html", reservations=reservations, status=status)

@admin_bp.post("/reservations/<int:reservation_id>/confirm")
@login_required
def reservation_confirm(reservation_id):
    res = Reservation.query.filter_by(tenant_id=g.tenant.id, id=reservation_id).first_or_404()
    res.status = "confirmed"
    if res.vehicle:
        res.vehicle.status = "booked"
    db.session.commit()
    flash("Reserva confirmada.", "success")
    return redirect(url_for("admin.reservations"))

@admin_bp.post("/reservations/<int:reservation_id>/cancel")
@login_required
def reservation_cancel(reservation_id):
    res = Reservation.query.filter_by(tenant_id=g.tenant.id, id=reservation_id).first_or_404()
    res.status = "canceled"  # usa uma grafia consistente com o fluxo público
    if res.vehicle:
        _recompute_vehicle_status(res.vehicle)
    db.session.commit()
    flash("Reserva cancelada.", "warning")
    return redirect(url_for("admin.reservations"))

def _cleanup_contract_files(res_id: int, tenant_slug: str):
    root = Path(current_app.root_path)
    contracts_dir  = root / "static" / "uploads" / "contracts" / tenant_slug
    signatures_dir = root / "static" / "uploads" / "signatures" / tenant_slug

    for p in [
        contracts_dir / f"contrato_reserva_{res_id}.pdf",
        contracts_dir / f"contrato_reserva_{res_id}_SIGNED.pdf",
        contracts_dir / f"contrato_reserva_{res_id}_audit.json",
        signatures_dir / f"assinatura_{res_id}.png",
    ]:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            current_app.logger.exception("Falha ao remover arquivo de contrato: %s", p)

@admin_bp.post("/reservations/<int:reservation_id>/delete")
@login_required
def reservation_delete(reservation_id):
    from app.models import Contract  # import local para evitar ciclos

    res = Reservation.query.filter_by(tenant_id=g.tenant.id, id=reservation_id).first_or_404()

    # apaga o contrato (se existir) antes da reserva
    if res.contract:
        # tenta remover arquivo físico
        try:
            if res.contract.file_path and os.path.isfile(res.contract.file_path):
                os.remove(res.contract.file_path)
        except Exception:
            current_app.logger.exception("Erro removendo PDF do contrato no path salvo.")

        # remove demais artefatos por padrão (base/signed/png/audit)
        try:
            _cleanup_contract_files(res.id, g.tenant.slug)
        except Exception:
            current_app.logger.exception("Erro no cleanup de arquivos do contrato.")

        db.session.delete(res.contract)

    v = res.vehicle
    db.session.delete(res)
    try:
        db.session.commit()
        if v:
            # recalcula status do veículo
            _recompute_vehicle_status(v)
        flash("Reserva e contrato removidos.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Falha ao remover reserva")
        flash("Não foi possível remover a reserva.", "danger")

    return redirect(url_for("admin.reservations"))


# =============================================================================
# Calendário
# =============================================================================
@admin_bp.get("/calendar")
@login_required
def calendar():
    start_str = request.args.get("start")
    days = request.args.get("days", type=int) or 30

    today = datetime.utcnow().date()
    if start_str:
        try:
            start = datetime.strptime(start_str, "%Y-%m-%d").date()
        except ValueError:
            start = today
    else:
        start = today

    end = start + timedelta(days=days)
    days_list = [start + timedelta(days=i) for i in range(days)]

    vehicles = (
        Vehicle.query
        .filter_by(tenant_id=g.tenant.id)
        .order_by(Vehicle.brand.asc(), Vehicle.model.asc())
        .all()
    )

    # (vehicle_id, date) -> True quando está alugado
    booked = {}
    reservations = (
        Reservation.query
        .filter_by(tenant_id=g.tenant.id, status="confirmed")
        .filter(Reservation.pickup_dt < end, Reservation.dropoff_dt > start)
        .all()
    )
    for r in reservations:
        d = r.pickup_dt.date()
        while d < r.dropoff_dt.date():
            if start <= d < end:
                booked[(r.vehicle_id, d)] = True
            d += timedelta(days=1)

    return render_template(
        "admin/calendar.html",
        vehicles=vehicles,
        days=days_list,
        booked=booked,
        start=start,
        end=end,
    )


# =============================================================================
# CRM
# =============================================================================
@admin_bp.get("/leads")
@login_required
def leads():
    page = request.args.get("page", 1, type=int)
    q = (request.args.get("q") or "").strip()
    stage = (request.args.get("stage") or "").strip()

    query = Lead.query.filter_by(tenant_id=g.tenant.id)
    if stage:
        query = query.filter(Lead.stage == stage)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Lead.name.ilike(like),
                Lead.email.ilike(like),
                Lead.phone.ilike(like),
                Lead.pickup_airport.ilike(like),
                Lead.dropoff_airport.ilike(like),
            )
        )
    query = query.order_by(Lead.created_at.desc())
    pager = query.paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/leads.html", pager=pager, q=q, stage=stage)

@admin_bp.post("/leads/<int:lead_id>/stage")
@login_required
def lead_change_stage(lead_id):
    lead = Lead.query.filter_by(tenant_id=g.tenant.id, id=lead_id).first_or_404()
    new_stage = (request.form.get("stage") or "").strip().lower()
    if new_stage not in ("new", "contacted", "converted", "closed"):
        return jsonify({"error": "Estágio inválido"}), 400
    lead.stage = new_stage
    db.session.commit()
    return jsonify({"ok": True, "stage": lead.stage})

@admin_bp.post("/leads/<int:lead_id>/delete")
@login_required
def lead_delete(lead_id):
    lead = Lead.query.filter_by(tenant_id=g.tenant.id, id=lead_id).first_or_404()
    db.session.delete(lead)
    db.session.commit()
    return jsonify({"ok": True})


# =============================================================================
# Contratos (admin) — Editor/Preview/Validate limitados aos campos solicitados
# =============================================================================

def _tpl_env_admin() -> SandboxedEnvironment:
    env = SandboxedEnvironment(autoescape=True, trim_blocks=True, lstrip_blocks=True)

    def money(value, currency="USD"):
        try:
            v = float(value)
            return f"{currency} {v:,.2f}"
        except Exception:
            return f"{currency} {value}"

    def datefmt(dt, fmt="%d/%m/%Y"):
        if not dt:
            return "-"
        if isinstance(dt, str):
            try:
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(dt)
            except Exception:
                return dt
        return dt.strftime(fmt)

    def datefmt_long_pt(value):
        """Formata datas como '11 de setembro de 2025'."""
        if isinstance(value, datetime):
            d = value.date()
        elif isinstance(value, date):
            d = value
        else:
            try:
                d = datetime.fromisoformat(str(value)).date()
            except Exception:
                d = date.today()
        meses = [
            "janeiro","fevereiro","março","abril","maio","junho",
            "julho","agosto","setembro","outubro","novembro","dezembro"
        ]
        return f"{d.day} de {meses[d.month-1]} de {d.year}"

    env.filters["money"] = money
    env.filters["datefmt"] = datefmt
    env.filters["datefmt_long_pt"] = datefmt_long_pt
    return env

# === Somente os campos que você pediu ===
_ALLOWED_VARS = {
    "cliente_nome",
    "cliente_doc",
    "cliente_pais",
    "voo_numero",
    "data_inicio",
    "data_fim",
    "hoje",
    "carro_marca",
    "carro_modelo",
    "carro_ano",
    "carro_cor",
    "tenant_name",
    "valor_total",  # usado como {{ valor_total|money(currency) }}
}

def _default_contract_template_admin() -> str:
    # Template de exemplo contendo apenas os campos permitidos acima
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  @page { size: A4; margin: 18mm 16mm; }
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; font-size: 12px; color:#111; line-height: 1.45; }
  h1 { font-size: 20px; margin: 10px 0 12px; text-align:center; letter-spacing:.5px; }
  h2 { font-size: 14px; margin: 18px 0 8px; }
  .small { font-size: 11px; color:#666; }
  .box { border:1px solid #ddd; border-radius:6px; padding:10px 12px; }
  .page-break { page-break-before: always; }
  .sign-hint { height:110px; }
</style></head>
<body>
<h1>Contrato de Locação de Veículo</h1>
<div class="box">
  <p><b>Locatário:</b> {{ cliente_nome }} — <b>Doc:</b> {{ cliente_doc }}</p>
  <p><b>Período:</b> {{ data_inicio|datefmt }} a {{ data_fim|datefmt }}</p>
  <p><b>Veículo:</b> {{ carro_marca }} {{ carro_modelo }} {{ carro_ano }} ({{ carro_cor }})</p>
  <p><b>Locadora:</b> {{ tenant_name }}</p>
  <p><b>Valor Total:</b> {{ valor_total|money(currency) }}</p>
</div>
<h2>Cláusulas</h2>
<p>Edite livremente este texto no editor acima. Os botões exibem apenas os campos dinâmicos permitidos.</p>
<div class="page-break"></div>
<h2>Assinaturas</h2>
<p class="small">A assinatura digital será aplicada aqui na geração do PDF.</p>
<div class="sign-hint"></div>
</body></html>"""

def _sample_context_for_preview():
    """Contexto só para PRÉVIA na tela de Configurações."""
    today = date.today()

    # nome do tenant; sem logo para não introduzir variável não permitida
    tenant_name = getattr(g.tenant, "name", "Minha Locadora")

    # Nome “humano” do usuário logado (se existir)
    sample_name = None
    if getattr(current_user, "is_authenticated", False):
        sample_name = getattr(current_user, "name", None)
        if not sample_name:
            email = getattr(current_user, "email", "")
            sample_name = (email.split("@")[0] if email else None)

    return {
        # campos permitidos
        "cliente_nome": sample_name or "Nome do Cliente",
        "cliente_doc": "000.000.000-00",
        "cliente_pais": "Brasil",
        "voo_numero": "LA3409",
        "data_inicio": today,
        "data_fim": today,
        "hoje": today,
        "carro_marca": "Marca",
        "carro_modelo": "Modelo",
        "carro_ano": "2023",
        "carro_cor": "Preto",
        "tenant_name": tenant_name,
        "valor_total": 1234.56,

        # necessário apenas para o filtro money(currency) no template
        "currency": "USD",
    }

@admin_bp.post("/settings/contract.preview")
@login_required
def contract_preview():
    data = request.get_json(silent=True) or {}
    html_src = (data.get("html") or "").strip() or (g.tenant.contract_template_html or "") or _default_contract_template_admin()
    # renderização sandbox
    env = _tpl_env_admin()
    tpl = env.from_string(html_src)
    rendered = tpl.render(**_sample_context_for_preview())
    # garante base de assets em /static
    static_base = url_for("static", filename="")
    style_reset = "html,body{writing-mode:horizontal-tb!important;white-space:normal!important;word-break:normal!important;}"
    wrapper = (
        '<!doctype html><html><head>'
        '<meta charset="utf-8">'
        f'<base href="{static_base}">'
        f'<style id="__preview_reset">{style_reset}</style>'
        '</head><body>'
        f'<div style="max-width:820px;margin:0 auto">{rendered}</div>'
        '</body></html>'
    )
    return wrapper

@admin_bp.post("/settings/contract.validate")
@login_required
def contract_validate():
    data = request.get_json(silent=True) or {}
    html_src = (data.get("html") or "")
    # captura {{ VAR ... }} — pega apenas o primeiro identificador após {{
    found = set()
    for m in re.finditer(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)", html_src):
        found.add(m.group(1))
    unknown = sorted([k for k in found if k not in _ALLOWED_VARS])
    return jsonify(ok=(len(unknown) == 0), unknown=unknown)
# ======== /CONTRATO ========

# Diretório para artefatos dos CHECKLISTS (mesma base do /static público)
def _uploads_ck_dir(*parts: str) -> Path:
    base = Path(current_app.root_path).parent / 'static' / 'uploads'
    folder = base.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def save_dataurl_image(dataurl: str, subdir: str) -> str | None:
    if not dataurl or not dataurl.startswith('data:'):
        return None
    header, b64data = dataurl.split(',', 1)
    ext = 'png' if 'png' in header else 'jpg'
    fname = f"{uuid.uuid4().hex}.{ext}"
    filepath = _uploads_ck_dir('checklists', g.tenant.slug, subdir) / fname
    with open(filepath, 'wb') as f:
        f.write(base64.b64decode(b64data))
    return f"/static/uploads/checklists/{g.tenant.slug}/{subdir}/{fname}"

def save_uploaded_photos(files) -> list[str]:
    paths: list[str] = []
    if not files:
        return paths
    for file in files:
        if not file or not getattr(file, "filename", None):
            continue
        safe = secure_filename(file.filename)
        fname = f"{uuid.uuid4().hex}_{safe}"
        dest = _uploads_ck_dir('checklists', g.tenant.slug, 'photos') / fname
        file.save(dest)
        paths.append(f"/static/uploads/checklists/{g.tenant.slug}/photos/{fname}")
    return paths


def generate_car_map_png(marks: dict) -> str:
    # Desenho do mapa (4 linhas x 3 colunas)
    W, H = 600, 420
    img = Image.new('RGB', (W, H), (255, 255, 255))  # <<< usa "img" e salva com "img.save"
    d = ImageDraw.Draw(img)

    pad = 16
    cols, rows = 3, 4
    cw, ch = (W - pad*2) // cols, (H - pad*2) // rows
    grid = [
        ('front', ['L','C','R']),
        ('left',  ['F','M','T']),
        ('right', ['F','M','T']),
        ('rear',  ['L','C','R']),
    ]

    # moldura e grades
    d.rectangle([pad, pad, pad+cols*cw, pad+rows*ch], outline=(0,0,0), width=2)
    for r in range(rows):
        y1 = pad + r*ch
        d.line([pad, y1, pad+cols*cw, y1], fill=(0,0,0), width=1)
        label, sub = grid[r]
        for c in range(cols):
            x1 = pad + c*cw
            d.line([x1, pad, x1, pad+rows*ch], fill=(0,0,0), width=1)
            tag = sub[c]
            d.text((x1+6, y1+6), f"{label}:{tag}", fill=(80,80,80))

    # bordas finais
    d.line([pad, pad+rows*ch, pad+cols*cw, pad+rows*ch], fill=(0,0,0), width=1)
    d.line([pad+cols*cw, pad, pad+cols*cw, pad+rows*ch], fill=(0,0,0), width=1)

    def draw_x(cx, cy):
        s = min(cw, ch) * 0.35
        d.line([cx-s, cy-s, cx+s, cy+s], fill=(200,0,0), width=5)
        d.line([cx+s, cy-s, cx-s, cy+s], fill=(200,0,0), width=5)

    for r, (label, sub) in enumerate(grid):
        selected = (marks or {}).get(label, [])
        for c, tag in enumerate(sub):
            if tag in selected:
                x1 = pad + c*cw
                y1 = pad + r*ch
                cx, cy = x1 + cw/2, y1 + ch/2
                draw_x(cx, cy)

    fname = f"car_map_{uuid.uuid4().hex}.png"
    dest = _uploads_ck_dir('checklists', g.tenant.slug, 'maps') / fname  # <<< por tenant
    img.save(dest, format='PNG')
    return f"/static/uploads/checklists/{g.tenant.slug}/maps/{fname}"




@admin_bp.route('/operator/checklists')
@login_required
def operator_checklists_index():
    stage = (request.args.get('stage', 'entrega') or 'entrega').strip().lower()
    q = (request.args.get('q') or '').strip()

    # Base
    query = Reservation.query.filter(Reservation.tenant_id == g.tenant.id)

    # Filtros por estágio
    if stage in ('entrega', 'entregas'):
        query = query.filter(~Reservation.checklists.any(OperatorChecklist.stage == 'entrega'))
        active_stage = 'entrega'
    elif stage in ('devolucao', 'devolução', 'devolucoes'):
        query = query.filter(Reservation.checklists.any(OperatorChecklist.stage == 'entrega'))
        query = query.filter(~Reservation.checklists.any(OperatorChecklist.stage == 'devolucao'))
        active_stage = 'devolucao'
    elif stage in ('finalizadas', 'finalizados', 'final'):
        query = query.filter(Reservation.checklists.any(OperatorChecklist.stage == 'entrega'))
        query = query.filter(Reservation.checklists.any(OperatorChecklist.stage == 'devolucao'))
        active_stage = 'finalizadas'
    else:
        # fallback seguro
        query = query.filter(~Reservation.checklists.any(OperatorChecklist.stage == 'entrega'))
        active_stage = 'entrega'

    # Busca (condutor/placa)
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Vehicle, Reservation.vehicle_id == Vehicle.id)

        conds = []
        # nomes de condutor em Reservation
        for colname in ['driver_name', 'customer_name', 'condutor_nome', 'renter_name', 'nome_condutor']:
            col = getattr(Reservation, colname, None)
            if isinstance(col, InstrumentedAttribute):
                conds.append(col.ilike(like))

        # placa no Vehicle
        for colname in ['plate', 'license_plate']:
            col = getattr(Vehicle, colname, None)
            if isinstance(col, InstrumentedAttribute):
                conds.append(col.ilike(like))

        # placa também em Reservation (se existir)
        for colname in ['vehicle_plate', 'placa', 'license_plate']:
            col = getattr(Reservation, colname, None)
            if isinstance(col, InstrumentedAttribute):
                conds.append(col.ilike(like))

        if conds:
            query = query.filter(or_(*conds))

    pickup_col = _first_col(Reservation, [
        'pickup_dt', 'pickup_datetime', 'pickup_at', 'start_at', 'start_datetime',
        'check_out_at', 'begin_at', 'from_datetime', 'data_retirada', 'retirada_em',
        'created_at'
    ]) or Reservation.id

    reservations = query.order_by(pickup_col.asc()).limit(200).all()

    # Mapa: reserva_id -> URL (sempre rota que regenera se faltar)
    entrega_pdfs = {}
    if reservations:
        res_ids = [r.id for r in reservations]
        entregas = (
            OperatorChecklist.query
            .filter(OperatorChecklist.reservation_id.in_(res_ids),
                    OperatorChecklist.stage == 'entrega')
            .order_by(OperatorChecklist.reservation_id.asc(),
                      OperatorChecklist.signed_at.desc().nullslast())
            .all()
        )
        vistos = set()
        for c in entregas:
            if c.reservation_id in vistos:
                continue
            entrega_pdfs[c.reservation_id] = url_for(
                'admin.operator_checklist_pdf',
                tenant_slug=g.tenant.slug,
                checklist_id=c.id
            )
            vistos.add(c.reservation_id)

    # SEMPRE retorna um template
    return render_template(
        'admin/operator_checklists/index.html',
        stage=active_stage,
        reservations=reservations,
        entrega_pdfs=entrega_pdfs
    )


@admin_bp.route('/operator/checklists/new')
@login_required
def operator_checklists_new():
    reservation_id = request.args.get('reservation_id', type=int)
    stage = request.args.get('stage', 'entrega')
    res = Reservation.query.filter_by(id=reservation_id, tenant_id=g.tenant.id).first_or_404()

    default_customer_name = _first_value(res, [
        'driver_name', 'customer_name', 'condutor_nome', 'renter_name', 'nome_condutor'
    ])
    default_customer_email = _first_value(res, [
        'driver_email', 'customer_email', 'renter_email', 'email', 'mail'
    ])

    return render_template(
        'admin/operator_checklists/form.html',
        reservation=res,
        stage=stage,
        default_customer_name=default_customer_name,
        default_customer_email=default_customer_email
    )

@admin_bp.route('/operator/checklists/<int:checklist_id>/pdf')
@login_required
def operator_checklist_pdf(checklist_id):
    c = OperatorChecklist.query.get_or_404(checklist_id)
    res = Reservation.query.filter_by(id=c.reservation_id, tenant_id=g.tenant.id).first_or_404()

    # base física sob .../<repo_root>/static/...
    def _abs_static(rel: str) -> Path:
        return Path(current_app.root_path).parent / rel.lstrip('/')  # <<< usa parent

    # se a primeira foto não for o mapa, gera um
    car_map_path = (c.photos[0] if c.photos and str(c.photos[0]).lower().endswith('.png')
                    else generate_car_map_png(c.marks or {}))

    needs_render = True
    if c.pdf_path:
        file_ok = _abs_static(c.pdf_path).exists()
        has_slug = f"/checklists/{g.tenant.slug}/" in c.pdf_path
        needs_render = not (file_ok and has_slug)

        # tentativa de migração de caminho sem slug -> com slug
        if (not file_ok) and ("/checklists/" in c.pdf_path) and (not has_slug):
            guess = c.pdf_path.replace("/checklists/", f"/checklists/{g.tenant.slug}/")
            if _abs_static(guess).exists():
                c.pdf_path = guess
                db.session.commit()
                needs_render = False

    if needs_render:
        c.pdf_path = render_checklist_pdf(c, res, car_map_path)
        db.session.commit()

    return send_file(
        _abs_static(c.pdf_path),
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f"checklist_{c.stage}_res{res.id}.pdf"
    )



@admin_bp.route('/operator/checklists', methods=['POST'])
@login_required
def operator_checklists_create():
    reservation_id = request.form.get('reservation_id', type=int)
    stage = request.form.get('stage')
    marks_json = request.form.get('marks_json')
    notes_ext = request.form.get('notes_ext')
    notes_int = request.form.get('notes_int')
    operator_name = request.form.get('operator_name')
    customer_name = request.form.get('customer_name')
    customer_email = request.form.get('customer_email')
    fuel_level = request.form.get('fuel_level', type=int)
    odometer = request.form.get('odometer', type=int)
    signature_dataurl = request.form.get('signature_dataurl')

    res = Reservation.query.filter_by(id=reservation_id, tenant_id=g.tenant.id).first_or_404()

    photos = save_uploaded_photos(request.files.getlist('photos'))
    signature_path = save_dataurl_image(signature_dataurl, 'signatures') if signature_dataurl else None

    try:
        marks = json.loads(marks_json) if marks_json else {}
    except Exception:
        marks = {}

    car_map_path = generate_car_map_png(marks)
    if photos:
        photos.insert(0, car_map_path)

    checklist = OperatorChecklist(
        reservation_id=res.id,
        stage=stage,
        marks=marks,
        notes_ext=notes_ext,
        notes_int=notes_int,
        photos=photos or [],
        signature_path=signature_path,
        operator_name=operator_name,
        customer_name=customer_name or getattr(res, 'driver_name', None),
        customer_email=customer_email or getattr(res, 'driver_email', None),
        fuel_level=fuel_level,
        odometer=odometer,
        signed_at=datetime.utcnow(),
    )
    db.session.add(checklist)
    db.session.commit()

    # ---- Ações pós-salvar por estágio ----
    if stage == 'entrega':
        # 1) PDF da entrega (se função existir)
        try:
            # render_checklist_pdf(checklist, res, car_map_path) deve retornar um path web (/static/...)
            pdf_path = render_checklist_pdf(checklist, res, car_map_path)  # noqa: F821 (ok se definida em outro trecho)
            if pdf_path:
                checklist.pdf_path = pdf_path
                db.session.commit()
        except Exception as e:
            current_app.logger.exception(e)

        # 2) E-mail para o cliente (se houver)
        if checklist.customer_email:
            try:
                _send_checklist_email(checklist, res, car_map_path)
                flash('Checklist de entrega salvo e e-mail enviado ao cliente.', 'success')
            except Exception as e:
                current_app.logger.exception(e)
                flash('Checklist salvo, mas houve erro ao enviar e-mail.', 'warning')
        else:
            flash('Checklist de entrega salvo.', 'success')

        next_stage = 'devolucao'

    else:
        # DEVOLUÇÃO → finaliza reserva e envia veículo para manutenção (preparo e limpeza)
        def _set_first_attr(obj, names, value):
            for n in names:
                if hasattr(obj, n):
                    setattr(obj, n, value)
                    return n
            return None

        # Reserva finalizada
        _set_first_attr(res, ['status', 'situation', 'state'], 'finalizada')
        _set_first_attr(res, ['finished_at', 'ended_at', 'closed_at', 'data_finalizada'], datetime.utcnow())
        db.session.add(res)

        # Veículo em manutenção (preparo e limpeza)
        if getattr(res, 'vehicle', None):
            v = res.vehicle
            _set_first_attr(v, ['status', 'situation', 'state'], 'maintenance')
            _set_first_attr(v, ['maintenance_reason', 'manutencao_motivo', 'work_note', 'obs_manutencao'], 'Preparo e limpeza')
            _set_first_attr(v, ['maintenance_started_at', 'manutencao_inicio', 'work_started_at'], datetime.utcnow())
            db.session.add(v)

        db.session.commit()
        flash('Checklist de devolução salvo. Reserva finalizada e veículo enviado para manutenção (preparo e limpeza).', 'success')
        next_stage = 'finalizadas'

    return redirect(url_for('admin.operator_checklists_index', tenant_slug=g.tenant.slug, stage=next_stage))

def _send_checklist_email(checklist: OperatorChecklist, res: Reservation, car_map_path: str):
    subject = f"Checklist de {checklist.stage.capitalize()} — Reserva #{res.id}"
    html = render_template(
        "emails/checklist.html",
        checklist=checklist,
        reservation=res,
        car_map_path=car_map_path,
    )
    try:
        from app.services.mailer import send_tenant_mail_html
        ok = send_tenant_mail_html(
            tenant=g.tenant,
            subject=subject,
            html=html,
            to=checklist.customer_email,
            text_alt="Segue o checklist da reserva."
        )
        if not ok:
            current_app.logger.info(
                "[EMAIL MOCK] (tenant=%s) To=%s Subject=%s",
                g.tenant.slug, checklist.customer_email, subject
            )
    except Exception:
        current_app.logger.exception("Falha ao enviar e-mail do checklist")



def _first_col(model, names: list[str]):
    """Retorna a primeira coluna existente no model pelas opções fornecidas."""
    for n in names:
        col = getattr(model, n, None)
        if isinstance(col, InstrumentedAttribute):
            return col
    return None

def _like_or_none(col, like):
    """Cria expressão LIKE se a coluna existir, senão retorna None."""
    if isinstance(col, InstrumentedAttribute) and like:
        return col.ilike(like)
    return None

def _first_value(obj, names: list[str]):
    """Retorna o primeiro atributo não vazio encontrado no objeto."""
    for n in names:
        v = getattr(obj, n, None)
        if v:
            return v
    return None
def _first_col(model, names):
    from sqlalchemy.orm.attributes import InstrumentedAttribute
    for n in names:
        col = getattr(model, n, None)
        if isinstance(col, InstrumentedAttribute):
            return col
    return None

def _set_first_attr(obj, names, value):
    for n in names:
        if hasattr(obj, n):
            setattr(obj, n, value)
            return n
    return None

def absolute_url_for_static(rel_path: str) -> str:
    """Converte '/static/...' em URL absoluta."""
    if not rel_path:
        return ''
    root = request.url_root.rstrip('/')
    return f"{root}{rel_path}"

def render_checklist_pdf(checklist: OperatorChecklist, reservation: Reservation, car_map_path: str) -> str:
    html = render_template(
        'pdfs/checklist.html',
        checklist=checklist,
        reservation=reservation,
        car_map_path=car_map_path,
        absolute_url=absolute_url_for_static
    )
    pdf_bytes = HTML(string=html, base_url=request.url_root).write_pdf()

    fname = f"checklist_{checklist.stage}_{uuid.uuid4().hex}.pdf"
    dest = _uploads_ck_dir('checklists', g.tenant.slug, 'pdfs') / fname  # <<< por tenant
    with open(dest, 'wb') as f:
        f.write(pdf_bytes)

    return f"/static/uploads/checklists/{g.tenant.slug}/pdfs/{fname}"



def _abs_static(rel_path: str) -> Path:
    """Converte '/static/...' em caminho físico dentro de app/static."""
    return Path(current_app.root_path) / rel_path.lstrip('/')


@admin_bp.post("/settings/mail.test")
@login_required
def mail_test():
    t = g.tenant

    # aceita JSON {"to": "..."} ou form
    payload = {}
    if request.is_json:
        try: payload = request.get_json(silent=True) or {}
        except Exception: payload = {}

    to_email = (payload.get("to") or request.form.get("to") or "").strip()
    if not to_email:
        # fallback: usuário logado ou remetente configurado
        to_email = getattr(current_user, "email", None) or (t.mail_from_email or None)

    if not to_email:
        return jsonify(ok=False, error="Informe um e-mail destino ou configure o remetente."), 400

    try:
        cfg = get_tenant_mail_creds(t)
        if not cfg:
            current_app.logger.info(
                "[EMAIL MOCK] (tenant=%s) To=[%s] Subject=Teste de e-mail — %s",
                t.slug, to_email, t.name or t.slug
            )
            return jsonify(ok=True, mock=True)

        send_test_mail(
            cfg=cfg,
            subject=f"Teste de e-mail — {t.name or t.slug}",
            body="E-mail de teste enviado com as credenciais deste tenant.",
            from_name=t.mail_from_name or t.name or "Locadora",
            from_email=t.mail_from_email or cfg.get("user") or "no-reply@example.com",
            to_email=to_email,
        )
        return jsonify(ok=True)
    except Exception as e:
        current_app.logger.exception("mail_test")
        return jsonify(ok=False, error=str(e)), 500
