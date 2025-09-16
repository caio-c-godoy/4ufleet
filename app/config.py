# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()  # carrega variáveis do .env

DEFAULT_DB_URI = "postgresql+psycopg://postgres:admin123@localhost:5432/carrentalflask"

class Config:
    # --- Flask / DB ---
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_DB_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # URL pública (ngrok)
    EXTERNAL_BASE_URL = os.getenv("PUBLIC_BASE_URL")  # já tinha

    # GlobalPays
    GP_MERCHANT_CODE = os.getenv("GP_MERCHANT_CODE")
    GP_PUB_KEY = os.getenv("GP_PUB_KEY")
    GP_CHECKOUT_ENDPOINT = os.getenv("GP_CHECKOUT_ENDPOINT")  # /checkoutapi/auth
    GP_API_BASE = os.getenv("GP_API_BASE")  # opcional; default no código
    GP_PAYMENT_LINK_ENDPOINT = os.getenv("GP_PAYMENT_LINK_ENDPOINT")  # << IMPORTANTE