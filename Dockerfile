# ---- Base Python com Debian slim
FROM python:3.12-slim

# Evitar prompts e deixar logs sem buffer
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---- Dependências de SO necessárias pro WeasyPrint (cairo/pango/gobject etc.)
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
    # utilitários
    curl ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# ---- Pasta de trabalho
WORKDIR /app

# Instala dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia o projeto
COPY . .

# Porta do Gunicorn (o App Service para container usa WEBSITES_PORT=8000)
ENV PORT=8000

# Comando de start (ajuste o alvo se seu app for diferente)
# Se seu run.py tem: from app import create_app; app = create_app()
CMD ["gunicorn", "run:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
