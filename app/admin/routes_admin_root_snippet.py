# Cole este trecho NO FINAL de app/admin/routes.py

from flask import redirect, url_for
from flask_login import login_required

# Redirecionar /<tenant>/admin e /<tenant>/admin/ para o dashboard
@admin_bp.get("")
@admin_bp.get("/")
@login_required
def admin_root():
    return redirect(url_for("admin.dashboard"))