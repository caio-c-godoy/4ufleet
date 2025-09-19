# app/__init__.py
from __future__ import annotations

import os
from datetime import date, datetime

from flask import Flask, redirect, url_for, g, request
from dotenv import load_dotenv, find_dotenv
from flask_migrate import Migrate

from .config import Config
from .extensions import db, login_manager

migrate = Migrate()

# importa utils uma única vez; o filtro imgsrc vem daqui
from . import utils


def create_app() -> Flask:
    # Carrega .env
    load_dotenv(find_dotenv(), override=True)

    app = Flask(
        __name__,
        static_folder="../static",
        template_folder="../templates",
        instance_relative_config=True,
    )
    app.config.from_object(Config())
    app.config.setdefault("TEMPLATES_AUTO_RELOAD", True)
    
    from .filters import imgsrc
    app.jinja_env.filters['imgsrc'] = imgsrc
    # --------- Filtros Jinja ----------
    app.add_template_filter(utils.imgsrc, "imgsrc")

    def datefmt_long_pt(value):
        def _to_date(val):
            if isinstance(val, date) and not isinstance(val, datetime):
                return val
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, str):
                val = val.strip()
                try:
                    return datetime.fromisoformat(val).date()
                except Exception:
                    pass
                try:
                    d, m, y = val.split("/")
                    return date(int(y), int(m), int(d))
                except Exception:
                    pass
            return date.today()

        d = _to_date(value)
        meses = [
            "janeiro", "fevereiro", "março", "abril", "maio", "junho",
            "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
        ]
        return f"{d.day} de {meses[d.month-1]} de {d.year}"

    app.add_template_filter(datefmt_long_pt, "datefmt_long_pt")

    @app.template_filter("static_rel")
    def static_rel(path: str | None) -> str:
        """Remove 'static/' e a barra inicial para uso em url_for('static', filename=...)."""
        if not path:
            return ""
        p = str(path).lstrip("/")
        if p.startswith("static/"):
            p = p[7:]
        return p

    # --------- Contexto comum nos templates ----------
    @app.context_processor
    def inject_common():
        def env(name, default=""):
            return os.getenv(name, default)

        t = getattr(g, "tenant", None)
        return {
            "config": app.config,
            "env": env,
            "tenant": t,
            "current_tenant": t,  # alias p/ templates legados
        }

    # --------- Extensões ----------
    db.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    # --------- Middleware: corrige /static/https:/... & /static/http:/... ----------
    @app.before_request
    def _fix_broken_static_external():
        p = request.path or ""
        if p.startswith("/static/https:/") and not p.startswith("/static/https://"):
            return redirect("https://" + p[len("/static/https:/"):], code=302)
        if p.startswith("/static/http:/") and not p.startswith("/static/http://"):
            return redirect("http://" + p[len("/static/http:/"):], code=302)

    # --------- CSP mínima para páginas com editores ----------
    @app.after_request
    def apply_csp(resp):
        if request.endpoint in ("admin.settings", "admin.contract_preview", "admin.contract_validate") \
           or "/admin/settings" in (request.path or ""):
            resp.headers["Content-Security-Policy"] = (
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
                "worker-src 'self' blob:; child-src 'self' blob:;"
            )
        return resp

    # --------- Blueprints ----------
    from .site import site_bp              # /landing, /signup
    from .public import public_bp          # /<tenant_slug>/
    from .auth import auth_bp              # /<tenant_slug>/auth
    from .admin import admin_bp            # /<tenant_slug>/admin
    from .superadmin import superadmin_bp  # /superadmin

    app.register_blueprint(site_bp)
    app.register_blueprint(public_bp,  url_prefix="/<tenant_slug>")
    app.register_blueprint(auth_bp,    url_prefix="/<tenant_slug>/auth")
    app.register_blueprint(admin_bp,   url_prefix="/<tenant_slug>/admin")
    app.register_blueprint(superadmin_bp)

    # --------- Home -> landing ----------
    @app.get("/")
    def index():
        return redirect(url_for("site.landing"))

    return app
