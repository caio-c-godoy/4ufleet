from flask import Blueprint

# único ponto de criação do blueprint público
public_bp = Blueprint("public", __name__)

# carrega as rotas (que vão importar public_bp daqui)
from . import routes  # noqa: E402,F401
from . import routes_site  # noqa: F401

