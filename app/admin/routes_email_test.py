# app/admin/routes_email_test.py
from flask import Blueprint, jsonify, request
from app.services.mailer import send_platform_mail_html

emailtest_bp = Blueprint("emailtest", __name__)

@emailtest_bp.get("/admin/test-email")
def test_email():
    to = request.args.get("to")
    if not to:
        return jsonify(ok=False, error="use ?to=email@dominio.com"), 400
    ok = send_platform_mail_html(
        subject="Teste ACS OK — RentCar",
        html="<p>Funcionou via ACS 👌</p>",
        to=to,
        text_alt="Funcionou via ACS"
    )
    return jsonify(ok=bool(ok))
