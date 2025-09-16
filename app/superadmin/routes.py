# app/superadmin/routes.py
from __future__ import annotations

from datetime import datetime, timedelta
from functools import wraps

from flask import (
    render_template, request, redirect, url_for, flash,
    session, jsonify, abort, current_app
)
from sqlalchemy import func, select, literal, or_

from app.services.mailer import send_platform_mail_html

from app.extensions import db
from app.models import Tenant, User, Reservation, Payment, SupportMessage, Prospect
from . import superadmin_bp

# ---------------- Config ----------------
WEEKLY_MIN_TARGET = 2  # meta atual: 2 reservas confirmadas / semana

# ---------------- Auth helper ----------------
def require_superadmin(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("su_id"):
            return redirect(url_for("superadmin.login"))
        return fn(*a, **kw)
    return wrapper

# ---------------- Login / Logout ----------------
@superadmin_bp.get("/login")
def login():
    return render_template("superadmin/login.html", hide_chrome=True)

@superadmin_bp.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    user = User.query.filter_by(email=email, is_superadmin=True).first()
    if not user or not user.check_password(password):
        flash("Credenciais inválidas.", "danger")
        return redirect(url_for("superadmin.login"))
    session["su_id"] = user.id
    flash("Bem-vindo(a), superadmin!", "success")
    return redirect(url_for("superadmin.dashboard"))

@superadmin_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("su_id", None)
    flash("Sessão encerrada.", "info")
    return redirect(url_for("superadmin.login"))

# ---------------- Páginas ----------------
@superadmin_bp.get("/")
@require_superadmin
def dashboard():
    # Renderiza o template (o JS dele chama as APIs abaixo)
    return render_template("superadmin/dashboard.html", hide_chrome=True)

@superadmin_bp.get("/tenants")
@require_superadmin
def tenants():
    q = (request.args.get("q") or "").strip()
    qry = Tenant.query
    if q:
        like = f"%{q}%"
        qry = qry.filter((Tenant.name.ilike(like)) | (Tenant.slug.ilike(like)))
    rows = qry.order_by(Tenant.created_at.desc()).limit(200).all()
    return render_template("superadmin/tenants.html", rows=rows, q=q, hide_chrome=True)

@superadmin_bp.get("/tenants/<int:tenant_id>")
@require_superadmin
def tenant_detail(tenant_id: int):
    t = Tenant.query.get_or_404(tenant_id)

    now = datetime.utcnow()
    d7  = now - timedelta(days=7)
    d30 = now - timedelta(days=30)

    res_7d = db.session.scalar(
        select(func.count()).select_from(Reservation).where(
            Reservation.tenant_id == t.id,
            Reservation.status == "confirmed",
            Reservation.created_at >= d7,
        )
    ) or 0

    res_30d = db.session.scalar(
        select(func.count()).select_from(Reservation).where(
            Reservation.tenant_id == t.id,
            Reservation.status == "confirmed",
            Reservation.created_at >= d30,
        )
    ) or 0

    gross_30d, fee_30d = db.session.execute(
        select(
            func.coalesce(func.sum(Payment.amount_gross_usd), 0),
            func.coalesce(func.sum(Payment.amount_fee_usd), 0),
        ).where(
            Payment.tenant_id == t.id,
            Payment.status == "succeeded",
            Payment.paid_at >= d30,
        )
    ).first() or (0, 0)

    kpi = {
        "res_7d": int(res_7d),
        "res_30d": int(res_30d),
        "gross_30d": float(gross_30d or 0),
        "fee_30d": float(fee_30d or 0),
    }
    return render_template("superadmin/tenant_detail.html", tenant=t, kpi=kpi, hide_chrome=True)

# ---------------- Ações do tenant ----------------
@superadmin_bp.post("/tenants/<int:tenant_id>/block")
@require_superadmin
def tenant_block(tenant_id: int):
    t = Tenant.query.get_or_404(tenant_id)
    t.is_blocked = True
    db.session.commit()
    flash("Tenant bloqueado.", "warning")
    return redirect(url_for("superadmin.tenant_detail", tenant_id=t.id))

@superadmin_bp.post("/tenants/<int:tenant_id>/unblock")
@require_superadmin
def tenant_unblock(tenant_id: int):
    t = Tenant.query.get_or_404(tenant_id)
    t.is_blocked = False
    db.session.commit()
    flash("Tenant desbloqueado.", "success")
    return redirect(url_for("superadmin.tenant_detail", tenant_id=t.id))

@superadmin_bp.post("/tenants/<int:tenant_id>/delete")
@require_superadmin
def tenant_delete(tenant_id: int):
    t = Tenant.query.get_or_404(tenant_id)
    db.session.delete(t)  # cascatas nos relacionamentos apagam tudo
    db.session.commit()
    flash("Tenant excluído (todos os dados removidos).", "danger")
    return redirect(url_for("superadmin.tenants"))

# ---------------- Chat (superadmin <-> tenant) ----------------
@superadmin_bp.get("/tenant/<int:tenant_id>/chat")
@require_superadmin
def chat(tenant_id: int):
    t = Tenant.query.get_or_404(tenant_id)
    return render_template("superadmin/chat.html", tenant=t, hide_chrome=True)

@superadmin_bp.get("/api/tenant/<int:tenant_id>/chat")
@require_superadmin
def api_chat_list(tenant_id: int):
    Tenant.query.get_or_404(tenant_id)
    msgs = (SupportMessage.query
            .filter_by(tenant_id=tenant_id)
            .order_by(SupportMessage.created_at.asc())
            .all())
    return jsonify([
        {
            "id": m.id,
            "sender": m.sender,
            "body": m.body,
            "created_at": m.created_at.isoformat(),
        } for m in msgs
    ])

@superadmin_bp.post("/api/tenant/<int:tenant_id>/chat")
@require_superadmin
def api_chat_send(tenant_id: int):
    Tenant.query.get_or_404(tenant_id)
    body = (request.json or {}).get("body", "").strip()
    if not body:
        return ("", 204)
    db.session.add(SupportMessage(
        tenant_id=tenant_id,
        sender="superadmin",
        body=body,
    ))
    db.session.commit()
    return ("", 204)

# ---------------- APIs para KPIs e gráficos ----------------
@superadmin_bp.get("/api/kpis")
@require_superadmin
def api_kpis():
    now = datetime.utcnow()
    d7  = now - timedelta(days=7)

    tenants_total = db.session.scalar(select(func.count()).select_from(Tenant)) or 0
    tenants_blocked = db.session.scalar(
        select(func.count()).select_from(Tenant).where(Tenant.is_blocked.is_(True))
    ) or 0
    tenants_active = tenants_total - tenants_blocked

    # tenants que bateram a meta semanal (>= WEEKLY_MIN_TARGET confirmadas nos últimos 7d)
    sub = (select(Reservation.tenant_id, func.count().label("c"))
           .where(Reservation.status == "confirmed", Reservation.created_at >= d7)
           .group_by(Reservation.tenant_id)).subquery()
    tenants_reached_7d = db.session.scalar(
        select(func.count()).select_from(sub).where(sub.c.c >= WEEKLY_MIN_TARGET)
    ) or 0

    gross_total, fee_total = db.session.execute(
        select(
            func.coalesce(func.sum(Payment.amount_gross_usd), 0),
            func.coalesce(func.sum(Payment.amount_fee_usd), 0),
        ).where(Payment.status == "succeeded")
    ).first() or (0, 0)

    return jsonify({
        "tenants_total": int(tenants_total),
        "tenants_active": int(tenants_active),
        "tenants_blocked": int(tenants_blocked),
        "tenants_reached_7d": int(tenants_reached_7d),
        "gross_total": float(gross_total or 0),
        "fee_total": float(fee_total or 0),
        "weekly_min_target": WEEKLY_MIN_TARGET,
    })

@superadmin_bp.get("/api/revenue_series")
@require_superadmin
def api_revenue_series():
    # últimos 12 meses (mês a mês)
    now = datetime.utcnow().replace(day=1)
    start = (now - timedelta(days=365)).replace(day=1)

    # agrupa por alias label "m" para evitar o erro de GROUP BY
    m = func.date_trunc(literal("month"), Payment.paid_at).label("m")
    rows = db.session.execute(
        select(
            m,
            func.coalesce(func.sum(Payment.amount_gross_usd), 0).label("gross"),
            func.coalesce(func.sum(Payment.amount_fee_usd),   0).label("fee"),
        )
        .where(Payment.status == "succeeded", Payment.paid_at >= start)
        .group_by(m)
        .order_by(m)
    ).all()

    def fmt(dt: datetime) -> str:
        return dt.strftime("%b/%Y")  # ex.: Jan/2025

    return jsonify([
        {"period": r.m.isoformat(), "period_label": fmt(r.m), "gross": float(r.gross), "fee": float(r.fee)}
        for r in rows
    ])

@superadmin_bp.get("/api/tenants_series")
@require_superadmin
def api_tenants_series():
    m = func.date_trunc(literal("month"), Tenant.created_at).label("m")
    rows = db.session.execute(
        select(m, func.count().label("c")).group_by(m).order_by(m)
    ).all()

    def fmt(dt: datetime) -> str:
        return dt.strftime("%b/%Y")

    return jsonify([
        {"period": r.m.isoformat(), "period_label": fmt(r.m), "count": int(r.c)}
        for r in rows
    ])

# ---------- CRM: lista ----------
@superadmin_bp.get("/crm")
@require_superadmin
def crm_index():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()

    qry = Prospect.query
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Prospect.name.ilike(like), Prospect.email.ilike(like), Prospect.phone.ilike(like)))
    if status:
        qry = qry.filter(Prospect.status == status)

    rows = qry.order_by(Prospect.created_at.desc()).limit(500).all()
    return render_template("superadmin/crm.html", rows=rows, q=q, status=status)

# ---------- CRM: atualizar status ----------
@superadmin_bp.post("/crm/<int:pid>/status")
@require_superadmin
def crm_update_status(pid: int):
    p = Prospect.query.get_or_404(pid)
    new_status = (request.form.get("status") or "").strip()
    if new_status not in {"new","contacted","qualified","converted","dropped"}:
        return jsonify(ok=False, error="status inválido"), 400
    p.status = new_status
    if new_status in {"contacted","qualified","converted"}:
        p.last_contact_at = datetime.utcnow()
        p.last_channel = (request.form.get("channel") or p.last_channel or "other")
    db.session.commit()
    return jsonify(ok=True)

# ---------- CRM: salvar nota ----------
@superadmin_bp.post("/crm/<int:pid>/note")
@require_superadmin
def crm_save_note(pid: int):
    p = Prospect.query.get_or_404(pid)
    notes = (request.form.get("notes") or "").strip()
    p.notes = notes
    db.session.commit()
    return jsonify(ok=True)

# ---------- CRM: envio de e-mail mkt (bulk) ----------
@superadmin_bp.post("/crm/email")
@require_superadmin
def crm_bulk_email():
    ids     = request.form.getlist("ids") or []
    subject = (request.form.get("subject") or "").strip()
    body    = (request.form.get("body") or "").strip()

    if not subject or not body:
        flash("Informe assunto e conteúdo do e-mail.", "warning")
        return redirect(url_for("superadmin.crm_index"))

    q = Prospect.query
    if ids and "all" not in ids:
        ids_int = [int(i) for i in ids if str(i).isdigit()]
        if ids_int:
            q = q.filter(Prospect.id.in_(ids_int))

    leads = q.order_by(Prospect.created_at.desc()).all()

    sent = 0
    fails = 0
    for p in leads:
        if not p.email:
            continue
        try:
            # Usa template se existir; senão, fallback simples em HTML
            try:
                html = render_template(
                    "emails/marketing_generic.html",
                    lead=p, subject=subject,
                    body_html=body.replace("\n", "<br>")
                )
            except Exception:
                html = f"<div style='font-family:system-ui,Segoe UI,Arial'><p>{body.replace(chr(10),'<br>')}</p></div>"

            send_platform_mail_html(
                subject=subject,
                html=html,
                to=p.email,
                text_alt=body
            )
            sent += 1
        except Exception:
            fails += 1
            current_app.logger.exception("Falha ao enviar mkt para %s", p.email)

    flash(f"E-mails enviados: {sent}. Falhas: {fails}.", "info")
    return redirect(url_for("superadmin.crm_index"))

