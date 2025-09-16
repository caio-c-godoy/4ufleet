# app/site/__init__.py
from flask import Blueprint

# sem prefixo (fica /landing diretamente)
site_bp = Blueprint("site", __name__)

# IMPORTANTE: carrega as rotas para “prender” os decorators no blueprint
from . import routes  # noqa: F401
