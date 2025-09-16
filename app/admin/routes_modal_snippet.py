# Cole este trecho no final de app/admin/routes.py

# -------- Modal edit for Category --------
@admin_bp.get("/categories/<int:category_id>/edit.modal")
@login_required
def edit_category_modal(category_id):
    t = g.tenant
    cat = VehicleCategory.query.filter_by(tenant_id=t.id, id=category_id).first_or_404()
    return render_template("admin/_category_edit_form.html", category=cat)

@admin_bp.post("/categories/<int:category_id>/edit.modal")
@login_required
def update_category_modal(category_id):
    t = g.tenant
    cat = VehicleCategory.query.filter_by(tenant_id=t.id, id=category_id).first_or_404()
    name = request.form.get("name", "").strip()
    desc = (request.form.get("description") or "").strip() or None
    if not name:
        return {"ok": False, "error": "Nome é obrigatório."}, 400
    cat.name = name
    cat.slug = slugify(name)
    cat.description = desc
    db.session.commit()
    return {"ok": True}

# -------- Modal edit for Rate --------
@admin_bp.get("/rates/<int:rate_id>/edit.modal")
@login_required
def edit_rate_modal(rate_id):
    t = g.tenant
    r = Rate.query.filter_by(tenant_id=t.id, id=rate_id).first_or_404()
    cats = VehicleCategory.query.filter_by(tenant_id=t.id).all()
    return render_template("admin/_rate_edit_form.html", rate=r, categories=cats)

@admin_bp.post("/rates/<int:rate_id>/edit.modal")
@login_required
def update_rate_modal(rate_id):
    t = g.tenant
    r = Rate.query.filter_by(tenant_id=t.id, id=rate_id).first_or_404()
    try:
        r.category_id = int(request.form.get("category_id"))
        r.daily_rate = float(request.form.get("daily_rate"))
        r.currency = (request.form.get("currency") or "USD").upper()
        r.min_age = int(request.form.get("min_age") or 21)
        r.deposit_amount = float(request.form.get("deposit_amount") or 200)
        db.session.commit()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 400