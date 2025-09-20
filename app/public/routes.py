# app/public/routes.py
from __future__ import annotations

import os
import re
import json
import time
import base64
import requests
from io import BytesIO
from hashlib import sha256
from datetime import datetime
from pathlib import Path
from jinja2.sandbox import SandboxedEnvironment
from flask import (
    render_template, render_template_string, request, url_for, jsonify, g, abort, current_app,
    redirect, flash, session, make_response, send_file
)
from sqlalchemy import and_
from itsdangerous import URLSafeSerializer, BadSignature
from app import utils  

from app.extensions import db
from app.models import (
    Tenant, VehicleCategory, Rate, Vehicle, Lead, Reservation, Contract
)
from . import public_bp  # blueprint criado no __init__.py

from weasyprint import HTML
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas
from PIL import Image
from markupsafe import escape


# ====== EMAIL: enviar cópia do contrato assinado ao cliente (thread) ======
from threading import Thread
from pathlib import Path
import mimetypes

from flask import current_app, url_for
from sqlalchemy import select
from app.extensions import db
from app.services import mailer as mailer_service  # usa seu services/mailer.py

# Se existir um helper de URL absoluta no seu utils, usamos; se não, caímos no url_for(_external=True)
try:
    from app.utils import absolute_url_for as _abs_url
except Exception:
    _abs_url = None

def _email_contract_async(app, reserva_id: int):
    """Envia o PDF assinado para o e-mail do cliente (thread-safe)."""
    with app.app_context():
        try:
            from app.models import Reservation, Tenant, Contract  # ajuste se o import for diferente
            from app.services import mailer

            # reserva + tenant
            reserva = Reservation.query.get(reserva_id)
            if not reserva:
                current_app.logger.error("[mail] reserva %s não encontrada", reserva_id)
                return

            tenant = getattr(reserva, "tenant", None) or Tenant.query.get(getattr(reserva, "tenant_id", None))
            if tenant is None:
                current_app.logger.error("[mail] tenant não encontrado para reserva %s", reserva_id)
                return

            # destinatário
            to = getattr(reserva, "email", None) or getattr(reserva, "customer_email", None)
            if not to:
                current_app.logger.warning("[mail] reserva %s sem e-mail do cliente — não enviando", reserva_id)
                return

            # caminho do PDF assinado
            # 1) pelo registro Contract (se existir)  2) pelo path padrão
            signed_path = None
            contrato = Contract.query.filter_by(reservation_id=reserva_id).first()
            if contrato and contrato.file_path:
                p = Path(contrato.file_path)
                if p.exists():
                    signed_path = p
            if not signed_path:
                from .routes import _signed_pdf_path  # se a função já está neste módulo
                p = _signed_pdf_path(reserva_id)
                if p.exists():
                    signed_path = p

            if not signed_path:
                current_app.logger.error("[mail] PDF assinado não encontrado para reserva %s", reserva_id)
                return

            # assunto + corpo
            subject = f"{tenant.name} — Contrato assinado #{reserva_id}"
            html = (
                f"<p>Olá {getattr(reserva, 'name', '') or getattr(reserva, 'customer_name', '')},</p>"
                f"<p>Segue em anexo o seu contrato <strong>assinado</strong>.</p>"
                f"<p>Obrigado por escolher a {tenant.name}!</p>"
            )
            text = "Segue em anexo o seu contrato assinado."

            # anexo: (nome, mime, bytes)
            attach = [("contrato-assinado.pdf", "application/pdf", signed_path.read_bytes())]

            # dispara usando o serviço existente
            mailer.send_email_for_tenant(
                tenant=tenant,
                recipients=to,
                subject=subject,
                html=html,
                text_alt=text,
                attachments=attach,
            )
            current_app.logger.info("[mail] contrato enviado p/ %s (reserva %s)", to, reserva_id)

        except Exception as e:
            current_app.logger.exception("[mail] falha ao enviar contrato (reserva %s): %s", reserva_id, e)
# ====== /EMAIL ===============================================================



# =========================
# MULTITENANT (slug no prefix)
# =========================
@public_bp.url_value_preprocessor
def _pull_tenant(endpoint, values):
    if values is None:
        return
    g.tenant_slug = values.pop("tenant_slug", None)

@public_bp.url_defaults
def _add_tenant_slug(endpoint, values):
    if "tenant_slug" in values or not getattr(g, "tenant_slug", None):
        return
    values["tenant_slug"] = g.tenant_slug

@public_bp.before_request
def _load_tenant():
    slug = getattr(g, "tenant_slug", None)
    if not slug:
        abort(404)
    g.tenant = Tenant.query.filter_by(slug=slug).first_or_404()


# =========================
# CONTEXT (templates)
# =========================
@public_bp.app_context_processor
def _inject_public_ctx():
    # expõe tenant e helper de token nos templates renderizados por este blueprint
    return {
        "tenant": getattr(g, "tenant", None),
        "make_contract_token": make_contract_token,  # definido mais abaixo
    }


# =========================
# CONFIG HELPERS
# =========================
def _bool_cfg(key: str, default=False) -> bool:
    v = str(_cfg_str(key, "true" if default else "false")).strip().lower()
    return v in ("1","true","t","yes","y","on")

def _cfg(key: str, default=None):
    val = current_app.config.get(key)
    if val in (None, ""):
        env_val = os.getenv(key)
        val = env_val if env_val not in (None, "") else default
    return val.strip() if isinstance(val, str) else val

def _cfg_str(key: str, default: str = "") -> str:
    v = _cfg(key, default)
    return "" if v is None else str(v).strip()

def _cfg_float(key: str, default: float = 0.0) -> float:
    v = _cfg(key, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)
def _tenant_pay_creds() -> dict:
    """
    Tenta carregar as credenciais do tenant a partir do Key Vault usando
    tenant.payment_secret_id (ex.: 'gpay-locadora1-dev').
    Formato esperado do segredo (JSON):
      { "public_key": "...", "merchant_code": "...", "token": "", "env": "dev", ... }
    Se não existir, cai para variáveis de ambiente/config (_cfg).
    """
    alias = getattr(getattr(g, "tenant", None), "payment_secret_id", None)
    if alias:
        try:
            raw = utils.kv_get_secret(alias)  # string JSON
            data = json.loads(raw or "{}")
            return {
                "public_key": (data.get("public_key") or "").strip(),
                "merchant_code": (data.get("merchant_code") or "").strip(),
                "token": (data.get("token") or "").strip(),
                "endpoint": (data.get("endpoint") or "").strip(),
            }
        except Exception:
            current_app.logger.exception("Falha ao ler segredo do KV para %s", alias)

    # fallback: variáveis
    return {
        "public_key": _cfg_str("GP_PUB_KEY"),
        "merchant_code": _cfg_str("GP_MERCHANT_CODE"),
        "token": _cfg_str("GP_TOKEN"),
        "endpoint": _cfg_str("GP_PAYMENT_LINK_ENDPOINT"),
    }


# =========================
# UTILS
# =========================
def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def _calc_days(pu, do):
    if not pu or not do:
        return 1
    d = (do - pu).days
    return d if d >= 1 else 1

def _external_url(endpoint: str, **values) -> str:
    """Gera URL externa (útil para callback/return com ngrok)."""
    url = url_for(endpoint, _external=True, **values)
    base = _cfg_str("EXTERNAL_BASE_URL", "")
    if not base:
        return url
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url); ext = urlsplit(base)
    return urlunsplit((ext.scheme or "https", ext.netloc, parts.path, parts.query, parts.fragment))

def _gval(args, k, default=None):
    v = (args.get(k) or '').strip()
    return v or default

def _parse_query(args):
    q = {
        'pickup_airport': _gval(args, 'pickup_airport', ''),
        'dropoff_airport': _gval(args, 'dropoff_airport', ''),
        'pickup_date': _gval(args, 'pickup_date'),
        'pickup_time': _gval(args, 'pickup_time', '10:00'),
        'dropoff_date': _gval(args, 'dropoff_date'),
        'dropoff_time': _gval(args, 'dropoff_time', '10:00'),
        'name': _gval(args, 'name'),
        'phone': _gval(args, 'phone'),
        'email': _gval(args, 'email'),
        'sort': _gval(args, 'sort', 'recommended'),
        'min_price': _gval(args, 'min_price'),
        'max_price': _gval(args, 'max_price'),
        'seats_min': _gval(args, 'seats_min'),
        'page': args.get('page', type=int) or 1,
        'per_page': args.get('per_page', type=int) or 10,
    }
    q['cat_ids'] = [int(x) for x in args.getlist('cat') if str(x).isdigit()]
    if q['per_page'] < 1 or q['per_page'] > 50:
        q['per_page'] = 10
    if q['page'] < 1:
        q['page'] = 1
    return q

def _overlap_clause(start_dt, end_dt):
    # conflito quando: inicio_existente < novo_fim  E  fim_existente > novo_inicio
    return and_(
        Reservation.pickup_dt < end_dt,
        Reservation.dropoff_dt > start_dt,
        Reservation.status.in_(("confirmed",))
    )


# =========================
# LISTAGEM / BUSCA
# =========================
@public_bp.get('/')
def search():
    return render_template('public/search.html')

@public_bp.get('/results')
def results():
    q = _parse_query(request.args)

    # dias (mínimo 1)
    try:
        pu = datetime.strptime(f"{q['pickup_date']} {q['pickup_time']}", '%Y-%m-%d %H:%M')
        do = datetime.strptime(f"{q['dropoff_date']} {q['dropoff_time']}", '%Y-%m-%d %H:%M')
        days = (do - pu).days
        if days < 1:
            days = 1
    except Exception:
        pu = do = None
        days = 1

    # CRM (Lead)
    try:
        contact_ok = bool(q.get("name") or q.get("email") or q.get("phone"))
        if contact_ok and hasattr(g, "tenant") and g.tenant:
            existing = None
            if q.get("email"):
                existing = Lead.query.filter(
                    Lead.tenant_id == g.tenant.id,
                    Lead.email == q["email"]
                ).order_by(Lead.created_at.desc()).first()
            if not existing and q.get("phone"):
                existing = Lead.query.filter(
                    Lead.tenant_id == g.tenant.id,
                    Lead.phone == q["phone"]
                ).order_by(Lead.created_at.desc()).first()

            if not existing:
                lead = Lead(
                    tenant_id=g.tenant.id,
                    name=q.get("name"),
                    email=q.get("email"),
                    phone=q.get("phone"),
                    pickup_airport=q.get("pickup_airport"),
                    dropoff_airport=q.get("dropoff_airport"),
                    pickup_dt=pu,
                    dropoff_dt=do,
                    stage="new",
                )
                db.session.add(lead)
                db.session.commit()
            else:
                existing.pickup_airport = q.get("pickup_airport") or existing.pickup_airport
                existing.dropoff_airport = q.get("dropoff_airport") or existing.dropoff_airport
                if pu: existing.pickup_dt = pu
                if do: existing.dropoff_dt = do
                db.session.commit()
    except Exception:
        current_app.logger.exception("CRM lead capture failed")

    # filtros auxiliares
    seats_min = None
    try:
        if q['seats_min']:
            seats_min = int(q['seats_min'])
            if seats_min < 1:
                seats_min = None
    except ValueError:
        seats_min = None

    categories = (
        VehicleCategory.query
        .filter_by(tenant_id=g.tenant.id)
        .order_by(VehicleCategory.name.asc())
        .all()
    )
    rate_map = {r.category_id: r for r in Rate.query.filter_by(tenant_id=g.tenant.id).all()}

    query = Vehicle.query.filter_by(tenant_id=g.tenant.id)
    if q['cat_ids']:
        query = query.filter(Vehicle.category_id.in_(q['cat_ids']))
    vehicles = query.order_by(Vehicle.model.asc()).all()

    results_list = []
    for v in vehicles:
        r = rate_map.get(v.category_id)
        if not r:
            continue

        seats = v.category.seats if v.category and v.category.seats else 5
        if seats_min is not None and seats < seats_min:
            continue

        item = {
            'id': v.id,
            'model': v.model,
            'brand': v.brand,
            'year': v.year,
            'image_url': v.image_url,
            'category_name': v.category.name if v.category else '',
            'daily': float(r.daily_rate),
            'currency': r.currency or 'USD',
            'days': days,
            'total': float(r.daily_rate) * days,
            'discount': 10 if days >= 7 else None,
            'seats': seats,
            'transmission': (v.category.transmission if v.category else 'Automatic'),
            'large_bags': v.category.large_bags if v.category else 1,
            'small_bags': v.category.small_bags if v.category else 1,
            'mileage_text': v.category.mileage_text if v.category else 'Unlimited mileage',
        }
        # filtros de preço
        try:
            if q['min_price'] and item['daily'] < float(q['min_price']):  # noqa: SIM108
                continue
        except ValueError:
            pass
        try:
            if q['max_price'] and item['daily'] > float(q['max_price']):  # noqa: SIM108
                continue
        except ValueError:
            pass

        results_list.append(item)

    if q['sort'] == 'price_asc':
        results_list.sort(key=lambda x: x['daily'])
    elif q['sort'] == 'price_desc':
        results_list.sort(key=lambda x: -x['daily'])

    # paginação em memória
    total = len(results_list)
    pages = max((total + q['per_page'] - 1) // q['per_page'], 1)
    page = min(q['page'], pages) if pages else 1
    start = (page - 1) * q['per_page']; end = start + q['per_page']
    results_paged = results_list[start:end]

    pagination = {
        'page': page, 'per_page': q['per_page'], 'total': total, 'pages': pages,
        'has_prev': page > 1, 'has_next': page < pages,
        'prev_page': page - 1 if page > 1 else 1,
        'next_page': page + 1 if page < pages else pages,
    }

    currency = results_paged[0]['currency'] if results_paged else 'USD'
    return render_template(
        'public/results.html',
        q=q,
        categories=categories,
        results=results_paged,
        currency=currency,
        pagination=pagination
    )


# =========================
# MODAL & RESERVA
# =========================
@public_bp.get('/reserve/new')
def reserve_modal():
    q = _parse_query(request.args)

    vehicle_id = request.args.get('vehicle_id', type=int)
    vehicle = Vehicle.query.filter_by(tenant_id=g.tenant.id, id=vehicle_id).first_or_404()

    # calcular dias / valores
    try:
        pu = datetime.strptime(f"{q['pickup_date']} {q['pickup_time']}", '%Y-%m-%d %H:%M')
        do = datetime.strptime(f"{q['dropoff_date']} {q['dropoff_time']}", '%Y-%m-%d %H:%M')
        days = (do - pu).days
        if days < 1:
            days = 1
    except Exception:
        pu = do = None
        days = 1

    rate = Rate.query.filter_by(tenant_id=g.tenant.id, category_id=vehicle.category_id).first()
    if not rate:
        abort(400, "Categoria sem tarifa")

    daily = float(rate.daily_rate)
    total = daily * days
    discount = 10 if days >= 7 else None

    return render_template(
        'public/_reserve_form.html',
        vehicle=vehicle,
        q=q,
        daily=daily,
        days=days,
        total=total,
        discount=discount,
        currency=rate.currency or "USD",
    )

@public_bp.post('/reserve')
def reserve():
    q = _parse_query(request.form)
    vehicle_id = request.form.get('vehicle_id', type=int)
    vehicle = Vehicle.query.filter_by(tenant_id=g.tenant.id, id=vehicle_id).first_or_404()

    try:
        pu_dt = datetime.strptime(f"{q['pickup_date']} {q['pickup_time']}", '%Y-%m-%d %H:%M')
        do_dt = datetime.strptime(f"{q['dropoff_date']} {q['dropoff_time']}", '%Y-%m-%d %H:%M')
    except Exception:
        abort(400, "Datas inválidas")

    rate = Rate.query.filter_by(tenant_id=g.tenant.id, category_id=vehicle.category_id).first()
    if not rate:
        abort(400, "Categoria sem tarifa")

    days = max(1, (do_dt - pu_dt).days)
    total = float(rate.daily_rate) * days

    # cria pré-reserva (pending)
    res = Reservation(
        tenant_id=g.tenant.id,
        vehicle_id=vehicle.id,
        category_id=vehicle.category_id,
        customer_name=q.get('name') or '',
        phone=q.get('phone') or '',
        email=q.get('email') or '',
        pickup_airport=q.get('pickup_airport') or '',
        dropoff_airport=q.get('dropoff_airport') or '',
        pickup_dt=pu_dt,
        dropoff_dt=do_dt,
        status='pending',
        total_price=total,
        notes=None
    )
    db.session.add(res)
    db.session.commit()

    return jsonify({'redirect': url_for('public.checkout', tenant_slug=g.tenant.slug, reservation_id=res.id)})


# =========================
# PAYMENT LINK / AUTH
# =========================
def _gp_pl_token(force: bool = False) -> str | None:
    """Autentica e guarda token na sessão (TTL ~8min)."""
    TTL = 8 * 60
    now = int(time.time())
    if not force:
        tok = session.get("gp_pl_token")
        ts  = session.get("gp_pl_ts", 0)
        if tok and (now - ts) < TTL:
            return tok

    creds = _tenant_pay_creds()
    pub = creds.get("public_key") or ""
    mch = creds.get("merchant_code") or ""
    base = _cfg_str("GP_API_V1_BASE", "https://apihml.tryglobalpays.com/v1").rstrip("/")

    # se não tiver pub no KV, tenta token direto do KV/env
    env_tok = creds.get("token") or ""
    if not pub and env_tok:
        session["gp_pl_token"] = env_tok
        session["gp_pl_ts"] = now
        return env_tok

    if not pub:
        return None

    auth_urls = [f"{base}/paymentapi/auth", f"{base}/checkoutapi/auth"]
    for auth_url in auth_urls:
        for payload in ({"pubKey": pub, "merchantCode": mch}, {"pubKey": pub}):
            try:
                resp = requests.post(auth_url, data=payload, timeout=20)
                data = resp.json() if "application/json" in resp.headers.get("content-type","") else {}
                token = (
                    (data or {}).get("token")
                    or ((data or {}).get("data") or {}).get("token")
                    or ((data or {}).get("data") if isinstance((data or {}).get("data"), str) else None)
                )
                if token and resp.status_code < 400:
                    session["gp_pl_token"] = token
                    session["gp_pl_ts"] = now
                    current_app.logger.info("PaymentLink auth OK em %s", auth_url)
                    return token
            except Exception:
                current_app.logger.exception("Falha ao autenticar em %s", auth_url)
                continue

    if env_tok:
        session["gp_pl_token"] = env_tok
        session["gp_pl_ts"] = now
        current_app.logger.warning("Usando GP_TOKEN do KV/env como fallback (Payment Link).")
        return env_tok
    return None


def _header_variants(tok: str):
    base = {"Accept": "application/json", "Content-Type": "application/json"}
    return [
        dict(base, **{"token": tok}),
        dict(base, **{"Authorization": f"Bearer {tok}"}),
        dict(base, **{"authorization": f"Bearer {tok}"}),
        dict(base, **{"Token": tok}),
    ]

def _service_charge(total: float) -> float:
    rate = _cfg_float("COMMISSION_RATE", 0.05)  # 5%
    return round(total * rate, 2)

def _amounts_for_modes(total: float, currency: str) -> dict:
    """
    - deposit: só o sinal, sem taxa
    - full: total + taxa
    - balance: (total + taxa) - sinal
    """
    deposit = _cfg_float("MIN_DEPOSIT_USD", 50)
    svc = _service_charge(total)
    full_amount = round(total + svc, 2)
    balance_amount = max(0.0, round(full_amount - deposit, 2))
    return {
        "currency": currency,
        "deposit": deposit,
        "service_charge": svc,
        "full": full_amount,
        "balance": balance_amount,
    }

def _with_commission_split(payload: dict, commission_amount: float) -> dict:
    plat = _cfg_str("PLATFORM_MERCHANT_CODE", "")
    if plat and commission_amount and commission_amount > 0:
        payload["commissionSplit"] = [
            {"merchantCode": plat, "amount": round(commission_amount, 2)}
        ]
    return payload


@public_bp.get('/checkout/<int:reservation_id>')
def checkout(reservation_id):
    r = Reservation.query.filter_by(tenant_id=g.tenant.id, id=reservation_id).first_or_404()
    v = Vehicle.query.filter_by(tenant_id=g.tenant.id, id=r.vehicle_id).first_or_404()
    rate = Rate.query.filter_by(tenant_id=g.tenant.id, category_id=v.category_id).first()

    daily = float(rate.daily_rate) if rate and rate.daily_rate is not None else 0.0
    currency = (rate.currency or 'USD') if rate else 'USD'
    pickup_dt  = getattr(r, 'pickup_dt', None)  or getattr(r, 'pickup_at', None)
    dropoff_dt = getattr(r, 'dropoff_dt', None) or getattr(r, 'dropoff_at', None)
    days  = _calc_days(pickup_dt, dropoff_dt)
    total = round(daily * days, 2)

    seats = v.category.seats if (v.category and v.category.seats is not None) else 5
    transmission = v.category.transmission if (v.category and v.category.transmission) else 'Automatic'
    mileage_text = v.category.mileage_text if (v.category and v.category.mileage_text) else 'Unlimited mileage'

    amounts = _amounts_for_modes(total, currency)
    commission_rate_pct = int(round(_cfg_float("COMMISSION_RATE", 0.05) * 100))

    # liberar pagamento depois de assinar (mostra status)
    contract = Contract.query.filter_by(reservation_id=r.id).first()

    # Tokens/links protegidos
    ctok = make_contract_token(r.id)
    contract_view_url = url_for('public.view_contract', tenant_slug=g.tenant.slug, reserva_id=r.id, t=ctok)
    contract_sign_url = url_for('public.sign_contract', tenant_slug=g.tenant.slug, reserva_id=r.id, t=ctok)
    contract_download_url = url_for('public.download_signed_contract', tenant_slug=g.tenant.slug, reserva_id=r.id, t=ctok)

    # pronto para pagar se tiver pubKey ou token vindos do KV/env
    creds = _tenant_pay_creds()
    pl_ready = bool((creds.get("public_key") or "").strip() or (creds.get("token") or "").strip())


    return render_template(
        'public/checkout.html',
        reservation=r, reservation_id=r.id, vehicle=v,
        pickup_airport=getattr(r, 'pickup_airport', ''), dropoff_airport=getattr(r, 'dropoff_airport', ''),
        pickup_dt=pickup_dt, dropoff_dt=dropoff_dt,
        days=days, daily=daily, total=total, currency=currency,
        seats=seats, transmission=transmission, mileage_text=mileage_text,
        pl_ready=pl_ready,
        service_charge=amounts["service_charge"],
        grand_total=amounts["full"],
        deposit_min=amounts["deposit"],
        commission_rate_pct=commission_rate_pct,
        amounts=amounts,
        contract=contract,
        contract_token=ctok,
        contract_view_url=contract_view_url,
        contract_sign_url=contract_sign_url,
        contract_download_url=contract_download_url,
    )


# Salva os dados do modal (checkout) e devolve a URL da assinatura com token
@public_bp.post("/checkout/<int:reservation_id>/customer")
def checkout_capture_customer(reservation_id):
    res = Reservation.query.filter_by(tenant_id=g.tenant.id, id=reservation_id).first_or_404()
    data = request.get_json(silent=True) or {}

    # Nome
    name = (data.get("customer_name") or "").strip()
    if name:
        res.customer_name = name

    # Documento do motorista (CNH/Passaporte) — usamos driver_id e mantemos compat com customer_doc
    driver_id = (data.get("driver_id") or data.get("customer_doc") or "").strip()
    if driver_id:
        res.customer_doc = driver_id

    # País (ISO-2, ex.: BR, US…)
    country = (data.get("customer_country") or "").strip().upper()
    if country:
        res.customer_country = country

    # Cidade/UF: se BR, montamos "Cidade/UF"; caso contrário, aceita customer_city_uf bruto
    city_uf = (data.get("customer_city_uf") or "").strip()
    if country == "BR":
        city  = (data.get("customer_city") or "").strip()
        state = (data.get("customer_state") or "").strip().upper()
        city_uf = f"{city}/{state}" if (city and state) else ""
    if city_uf:
        res.customer_city_uf = city_uf
    else:
        res.customer_city_uf = None

    # Nº do voo (opcional)
    flight_no = (data.get("flight_no") or "").strip().upper()
    res.flight_no = flight_no or None

    db.session.commit()

    # devolvemos a URL de assinatura JÁ COM TOKEN (seguro em produção)
    ctok = make_contract_token(reservation_id)
    redirect_url = url_for("public.sign_contract",
                           tenant_slug=g.tenant.slug,
                           reserva_id=reservation_id,
                           t=ctok)
    return jsonify(ok=True, redirect_url=redirect_url)



@public_bp.post('/checkout/<int:reservation_id>/pay/link')
def checkout_pay_link(reservation_id):
    """Gera Payment Link (full/deposit/balance) e redireciona para o provedor."""
    want_json = (request.args.get("return") == "1") or (request.form.get("return") == "1")
    mode = (request.args.get("mode") or request.form.get("mode") or "full").lower()
    if mode not in {"full", "deposit", "balance"}:
        mode = "full"

    r = Reservation.query.filter_by(tenant_id=g.tenant.id, id=reservation_id).first_or_404()
    v = Vehicle.query.filter_by(tenant_id=g.tenant.id, id=r.vehicle_id).first_or_404()
    rate = Rate.query.filter_by(tenant_id=g.tenant.id, category_id=v.category_id).first()

    daily = float(rate.daily_rate) if rate and rate.daily_rate is not None else 0.0
    currency = (rate.currency or 'USD') if rate else 'USD'
    pickup_dt  = getattr(r, 'pickup_dt', None)  or getattr(r, 'pickup_at', None)
    dropoff_dt = getattr(r, 'dropoff_dt', None) or getattr(r, 'dropoff_at', None)
    days = max(1, (dropoff_dt - pickup_dt).days if (pickup_dt and dropoff_dt) else 1)
    total = round(daily * days, 2)

    pack = _amounts_for_modes(total, currency)
    if mode == "deposit":
        amount = pack["deposit"]
        commission_amount = 0.0
        desc = f"Reserva #{r.id} — Depósito (sinal)"
    elif mode == "balance":
        amount = pack["balance"]
        commission_amount = pack["service_charge"]
        desc = f"Reserva #{r.id} — Saldo + ServiceCharge {currency} {pack['service_charge']:.2f}"
    else:
        amount = pack["full"]
        commission_amount = pack["service_charge"]
        desc = f"Reserva #{r.id} — Pagamento total (inclui ServiceCharge {currency} {pack['service_charge']:.2f})"

    if amount <= 0:
        msg = "Nada a cobrar neste modo. Verifique os valores."
        if want_json: 
            return jsonify(ok=False, error=msg), 400
        flash(msg, "warning")
        return redirect(url_for('public.checkout', tenant_slug=g.tenant.slug, reservation_id=reservation_id))

    # dados do cliente
    name  = (getattr(r, 'customer_name', "")  or getattr(r, 'name', "")  or "Cliente Teste")
    email = (getattr(r, 'customer_email', "") or getattr(r, 'email', "") or f"sandbox+{r.id}@example.com")
    phone = _digits(getattr(r, 'customer_phone', "") or getattr(r, 'phone', "") or "11999999999")
    doc   = _digits(getattr(r, 'customer_document', "") or getattr(r, 'document', "") or "52998224725")
    cep   = _digits(getattr(r, 'zipcode', "") or "01001000")
    address = (getattr(r, 'address', "") or "Praça da Sé")
    number  = (getattr(r, 'addr_number', "") or "1")
    district = (getattr(r, 'district', "") or "Centro")
    city     = (getattr(r, 'city', "") or "São Paulo")
    state    = (getattr(r, 'state', "") or "SP").upper()[:2]

    client = {
        "name": name, "email": email, "doc": doc, "phone": phone,
        "cep": cep, "address": address, "numberAddress": number,
        "district": district, "city": city, "state": state,
    }

    # credenciais por tenant
    creds = _tenant_pay_creds()
    merchant_code = creds.get("merchant_code") or _cfg("GP_MERCHANT_CODE")
    tenant_ep = (creds.get("endpoint") or "").strip()

    # endpoints em ordem de preferência
    base = _cfg("GP_API_V1_BASE", "https://apihml.tryglobalpays.com/v1").rstrip("/")
    endpoints = []
    if tenant_ep:
        endpoints.append(tenant_ep)
    cfg_ep = _cfg("GP_PAYMENT_LINK_ENDPOINT")
    if cfg_ep:
        endpoints.append(cfg_ep)
    endpoints += [
        f"{base}/paymentapi/order",
        f"{base}/paymentapi/createOrder",
        f"{base}/paymentapi/transaction",
    ]
    seen = set()
    endpoints = [u for u in endpoints if u and (u not in seen and not seen.add(u))]

    tok = _gp_pl_token(force=False)
    if not tok:
        msg = "Falha ao autenticar no provedor (Payment Link)."
        if want_json: 
            return jsonify(ok=False, error=msg), 400
        flash(msg, "danger")
        return redirect(url_for('public.checkout', tenant_slug=g.tenant.slug, reservation_id=reservation_id))

    header_variants = _header_variants(tok)

    def build_payload(include_split: bool) -> dict:
        invoice_unique = f"RES-{r.id}-{mode.upper()}-{int(time.time())}"
        payload = {
            "merchantCode": merchant_code,
            "amount": round(float(amount), 2),
            "currency": currency,
            "description": desc,
            "externalReference": str(r.id),
            "invoice": invoice_unique,
            "installment": 1,
            "callback": _external_url('public.payment_return', tenant_slug=g.tenant.slug),
            "client": client,
        }

        # aviso se houver comissão mas não houver código da plataforma
        if commission_amount > 0 and not _cfg_str("PLATFORM_MERCHANT_CODE"):
            current_app.logger.warning(
                "PLATFORM_MERCHANT_CODE não definido — split de comissão será omitido."
            )

        # aplica split só quando solicitado e houver PLATFORM_MERCHANT_CODE
        if include_split and _cfg_str("PLATFORM_MERCHANT_CODE"):
            _with_commission_split(payload, commission_amount)

        return payload

    try:
        last_status = last_text = last_url = None
        # STRICT_COMMISSION_SPLIT: se True, só tenta com split; se False, tenta com e depois sem (fallback).
        strict_raw = _cfg("STRICT_COMMISSION_SPLIT", False)
        strict_split = (str(strict_raw).strip().lower() in ("1", "true", "yes", "on"))

        for url in endpoints:
            if commission_amount > 0:
                include_opts = [True] if strict_split else [True, False]
            else:
                include_opts = [False]

            for include_split in include_opts:
                payload = build_payload(include_split)
                for hdr in header_variants:
                    resp = requests.post(url, json=payload, headers=hdr, timeout=30)
                    last_status, last_url = resp.status_code, url
                    ct = resp.headers.get("content-type", "")
                    try:
                        data = resp.json() if "application/json" in ct else {}
                    except Exception:
                        data = {}
                        last_text = resp.text

                    if resp.status_code == 401:
                        # reauth e tenta novamente uma vez
                        tok2 = _gp_pl_token(force=True)
                        if tok2:
                            if "token" in hdr: hdr["token"] = tok2
                            if "Token" in hdr: hdr["Token"] = tok2
                            if "Authorization" in hdr: hdr["Authorization"] = f"Bearer {tok2}"
                            if "authorization" in hdr: hdr["authorization"] = f"Bearer {tok2}"
                            resp = requests.post(url, json=payload, headers=hdr, timeout=30)
                            ct = resp.headers.get("content-type", "")
                            try:
                                data = resp.json() if "application/json" in ct else {}
                            except Exception:
                                last_text = resp.text
                            last_status = resp.status_code

                    if isinstance(data, dict) and (data.get("msg", "").lower().strip() == "rota não encontrada"):
                        # tenta próximo endpoint
                        break

                    link = None
                    if isinstance(data, dict):
                        link = (
                            (data.get("data") or {}).get("url")
                            or data.get("url")
                            or data.get("paymentUrl")
                            or data.get("redirectUrl")
                        )
                        kyc_url = (
                            (data.get("data") or {}).get("clientAreaUrl")
                            or data.get("clientAreaUrl")
                            or data.get("client_area_url")
                            or (data.get("data") or {}).get("kycUrl")
                        )
                        if kyc_url:
                            session["kyc_url"] = kyc_url

                    if resp.status_code < 400 and link:
                        r.status = "pending_payment"
                        db.session.commit()
                        if want_json:
                            return jsonify(ok=True, link=link)
                        return redirect(link)

        current_app.logger.error(
            "payment/link error %s url=%s resp_text=%s", last_status, last_url, last_text
        )
        msg = "Não foi possível gerar o link de pagamento. Verifique credenciais/endpoint."
        if want_json:
            return jsonify(ok=False, error=msg), 400
        flash(msg, "danger")
        return redirect(url_for('public.checkout', tenant_slug=g.tenant.slug, reservation_id=reservation_id))

    except Exception:
        current_app.logger.exception("Falha ao criar Payment Link")
        msg = "Falha ao criar Payment Link."
        if want_json:
            return jsonify(ok=False, error=msg), 500
        flash(msg, "danger")
        return redirect(url_for('public.checkout', tenant_slug=g.tenant.slug, reservation_id=reservation_id))

# =========================
# RETORNO / WEBHOOK / THANKS
# =========================
def _gp_consult_order(order_id: str | int) -> dict:
    tok = _gp_pl_token(False) or _gp_pl_token(True)
    if not tok:
        return {}

    base = _cfg_str("GP_API_V1_BASE", "https://apihml.tryglobalpays.com/v1").rstrip("/")
    urls = [
        f"{base}/paymentapi/order/{order_id}",
        f"{base}/paymentapi/order?id={order_id}",
        f"{base}/paymentapi/transaction/{order_id}",
        f"{base}/paymentapi/transaction?id={order_id}",
    ]
    for url in urls:
        for hdr in _header_variants(tok):
            try:
                resp = requests.get(url, headers=hdr, timeout=20)
                if resp.status_code >= 400:
                    continue
                data = resp.json() if "application/json" in resp.headers.get("content-type","") else {}
            except Exception:
                continue

            bloc = data.get("data") if isinstance(data.get("data"), dict) else None
            if (data.get("msg") or "").lower().strip() == "rota não encontrada":
                continue

            status = (data.get("status") or (bloc or {}).get("status") or "").lower()
            order = str(data.get("orderId") or (bloc or {}).get("orderId") or order_id)
            extref = (data.get("externalReference") or data.get("reference")
                      or (bloc or {}).get("externalReference") or (bloc or {}).get("reference"))
            invoice = (data.get("invoice") or (bloc or {}).get("invoice") or "")
            kyc_url = (data.get("clientAreaUrl") or (bloc or {}).get("clientAreaUrl")
                       or data.get("url_checkout") or (bloc or {}).get("url_checkout"))
            installment = (data.get("installment") or (bloc or {}).get("installment"))

            return {
                "status": status,
                "order_id": order,
                "external_reference": extref,
                "invoice": invoice,
                "client_area_url": kyc_url,
                "installment": installment,
            }
    return {}

# Alias de retorno: /checkout/retorno -> reaproveita a lógica de /payments/return
@public_bp.route('/checkout/retorno', methods=['GET', 'POST'])
def checkout_retorno_alias():
    # muitos PSPs enviam orderId/order_id e status por GET ou POST (form/json)
    order_id = (
        request.values.get('orderId')
        or request.values.get('order_id')
        or (request.get_json(silent=True) or {}).get('orderId')
        or (request.get_json(silent=True) or {}).get('order_id')
    )
    status = (
        request.values.get('status')
        or (request.get_json(silent=True) or {}).get('status')
        or ""
    )

    # redireciona para a rota oficial de retorno (mantém um possível status)
    params = {}
    if order_id: params['orderId'] = order_id
    if status:   params['status']  = status
    return redirect(url_for('public.payment_return', tenant_slug=g.tenant.slug, **params), code=302)


@public_bp.get('/payments/return')
def payment_return():
    order_id = request.args.get('orderId') or request.args.get('order_id')
    status = (request.args.get('status') or "").lower()

    reservation_id = None
    info = {}

    if order_id:
        info = _gp_consult_order(order_id) or {}
        status = (info.get("status") or status or "").lower()
        extref = info.get("external_reference")
        if extref and str(extref).isdigit():
            reservation_id = int(extref)
        else:
            inv = (info.get("invoice") or "") or ""
            m = re.search(r'RES-(\d+)', inv)
            if m:
                reservation_id = int(m.group(1))
        if info.get("client_area_url"):
            session["kyc_url"] = info["client_area_url"]

    if not reservation_id:
        ref = request.args.get('externalReference') or request.args.get('reference')
        if ref and ref.isdigit():
            reservation_id = int(ref)

    if reservation_id:
        r = Reservation.query.filter_by(tenant_id=g.tenant.id, id=reservation_id).first()
        if r:
            st = (status or "").lower().strip()
            if st in {"approved", "paid", "success", "confirmed", "delivered"}:
                r.status = "confirmed"
                v = Vehicle.query.filter_by(tenant_id=g.tenant.id, id=r.vehicle_id).first()
                if v:
                    v.status = "booked"
            elif st in {"canceled", "refused", "failed", "error", "aborted"}:
                r.status = "canceled"
            elif st in {"analysis", "under_review", "review", "pending"}:
                r.status = "under_review"
            db.session.commit()

    if status in {"analysis", "under_review", "review", "pending"} and session.get("kyc_url"):
        return redirect(session["kyc_url"])

    return redirect(url_for('public.thanks', tenant_slug=g.tenant.slug))

@public_bp.post('/payments/webhook')
def payment_webhook():
    data = request.get_json(silent=True) or {}
    status = (data.get('status') or "").lower()

    ref = str(data.get('externalReference') or data.get('reference') or data.get('orderId') or "")
    if not ref.isdigit():
        inv = data.get("invoice") or ""
        m = re.search(r'RES-(\d+)', inv)
        if m:
            ref = m.group(1)

    kyc_url = (data.get('clientAreaUrl') or data.get('client_area_url')
               or data.get('url_checkout') or data.get('kycUrl'))
    if kyc_url:
        session["kyc_url"] = kyc_url

    if ref.isdigit():
        r = Reservation.query.filter_by(tenant_id=g.tenant.id, id=int(ref)).first()
        if r:
            st = (status or "").lower().strip()
            if st in {"approved", "paid", "success", "confirmed", "delivered"}:
                r.status = "confirmed"
                v = Vehicle.query.filter_by(tenant_id=g.tenant.id, id=r.vehicle_id).first()
                if v:
                    v.status = "booked"
            elif st in {"canceled", "refused", "failed", "error", "aborted"}:
                r.status = "canceled"
            elif st in {"analysis", "under_review", "review", "pending"}:
                r.status = "under_review"
            db.session.commit()

    return ("", 200)

@public_bp.get("/thanks")
def thanks():
    info = session.pop("last_payment", {}) or {}
    try:
        return render_template("public/thanks.html", pay=info)
    except Exception:
        order_id = escape(info.get("order_id") or "")
        rid      = escape(info.get("reservation_id") or "")
        status   = escape((info.get("status") or "").upper())
        inst     = escape(str(info.get("installments") or 1))
        amount   = info.get("amount"); curr = info.get("currency") or ""
        total    = f"{curr} {amount}" if amount else ""
        html = f"""
        <div style="max-width:680px;margin:40px auto;font-family:system-ui,Segoe UI,Arial">
          <h2>Pagamento processado</h2>
          <p><b>Status:</b> {status}</p>
          <p><b>Order ID:</b> {order_id}</p>
          <p><b>Reserva:</b> {rid}</p>
          <p><b>Parcelas:</b> {inst}</p>
          <p><b>Total:</b> {total}</p>
          <p><a href="{url_for('public.search', tenant_slug=g.tenant.slug)}">Nova busca</a></p>
        </div>
        """
        return html


# =========================
# AIRPORTS JSON (autocomplete)
# =========================
@public_bp.get('/airports.json')
def airports_json():
    """
    Retorna sugestões de aeroportos no formato "Nome (IATA) — Cidade".
    Busca o arquivo em:
      - <raiz>/static/data/airports_us.json   (seu caso)
      - <app>/static/data/airports_us.json    (fallback)
      - <raiz>/static/data/airports_us_iata.json (formato antigo, com 'iata')
    Se receber ?q=, filtra por code/name/city (case-insensitive) e limita a 20 itens.
    """
    q = (request.args.get("q") or "").strip().lower()
    items = []

    try:
        # caminhos candidatos
        root_dir = os.path.abspath(os.path.join(current_app.root_path, os.pardir))  # <raiz do projeto>
        candidates = [
            os.path.join(root_dir, "static", "data", "airports_us.json"),
            os.path.join(current_app.root_path, "static", "data", "airports_us.json"),
            os.path.join(root_dir, "static", "data", "airports_us_iata.json"),
        ]
        path = next((p for p in candidates if os.path.isfile(p)), None)

        if path:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            def norm(a, key1, key2=None):
                v = (a.get(key1) or "").strip()
                if not v and key2:
                    v = (a.get(key2) or "").strip()
                return v

            out = []
            for a in data:
                code = norm(a, "code", "iata").upper()
                name = norm(a, "name")
                city = norm(a, "city")
                if not (code and name):
                    continue

                if q and len(q) >= 2:
                    # filtro: code começa com q OU name/city contém q
                    if not (code.lower().startswith(q) or (q in name.lower()) or (city and q in city.lower())):
                        continue

                city_txt = f" — {city}" if city else ""
                out.append(f"{name} ({code}){city_txt}")

            # se veio q, já limitamos; se não veio, não devolve tudo pra não lotar
            items = out[:20] if q else []
        else:
            current_app.logger.warning("airports.json: arquivo não encontrado em %s", candidates)

    except Exception as e:
        current_app.logger.exception("Falha ao carregar/parsear airports JSON: %s", e)

    # fallback (ainda filtrável) se nada deu certo
    if not items:
        fallback = [
            "Orlando International Airport (MCO) — Orlando",
            "Los Angeles International Airport (LAX) — Los Angeles",
            "John F. Kennedy International Airport (JFK) — New York",
            "Miami International Airport (MIA) — Miami",
            "San Francisco International Airport (SFO) — San Francisco",
        ]
        if q and len(q) >= 2:
            ql = q.lower()
            items = [s for s in fallback if (ql in s.lower())][:20]
        else:
            items = []  # sem q>=2 não manda lista gigante

    return jsonify({"items": items})



# =========================
# CONTRATO — paths/helpers (tenant-aware e privados)
# =========================
# Dica: deixe CALIBRATE_SIGNATURE_BOX=True temporariamente para ajustar a posição,
# depois volte para False.
CALIBRATE_SIGNATURE_BOX = False

def _tenant_slug() -> str:
    return getattr(getattr(g, "tenant", None), "slug", "default")

def _contracts_root() -> Path:
    """
    Pasta base persistente dos PDFs por tenant:
    instance/uploads/contracts/<tenant_slug>/
    (fora do /static, privado)
    """
    p = Path(current_app.instance_path) / "uploads" / "contracts" / _tenant_slug()
    p.mkdir(parents=True, exist_ok=True)
    return p

def _signatures_root() -> Path:
    """
    Pasta para imagens/temporários de assinatura por tenant:
    instance/uploads/signatures/<tenant_slug>/
    (fora do /static, privado)
    """
    p = Path(current_app.instance_path) / "uploads" / "signatures" / _tenant_slug()
    p.mkdir(parents=True, exist_ok=True)
    return p

# compat leitura (se houver legado em /static/contracts)
def _legacy_base(reserva_id: int) -> Path:
    return Path(current_app.root_path) / "contracts" / f"contrato_{reserva_id}.pdf"
def _legacy_signed(reserva_id: int) -> Path:
    return Path(current_app.root_path) / "contracts" / f"contrato_{reserva_id}_signed.pdf"

def _base_pdf_path(reserva_id: int) -> Path:
    return _contracts_root() / f"contrato_reserva_{reserva_id}.pdf"

def _signed_pdf_path(reserva_id: int) -> Path:
    return _contracts_root() / f"contrato_reserva_{reserva_id}_SIGNED.pdf"

def _signature_png_path(reserva_id: int) -> Path:
    return _signatures_root() / f"assinatura_{reserva_id}.png"

def _audit_json_path(reserva_id: int) -> Path:
    return _contracts_root() / f"contrato_reserva_{reserva_id}_audit.json"

def _resolve_paths(reserva_id: int) -> dict:
    return {
        "base": _base_pdf_path(reserva_id),
        "signed": _signed_pdf_path(reserva_id),
        "legacy_base": _legacy_base(reserva_id),
        "legacy_signed": _legacy_signed(reserva_id),
    }

# ---------- segurança: token assinado ----------
def _signer() -> URLSafeSerializer:
    # inclui tenant no salt para isolar
    salt = f"contract-link:{_tenant_slug()}"
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=salt)

def make_contract_token(reserva_id: int) -> str:
    return _signer().dumps({"rid": reserva_id, "ten": getattr(g.tenant, "id", None), "ts": int(time.time())})

def _extract_token(reserva_id: int) -> str | None:
    # tenta query, form, JSON e por fim sessão (setada ao abrir a tela)
    tok = (request.args.get("t") or request.form.get("t") or "").strip()
    if not tok:
        payload = request.get_json(silent=True) or {}
        tok = (payload.get("t") or "").strip() if isinstance(payload, dict) else ""
    if not tok:
        tok = session.get(f"ctok:{reserva_id}")
    return tok or None

def require_contract_token(reserva_id: int, *, strict: bool) -> bool:
    """
    strict=True: sempre obriga token válido.
    strict=False: em DEBUG sem token, permite (para não quebrar dev),
                  mas registra na sessão para os próximos POSTs.
    """
    tok = _extract_token(reserva_id)
    if not tok:
        if not strict and current_app.debug:
            # gera e guarda um token de sessão para fluxo local
            ctok = make_contract_token(reserva_id)
            session[f"ctok:{reserva_id}"] = ctok
            current_app.logger.warning("Contract token ausente; tolerado em DEBUG e registrado na sessão.")
            return True
        abort(403)

    try:
        data = _signer().loads(tok)
    except BadSignature:
        abort(403)

    if data.get("rid") != reserva_id or data.get("ten") != getattr(g.tenant, "id", None):
        abort(403)

    # guarda o token na sessão para POST subsequentes (ex.: apply-signature)
    session[f"ctok:{reserva_id}"] = tok
    return True

def _res_by_tenant_or_404(reserva_id: int):
    return Reservation.query.filter_by(tenant_id=g.tenant.id, id=reserva_id).first_or_404()

# ---------- gerar/atualizar o PDF base ----------
# --- helpers de caminho static p/ WeasyPrint
def _static_base_dir() -> str:
    # .../app/static
    return str(Path(current_app.root_path) / "static")

def _tpl_env() -> SandboxedEnvironment:
    env = SandboxedEnvironment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
    # filtros
    def money(value, currency="USD"):
        try:
            v = float(value)
            return f"{currency} {v:,.2f}"
        except Exception:
            return f"{currency} {value}"
    def datefmt(dt, fmt="%d/%m/%Y"):
        if not dt:
            return "-"
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except Exception:
                return dt
        return dt.strftime(fmt)
    env.filters["money"] = money
    env.filters["datefmt"] = datefmt
    return env

def _default_contract_template() -> str:
    # Template de exemplo com CSS embutido e placeholders.
    # Pode editar no admin depois (aba Contrato).
    return """<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<style>
  @page { size: A4; margin: 18mm 16mm 18mm 16mm; }
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; font-size: 12px; color:#111; line-height: 1.45; }
  .header { display:flex; align-items:center; gap:12px; margin-bottom: 10px; }
  .logo { height: 42px; }
  h1 { font-size: 20px; margin: 10px 0 12px; text-align:center; letter-spacing:.5px; }
  h2 { font-size: 14px; margin: 18px 0 8px; }
  .muted { color:#666; }
  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:10px 16px; }
  .box { border:1px solid #ddd; border-radius:6px; padding:10px 12px; }
  table.info { width:100%; border-collapse: collapse; }
  table.info td { padding:6px 8px; vertical-align: top; }
  .hr { height:1px; background:#e5e5e5; margin:12px 0; }
  .page-break { page-break-before: always; }
  .small { font-size: 11px; }
  .sign-hint { height: 110px; } /* espaço onde a assinatura será aplicada na última página */
</style>
</head>
<body>

<div class="header">
  {% if tenant_logo_rel %}<img class="logo" src="{{ tenant_logo_rel }}">{% endif %}
  <div>
    <div style="font-weight:700">{{ tenant_name }}</div>
    <div class="small muted">{{ tenant_email or '' }}</div>
  </div>
</div>

<h1>Contrato de Locação de Veículo</h1>

<div class="box">
  <table class="info">
    <tr><td><b>Locatário:</b> {{ cliente_nome }}</td><td><b>Documento:</b> {{ cliente_doc }}</td></tr>
    <tr><td><b>Período:</b> {{ data_inicio|datefmt }} a {{ data_fim|datefmt }}</td><td><b>Valor Total:</b> {{ valor_total|money(currency) }}</td></tr>
    <tr><td><b>Veículo:</b> {{ carro_marca }} {{ carro_modelo }} {{ carro_ano }}</td><td><b>Cor:</b> {{ carro_cor }}</td></tr>
  </table>
</div>

<h2>Cláusulas</h2>
<p>
Este Acordo define os direitos e obrigações relativos ao aluguel do veículo. O locatário declara estar apto a conduzir nos termos da lei aplicável e concorda em devolver o veículo nas mesmas condições em que recebeu, salvo desgaste natural de uso.
</p>
<p>
Quilometragem, combustível, multas, pedágios, seguros e franquias seguem a tabela vigente do locador. Em caso de sinistro, o locatário se compromete com os valores de franquia e demais despesas não cobertas.
</p>
<p class="muted small">
Observações do locador: ____________________________
</p>

<div class="page-break"></div>

<h2>Assinaturas</h2>
<p class="small">Assinatura do locatário abaixo (será aplicada digitalmente neste espaço ao finalizar a assinatura):</p>
<div class="sign-hint"></div>

<p class="muted small">
Carimbo/Auditoria: data, IP e agente do usuário são registrados automaticamente.
</p>

</body>
</html>"""

def _render_contract_html(reserva) -> str:
    # contexto disponível no template (somente chaves permitidas)
    t = getattr(g, "tenant", None)
    currency = None
    try:
        if reserva and reserva.total_price:
            # tenta deduzir moeda a partir da tarifa da categoria
            rate = Rate.query.filter_by(tenant_id=g.tenant.id, category_id=reserva.category_id).first()
            currency = (rate.currency if rate and rate.currency else "USD")
    except Exception:
        currency = "USD"

    ctx = dict(
        # tenant/branding
        tenant_name=(getattr(t, "name", None) or getattr(t, "slug", "TENANT")).upper(),
        tenant_logo_rel=(getattr(t, "logo_path", None) or ""),  # ex.: uploads/branding/<slug>/logo.png
        tenant_email=getattr(t, "email", None),
        currency=currency or "USD",

        # cliente (AGORA com os novos campos)
        cliente_nome=      (getattr(reserva, "customer_name", "") or getattr(reserva, "name", "") or ""),
        cliente_doc=       (getattr(reserva, "customer_doc", None) or getattr(reserva, "email", "") or ""),
        cliente_pais=      (getattr(reserva, "customer_country", None) or ""),
        cliente_cidade_uf= (getattr(reserva, "customer_city_uf", None) or ""),
        voo_numero=        (getattr(reserva, "flight_no", None) or ""),

        # veículo
        carro_marca=(reserva.vehicle.brand  if reserva.vehicle else "N/D"),
        carro_modelo=(reserva.vehicle.model if reserva.vehicle else "N/D"),
        carro_ano=(reserva.vehicle.year     if reserva.vehicle else "N/D"),
        carro_cor=((getattr(reserva.vehicle, "color", None) if reserva.vehicle else None) or "N/D"),

        # datas/valores
        data_inicio=(reserva.pickup_dt  if getattr(reserva, "pickup_dt",  None) else None),
        data_fim=   (reserva.dropoff_dt if getattr(reserva, "dropoff_dt", None) else None),
        valor_total=getattr(reserva, "total_price", 0.0),
        data_contrato=(getattr(reserva, "created_at", None) or datetime.utcnow()),
    )

    # fonte: HTML salvo no admin; se vazio, usa o padrão
    html_src = ""
    if t and getattr(t, "contract_template_html", None):
        html_src = t.contract_template_html
    if not html_src:
        html_src = _default_contract_template()

    # sandbox + filtros
    env = _tpl_env()
    template = env.from_string(html_src)
    return template.render(**ctx)


def _ensure_base_pdf(reserva) -> Path:
    """
    Garante que o PDF base existe (gera se não existir) e atualiza/insere o Contract.
    Agora passamos base_url=/static para imagens funcionarem no WeasyPrint.
    """
    paths = _resolve_paths(reserva.id)

    # Reutiliza base existente
    for p in (paths["base"], paths["legacy_base"]):
        if p.exists():
            contrato = Contract.query.filter_by(reservation_id=reserva.id).first()
            if not contrato:
                contrato = Contract(reservation_id=reserva.id)
                db.session.add(contrato)
            contrato.file_path = str(p)
            contrato.signature_type = contrato.signature_type or "generated"
            try:
                contrato.signature_hash = sha256(p.read_bytes()).hexdigest()
            except Exception:
                pass
            db.session.commit()
            return p

    # Gera novo PDF (com base_url correto)
    html = _render_contract_html(reserva)
    out = paths["base"]
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes = HTML(string=html, base_url=_static_base_dir()).write_pdf()
    out.write_bytes(pdf_bytes)

    contrato = Contract.query.filter_by(reservation_id=reserva.id).first()
    if not contrato:
        contrato = Contract(reservation_id=reserva.id)
        db.session.add(contrato)
    contrato.file_path = str(out)
    contrato.signature_type = "generated"
    contrato.signature_hash = sha256(pdf_bytes).hexdigest()
    db.session.commit()
    return out

def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

def _sign_conf() -> dict:
    """Config de assinatura por tenant (com defaults seguros)."""
    t = getattr(g, 'tenant', None)
    return {
        "x_rel": (t.sign_x_rel if t and t.sign_x_rel is not None else 0.62),
        "y_rel": (t.sign_y_rel if t and t.sign_y_rel is not None else 0.13),
        "w": int((t.sign_w_pt or 200) if t else 200),
        "h": int((t.sign_h_pt or 80) if t else 80),
        "rub_w": int((t.rub_w_pt or 120) if t else 120),
        "rub_h": int((t.rub_h_pt or 48) if t else 48),
        "rub_m": int((t.rub_margin_pt or 20) if t else 20),
        "rub_on_last": bool(t.rubrica_on_last) if t else False,
        "audit": (t.audit_stamp if t and t.audit_stamp is not None else True),
    }


# ---------- VIEW: abre assinado se existir; senão base ----------
@public_bp.get("/contrato/<int:reserva_id>/view")
def view_contract(reserva_id):
    # em produção: exige token; em DEBUG permite sem (para não quebrar fluxo local)
    require_contract_token(reserva_id, strict=not current_app.debug)

    reserva = _res_by_tenant_or_404(reserva_id)

    paths = _resolve_paths(reserva.id)
    pdf = (
        (paths["signed"] if paths["signed"].exists() else None)
        or (paths["legacy_signed"] if paths["legacy_signed"].exists() else None)
        or (_ensure_base_pdf(reserva))
    )

    resp = make_response(send_file(str(pdf), mimetype="application/pdf", conditional=False))
    return _no_cache(resp)

# ---------- página de assinatura ----------
@public_bp.get("/contrato/<int:reserva_id>/sign", endpoint="sign_contract")
def sign_contract(reserva_id):
    # mesma lógica de tolerância em DEBUG
    require_contract_token(reserva_id, strict=not current_app.debug)
    reserva = _res_by_tenant_or_404(reserva_id)
    _ensure_base_pdf(reserva)  # garante PDF base

    # se veio token na query, deixa registrado na sessão pro POST subsequente
    qt = request.args.get("t")
    if qt:
        session[f"ctok:{reserva_id}"] = qt

    return render_template("public/contrato_sign.html", reserva_id=reserva.id, cache_ts=int(time.time()))

# ---------- POST: recebe desenho e assina ----------
@public_bp.post("/contrato/<int:reserva_id>/apply-signature", endpoint="apply_signature")
def apply_signature(reserva_id):
    # aqui o token é sempre exigido (POST sensível)
    require_contract_token(reserva_id, strict=True)
    _ = _res_by_tenant_or_404(reserva_id)

    # entrada (dataURL)
    data = request.get_json(silent=True) or {}
    data_url = (data.get("image") or "").strip()
    if "," not in data_url:
        return jsonify(ok=False, error="Nenhuma assinatura recebida"), 400

    _, b64 = data_url.split(",", 1)
    try:
        sign_bytes = base64.b64decode(b64)
    except Exception:
        return jsonify(ok=False, error="Imagem inválida"), 400

    # salva PNG bruto (auditoria) — privado
    _signature_png_path(reserva_id).write_bytes(sign_bytes)

    # PDF base
    base_pdf = _base_pdf_path(reserva_id)
    if not base_pdf.exists():
        legacy = _legacy_base(reserva_id)
        if not legacy.exists():
            return jsonify(ok=False, error="Contrato não encontrado"), 404
        base_pdf = legacy

    # metadados/auditoria
    signed_at = datetime.utcnow()
    client_ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "-").split(",")[0].strip()
    user_agent = request.headers.get("User-Agent", "-")
    audit = {
        "reservation_id": reserva_id,
        "signed_at_utc": signed_at.isoformat(timespec="seconds") + "Z",
        "ip": client_ip,
        "user_agent": user_agent,
    }
    _audit_json_path(reserva_id).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    # conf de assinatura do tenant
    conf = _sign_conf()
    SIGN_FULL_W = conf["w"]
    SIGN_FULL_H = conf["h"]
    SIGN_FULL_X_REL = conf["x_rel"]
    SIGN_FULL_Y_REL = conf["y_rel"]
    RUB_W = conf["rub_w"]
    RUB_H = conf["rub_h"]
    RUB_MARGIN = conf["rub_m"]
    RUBRICA_ON_LAST = conf["rub_on_last"]
    AUDIT_STAMP = conf["audit"]

    # abre PDF e aplica: rubrica nas anteriores + assinatura completa na última
    with base_pdf.open("rb") as fh:
        reader = PdfReader(fh)
        writer = PdfWriter()

        # prepara imagens (full e rubrica)
        full_img   = Image.open(BytesIO(sign_bytes)).convert("RGBA").resize((SIGN_FULL_W, SIGN_FULL_H))
        rubric_img = Image.open(BytesIO(sign_bytes)).convert("RGBA").resize((RUB_W, RUB_H))

        tmp_full = _signatures_root() / f"__tmp_full_{reserva_id}.png"
        tmp_rub  = _signatures_root() / f"__tmp_rub_{reserva_id}.png"
        full_img.save(tmp_full)
        rubric_img.save(tmp_rub)

        try:
            last_idx = len(reader.pages) - 1

            for i, page in enumerate(reader.pages):
                w = float(page.mediabox.width)
                h = float(page.mediabox.height)

                packet = BytesIO()
                can = rl_canvas.Canvas(packet, pagesize=(w, h))

                # RUBRICA (todas as páginas, exceto a última se RUBRICA_ON_LAST=False)
                if (i != last_idx) or RUBRICA_ON_LAST:
                    can.drawImage(
                        str(tmp_rub),
                        w - RUB_W - RUB_MARGIN,
                        RUB_MARGIN,
                        width=RUB_W,
                        height=RUB_H,
                        mask="auto",
                    )

                # CARIMBO DE AUDITORIA (rodapé)
                if AUDIT_STAMP:
                    can.setFont("Helvetica", 7)
                    carimbo = f"Signed {signed_at:%Y-%m-%d %H:%M UTC} • IP {client_ip}"
                    can.drawString(24, 14, carimbo)

                # ASSINATURA COMPLETA SOMENTE NA ÚLTIMA PÁGINA
                if i == last_idx:
                    x_full = w * SIGN_FULL_X_REL
                    y_full = h * SIGN_FULL_Y_REL

                    if CALIBRATE_SIGNATURE_BOX:
                        # retângulo magenta para ajudar a calibrar posição/tamanho
                        can.setStrokeColorRGB(1, 0, 1)
                        can.setLineWidth(1)
                        can.rect(x_full, y_full, SIGN_FULL_W, SIGN_FULL_H)

                    can.drawImage(
                        str(tmp_full),
                        x_full,
                        y_full,
                        width=SIGN_FULL_W,
                        height=SIGN_FULL_H,
                        mask="auto",
                    )

                can.save()
                packet.seek(0)
                overlay_pdf = PdfReader(packet)
                page.merge_page(overlay_pdf.pages[0])
                writer.add_page(page)

            signed_path = _signed_pdf_path(reserva_id)
            with signed_path.open("wb") as out:
                writer.write(out)
        finally:
            try: tmp_full.unlink(missing_ok=True)
            except Exception: pass
            try: tmp_rub.unlink(missing_ok=True)
            except Exception: pass

    # persiste no DB
    contrato = Contract.query.filter_by(reservation_id=reserva_id).first()
    if not contrato:
        contrato = Contract(reservation_id=reserva_id)
        db.session.add(contrato)

    contrato.file_path = str(signed_path)
    contrato.signature_type = "drawn"
    contrato.signature_hash = sha256(sign_bytes).hexdigest()
    contrato.signed_at = signed_at
    db.session.commit()
    # depois do db.session.commit()
    app = current_app._get_current_object()  # captura o app real
    Thread(target=_email_contract_async, args=(app, reserva_id), daemon=True).start()


    return jsonify(
        ok=True,
        redirect_url=url_for("public.checkout", tenant_slug=_tenant_slug(), reservation_id=reserva_id)
    )




# ---------- download do assinado ----------
@public_bp.get("/contrato/<int:reserva_id>/download")
def download_signed_contract(reserva_id):
    paths = _resolve_paths(reserva_id)
    pdf = paths["signed"] if paths["signed"].exists() else paths["legacy_signed"]
    if not pdf or not pdf.exists():
        return "Contrato assinado não encontrado", 404
    return send_file(
        str(pdf),
        as_attachment=True,
        download_name=f"contrato_{reserva_id}_assinado.pdf",
        mimetype="application/pdf",
        conditional=True,
        max_age=0
    )
# ======= FIM – rotas de contrato =======

# Salva dados do modal e segue para assinatura
@public_bp.post("/contract/start")
def public_contract_start():
    res_id = request.form.get("reservation_id", type=int)
    if not res_id:
        return jsonify(ok=False, error="Reserva inválida"), 400

    res = Reservation.query.filter_by(id=res_id, tenant_id=g.tenant.id).first()
    if not res:
        return jsonify(ok=False, error="Reserva não encontrada"), 404

    # Campos do modal
    res.customer_name    = (request.form.get("customer_name") or "").strip() or res.customer_name
    res.customer_doc     = (request.form.get("customer_doc") or "").strip() or None
    res.customer_country = (request.form.get("customer_country") or "").strip() or None
    res.customer_city_uf = (request.form.get("customer_city_uf") or "").strip() or None
    res.flight_no        = (request.form.get("flight_no") or "").strip() or None

    db.session.commit()

    # URL da sua página de assinatura já existente
    next_url = request.form.get("next_url") or url_for(
        "public.contract_sign", tenant_slug=g.tenant.slug, reservation_id=res.id
    )
    return jsonify(ok=True, next_url=next_url)