# app/public/routes_site.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from flask import render_template, abort, g, make_response, request
from sqlalchemy.orm import joinedload

from . import public_bp  # blueprint público
from app.models import Tenant
from app.models_site import Site, SitePage


# ---------- Helpers ----------
def _resolve_tenant_slug(incoming: Optional[str] = None) -> str:
    """Garante um tenant_slug funcionando mesmo se o blueprint não passar o parâmetro."""
    if incoming:
        return incoming

    # 1) Tenta pelo view_args
    va = getattr(request, "view_args", {}) or {}
    if "tenant_slug" in va and va["tenant_slug"]:
        return va["tenant_slug"]

    # 2) Tenta parsear do path: /<tenant>/site[/...]
    #    ex.: "/locadora1/site" -> "locadora1"
    parts = (request.path or "").strip("/").split("/")
    if len(parts) >= 2 and parts[1] == "site":
        return parts[0]

    abort(400, description="Não foi possível resolver o tenant da URL.")


def _get_tenant_or_404(tenant_slug: str) -> Tenant:
    tenant = Tenant.query.filter_by(slug=tenant_slug).first()
    if not tenant:
        abort(404, description="Tenant não encontrado")
    g.tenant = tenant
    return tenant


def _get_site_or_404(tenant: Tenant) -> Site:
    site = (
        Site.query
        .options(joinedload(Site.pages).joinedload(SitePage.blocks))
        .filter_by(tenant_id=tenant.id)
        .first()
    )
    if not site or not site.is_enabled:
        abort(404, description="Site do tenant não encontrado ou desabilitado")
    return site


def _select_page(site: Site, slug: Optional[str]) -> SitePage:
    if not slug or slug.strip("/") == "":
        for p in site.pages:
            if p.is_home:
                return p
    want = "/" + slug.strip("/")
    for p in site.pages:
        if p.slug == want:
            return p
    abort(404, description="Página não encontrada")


# ---------- Rotas ----------
# OBS: Mantemos o blueprint registrado com url_prefix="/<tenant_slug>" no seu __init__.py.
# Se por alguma razão o tenant_slug não for injetado, _resolve_tenant_slug cobre o caso.

@public_bp.get("/site")
def site_home(tenant_slug: Optional[str] = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_site_or_404(tenant)
    page = _select_page(site, slug=None)
    blocks = [b for b in sorted(page.blocks, key=lambda x: x.order) if b.is_visible]

    resp = make_response(
        render_template(
            "site/site_page.html",  # template virá no próximo passo
            site=site,
            page=page,
            blocks=blocks,
            now=datetime.now(timezone.utc),
        )
    )
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@public_bp.get("/site/<path:slug>")
def site_page(slug: str, tenant_slug: Optional[str] = None):
    tenant_slug = _resolve_tenant_slug(tenant_slug)
    tenant = _get_tenant_or_404(tenant_slug)
    site = _get_site_or_404(tenant)
    page = _select_page(site, slug=slug)
    blocks = [b for b in sorted(page.blocks, key=lambda x: x.order) if b.is_visible]

    resp = make_response(
        render_template(
            "site/site_page.html",  # template virá no próximo passo
            site=site,
            page=page,
            blocks=blocks,
            now=datetime.now(timezone.utc),
        )
    )
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp
