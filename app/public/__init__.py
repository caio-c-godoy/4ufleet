from flask import Blueprint

# único ponto de criação do blueprint público
public_bp = Blueprint("public", __name__)

# carrega as rotas (que vão importar public_bp daqui)
from . import routes  # noqa: E402,F401
from . import routes_site  # noqa: F401

# === NOVO: injeta o número do WhatsApp em g antes de qualquer request público ===
from flask import g, current_app
from app.services.tenant_settings import load_tenant_whatsapp

@public_bp.before_app_request
def _inject_public_whatsapp():
    """
    Garante que g.tenant_wa_public esteja populado para qualquer página pública.
    """
    try:
        t = getattr(g, "tenant", None)
        if t:
            g.tenant_wa_public = load_tenant_whatsapp(current_app.instance_path, t.slug) or ""
        else:
            g.tenant_wa_public = ""
    except Exception:
        # em caso de erro, não quebra a página
        g.tenant_wa_public = ""