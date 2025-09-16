# app/models.py
from __future__ import annotations

from datetime import datetime
from flask import url_for
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db, login_manager

# JSON portátil (usa JSONB no Postgres, JSON genérico nos demais)
try:
    from sqlalchemy.dialects.postgresql import JSONB as JSONType  # type: ignore
except Exception:  # fallback p/ SQLite, MySQL, etc.
    from sqlalchemy.types import JSON as JSONType  # type: ignore


# =====================================================================
# MIXIN: escopo multi-tenant (usado pelo filtro automático no __init__)
# =====================================================================
class TenantScoped:
    """Marque modelos multi-tenant herdando deste mixin."""
    pass


# =====================================================================
# TENANT
# =====================================================================
class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)

    # Identidade
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)

    # Branding
    logo_path = db.Column(db.String(255))
    brand_primary = db.Column(db.String(9))       # ex. '#0d6efd'
    brand_navbar_bg = db.Column(db.String(9))
    brand_sidebar_bg = db.Column(db.String(9))
    terms_accepted_at = db.Column(db.DateTime)

    # E-mail por tenant
    mail_from_name   = db.Column(db.String(120))
    mail_from_email  = db.Column(db.String(120))
    mail_secret_id   = db.Column(db.String(200))   # alias no Key Vault
    mail_provider    = db.Column(db.String(30))    # opcional (ex.: 'smtp')

    # PAGAMENTOS — seguro (via Key Vault)
    payment_secret_id = db.Column(db.String(200))  # guarda SÓ o alias, nunca a chave
    payment_endpoint  = db.Column(db.String(255))  # público/menos sensível

    # PAGAMENTOS — legado (deprecado, manter temporariamente)
    gp_pub_key = db.Column(db.String(255))           # DEPRECADO
    gp_token = db.Column(db.String(255))             # DEPRECADO
    gp_merchant_code = db.Column(db.String(64))      # DEPRECADO
    gp_payment_endpoint = db.Column(db.String(255))  # DEPRECADO

    # Contrato (HTML Jinja)
    contract_template_html = db.Column(db.Text)

    # Assinatura (posição/tamanho)
    sign_x_rel = db.Column(db.Float)
    sign_y_rel = db.Column(db.Float)
    sign_w_pt  = db.Column(db.Integer)
    sign_h_pt  = db.Column(db.Integer)
    rub_w_pt   = db.Column(db.Integer)
    rub_h_pt   = db.Column(db.Integer)
    rub_margin_pt = db.Column(db.Integer)
    rubrica_on_last = db.Column(db.Boolean, default=False)
    audit_stamp = db.Column(db.Boolean, default=True)

    # --------- NOVOS CAMPOS OPERACIONAIS / PLANO FREE ----------
    is_blocked = db.Column(db.Boolean, default=False, nullable=False)
    weekly_min = db.Column(db.Integer, default=3, nullable=False)
    unlock_price_usd = db.Column(db.Numeric(10, 2), default=89.90, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_activity_at = db.Column(db.DateTime)                 # atualizado quando alguém usa
    to_be_deleted_at = db.Column(db.DateTime, nullable=True)  # marcado p/ exclusão futura

    # Relacionamentos
    users = db.relationship("User", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    vehicles = db.relationship("Vehicle", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    rates = db.relationship("Rate", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    reservations = db.relationship("Reservation", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    leads = db.relationship("Lead", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    maintenance_logs = db.relationship("MaintenanceLog", back_populates="tenant", lazy=True, cascade="all, delete-orphan")

    # Novos relacionamentos
    payments = db.relationship("Payment", back_populates="tenant", lazy="dynamic", cascade="all, delete-orphan")
    usage_snapshots = db.relationship("UsageSnapshot", back_populates="tenant", lazy="dynamic", cascade="all, delete-orphan")
    support_messages = db.relationship(  # <--- chat
        "SupportMessage",
        back_populates="tenant",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    @property
    def logo_url(self):
        if not self.logo_path:
            return None
        return url_for("static", filename=self.logo_path)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tenant {self.slug}>"


# =====================================================================
# USER
# =====================================================================
class User(UserMixin, db.Model, TenantScoped):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    is_admin = db.Column(db.Boolean, default=True)
    email_confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # --------- NOVOS CAMPOS SUPERADMIN / AUDITORIA ----------
    is_superadmin = db.Column(db.Boolean, default=False, nullable=False)
    last_login_at = db.Column(db.DateTime)

    tenant = db.relationship("Tenant", back_populates="users", lazy=True)

    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except Exception:
        return None


# =====================================================================
# VEHICLE CATEGORY
# =====================================================================
class VehicleCategory(db.Model, TenantScoped):
    __tablename__ = "vehicle_categories"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    slug = db.Column(db.String(80), nullable=False, index=True)  # usado em seeds/rotas
    description = db.Column(db.Text)

    # Especificações padrão por categoria
    seats = db.Column(db.Integer, default=5)
    transmission = db.Column(db.String(20), default="Automatic")
    large_bags = db.Column(db.Integer, default=1)
    small_bags = db.Column(db.Integer, default=1)
    mileage_text = db.Column(db.String(40), default="Unlimited mileage")

    vehicles = db.relationship("Vehicle", back_populates="category", lazy=True)

    def __repr__(self):
        return f"<VehicleCategory {self.name}>"


# =====================================================================
# RATE
# =====================================================================
class Rate(db.Model, TenantScoped):
    __tablename__ = "rates"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("vehicle_categories.id", ondelete="CASCADE"), nullable=False, index=True)

    daily_rate = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(8), default="USD")
    min_age = db.Column(db.Integer, default=21)
    deposit_amount = db.Column(db.Float, default=200.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    tenant = db.relationship("Tenant", back_populates="rates", lazy=True)
    category = db.relationship("VehicleCategory", backref=db.backref("rates", lazy=True))

    def __repr__(self):
        return f"<Rate cat={self.category_id} {self.currency} {self.daily_rate}>"


# =====================================================================
# VEHICLE
# =====================================================================
class Vehicle(db.Model, TenantScoped):
    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("vehicle_categories.id"), nullable=False, index=True)

    plate = db.Column(db.String(32), unique=True)
    brand = db.Column(db.String(80))
    model = db.Column(db.String(120))
    year = db.Column(db.Integer)
    status = db.Column(db.String(32), default="available")  # available, maintenance, booked
    image_url = db.Column(db.String(255))
    color = db.Column(db.String(40))

    tenant = db.relationship("Tenant", back_populates="vehicles", lazy=True)
    category = db.relationship("VehicleCategory", back_populates="vehicles", lazy=True)

    reservations = db.relationship("Reservation", back_populates="vehicle", lazy=True)

    def __repr__(self):
        return f"<Vehicle {self.brand} {self.model} #{self.id}>"


# =====================================================================
# RESERVATION
# =====================================================================
class Reservation(db.Model, TenantScoped):
    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("vehicle_categories.id"), nullable=False, index=True)

    customer_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=False)

    # Dados extras p/ contrato
    customer_doc     = db.Column(db.String(120))   # CNH/passaporte/etc.
    customer_country = db.Column(db.String(80))    # País
    customer_city_uf = db.Column(db.String(120))   # Cidade/UF
    flight_no        = db.Column(db.String(40))    # Nº do voo

    pickup_airport = db.Column(db.String(64), nullable=False)
    pickup_dt = db.Column(db.DateTime, nullable=False)
    dropoff_airport = db.Column(db.String(64), nullable=False)
    dropoff_dt = db.Column(db.DateTime, nullable=False)

    status = db.Column(db.String(32), default="pending", index=True)
    total_price = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Payment (opcionais)
    gp_order_id = db.Column(db.String(64), index=True)
    gp_installments = db.Column(db.Integer)
    gp_payment_status = db.Column(db.String(32))
    gp_payment_method = db.Column(db.String(64))
    gp_auth_code = db.Column(db.String(64))
    gp_amount_brl = db.Column(db.Numeric(12, 2))
    gp_amount_usd = db.Column(db.Numeric(12, 2))
    gp_raw = db.Column(JSONType)  # payload bruto (auditoria)

    tenant = db.relationship("Tenant", back_populates="reservations", lazy=True)
    vehicle = db.relationship("Vehicle", back_populates="reservations", lazy=True)
    category = db.relationship("VehicleCategory", backref=db.backref("reservations", lazy=True))

    # 1:1 com Contract
    contract = db.relationship(
        "Contract",
        back_populates="reservation",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<Reservation #{self.id} status={self.status}>"


# =====================================================================
# MAINTENANCE LOG
# =====================================================================
class MaintenanceLog(db.Model, TenantScoped):
    __tablename__ = "maintenance_logs"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True)

    reason = db.Column(db.Text, nullable=False)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    finished = db.Column(db.Boolean, nullable=False, default=False)

    tenant = db.relationship("Tenant", back_populates="maintenance_logs", lazy=True)
    vehicle = db.relationship("Vehicle", backref=db.backref("maintenance_logs", lazy=True))

    def __repr__(self):
        return f"<MaintenanceLog vehicle_id={self.vehicle_id} finished={self.finished}>"


# =====================================================================
# LEAD (CRM)
# =====================================================================
class Lead(db.Model, TenantScoped):
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # contato
    name = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(60))

    # contexto da busca
    pickup_airport = db.Column(db.String(80))
    dropoff_airport = db.Column(db.String(80))
    pickup_dt = db.Column(db.DateTime)
    dropoff_dt = db.Column(db.DateTime)

    # pipeline simples
    stage = db.Column(db.String(20), default="new")  # new, contacted, converted, closed
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    tenant = db.relationship("Tenant", back_populates="leads", lazy=True)

    def stage_badge(self):
        m = {"new": "secondary", "contacted": "info", "converted": "success", "closed": "dark"}
        return m.get((self.stage or "new").lower(), "secondary")

    def __repr__(self):
        return f"<Lead {self.email or self.phone or self.name}>"


# =====================================================================
# CONTRACT (1:1 com Reservation)
# =====================================================================
class Contract(db.Model):
    __tablename__ = "contracts"

    id = db.Column(db.Integer, primary_key=True, index=True)
    reservation_id = db.Column(
        db.Integer,
        db.ForeignKey("reservations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    file_path = db.Column(db.String, nullable=False)          # caminho do PDF salvo
    signature_type = db.Column(db.String, default="drawn")    # drawn / certificate / generated
    signature_hash = db.Column(db.String, nullable=True)      # SHA256 do PDF/assinatura
    signed_at = db.Column(db.DateTime)                        # setado quando assinar

    reservation = db.relationship("Reservation", back_populates="contract", lazy=True)

    def __repr__(self):
        return f"<Contract res_id={self.reservation_id}>"


# =====================================================================
# OPERATOR CHECKLIST
# =====================================================================
class OperatorChecklist(db.Model):
    __tablename__ = "operator_checklists"

    id = db.Column(db.Integer, primary_key=True)
    reservation_id = db.Column(db.Integer, db.ForeignKey("reservations.id"), nullable=False, index=True)
    stage = db.Column(db.String(20), nullable=False)  # 'entrega' | 'devolucao'

    # Zonas marcadas (JSON) — ex.: {"front": ["L","C"], "left": ["F","M"], ...}
    marks = db.Column(db.JSON, nullable=False, default=dict)

    fuel_level = db.Column(db.Integer)  # 0-100
    odometer = db.Column(db.Integer)

    notes_ext = db.Column(db.Text)
    notes_int = db.Column(db.Text)

    photos = db.Column(db.JSON)  # lista de paths salvos
    signature_path = db.Column(db.String(255))

    operator_name = db.Column(db.String(120))
    customer_name = db.Column(db.String(120))
    customer_email = db.Column(db.String(120))
    pdf_path = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    signed_at = db.Column(db.DateTime)

    reservation = db.relationship('Reservation', backref=db.backref('checklists', lazy='dynamic'))

    def __repr__(self):
        return f"<OperatorChecklist res_id={self.reservation_id} stage={self.stage}>"


# =====================================================================
# USAGE SNAPSHOT (NOVO)
# =====================================================================
class UsageSnapshot(db.Model):
    __tablename__ = "usage_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), index=True, nullable=False)
    taken_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    rentals_last_7d = db.Column(db.Integer, default=0, nullable=False)
    reached_min = db.Column(db.Boolean, default=False, nullable=False)

    tenant = db.relationship("Tenant", back_populates="usage_snapshots", lazy=True)

    def __repr__(self):
        return f"<UsageSnapshot tenant_id={self.tenant_id} rentals7d={self.rentals_last_7d}>"


# =====================================================================
# PAYMENT (NOVO)
# =====================================================================
class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), index=True, nullable=False)

    amount_gross_usd = db.Column(db.Numeric(10, 2), nullable=False)    # total cobrado do cliente final
    fee_pct = db.Column(db.Numeric(5, 2), default=5.00, nullable=False) # sua taxa 5%
    amount_fee_usd = db.Column(db.Numeric(10, 2), nullable=False)       # = gross * fee_pct/100

    paid_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    external_id = db.Column(db.String(64))  # id do gateway
    status = db.Column(db.String(20), default="succeeded", index=True)

    tenant = db.relationship("Tenant", back_populates="payments", lazy=True)

    def __repr__(self):
        return f"<Payment tenant_id={self.tenant_id} gross={self.amount_gross_usd} fee={self.amount_fee_usd}>"


# =====================================================================
# CHAT ADMIN TENANT (NOVO)
# =====================================================================
class SupportMessage(db.Model):
    __tablename__ = "support_messages"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), index=True, nullable=False)
    sender = db.Column(db.String(20), nullable=False)  # "tenant" ou "superadmin"
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    tenant = db.relationship("Tenant", back_populates="support_messages", lazy=True)

    def __repr__(self):
        return f"<SupportMessage tenant_id={self.tenant_id} sender={self.sender}>"


# =====================================================================
# PROSPECT (pré-signup / CRM simples para Superadmin)
# =====================================================================
class Prospect(db.Model):
    __tablename__ = "prospects"

    id = db.Column(db.Integer, primary_key=True)
    # dados básicos
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(60))

    # tracking
    source = db.Column(db.String(60), default="landing_pre_signup")  # ex.: landing_pre_signup
    status = db.Column(db.String(20), default="new", index=True)     # new, contacted, qualified, converted, dropped
    notes = db.Column(db.Text)

    # atividades
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    last_contact_at = db.Column(db.DateTime)
    last_channel = db.Column(db.String(20))  # email | phone | whatsapp | other

    def status_badge(self):
        m = {
            "new": "secondary",
            "contacted": "info",
            "qualified": "primary",
            "converted": "success",
            "dropped": "dark",
        }
        return m.get((self.status or "new").lower(), "secondary")

    def __repr__(self):
        return f"<Prospect {self.email} status={self.status}>"
