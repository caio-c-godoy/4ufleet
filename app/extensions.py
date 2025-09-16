from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask import g, redirect, url_for


db = SQLAlchemy()
login_manager = LoginManager()

@login_manager.unauthorized_handler
def _unauth():
    # Redirect to tenant-scoped login if available
    tenant_slug = getattr(g, "tenant_slug", None)
    if tenant_slug:
        return redirect(url_for("auth.login", tenant_slug=tenant_slug))
    return redirect("/")
