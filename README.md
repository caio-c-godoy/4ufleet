# Car Rental (Flask + Bootstrap, Multi-tenant)

Multi-tenant starter for small car rental companies (locadoras).

## Features
- URL-based multitenancy: `/<tenant_slug>/...`
- Admin (protected): Dashboard, Categories (with rates), Fleet, Reservations list, Calendar
- Public booking form with airports autocomplete (US airports sample list; easily extendable)
- All data scoped by tenant
- Bootstrap 5 UI
- SQLite by default (swap to Postgres later)

## Quick Start
```bash
# 1) Create venv and install deps
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt

# 2) Set up env
copy .env.example .env  # (Windows)
# cp .env.example .env  # (macOS/Linux)

# 3) Initialize DB and seed a sample tenant + admin user
python seed.py

# 4) Run
python run.py
# App at: http://127.0.0.1:5000
# Public:   http://127.0.0.1:5000/locadora1/
# Admin:    http://127.0.0.1:5000/locadora1/admin/dashboard
# Login:    http://127.0.0.1:5000/locadora1/auth/login
# Admin user: admin@locadora1.com / 123456
```

## Tenancy
Routes are prefixed by `/<tenant_slug>/...`. All queries filter by the tenant in the current URL.

## Airports Autocomplete
A lightweight `static/data/airports_us.json` ships with ~60 major US airports to start.
You can expand it by appending more entries: `[{ "code": "MCO", "name": "Orlando Intl", "city": "Orlando", "state": "FL" }, ...]`

## Swap to PostgreSQL
Set `DATABASE_URL` in `.env`, e.g.:
```
DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
```
Then run `python seed.py` again in a clean DB.

## Structure
```
app/
  __init__.py
  extensions.py
  config.py
  models.py
  utils.py
  auth/
    __init__.py
    routes.py
  admin/
    __init__.py
    routes.py
  public/
    __init__.py
    routes.py
templates/
  base.html
  auth/login.html
  admin/*.html
  public/*.html
static/
  css/custom.css
  data/airports_us.json
```

## Notes
- Calendar uses FullCalendar via CDN on the admin page.
- This is a starter; refine validations, permissions, and edge cases as you grow.


## PostgreSQL Setup (Docker + pgAdmin)
1) Install Docker Desktop.
2) From the project root, run:
   ```bash
   docker compose up -d
   ```
   This starts:
   - Postgres at `localhost:5433` (db=carrental / user=caruser / pass=carpass)
   - pgAdmin at `http://127.0.0.1:5050` (admin@local / admin123)
3) Ensure your `.env` contains:
   ```
   DATABASE_URL=postgresql+psycopg2://caruser:carpass@localhost:5433/carrental
   ```
4) Initialize DB (first time):
   ```bash
   # Windows PowerShell
   .\.venv\Scripts\activate
   python seed.py
   python run.py
   ```
   (Alternatively, use Flask-Migrate)
   ```bash
   $env:FLASK_APP="run.py"
   flask db init
   flask db migrate -m "init"
   flask db upgrade
   ```


## Driver do Postgres (Windows + Python 3.13)
Este projeto usa **psycopg 3** (`psycopg[binary]`), compatível com Python 3.13.
Se você tinha `psycopg2-binary`, remova e reinstale os requisitos.


### Nota de migração
A coluna `vehicles.plate` agora aceita vazio (nullable) para permitir cadastrar a frota sem placa e preencher depois.
Use Flask-Migrate para aplicar:
```
$env:FLASK_APP="run.py"
flask db migrate -m "vehicle plate nullable"
flask db upgrade
```
