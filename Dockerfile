# ---- Base Python com Debian slim
FROM python:3.12-slim

# Evitar prompts e logs com buffer
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ---- Dependências de SO necessárias pro WeasyPrint (cairo/pango/gobject etc.)
# (Pacotes alinhados com Debian Bookworm, base do python:3.12-slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libglib2.0-0 \
    libgobject-2.0-0 \
    libffi8 \
    fonts-dejavu-core \
    curl \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# ---- Pasta de trabalho
WORKDIR /app

# ---- Instala dependências Python antes do código (melhor cache de build)
COPY requirements.txt .
RUN python -m pip install --upgrade pip && \
    pip install -r requirements.txt

# ---- Copia o projeto
COPY . .

# ---- Porta do Gunicorn (App Service para containers expõe WEBSITES_PORT=8000)
ENV PORT=8000

# ---- Start (ajuste se seu alvo não for run:app)
# Se seu run.py tem: from app import create_app; app = create_app()
CMD ["gunicorn", "run:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
