# app/admin/routes_email_test.py
from flask import Blueprint, jsonify, request
from app.services.mailer import send_platform_mail_html
from app.services import mailer as mailer_mod  # para acessar flags internas

emailtest_bp = Blueprint("emailtest", __name__)

@emailtest_bp.get("/admin/test-email")
def test_email():
    """
    Teste de envio de e-mail via Plataforma.
    Query params:
      to=dest@dominio
      debug=1        -> inclui detalhes do ambiente
      force_acs=1    -> tenta somente ACS; não cai para SMTP
    """
    to = (request.args.get("to") or "").strip()
    debug = request.args.get("debug") == "1"
    force_acs = request.args.get("force_acs") == "1"

    if not to:
        return jsonify(ok=False, error="use ?to=email@dominio.com"), 400

    info = {}
    if debug:
        # expõe o que o módulo mailer enxerga
        try:
            acs_on = mailer_mod._acs_enabled()  # noqa
        except Exception as e:
            acs_on = False
            info["acs_check_error"] = str(e)
        info.update({
            "ACS_AVAILABLE_IMPORT": getattr(mailer_mod, "_ACS_AVAILABLE", False),
            "ACS_CONN_SET": bool(getattr(mailer_mod, "_ACS_CONN", None) or request.environ.get("ACS_EMAIL_CONNECTION_STRING")),
            "EMAIL_FROM": getattr(mailer_mod, "_EMAIL_FROM", None) or "",
            "PLATFORM_SMTP_HOST": (mailer_mod._getenv("PLATFORM_SMTP_HOST") or ""),
            "PLATFORM_SMTP_USER": (mailer_mod._getenv("PLATFORM_SMTP_USER") or ""),
            "acs_enabled": bool(acs_on),
        })

    # Tenta enviar
    try:
        if force_acs:
            # Força ACS direto, sem fallback, para capturar o erro real
            mailer_mod._send_via_acs(  # noqa
                subject="Teste ACS — RentCar",
                html="<p>Funcionou via ACS 👌</p>",
                text="Funcionou via ACS",
                to=to,
                reply_to=mailer_mod._getenv("PLATFORM_REPLY_TO", "support@4ufleet.com"),
            )
            ok = True
        else:
            ok = bool(send_platform_mail_html(
                subject="Teste Plataforma — RentCar",
                html="<p>Teste de envio 👌</p>",
                to=to,
                text_alt="Teste de envio"
            ))
        return jsonify(ok=ok, **({"diag": info} if debug else {}))
    except Exception as e:
        # Erro real do ACS (ou do SMTP, se ele forçado)
        return jsonify(ok=False, error=str(e), **({"diag": info} if debug else {})), 500
