# app/__init__.py
from __future__ import annotations

import os
import subprocess
from datetime import date, datetime

from flask import Flask, redirect, url_for, g, request, session
from dotenv import load_dotenv, find_dotenv
from flask_migrate import Migrate
from . import models_site
from .config import Config
from .extensions import db, login_manager
from app.admin.routes_email_test import emailtest_bp

migrate = Migrate()

# importa utils uma única vez; o filtro imgsrc vem daqui
from . import utils


def create_app(config_override: dict | None = None) -> Flask:
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
    if config_override:
        app.config.update(config_override)
    app.config.setdefault("LANGUAGES", ["pt", "en", "es"])
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "pt")

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def _git_cmd(args: list[str]) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", repo_root, *args],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            return ""

    app_version = os.getenv("APP_VERSION") or os.getenv("RELEASE_VERSION") or ""
    git_sha = os.getenv("GIT_SHA") or os.getenv("COMMIT_SHA") or ""
    if not app_version:
        app_version = _git_cmd(["describe", "--tags", "--always"])
    if not git_sha:
        git_sha = _git_cmd(["rev-parse", "--short", "HEAD"])
    app.config["APP_VERSION"] = app_version
    app.config["GIT_SHA"] = git_sha

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

    def _gettext(message: str, **kwargs) -> str:
        if not kwargs:
            return message
        try:
            return message % kwargs
        except Exception:
            return message.format(**kwargs)

    def _select_locale() -> str:
        langs = app.config.get("LANGUAGES", ["pt", "en", "es"])
        qlang = request.args.get("lang")
        if qlang and qlang in langs:
            session["lang"] = qlang
            return qlang
        slang = session.get("lang")
        if slang in langs:
            return slang
        return app.config.get("BABEL_DEFAULT_LOCALE", "pt")

    def locale_url(lang_code: str) -> str:
        try:
            ep = request.endpoint or "site.landing"
            path_args = dict(request.view_args or {})
            path_args["lang"] = lang_code
            qs = request.args.to_dict(flat=True) if request.args else {}
            qs.pop("lang", None)
            return url_for(ep, **path_args, **qs)
        except Exception:
            return url_for("site.landing", lang=lang_code)

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
            "LANGUAGES": app.config.get("LANGUAGES", ["pt", "en", "es"]),
            "current_locale": _select_locale(),
            "locale_url": locale_url,
            "_": _gettext,
            "app_version": app.config.get("APP_VERSION", ""),
            "git_sha": app.config.get("GIT_SHA", ""),
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
    app.register_blueprint(emailtest_bp, url_prefix="/")

    return app
