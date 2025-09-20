# app/admin/routes_email_test.py
from __future__ import annotations

import os
from flask import Blueprint, jsonify, request
from app.services.mailer import send_platform_mail_html
from app.services import mailer as mailer_mod  # acessar flags/funcs internas

try:
    # Disponibiliza versão dos pacotes no diagnóstico
    import importlib.metadata as md  # py3.8+
except Exception:  # pragma: no cover
    md = None  # ambiente muito restrito

emailtest_bp = Blueprint("emailtest", __name__)


@emailtest_bp.get("/admin/test-email")
def test_email():
    """
    Testa envio de e-mail pela PLATAFORMA.

    Query params:
      - to=dest@dominio
      - debug=1         -> retorna bloco 'diag' com informações do ambiente
      - force_acs=1     -> envia SOMENTE via ACS (sem fallback p/ SMTP) p/ ver erro real
    """
    to = (request.args.get("to") or "").strip()
    debug = (request.args.get("debug") == "1")
    force_acs = (request.args.get("force_acs") == "1")

    if not to:
        return jsonify(ok=False, error="use ?to=email@dominio.com"), 400

    # ----------------------------
    # Bloco de diagnóstico (opcional)
    # ----------------------------
    diag = {}
    if debug:
        # Estado percebido pelo módulo mailer
        try:
            acs_on = mailer_mod._acs_enabled()  # noqa: SLF001 (uso interno deliberado)
        except Exception as e:
            acs_on = False
            diag["acs_check_error"] = str(e)

        # Versões de pacotes (se disponíveis)
        def _ver(pkg: str) -> str:
            try:
                return md.version(pkg) if md else "n/a"
            except Exception:
                return "not-installed"

        diag.update(
            {
                # flags internas do mailer
                "ACS_AVAILABLE_IMPORT": bool(getattr(mailer_mod, "_ACS_AVAILABLE", False)),
                "ACS_IMPORT_ERR": getattr(mailer_mod, "_ACS_IMPORT_ERR", "")
                or "",  # só aparece se você adicionou no mailer; senão string vazia

                # variáveis efetivas
                "ACS_EMAIL_CONNECTION_STRING_env": bool(
                    (os.getenv("ACS_EMAIL_CONNECTION_STRING") or "").strip()
                ),
                "EMAIL_FROM": mailer_mod._getenv("EMAIL_FROM") or "",  # noqa: SLF001
                "PLATFORM_SMTP_HOST": mailer_mod._getenv("PLATFORM_SMTP_HOST") or "",
                "PLATFORM_SMTP_USER": mailer_mod._getenv("PLATFORM_SMTP_USER") or "",

                # estado final
                "acs_enabled": bool(acs_on),

                # versões úteis
                "pkg.azure_communication_email": _ver("azure-communication-email"),
                "pkg.azure_core": _ver("azure-core"),
            }
        )

    # ----------------------------
    # Envio
    # ----------------------------
    try:
        if force_acs:
            # Força ACS direto, sem fallback, para capturar erro real de config/import.
            mailer_mod._send_via_acs(  # noqa: SLF001
                subject="Teste ACS — 4uFleet",
                html="<p>Funcionou via <strong>ACS</strong> 👌</p>",
                text="Funcionou via ACS",
                to=to,
                reply_to=mailer_mod._getenv("PLATFORM_REPLY_TO", "support@4ufleet.com"),  # noqa: SLF001
            )
            ok = True
        else:
            ok = bool(
                send_platform_mail_html(
                    subject="Teste Plataforma — 4uFleet",
                    html="<p>Teste de envio 👌</p>",
                    to=to,
                    text_alt="Teste de envio",
                )
            )

        return jsonify(ok=ok, **({"diag": diag} if debug else {}))

    except Exception as e:
        # Erro real (de ACS quando force_acs=1, ou de SMTP/platform)
        return (
            jsonify(
                ok=False,
                error=str(e),
                **({"diag": diag} if debug else {}),
            ),
            500,
        )
