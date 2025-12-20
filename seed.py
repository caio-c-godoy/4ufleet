# seeds.py
from __future__ import annotations

import os
import json

from app import create_app
from app.extensions import db
from app.models import Tenant, User, VehicleCategory, Rate, Vehicle
from app.services.subscription import initialize_trial

# slugify fallback simples (caso não tenha util pronto)
def slugify(s: str) -> str:
    import re
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "cat"


def _load_default_categories_from_static():
    """
    Lê app/static/data/default_categories.json
    Formatos aceitos:
      [{"name":"Economy","seats":5,"transmission":"Automatic"}, ...]
      ou [{"name":"SUV"}, ...] (campos opcionais)
    """
    from flask import current_app
    base = os.path.join(current_app.root_path, "static", "data")
    path = os.path.join(base, "default_categories.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    # fallback mínimo
    return [
        {"name": "Economy",  "seats": 5, "transmission": "Automatic"},
        {"name": "Compact",  "seats": 5, "transmission": "Automatic"},
        {"name": "Standard", "seats": 5, "transmission": "Automatic"},
        {"name": "SUV",      "seats": 5, "transmission": "Automatic"},
        {"name": "Minivan",  "seats": 7, "transmission": "Automatic"},
        {"name": "Luxury",   "seats": 5, "transmission": "Automatic"},
    ]


def seed():
    """
    Seed de desenvolvimento:
      - cria tenant 'locadora1' + usuário admin
      - cria categorias a partir de app/static/data/default_categories.json
      - cria Rates com diária = 0 (ajusta depois no admin)
      - opcionalmente, cria uma frota básica (comentado)
    """
    app = create_app()
    with app.app_context():
        db.create_all()

        # Tenant
        t = Tenant.query.filter_by(slug="locadora1").first()
        if not t:
            t = Tenant(name="Locadora 1", slug="locadora1")
            initialize_trial(t)
            db.session.add(t)
            db.session.commit()

        # Admin User
        u = User.query.filter_by(email="admin@locadora1.com", tenant_id=t.id).first()
        if not u:
            u = User(tenant_id=t.id, email="admin@locadora1.com", is_admin=True)
            u.set_password("123456")
            db.session.add(u)
            db.session.commit()

        # Categorias a partir do JSON (tarifas zeradas)
        cats = _load_default_categories_from_static()
        for item in cats:
            name = (item.get("name") or "").strip() or "Categoria"
            c_slug = slugify(name)
            cat = VehicleCategory.query.filter_by(tenant_id=t.id, slug=c_slug).first()
            if not cat:
                cat = VehicleCategory(
                    tenant_id=t.id,
                    name=name,
                    slug=c_slug,
                    seats=item.get("seats"),
                    transmission=item.get("transmission"),
                    large_bags=item.get("large_bags"),
                    small_bags=item.get("small_bags"),
                    mileage_text=item.get("mileage_text"),
                )
                db.session.add(cat)
                db.session.flush()

                db.session.add(Rate(
                    tenant_id=t.id,
                    category_id=cat.id,
                    daily_rate=0.0,   # <<< zerado por padrão
                    currency="USD",
                    min_age=21,
                    deposit_amount=200.0,
                ))
                db.session.commit()

        # (Opcional) Exemplo de frota básica — deixe comentado se quiser ambiente vazio
        """
        demo_models = [("Toyota","Corolla",2024), ("Nissan","Versa",2023), ("Kia","Rio",2023)]
        econ = VehicleCategory.query.filter_by(tenant_id=t.id, slug=slugify("Economy")).first()
        if econ:
            for brand, model, year in demo_models:
                exists = Vehicle.query.filter_by(
                    tenant_id=t.id, category_id=econ.id, brand=brand, model=model, year=year
                ).first()
                if not exists:
                    db.session.add(Vehicle(
                        tenant_id=t.id, category_id=econ.id,
                        brand=brand, model=model, year=year, status="available"
                    ))
            db.session.commit()
        """

        print("Seed complete.")
        print("Tenant: locadora1")
        print("Admin: admin@locadora1.com / 123456")


if __name__ == "__main__":
    seed()
