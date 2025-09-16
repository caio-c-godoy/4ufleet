# migrations/env.py
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- Caminho do projeto (raiz/migrations/.. -> raiz) ---
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Config do Alembic
config = context.config

# Logging (usa alembic.ini)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Importa sua app e o db
from app.extensions import db  # SQLAlchemy()
# Tentamos pegar a app de duas formas: app singleton OU factory create_app()
def _get_flask_app():
    try:
        # se você tem 'app = Flask(__name__)' em app/__init__.py
        from app import app as flask_app
        return flask_app
    except Exception:
        # se você usa factory pattern: def create_app(): ...
        from app import create_app
        return create_app()

target_metadata = db.Model.metadata  # todos os modelos

def run_migrations_offline() -> None:
    """Migrations em modo offline (usa URL)."""
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or ""
    if not url:
        # fallback comum em dev; ajuste se quiser
        url = "sqlite:///instance/app.db"

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Migrations em modo online usando o engine do Flask."""
    app = _get_flask_app()
    with app.app_context():
        # Usa o engine já configurado pela sua app (SQLALCHEMY_DATABASE_URI)
        connectable = db.engine

        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )

            with context.begin_transaction():
                context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
