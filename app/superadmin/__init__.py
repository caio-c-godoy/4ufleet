from __future__ import annotations
from flask import Blueprint

# Prefixo fica no próprio blueprint
superadmin_bp = Blueprint(
    "superadmin",
    __name__,
    url_prefix="/superadmin",
    template_folder="templates",   # caso use templates próprios
    static_folder=None,
)

# Importa as rotas (necessário para registrá-las de fato)
from . import routes  # noqa: E402,F401
