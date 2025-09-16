# app/admin/__init__.py
from __future__ import annotations

from datetime import datetime
from flask import Blueprint, g, request
from app.extensions import db

# único ponto de criação do blueprint admin
admin_bp = Blueprint("admin", __name__)

@admin_bp.before_app_request
def ping_tenant_activity():
    """Marca a última atividade do tenant quando acessar /<slug>/admin/*."""
    t = getattr(g, "tenant", None)
    if not t:
        return

    if "/admin" in (request.path or ""):
        # evita gravar a cada request: só a cada 5 min
        last = t.last_activity_at or datetime(1970, 1, 1)
        if (datetime.utcnow() - last).total_seconds() > 300:
            t.last_activity_at = datetime.utcnow()
            db.session.commit()

# Carrega as rotas (que importam admin_bp daqui)
from . import routes  # noqa: E402,F401
