# app/superadmin/guards.py
from functools import wraps
from flask import abort
from flask_login import current_user, login_required

def sa_only(view):
    """Permite acesso apenas a usuários com is_superadmin=True."""
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not getattr(current_user, "is_superadmin", False):
            abort(403)
        return view(*args, **kwargs)
    return wrapper
