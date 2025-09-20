# app/cli_site.py
"""
CLI standalone para criar (seed) o microsite do tenant.
Uso:
  python -m app.cli_site seed-site --tenant <slug-do-tenant> [--publish/--no-publish]
Exemplo:
  python -m app.cli_site seed-site --tenant locadora1 --publish
"""

import sys
import json
import click
from datetime import datetime

# importa a app e o DB
from app import create_app
from app.extensions import db

# modelos
from app.models import Tenant  # ajuste se seu caminho for diferente
from app.models_site import Site, SitePage, SiteBlock


def _get_or_create_site(tenant: Tenant) -> Site:
    site = Site.query.filter_by(tenant_id=tenant.id).first()
    if site:
        return site

    site = Site(
        tenant_id=tenant.id,
        theme="clean",
        site_title=tenant.name or "Sua Locadora",
        site_description="Gestão de locação de veículos com reserva online.",
        primary_color="#0d6efd",
        secondary_color="#6c757d",
        logo_url=(tenant.brand_logo_url if hasattr(tenant, "brand_logo_url") else None),
    )
    db.session.add(site)
    db.session.flush()
    return site


def _ensure_home_page(site: Site) -> SitePage:
    home = SitePage.query.filter_by(site_id=site.id, is_home=True).first()
    if home:
        return home

    home = SitePage(
        site_id=site.id,
        title="Início",
        slug="/",
        order=1,
        is_home=True,
        meta_title=site.site_title or "Sua Locadora",
        meta_description=site.site_description or "Reserve seu carro agora.",
    )
    db.session.add(home)
    db.session.flush()
    return home


def _make_block(block_type: str, order: int, payload: dict) -> SiteBlock:
    return SiteBlock(
        block_type=block_type,
        order=order,
        is_visible=True,
        payload=payload or {},
    )


def _seed_blocks_for_home(home: SitePage, tenant: Tenant):
    """Cria 3 blocos padrão na home se ainda não existirem."""
    existing = {b.block_type for b in home.blocks}

    # 1) HERO
    if "HERO" not in existing:
        hero = _make_block(
            "HERO",
            1,
            {
                "headline": tenant.name or "Sua Locadora de Confiança",
                "subline": "Reserve online com confirmação rápida e atendimento direto.",
                "bg_image": None,
                "cta_text": "Buscar carros",
                "cta_url": f"/{tenant.slug}/site#frota",
                "align": "center",
                "style": "filled",
            },
        )
        hero.page = home
        db.session.add(hero)

    # 2) ABOUT
    if "ABOUT" not in existing:
        about = _make_block(
            "ABOUT",
            2,
            {
                "title": "Sobre nós",
                "html": (
                    "<p>Atendemos com transparência, frota selecionada e preços claros. "
                    "Nosso processo de retirada e devolução é ágil, com checklist e contrato digital.</p>"
                ),
                "features": [
                    {"icon": "bi-check2-circle", "text": "Reserva online e confirmação rápida"},
                    {"icon": "bi-shield-lock", "text": "Checklists e contratos digitais"},
                    {"icon": "bi-geo-alt", "text": "Retirada e devolução facilitadas"},
                ],
            },
        )
        about.page = home
        db.session.add(about)

    # 3) FLEET_SEARCH (busca embutida)
    if "FLEET_SEARCH" not in existing:
        fleet = _make_block(
            "FLEET_SEARCH",
            3,
            {
                "title": "Encontre seu carro",
                "anchor": "frota",  # para #frota
                "show_filters": True,
                "show_dates": True,
                "default_sort": "price_asc",
                # Opcionalmente, você pode filtrar por categoria ou mostrar todas:
                "categories": [],  # vazio = todas
                "cta_book_text": "Reservar",
            },
        )
        fleet.page = home
        db.session.add(fleet)


@click.group()
def cli():
    pass


@cli.command("seed-site")
@click.option("--tenant", "tenant_slug", required=True, help="Slug do tenant (ex.: locadora1)")
@click.option("--publish/--no-publish", default=True, help="Publicar o site após criar/atualizar")
def seed_site(tenant_slug: str, publish: bool):
    """Cria/atualiza o microsite do tenant com homepage + blocos padrão."""
    app = create_app()
    with app.app_context():
        tenant = Tenant.query.filter_by(slug=tenant_slug).first()
        if not tenant:
            click.echo(f"[ERRO] Tenant com slug '{tenant_slug}' não encontrado.", err=True)
            sys.exit(1)

        site = _get_or_create_site(tenant)
        home = _ensure_home_page(site)
        _seed_blocks_for_home(home, tenant)

        if publish:
            site.is_enabled = True
            site.is_published = True
            site.published_at = site.published_at or datetime.utcnow()

        site.updated_at = datetime.utcnow()
        db.session.commit()

        click.echo(f"[OK] Site semeado para tenant '{tenant_slug}'.")
        click.echo(f"     Home: slug='/' | publicado={site.is_published} | theme={site.theme}")
        click.echo(f"     Acesse: /{tenant.slug}/site")


if __name__ == "__main__":
    cli()
