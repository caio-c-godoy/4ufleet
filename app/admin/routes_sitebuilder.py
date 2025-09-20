# app/admin/routes_sitebuilder.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from flask import (
    render_template, request, redirect, url_for, flash, g, abort
)
from flask_login import login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from . import admin_bp
from app.extensions import db
from app.models import Tenant
from app.models_site import Site, SitePage, SiteBlock


# ----------------- helpers -----------------
def _now():
    """Retorna datetime timezone-aware (UTC), compatível com Py<3.11 e Py>=3.11."""
    try:
        from datetime import UTC
        return datetime.now(UTC)
    except Exception:
        return datetime.now(timezone.utc)


def _resolve_tenant_slug(incoming: Optional[str] = None) -> str:
    """
    Garante um tenant_slug mesmo se o blueprint não injetar.
    Para URLs: /<tenant_slug>/admin/site-builder[...]
    """
    if incoming:
        return incoming

    va = getattr(request, "view_args", {}) or {}
    if "tenant_slug" in va and va["tenant_slug"]:
        return va["tenant_slug"]

    # Ex.: "/locadora1/admin/site-builder" -> "locadora1"
    parts = (request.path or "").strip("/").split("/")
    # esperamos algo como ["locadora1", "admin", "site-builder", ...]
    if len(parts) >= 2 and parts[1] == "admin":
        return parts[0]

    abort(400, description="Não foi possível resolver o tenant da URL.")


def _get_tenant_or_404(tenant_slug: str) -> Tenant:
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    if not tenant:
        abort(404, description="Tenant não encontrado")
    g.tenant = tenant
    return tenant


def _get_or_create_site(tenant: Tenant) -> Site:
    site = (
        Site.query
        .options(joinedload(Site.pages).joinedload(SitePage.blocks))
        .filter_by(tenant_id=tenant.id)
        .first()
    )
    if site:
        return site

    site = Site(
        tenant_id=tenant.id,
        theme="clean",
        site_title=tenant.name or "Sua Locadora",
        site_description="Reserve online sua locação.",
        primary_color="#0d6efd",
        secondary_color="#6c757d",
        logo_url=getattr(tenant, "brand_logo_url", None),
        is_enabled=True,
        is_published=False,
        created_at=_now(),
        updated_at=_now(),
    )
    db.session.add(site)
    db.session.commit()
    return site


def _page_by_id(site: Site, page_id: int) -> SitePage | None:
    return next((p for p in site.pages if p.id == page_id), None)


def _block_by_id(page: SitePage, block_id: int) -> SiteBlock | None:
    return next((b for b in page.blocks if b.id == block_id), None)


# ----------------- views -----------------
# IMPORTANTE:
# O blueprint Admin está registrado com url_prefix="/<tenant_slug>/admin",
# então as views aceitam 'tenant_slug' e usamos SEM <tenant_slug> no decorator.


@admin_bp.get("/site-builder")
@login_required
def site_builder_home(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_or_create_site(tenant)

    pages = sorted(site.pages, key=lambda p: p.order)
    for p in pages:
        p.blocks_sorted = sorted(p.blocks, key=lambda b: b.order)

    return render_template(
        "admin/site_builder.html",
        site=site,
        pages=pages,
    )


@admin_bp.post("/site-builder/publish")
@login_required
def site_builder_publish(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_or_create_site(tenant)
    action = (request.form.get("action") or "").lower()  # "publish" | "unpublish"

    if action == "publish":
        site.is_published = True
        site.published_at = site.published_at or _now()
        flash("Microsite publicado.", "success")
    else:
        site.is_published = False
        flash("Microsite despublicado.", "warning")

    site.updated_at = _now()
    db.session.commit()
    return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))


@admin_bp.post("/site-builder/page/create")
@login_required
def site_builder_page_create(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_or_create_site(tenant)

    title = (request.form.get("title") or "").strip()
    slug = (request.form.get("slug") or "").strip()
    is_home = bool(request.form.get("is_home"))

    if not title:
        flash("Informe um título.", "warning")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    # normaliza slug
    if not slug:
        slug = "/"  # home
    if not slug.startswith("/"):
        slug = "/" + slug
    if slug != "/":
        slug = slug.rstrip("/")

    # próxima ordem
    max_order = (
        db.session.query(db.func.coalesce(db.func.max(SitePage.order), 0))
        .filter(SitePage.site_id == site.id)
        .scalar()
    )
    next_order = int(max_order) + 1

    page = SitePage(
        site_id=site.id,
        title=title,
        slug=slug,
        order=next_order,
        is_home=is_home,
        meta_title=title,
        meta_description=f"{title} — 4uFleet",
        created_at=_now(),  # hotfix até defaults do DB
        updated_at=_now(),
    )

    db.session.add(page)

    try:
        db.session.flush()  # garante page.id

        if is_home:
            # desmarcar as demais homes do mesmo site
            SitePage.query.filter(
                SitePage.site_id == site.id,
                SitePage.id != page.id
            ).update({"is_home": False}, synchronize_session=False)

        site.updated_at = _now()
        db.session.commit()
        flash("Página criada com sucesso.", "success")
    except IntegrityError as e:
        db.session.rollback()
        msg = "Não foi possível criar a página."
        if "uq_site_page_slug" in str(e.orig).lower():
            msg = "Já existe uma página com esse slug neste site."
        flash(msg, "danger")

    return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))


@admin_bp.post("/site-builder/page/delete")
@login_required
def site_builder_page_delete(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_or_create_site(tenant)

    page_id = request.form.get("page_id", type=int)
    page = _page_by_id(site, page_id)
    if not page:
        flash("Página não encontrada.", "danger")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    if page.is_home:
        flash("Não é possível excluir a página marcada como Home.", "warning")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    db.session.delete(page)
    site.updated_at = _now()
    db.session.commit()
    flash("Página excluída.", "success")
    return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))


@admin_bp.post("/site-builder/page/set-home")
@login_required
def site_builder_page_set_home(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)

    page_id = request.form.get("page_id", type=int)
    p = SitePage.query.get_or_404(page_id)
    if p.site.tenant_id != tenant.id:
        flash("Operação inválida.", "danger")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    try:
        SitePage.query.filter(
            SitePage.site_id == p.site_id,
            SitePage.id != p.id
        ).update({"is_home": False}, synchronize_session=False)
        p.is_home = True
        p.updated_at = _now()
        p.site.updated_at = _now()
        db.session.commit()
        flash("Página definida como Home.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Não foi possível definir Home (restrição de banco).", "danger")

    return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))


@admin_bp.post("/site-builder/block/add")
@login_required
def site_builder_block_add(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_or_create_site(tenant)

    page_id = request.form.get("page_id", type=int)
    block_type = (request.form.get("block_type") or "").strip().upper()

    page = _page_by_id(site, page_id)
    if not page:
        flash("Página não encontrada.", "danger")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    next_order = (max([b.order for b in page.blocks], default=0) + 1)

    default_payloads = {
        "HERO": {
            "headline": tenant.name or "Sua Locadora de Confiança",
            "subline": "Reserve online com praticidade.",
            "bg_image": None,
            "cta_text": "Buscar carros",
            "cta_url": f"/{tenant.slug}/site#frota",
            "align": "center",
        },
        "ABOUT": {
            "title": "Sobre nós",
            "html": "<p>Frota selecionada, transparência e agilidade na retirada e devolução.</p>",
            "features": [
                {"icon": "bi-check2-circle", "text": "Reserva online e confirmação rápida"},
                {"icon": "bi-shield-lock", "text": "Checklists e contratos digitais"},
                {"icon": "bi-geo-alt", "text": "Retirada e devolução facilitadas"},
            ],
        },
        "FLEET_SEARCH": {
            "title": "Encontre seu carro",
            "anchor": "frota",
            "show_filters": True,
            "show_dates": True,
            "default_sort": "price_asc",
            "categories": [],
            "cta_book_text": "Ver todos",
        },
    }

    if block_type not in default_payloads:
        flash("Tipo de bloco inválido.", "danger")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    block = SiteBlock(
        page_id=page.id,
        block_type=block_type,
        payload=default_payloads[block_type],
        order=next_order,
        is_visible=True,
        created_at=_now(),  # hotfix até defaults do DB
        updated_at=_now(),
    )
    db.session.add(block)

    site.updated_at = _now()
    db.session.commit()
    flash(f"Bloco {block_type} adicionado.", "success")
    return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))


@admin_bp.post("/site-builder/block/delete")
@login_required
def site_builder_block_delete(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_or_create_site(tenant)

    page_id = request.form.get("page_id", type=int)
    block_id = request.form.get("block_id", type=int)

    page = _page_by_id(site, page_id)
    block = _block_by_id(page, block_id) if page else None
    if not block:
        flash("Bloco não encontrado.", "danger")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    db.session.delete(block)
    site.updated_at = _now()
    db.session.commit()
    flash("Bloco excluído.", "success")
    return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))


@admin_bp.post("/site-builder/block/update")
@login_required
def site_builder_block_update(tenant_slug: str | None = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)

    block_id = request.form.get("block_id", type=int)
    page_id = request.form.get("page_id", type=int)

    b = SiteBlock.query.get_or_404(block_id)
    p = SitePage.query.get_or_404(page_id)

    # segurança: o bloco precisa pertencer à página e àquele tenant
    if b.page_id != p.id or p.site.tenant_id != tenant.id:
        flash("Operação inválida.", "danger")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    # atualiza payload conforme tipo
    payload = dict(b.payload or {})
    t = b.block_type

    if t == "HERO":
        payload["headline"] = request.form.get("headline") or payload.get("headline")
        payload["subline"]  = request.form.get("subline") or payload.get("subline")
        payload["cta_text"] = request.form.get("cta_text") or payload.get("cta_text")
        payload["cta_url"]  = request.form.get("cta_url") or payload.get("cta_url")
        payload["bg_image"] = request.form.get("bg_image") or payload.get("bg_image")
        payload["align"]    = request.form.get("align") or payload.get("align") or "center"

    elif t == "ABOUT":
        payload["title"]    = request.form.get("title") or payload.get("title")
        payload["html"]     = request.form.get("html") or payload.get("html")
        # features como lista simples (1 por linha)
        feats_raw = request.form.get("features")
        if feats_raw is not None:
            feats = []
            for line in feats_raw.splitlines():
                line = line.strip()
                if line:
                    feats.append({"icon": "bi-check2-circle", "text": line})
            payload["features"] = feats

    elif t == "FLEET_SEARCH":
        payload["title"]         = request.form.get("title") or payload.get("title")
        payload["cta_book_text"] = request.form.get("cta_book_text") or payload.get("cta_book_text")
        payload["show_dates"]    = bool(request.form.get("show_dates"))
        payload["show_filters"]  = bool(request.form.get("show_filters"))
        payload["default_sort"]  = request.form.get("default_sort") or payload.get("default_sort") or "price_asc"
        # categorias separadas por vírgula
        cats = request.form.get("categories")
        if cats is not None:
            payload["categories"] = [c.strip() for c in cats.split(",") if c.strip()]
        payload["anchor"]        = request.form.get("anchor") or payload.get("anchor") or "frota"

    # grava
    b.payload = payload
    b.updated_at = _now()
    b.page.updated_at = _now()
    b.page.site.updated_at = _now()
    db.session.commit()
    flash("Bloco atualizado.", "success")
    return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))


@admin_bp.post("/site-builder/block/reorder")
@login_required
def site_builder_block_reorder(tenant_slug: str | None = None):
    """
    Recebe: page_id e lista 'order[]' com IDs de blocos na nova ordem.
    """
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)

    page_id = request.form.get("page_id", type=int)
    ids = request.form.getlist("order[]")  # strings
    ids = [int(i) for i in ids if i.isdigit()]
    p = SitePage.query.get_or_404(page_id)

    if p.site.tenant_id != tenant.id:
        flash("Operação inválida.", "danger")
        return redirect(url_for("admin.site_builder_home", tenant_slug=tenant.slug))

    # aplica ordem 1..N somente para blocos desta página
    order = 1
    for bid in ids:
        b = SiteBlock.query.get(bid)
        if b and b.page_id == p.id:
            b.order = order
            b.updated_at = _now()
            order += 1
    p.updated_at = _now()
    p.site.updated_at = _now()
    db.session.commit()
    return ("", 204)
