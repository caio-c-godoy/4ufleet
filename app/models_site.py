# app/models_site.py
from __future__ import annotations

from sqlalchemy import func, UniqueConstraint, Index
from .extensions import db
from .models import Tenant  # ajuste o caminho se necessário


class Site(db.Model):
    __tablename__ = "sites"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenants.id"), unique=True, nullable=False
    )  # garante 1 site por tenant

    # publicação
    is_enabled = db.Column(db.Boolean, default=True, nullable=False)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    published_at = db.Column(db.DateTime(timezone=True))  # opcional, com TZ

    # tema/branding
    theme = db.Column(db.String(50), default="clean", nullable=False)
    primary_color = db.Column(db.String(9), default="#0d6efd")
    secondary_color = db.Column(db.String(9), default="#6c757d")
    font_family = db.Column(
        db.String(120),
        default="Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif",
    )

    # mídia principal
    logo_url = db.Column(db.String(255))
    hero_image_url = db.Column(db.String(255))

    # SEO global
    site_title = db.Column(db.String(120))
    site_description = db.Column(db.String(300))
    site_keywords = db.Column(db.String(300))

    # recursos IA (add-on pago)
    ai_copy_enabled = db.Column(db.Boolean, default=False, nullable=False)
    ai_image_enabled = db.Column(db.Boolean, default=False, nullable=False)

    # domínio customizado (MVP)
    custom_domain = db.Column(db.String(255))

    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tenant = db.relationship(
        Tenant,
        backref=db.backref("site", uselist=False, cascade="all, delete-orphan"),
    )
    pages = db.relationship(
        "SitePage",
        backref="site",
        cascade="all, delete-orphan",
        order_by="SitePage.order",
    )

    def __repr__(self):
        return f"<Site tenant_id={self.tenant_id} theme={self.theme}>"


class SitePage(db.Model):
    __tablename__ = "site_pages"

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, db.ForeignKey("sites.id"), nullable=False)

    title = db.Column(db.String(120), nullable=False, default="Página")
    slug = db.Column(db.String(120), nullable=False, default="/")  # "/", "/sobre", etc.
    order = db.Column(db.Integer, nullable=False, default=1)
    is_home = db.Column(db.Boolean, default=False, nullable=False)

    # SEO por página
    meta_title = db.Column(db.String(120))
    meta_description = db.Column(db.String(300))
    meta_keywords = db.Column(db.String(300))
    og_image_url = db.Column(db.String(255))

    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    blocks = db.relationship(
        "SiteBlock",
        backref="page",
        cascade="all, delete-orphan",
        order_by="SiteBlock.order",
    )

    __table_args__ = (
        # slug único por site
        UniqueConstraint("site_id", "slug", name="uq_site_page_slug"),
        # índice útil para ordenação/listagem
        Index("ix_site_pages_site_order", "site_id", "order"),
    )

    def __repr__(self):
        return f"<SitePage site_id={self.site_id} slug={self.slug}>"


class SiteBlock(db.Model):
    __tablename__ = "site_blocks"

    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey("site_pages.id"), nullable=False)

    # Tipos de bloco: HERO, ABOUT, FLEET_SEARCH, TESTIMONIALS, FAQ, CONTACT, GALLERY, FEATURES
    block_type = db.Column(db.String(40), nullable=False)

    # Conteúdo/Config em JSON (evitar default mutável)
    payload = db.Column(db.JSON, nullable=False, default=dict)

    order = db.Column(db.Integer, nullable=False, default=1)
    is_visible = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_site_blocks_page_order", "page_id", "order"),
    )

    def __repr__(self):
        return f"<SiteBlock page_id={self.page_id} type={self.block_type} order={self.order}>"


class MediaAsset(db.Model):
    __tablename__ = "media_assets"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)

    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(80))
    url = db.Column(db.String(255), nullable=False)  # URL pública (Azure Blob/static)
    size_bytes = db.Column(db.Integer)

    # metadados
    alt_text = db.Column(db.String(200))
    tags = db.Column(db.String(200))  # "logo,hero,frota"

    created_at = db.Column(
        db.DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tenant = db.relationship(
        Tenant, backref=db.backref("media_assets", cascade="all, delete-orphan")
    )

    def __repr__(self):
        return f"<MediaAsset tenant_id={self.tenant_id} filename={self.filename}>"
