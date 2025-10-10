from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Iterable, List, Optional

# === Diretórios auxiliares ===

def _tenant_settings_dir(instance_path: str | Path, tenant_slug: str) -> Path:
    """
    Base p/ configurações por tenant, fora do pacote app:
    <instance>/uploads/tenant_settings/<slug>/
    """
    base = Path(instance_path)
    d = base / "uploads" / "tenant_settings" / tenant_slug
    d.mkdir(parents=True, exist_ok=True)
    return d


# === WhatsApp (string simples em whatsapp.txt) ===

def load_tenant_whatsapp(instance_path: str | Path, tenant_slug: str) -> str:
    """
    Lê o número público de WhatsApp do tenant (string).
    Retorna "" se não existir.
    """
    d = _tenant_settings_dir(instance_path, tenant_slug)
    f = d / "whatsapp.txt"
    try:
        return f.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_tenant_whatsapp(instance_path: str | Path, tenant_slug: str, phone: str | None) -> bool:
    """
    Salva/atualiza o número público de WhatsApp do tenant.
    Se phone for vazio/None, zera o arquivo.
    """
    d = _tenant_settings_dir(instance_path, tenant_slug)
    f = d / "whatsapp.txt"
    try:
        f.write_text((phone or "").strip(), encoding="utf-8")
        return True
    except Exception:
        return False


# === Airports (lista de strings) ===

def _airports_file(instance_path: str | Path, tenant_slug: str) -> Path:
    d = _tenant_settings_dir(instance_path, tenant_slug)
    return d / "airports.json"


def load_tenant_airports(instance_path: str | Path, tenant_slug: str) -> List[str]:
    """
    Retorna lista de aeroportos (strings) salva para o tenant.
    """
    f = _airports_file(instance_path, tenant_slug)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        items = data.get("items", []) if isinstance(data, dict) else data
        if isinstance(items, list):
            # normaliza para strings
            return [str(x) for x in items][:500]
        return []
    except Exception:
        return []


def save_tenant_airports(instance_path: str | Path, tenant_slug: str, items: Iterable[str]) -> bool:
    """
    Persiste a lista inteira de aeroportos.
    """
    f = _airports_file(instance_path, tenant_slug)
    try:
        items = [str(x) for x in (items or [])][:500]
        f.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=0), encoding="utf-8")
        return True
    except Exception:
        return False


# === WhatsApp config avançado (whatsapp.json) ===

def _wa_cfg_file(instance_path: str | Path, tenant_slug: str) -> Path:
    d = _tenant_settings_dir(instance_path, tenant_slug)
    return d / "whatsapp.json"


# Defaults (inclui 'enabled')
_WA_DEFAULT = {
    "enabled": True,                     # <- NOVO: permite habilitar/desabilitar o botão
    "phone": "",                         # ex: "+5511999999999"
    "message": "Olá! Preciso de ajuda com minha reserva.",
    "position": "br",                    # br | bl | tr | tl
    "offset_x": 18,                      # px
    "offset_y": 18,                      # px
    "size_px": 56,                       # diâmetro do FAB
    "z_index": 1040,
    "palette": "success",                # success | brand | custom
    "bg": "",                            # usado se palette == "custom"
    "fg": "",                            # usado se palette == "custom"
    "show_on": "all",                    # all | home_only
}


def _read_json_relaxed(path: Path) -> dict:
    """
    Lê JSON permitindo comentários:
    - // linha
    - /* bloco */
    Retorna {} em caso de erro.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except Exception:
        return {}
    try:
        # remove /* ... */ (bloco)
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        # remove // ... (linha)
        raw = re.sub(r"//.*?$", "", raw, flags=re.M)
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_wa_config(instance_path: str | Path, tenant_slug: str) -> dict:
    """
    Carrega whatsapp.json (aceita comentários) e faz fallback para whatsapp.txt se 'phone' estiver vazio.
    Sempre retorna um dict com defaults preenchidos e normalizados.
    """
    cfg = dict(_WA_DEFAULT)
    f = _wa_cfg_file(instance_path, tenant_slug)

    # 1) carrega arquivo (tolerante a comentários)
    data = _read_json_relaxed(f)
    if data:
        # merge apenas nas chaves conhecidas
        for k in _WA_DEFAULT.keys():
            if k in data:
                cfg[k] = data[k]

    # 2) fallback: se não houver phone no JSON, tenta whatsapp.txt
    if not str(cfg.get("phone", "")).strip():
        txt = load_tenant_whatsapp(instance_path, tenant_slug)
        if txt:
            cfg["phone"] = txt

    # 3) normalizações
    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["phone"] = str(cfg.get("phone", "")).strip()
    cfg["message"] = str(cfg.get("message", "")).strip() or _WA_DEFAULT["message"]

    pos = (cfg.get("position") or "br").lower()
    if pos not in ("br", "bl", "tr", "tl"):
        pos = "br"
    cfg["position"] = pos

    pal = (cfg.get("palette") or "success").lower()
    if pal not in ("success", "brand", "custom"):
        pal = "success"
    cfg["palette"] = pal

    try:
        cfg["offset_x"] = int(cfg.get("offset_x") if cfg.get("offset_x") not in (None, "") else _WA_DEFAULT["offset_x"])
        cfg["offset_y"] = int(cfg.get("offset_y") if cfg.get("offset_y") not in (None, "") else _WA_DEFAULT["offset_y"])
        cfg["size_px"]  = int(cfg.get("size_px")  if cfg.get("size_px")  not in (None, "") else _WA_DEFAULT["size_px"])
        cfg["z_index"]  = int(cfg.get("z_index")  if cfg.get("z_index")  not in (None, "") else _WA_DEFAULT["z_index"])
    except Exception:
        # se algo vier inválido, força defaults
        cfg["offset_x"] = _WA_DEFAULT["offset_x"]
        cfg["offset_y"] = _WA_DEFAULT["offset_y"]
        cfg["size_px"]  = _WA_DEFAULT["size_px"]
        cfg["z_index"]  = _WA_DEFAULT["z_index"]

    show_on = (cfg.get("show_on") or "all").lower()
    if show_on not in ("all", "home_only"):
        show_on = "all"
    cfg["show_on"] = show_on

    # normaliza bg/fg para strings
    cfg["bg"] = str(cfg.get("bg") or "").strip()
    cfg["fg"] = str(cfg.get("fg") or "").strip()

    return cfg


def save_wa_config(instance_path: str | Path, tenant_slug: str, **updates) -> bool:
    """
    Faz merge da config atual com 'updates' e grava em whatsapp.json.
    Retorna True/False.
    """
    allowed = {
        "enabled", "phone", "message", "position",
        "offset_x", "offset_y", "size_px", "z_index",
        "palette", "bg", "fg", "show_on",
    }
    try:
        cfg = load_wa_config(instance_path, tenant_slug)
        for k, v in (updates or {}).items():
            if k in allowed:
                cfg[k] = v
        f = _wa_cfg_file(instance_path, tenant_slug)
        f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
