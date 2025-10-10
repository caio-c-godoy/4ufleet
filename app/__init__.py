# app/__init__.py
from __future__ import annotations
from pathlib import Path

import os
from datetime import date, datetime

from flask import (
    Flask, redirect, url_for, g, request, current_app, session
)
from dotenv import load_dotenv, find_dotenv
from flask_migrate import Migrate

# i18n
from flask_babel import Babel, gettext, ngettext, get_locale

from . import models_site  # noqa: F401
from .config import Config
from .extensions import db, login_manager
from app.admin.routes_email_test import emailtest_bp

from . import utils

migrate = Migrate()
babel = Babel()


# --- helper p/ gerar URL da mesma página trocando o lang ---
def locale_url(lang_code: str) -> str:
    try:
        ep = request.endpoint or "site.landing"

        # path vars (ex.: <tenant_slug>) + lang
        path_args = dict(request.view_args or {})
        path_args["lang"] = lang_code

        # query string atual (sem duplicar lang)
        qs = request.args.to_dict(flat=True) if request.args else {}
        qs.pop("lang", None)

        # monta a URL preservando endpoint, path args e query
        return url_for(ep, **path_args, **qs)
    except Exception:
        # fallback
        return url_for("site.landing", lang=lang_code)


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

    # ---------- i18n / Babel ----------
    app.config.setdefault("LANGUAGES", ["pt", "en", "es"])
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "pt")
    app.config.setdefault("BABEL_DEFAULT_TIMEZONE", "America/Sao_Paulo")
    _trans_dir = str((Path(app.root_path) / ".." / "translations").resolve())
    app.config["BABEL_TRANSLATION_DIRECTORIES"] = _trans_dir

    # Seletor de locale: ?lang= -> session -> Accept-Language -> default
    def _select_locale():
        langs = current_app.config.get("LANGUAGES", ["pt", "en", "es"])
        qlang = request.args.get("lang")
        if qlang and qlang in langs:
            session["lang"] = qlang
            return qlang
        slang = session.get("lang")
        if slang in langs:
            return slang
        return (
            request.accept_languages.best_match(langs)
            or current_app.config.get("BABEL_DEFAULT_LOCALE", "pt")
        )

    babel.init_app(app, locale_selector=_select_locale)

    # Helpers do Jinja (inclui locale_url!)
    app.jinja_env.globals.update(
        _=gettext,
        ngettext=ngettext,
        get_locale=get_locale,
        locale_url=locale_url,
    )
    app.jinja_env.add_extension("jinja2.ext.i18n")

    # --------- Filtros Jinja ----------
    from .filters import imgsrc
    app.jinja_env.filters["imgsrc"] = imgsrc
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
        if not path:
            return ""
        p = str(path).lstrip("/")
        if p.startswith("static/"):
            p = p[7:]
        return p

    # --------- Contexto comum ----------
    @app.context_processor
    def inject_common():
        def env(name, default=""):
            return os.getenv(name, default)

        t = getattr(g, "tenant", None)

        wa = getattr(g, "tenant_wa_public", None)
        if (not wa) and t:
            try:
                from app.services.tenant_settings import load_tenant_whatsapp
                wa = load_tenant_whatsapp(current_app.instance_path, t.slug) or ""
            except Exception:
                wa = ""

        return {
            "config": current_app.config,
            "env": env,
            "tenant": t,
            "current_tenant": t,      # alias legado
            "tenant_wa_public": wa,   # legado
            "wa_cfg_public": wa,      # usado no base_public
            "LANGUAGES": current_app.config.get("LANGUAGES", ["pt", "en", "es"]),
            "current_locale": str(get_locale() or "pt"),
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

    # --------- CSP mínima ----------
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
    from .site import site_bp
    from .public import public_bp
    from .auth import auth_bp
    from .admin import admin_bp
    from .superadmin import superadmin_bp

    app.register_blueprint(site_bp)
    app.register_blueprint(public_bp,  url_prefix="/<tenant_slug>")
    app.register_blueprint(auth_bp,    url_prefix="/<tenant_slug>/auth")
    app.register_blueprint(admin_bp,   url_prefix="/<tenant_slug>/admin")
    app.register_blueprint(superadmin_bp)
    app.register_blueprint(emailtest_bp, url_prefix="/")

    # --------- Home -> landing ----------
    @app.get("/")
    def index():
        return redirect(url_for("site.landing"))

    return app
