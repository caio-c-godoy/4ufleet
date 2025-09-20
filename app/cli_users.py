# app/cli_users.py
"""
Criar/atualizar um admin para um tenant específico.

Uso:
  python -m app.cli_users seed-admin --tenant locadora1 --email admin@admin.com --password 123456
"""

from __future__ import annotations

import sys
import click
from datetime import datetime, timezone

from app import create_app
from app.extensions import db
from app.models import Tenant, User

try:
    HAVE_SETPW = hasattr(User, "set_password")
except Exception:
    HAVE_SETPW = False

try:
    from werkzeug.security import generate_password_hash
except Exception:
    generate_password_hash = None


def _set_password_on_instance(user: User, password: str):
    """
    Define a senha ANTES de adicionar o user na sessão/flush:
    - Usa user.set_password() se existir.
    - Senão, preenche user.password_hash com werkzeug.
    - Como último recurso, usa user.password.
    """
    if HAVE_SETPW and callable(getattr(user, "set_password", None)):
        user.set_password(password)
        return

    if hasattr(user, "password_hash") and generate_password_hash:
        user.password_hash = generate_password_hash(password)
        return

    if hasattr(user, "password"):
        user.password = password
        return

    raise RuntimeError(
        "Não foi possível definir a senha (sem set_password/password_hash/password)."
    )


@click.group()
def cli():
    pass


@cli.command("seed-admin")
@click.option("--tenant", "tenant_slug", required=True, help="Slug do tenant (ex.: locadora1)")
@click.option("--email", required=True, help="E-mail do usuário admin")
@click.option("--password", required=True, help="Senha a ser definida")
def seed_admin(tenant_slug: str, email: str, password: str):
    """
    Upsert do usuário por (tenant_id, email):
    - Se existir, atualiza senha e flags.
    - Se não existir, cria com tenant_id e JÁ define password antes do flush.
    - Se existir com mesmo email em outro tenant, realoca para este tenant.
    """
    app = create_app()
    with app.app_context():
        tenant = Tenant.query.filter_by(slug=tenant_slug).first()
        if not tenant:
            click.echo(f"[ERRO] Tenant '{tenant_slug}' não encontrado.", err=True)
            sys.exit(1)

        # 1) Procura por (tenant_id, email)
        user = User.query.filter_by(tenant_id=tenant.id, email=email).first()

        # 2) Se não houver, procura por email (pode estar em outro tenant)
        if not user:
            user_any = User.query.filter_by(email=email).first()
            if user_any:
                user = user_any  # vamos realocar tenant_id abaixo

        created = False
        if not user:
            # CRIA o user em memória e já define a senha ANTES de adicionar/flushear
            try:
                user = User(tenant_id=tenant.id, email=email)
                _set_password_on_instance(user, password)  # <-- define password_hash antes do add
                # flags comuns do seu schema (se existirem)
                if hasattr(user, "is_admin"):
                    user.is_admin = True
                if hasattr(user, "is_superadmin"):
                    user.is_superadmin = False
                if hasattr(user, "email_confirmed_at"):
                    user.email_confirmed_at = datetime.now(timezone.utc)
                if hasattr(user, "permissions") and getattr(user, "permissions") is None:
                    user.permissions = {}

                db.session.add(user)   # só agora adiciona
                db.session.commit()    # commit direto, evita autoflush parcial
                created = True
            except Exception as e:
                db.session.rollback()
                click.echo(f"[ERRO] criando usuário: {e}", err=True)
                sys.exit(1)
        else:
            # Já existe: realoca tenant se necessário
            try:
                if getattr(user, "tenant_id", None) != tenant.id:
                    user.tenant_id = tenant.id
                _set_password_on_instance(user, password)
                if hasattr(user, "is_admin"):
                    user.is_admin = True
                if hasattr(user, "is_superadmin"):
                    user.is_superadmin = False
                if hasattr(user, "email_confirmed_at"):
                    user.email_confirmed_at = datetime.now(timezone.utc)
                if hasattr(user, "permissions") and getattr(user, "permissions") is None:
                    user.permissions = {}
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                click.echo(f"[ERRO] atualizando usuário: {e}", err=True)
                sys.exit(1)

        click.echo(f"[OK] Admin {'criado' if created else 'atualizado'}: {email} em {tenant_slug}")
        click.echo(f"Login: http://localhost:5000/{tenant_slug}/auth/login  →  depois  /{tenant_slug}/admin/site-builder")


if __name__ == "__main__":
    cli()
