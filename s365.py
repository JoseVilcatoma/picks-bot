"""Cliente de 365Scores: partidos, alineaciones, cuotas y estadísticas."""
import json
import os
import re
import time

import requests

BASE = "https://webws.365scores.com/web"
PARAMS = {"appTypeId": 5, "langId": 14, "timezoneName": "America/Lima", "userCountryId": 112}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.365scores.com/",
}
_s = requests.Session()

CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "cache.json")
_cache = None


def get(path, **params):
    p = dict(PARAMS)
    p.update(params)
    for intento in range(3):
        try:
            r = _s.get(f"{BASE}/{path}/", params=p, headers=HEADERS, timeout=25)
            if r.status_code == 200:
                return r.json()
            print(f"[365] {path} -> HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            print(f"[365] {path} error: {e}")
        time.sleep(2 * (intento + 1))
    return None


# ---------------- caché en disco (se guarda en el repo) ----------------
def _c():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def guardar_cache():
    c = _c()
    ahora = time.time()
    for k in [k for k, v in c.items() if ahora - v.get("t", 0) > 45 * 86400]:
        del c[k]
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, separators=(",", ":"))


# ---------------- utilidades ----------------
def _num(v):
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


def terminado(g):
    txt = (g.get("statusText") or "").lower()
    if any(x in txt for x in ("aplaz", "suspend", "cancel", "abandon")):
        return False
    return g.get("statusGroup") == 4 and (g.get("homeCompetitor", {}).get("score", -1) or 0) >= 0


def anulado(g):
    txt = (g.get("statusText") or "").lower()
    return any(x in txt for x in ("aplaz", "suspend", "cancel", "abandon"))


# ---------------- endpoints ----------------
def partidos_del_dia(fecha_ddmmyyyy):
    return get("games/allscores", sports=1, startDate=fecha_ddmmyyyy,
               endDate=fecha_ddmmyyyy, showOdds="false")


def detalle(game_id):
    """Devuelve (game, miembros{id: nombre})."""
    j = get("game", gameId=game_id)
    if not j or "game" not in j:
        return None, {}
    g = j["game"]
    miembros = {}
    for m in (g.get("members") or j.get("members") or []):
        miembros[m.get("id")] = m.get("shortName") or m.get("name") or str(m.get("id"))
    return g, miembros


def alineacion(comp):
    """(confirmada, formación, titulares[ids], posiciones{id: 'Defensa'...})"""
    lu = comp.get("lineups") or {}
    estado = (lu.get("status") or "").lower()
    titulares, pos = [], {}
    for m in lu.get("members") or []:
        if m.get("status") == 1:
            titulares.append(m.get("id"))
            pos[m.get("id")] = (m.get("position") or {}).get("name", "")
    return estado.startswith("confirm"), lu.get("formation", ""), titulares, pos


def cuotas(game_id):
    """Líneas de Bet365 tal como vienen de 365Scores."""
    j = get("bets/lines", games=game_id)
    return (j or {}).get("lines") or []


def stats_partido(game_id):
    """{competitorId: {'c': córners, 'y': amarillas, 'r': rojas}} (cacheado)."""
    c = _c()
    k = f"st:{game_id}"
    if k in c:
        return {int(x): v for x, v in c[k]["d"].items()}
    j = get("game/stats", games=game_id)
    if not j:
        return {}
    out = {}
    for s in j.get("statistics") or []:
        cid = s.get("competitorId")
        nombre = (s.get("name") or "").lower()
        campo = None
        if s.get("id") == 8 or "esquina" in nombre:
            campo = "c"
        elif s.get("id") == 1 or "amarilla" in nombre:
            campo = "y"
        elif s.get("id") == 2 or "roja" in nombre:
            campo = "r"
        if campo and cid is not None:
            v = _num(s.get("value"))
            if v is not None:
                out.setdefault(cid, {})[campo] = v
    if out:
        c[k] = {"t": time.time(), "d": out}
    return out


def resumen_partido(game_id):
    """Titulares, posiciones, goles y asistencias por jugador de un partido ya jugado (cacheado)."""
    c = _c()
    k = f"det:{game_id}"
    if k in c:
        return c[k]["d"]
    g, nombres = detalle(game_id)
    if not g or not terminado(g):
        return None
    d = {"eq": {}, "n": {}}
    for lado in ("homeCompetitor", "awayCompetitor"):
        comp = g.get(lado) or {}
        _, _, xi, pos = alineacion(comp)
        gol, asi = {}, {}
        for m in (comp.get("lineups") or {}).get("members") or []:
            for st in m.get("stats") or []:
                n = (st.get("name") or "").lower()
                v = _num(st.get("value")) or 0
                if n == "goles" and v:
                    gol[str(m["id"])] = v
                elif n.startswith("asistencia") and v:
                    asi[str(m["id"])] = v
        d["eq"][str(comp.get("id"))] = {"xi": xi, "pos": {str(a): b for a, b in pos.items()},
                                        "g": gol, "a": asi}
        for pid in xi:
            if pid in nombres:
                d["n"][str(pid)] = nombres[pid]
    c[k] = {"t": time.time(), "d": d}
    return d


def resultados_equipo(team_id, n=10):
    """Últimos n partidos terminados: [(gid, inicio, local_id, visita_id, gl, gv)]."""
    c = _c()
    k = f"res:{team_id}"
    if k in c and time.time() - c[k]["t"] < 6 * 3600:
        return c[k]["d"]
    j = get("games/results", competitors=team_id)
    out = []
    for g in (j or {}).get("games") or []:
        if not terminado(g):
            continue
        h, a = g.get("homeCompetitor") or {}, g.get("awayCompetitor") or {}
        out.append([g["id"], g.get("startTime", ""), h.get("id"), a.get("id"),
                    h.get("score"), a.get("score")])
    out.sort(key=lambda x: x[1], reverse=True)
    out = out[:n]
    if out:
        c[k] = {"t": time.time(), "d": out}
    return out


def marcador_1t(g):
    """Marcador al descanso (local, visita) o None si no se puede leer."""
    for st in g.get("stages") or []:
        nombre = (st.get("name") or st.get("shortName") or "").lower()
        if "1" in nombre or "primer" in nombre or "first" in nombre:
            hs, as_ = st.get("homeCompetitorScore"), st.get("awayCompetitorScore")
            if hs is not None and as_ is not None and hs >= 0:
                return int(hs), int(as_)
    hid = (g.get("homeCompetitor") or {}).get("id")
    eventos = g.get("events")
    if not eventos:
        return None
    h = a = 0
    for e in eventos:
        nombre = ((e.get("eventType") or {}).get("name") or "").lower()
        if nombre.startswith("gol") and "anul" not in nombre:
            if (e.get("gameTime") or 99) <= 45:
                if e.get("competitorId") == hid:
                    h += 1
                else:
                    a += 1
    return h, a
