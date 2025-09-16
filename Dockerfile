FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# Bibliotecas nativas necessárias para WeasyPrint (Cairo/Pango/etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 libglib2.0-0 libgobject-2.0-0 \
    libharfbuzz0b libfribidi0 libjpeg62-turbo libpng16-16 \
    libxml2 libxslt1.1 shared-mime-info fonts-dejavu-core \
    libffi-dev gcc build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências Python
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# Copia o restante do código
COPY . .

EXPOSE 8000
# Ajuste "run:app" se seu entrypoint for outro
CMD ["gunicorn","-b","0.0.0.0:8000","-w","2","run:app"]



