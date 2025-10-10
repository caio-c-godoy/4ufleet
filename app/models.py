# app/models.py
from __future__ import annotations

from datetime import datetime
from flask import url_for
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db, login_manager

# JSON portátil (JSONB no Postgres, JSON em outros)
try:
    from sqlalchemy.dialects.postgresql import JSONB as JSONType  # type: ignore
except Exception:
    from sqlalchemy.types import JSON as JSONType  # type: ignore

from sqlalchemy.ext.mutable import MutableDict

# ---------------------------------------------------------------------
# Mixin de escopo multi-tenant (marcador apenas)
# ---------------------------------------------------------------------
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
    brand_primary = db.Column(db.String(9))
    brand_navbar_bg = db.Column(db.String(9))
    brand_sidebar_bg = db.Column(db.String(9))
    terms_accepted_at = db.Column(db.DateTime)

    # E-mail por tenant
    mail_from_name = db.Column(db.String(120))
    mail_from_email = db.Column(db.String(120))
    mail_secret_id = db.Column(db.String(200))
    mail_provider = db.Column(db.String(30))

    # Pagamentos (seguro via KV)
    payment_secret_id = db.Column(db.String(200))
    payment_endpoint = db.Column(db.String(255))

    # Pagamentos legado (deprecado)
    gp_pub_key = db.Column(db.String(255))
    gp_token = db.Column(db.String(255))
    gp_merchant_code = db.Column(db.String(64))
    gp_payment_endpoint = db.Column(db.String(255))

    # Contrato (HTML Jinja)
    contract_template_html = db.Column(db.Text)

    # Login hero
    login_hero_enabled = db.Column(db.Boolean, default=True, nullable=False)
    login_hero_kicker = db.Column(db.String(120))
    login_hero_title = db.Column(db.String(180))
    login_hero_desc = db.Column(db.Text)
    login_hero_image = db.Column(db.String(300))

    # Assinatura (posicionamento)
    sign_x_rel = db.Column(db.Float)
    sign_y_rel = db.Column(db.Float)
    sign_w_pt = db.Column(db.Integer)
    sign_h_pt = db.Column(db.Integer)
    rub_w_pt = db.Column(db.Integer)
    rub_h_pt = db.Column(db.Integer)
    rub_margin_pt = db.Column(db.Integer)
    rubrica_on_last = db.Column(db.Boolean, default=False)
    audit_stamp = db.Column(db.Boolean, default=True)

    # Operação / plano
    is_blocked = db.Column(db.Boolean, default=False, nullable=False)
    weekly_min = db.Column(db.Integer, default=3, nullable=False)
    unlock_price_usd = db.Column(db.Numeric(10, 2), default=89.90, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_activity_at = db.Column(db.DateTime)
    to_be_deleted_at = db.Column(db.DateTime, nullable=True)

    # -------------------- Relacionamentos base --------------------
    users = db.relationship("User", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    vehicles = db.relationship("Vehicle", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    rates = db.relationship("Rate", back_populates="tenant", lazy=True, cascade="all, delete-orphan")

    # ⚠️ Desambiguação: reservations (legado) usa Reservation.tenant_id
    reservations = db.relationship(
        "Reservation",
        back_populates="tenant",
        lazy=True,
        cascade="all, delete-orphan",
        primaryjoin="Tenant.id==Reservation.tenant_id",
        foreign_keys="[Reservation.tenant_id]",
    )

    leads = db.relationship("Lead", back_populates="tenant", lazy=True, cascade="all, delete-orphan")
    maintenance_logs = db.relationship("MaintenanceLog", back_populates="tenant", lazy=True, cascade="all, delete-orphan")

    payments = db.relationship("Payment", back_populates="tenant", lazy="dynamic", cascade="all, delete-orphan")
    usage_snapshots = db.relationship("UsageSnapshot", back_populates="tenant", lazy="dynamic", cascade="all, delete-orphan")
    support_messages = db.relationship("SupportMessage", back_populates="tenant", lazy="dynamic", cascade="all, delete-orphan")

    # ---------- Compartilhamento de veículos ----------
    vehicles_shared_with_me = db.relationship(
        "VehicleShare",
        back_populates="shared_with_tenant",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="[VehicleShare.shared_with_tenant_id]",
    )
    partner_vehicles = db.relationship(
        "Vehicle",
        secondary="vehicle_shares",
        primaryjoin="Tenant.id==VehicleShare.shared_with_tenant_id",
        secondaryjoin="Vehicle.id==VehicleShare.vehicle_id",
        viewonly=True,
    )

    # ---------- Parcerias por código (restrição do universo de parceiros) ----------
    partners = db.relationship(
        "Tenant",
        secondary="tenant_partners",
        primaryjoin="Tenant.id==TenantPartner.tenant_id",
        secondaryjoin="Tenant.id==TenantPartner.partner_tenant_id",
        viewonly=True,
    )

    @property
    def logo_url(self):
        if not self.logo_path:
            return None
        return url_for("static", filename=self.logo_path)

    def __repr__(self) -> str:
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

    # Superadmin / auditoria
    is_superadmin = db.Column(db.Boolean, default=False, nullable=False)
    last_login_at = db.Column(db.DateTime)

    # Permissões (JSON mutável)
    permissions = db.Column(
        MutableDict.as_mutable(JSONType),
        default=dict,
        nullable=False,
    )

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
    slug = db.Column(db.String(80), nullable=False, index=True)
    description = db.Column(db.Text)

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

    # Compartilhamento
    shares = db.relationship(
        "VehicleShare",
        back_populates="vehicle",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    shared_with_tenants = db.relationship(
        "Tenant",
        secondary="vehicle_shares",
        primaryjoin="Vehicle.id==VehicleShare.vehicle_id",
        secondaryjoin="Tenant.id==VehicleShare.shared_with_tenant_id",
        viewonly=True,
    )

    def __repr__(self):
        return f"<Vehicle {self.brand} {self.model} #{self.id}>"


# =====================================================================
# RESERVATION
# =====================================================================
class Reservation(db.Model, TenantScoped):
    __tablename__ = "reservations"

    id = db.Column(db.Integer, primary_key=True)

    # Legado: tenant "dono do registro" (para compatibilidade de telas antigas)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    # Veículo/categoria
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"), nullable=True, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("vehicle_categories.id"), nullable=False, index=True)

    # Cliente
    customer_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    customer_doc = db.Column(db.String(120))
    customer_country = db.Column(db.String(80))
    customer_city_uf = db.Column(db.String(120))
    flight_no = db.Column(db.String(40))

    # Retirada/Devolução
    pickup_airport = db.Column(db.String(64), nullable=False)
    pickup_dt = db.Column(db.DateTime, nullable=False)
    dropoff_airport = db.Column(db.String(64), nullable=False)
    dropoff_dt = db.Column(db.DateTime, nullable=False)

    status = db.Column(db.String(32), default="pending", index=True)
    total_price = db.Column(db.Float, default=0.0)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Pagamento (opcional)
    gp_order_id = db.Column(db.String(64), index=True)
    gp_installments = db.Column(db.Integer)
    gp_payment_status = db.Column(db.String(32))
    gp_payment_method = db.Column(db.String(64))
    gp_auth_code = db.Column(db.String(64))
    gp_amount_brl = db.Column(db.Numeric(12, 2))
    gp_amount_usd = db.Column(db.Numeric(12, 2))
    gp_raw = db.Column(JSONType)

    # NOVO: quem vendeu x quem é dono do carro
    booking_tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    owner_tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)

    tenant = db.relationship("Tenant", back_populates="reservations", lazy=True, foreign_keys=[tenant_id])
    vehicle = db.relationship("Vehicle", back_populates="reservations", lazy=True)
    category = db.relationship("VehicleCategory", backref=db.backref("reservations", lazy=True))

    booking_tenant = db.relationship(
        "Tenant",
        foreign_keys=[booking_tenant_id],
        backref=db.backref("reservations_as_booking", lazy="dynamic"),
    )
    owner_tenant = db.relationship(
        "Tenant",
        foreign_keys=[owner_tenant_id],
        backref=db.backref("reservations_as_owner", lazy="dynamic"),
    )

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

    name = db.Column(db.String(120))
    email = db.Column(db.String(120))
    phone = db.Column(db.String(60))

    pickup_airport = db.Column(db.String(80))
    dropoff_airport = db.Column(db.String(80))
    pickup_dt = db.Column(db.DateTime)
    dropoff_dt = db.Column(db.DateTime)

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

    file_path = db.Column(db.String, nullable=False)
    signature_type = db.Column(db.String, default="drawn")
    signature_hash = db.Column(db.String, nullable=True)
    signed_at = db.Column(db.DateTime)

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

    marks = db.Column(db.JSON, nullable=False, default=dict)
    fuel_level = db.Column(db.Integer)
    odometer = db.Column(db.Integer)
    notes_ext = db.Column(db.Text)
    notes_int = db.Column(db.Text)
    photos = db.Column(db.JSON)
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
# USAGE SNAPSHOT
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
# PAYMENT
# =====================================================================
class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), index=True, nullable=False)

    amount_gross_usd = db.Column(db.Numeric(10, 2), nullable=False)
    fee_pct = db.Column(db.Numeric(5, 2), default=5.00, nullable=False)
    amount_fee_usd = db.Column(db.Numeric(10, 2), nullable=False)

    paid_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    external_id = db.Column(db.String(64))
    status = db.Column(db.String(20), default="succeeded", index=True)

    tenant = db.relationship("Tenant", back_populates="payments", lazy=True)

    def __repr__(self):
        return f"<Payment tenant_id={self.tenant_id} gross={self.amount_gross_usd} fee={self.amount_fee_usd}>"


# =====================================================================
# CHAT ADMIN TENANT
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
# PROSPECT
# =====================================================================
class Prospect(db.Model):
    __tablename__ = "prospects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False, index=True)
    phone = db.Column(db.String(60))

    source = db.Column(db.String(60), default="landing_pre_signup")
    status = db.Column(db.String(20), default="new", index=True)
    notes = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    last_contact_at = db.Column(db.DateTime)
    last_channel = db.Column(db.String(20))  # email | phone | whatsapp | other

    def status_badge(self):
        m = {"new": "secondary", "contacted": "info", "qualified": "primary", "converted": "success", "dropped": "dark"}
        return m.get((self.status or "new").lower(), "secondary")

    def __repr__(self):
        return f"<Prospect {self.email} status={self.status}>"


# =====================================================================
# VEHICLE SHARE (compartilhamento de veículo)
# =====================================================================
class VehicleShare(db.Model):
    __tablename__ = "vehicle_shares"

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    shared_with_tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    vehicle = db.relationship("Vehicle", back_populates="shares")
    shared_with_tenant = db.relationship("Tenant", back_populates="vehicles_shared_with_me")

    __table_args__ = (
        db.UniqueConstraint("vehicle_id", "shared_with_tenant_id", name="uq_vehicle_share_unique"),
    )


# =====================================================================
# PARTNER INVITE (código) + TENANT PARTNER (vínculo)
# =====================================================================
class PartnerInvite(db.Model):
    __tablename__ = "partner_invites"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    inviter_tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    invitee_tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    status = db.Column(db.String(16), default="pending", index=True)  # pending | claimed | revoked | expired
    note = db.Column(db.String(120))
    expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    inviter_tenant = db.relationship("Tenant", foreign_keys=[inviter_tenant_id])
    invitee_tenant = db.relationship("Tenant", foreign_keys=[invitee_tenant_id])

    def is_valid(self):
        if self.status != "pending":
            return False
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return False
        return True

    def __repr__(self):
        return f"<PartnerInvite code={self.code} status={self.status}>"


class TenantPartner(db.Model):
    """
    Parceria bilateral simples. Gravamos 1 linha por direção:
    (tenant_id -> partner_tenant_id) e a outra direção espelhada.
    """
    __tablename__ = "tenant_partners"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    partner_tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "partner_tenant_id", name="uq_tenant_partner_unique"),
    )

    tenant = db.relationship("Tenant", foreign_keys=[tenant_id])
    partner = db.relationship("Tenant", foreign_keys=[partner_tenant_id])

    def __repr__(self):
        return f"<TenantPartner tenant={self.tenant_id} partner={self.partner_tenant_id} active={self.active}>"
