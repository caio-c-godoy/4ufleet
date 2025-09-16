# app/services/payments.py
import os, json, time, re
from app import utils
from app.extensions import db

def _kv_safe_name(name: str) -> str:
    # permitido pelo Azure Key Vault: letras, números e hífen
    name = name.lower()
    name = re.sub(r'[^0-9a-z-]+', '-', name)   # troca tudo que não é permitido por '-'
    name = re.sub(r'-{2,}', '-', name).strip('-')
    return name[:120]  # margem < 127 chars

def save_tenant_payment_creds(tenant, public_key: str, merchant_code: str, token: str | None, endpoint: str | None):
    env = os.environ.get("APP_ENV", "dev")
    alias_raw = f"gpay-{tenant.slug}-{env}"
    alias = _kv_safe_name(alias_raw)

    payload = {
        "public_key": public_key.strip(),
        "merchant_code": merchant_code.strip(),
        "token": (token or "").strip(),
        "saved_at": int(time.time()),
        "env": env,
        "provider": "globalpay",
    }

    # grava no Key Vault
    utils.kv_set_secret(alias, json.dumps(payload), tags={"tenant": tenant.slug, "env": env, "provider": "globalpay"})

    # guarda só o alias/endpoint no banco
    tenant.payment_secret_id = alias
    tenant.payment_endpoint = (endpoint or "").strip() or None
    db.session.add(tenant)
    db.session.commit()
    return alias
