"""Cuotas frescas de varias casas vía The Odds API (plan gratis: 500 créditos/mes).

- Buscar partidos es gratis; las cuotas cuestan 1 crédito por mercado devuelto (región eu).
- Pinnacle (sin margen) es la referencia de "probabilidad justa". Si no está, se usa el consenso.
- Guarda los créditos restantes en data/odds_quota.json y no gasta si quedan pocos.
"""
import difflib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

import config as C
import s365

KEY = os.getenv("ODDS_API_KEY", "")
BASE = "https://api.the-odds-api.com/v4"
QUOTA = os.path.join(os.path.dirname(__file__), "data", "odds_quota.json")

# 365Scores competitionId -> sport key de The Odds API
LIGAS = {
    7: "soccer_epl",
    11: "soccer_spain_la_liga",
    17: "soccer_italy_serie_a",
    25: "soccer_germany_bundesliga",
    35: "soccer_france_ligue_one",
    572: "soccer_uefa_champs_league",
    573: "soccer_uefa_europa_league",
    7685: "soccer_uefa_europa_conference_league",
    102: "soccer_conmebol_copa_libertadores",
    389: "soccer_conmebol_copa_sudamericana",
    113: "soccer_brazil_campeonato",
    73: "soccer_portugal_primeira_liga",
}


def sport_de(g):
    cid = g.get("competitionId")
    if cid in LIGAS:
        return LIGAS[cid]
    nombre = (g.get("competitionDisplayName") or "").lower()
    if "nations" in nombre:
        return "soccer_uefa_nations_league"
    if ("eliminatoria" in nombre or "clasificaci" in nombre) and ("sudam" in nombre or "conmebol" in nombre):
        return "soccer_fifa_world_cup_qualifiers_south_america"
    return None


# ---------------- créditos ----------------
def leer_quota():
    try:
        with open(QUOTA, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _guardar_quota(r):
    rem, used = r.headers.get("x-requests-remaining"), r.headers.get("x-requests-used")
    if rem is None:
        return
    os.makedirs(os.path.dirname(QUOTA), exist_ok=True)
    with open(QUOTA, "w", encoding="utf-8") as f:
        json.dump({"restantes": int(float(rem)), "usados": int(float(used or 0)),
                   "ultimo_costo": r.headers.get("x-requests-last"), "t": time.time()}, f)


def _get(path, **params):
    params["apiKey"] = KEY
    try:
        r = requests.get(f"{BASE}/{path}", params=params, timeout=25)
    except requests.RequestException as e:
        print("[odds] error:", e)
        return None
    _guardar_quota(r)
    if r.status_code != 200:
        print(f"[odds] {path} -> HTTP {r.status_code}: {r.text[:200]}")
        return None
    return r.json()


# ---------------- emparejar equipos ----------------
RUIDO = {"fc", "cf", "ac", "sc", "cd", "club", "de", "the", "afc", "calcio", "sv", "vfb", "rc", "as",
         "ss", "ud", "sd", "ca", "se", "cr", "ec", "fk", "sk", "ssc", "us", "rcd", "tsg", "vfl", "bv",
         "1", "04", "05", "1899", "1909", "1910", "1846", "1907", "1913", "y", "and", "e", "da", "do"}


def norm(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(w for w in t.split() if w not in RUIDO)


def parecido(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    sa, sb = set(a.split()), set(b.split())
    jac = len(sa & sb) / max(1, min(len(sa), len(sb)))
    return max(difflib.SequenceMatcher(None, a, b).ratio(), jac)


def buscar_evento(sport, inicio_iso, local, visita):
    """Busca el partido en The Odds API (gratis). Devuelve event id o None."""
    inicio = datetime.fromisoformat(inicio_iso).astimezone(timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    evs = _get(f"sports/{sport}/events",
               commenceTimeFrom=(inicio - timedelta(hours=3)).strftime(fmt),
               commenceTimeTo=(inicio + timedelta(hours=3)).strftime(fmt)) or []
    mejor, puntaje = None, 0.0
    for e in evs:
        t = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        if abs((t - inicio).total_seconds()) > 100 * 60:
            continue
        p = parecido(local, e["home_team"]) + parecido(visita, e["away_team"])
        p_inv = parecido(local, e["away_team"]) + parecido(visita, e["home_team"])
        p = max(p, p_inv - 0.2)
        if p > puntaje:
            mejor, puntaje = e, p
    if mejor and (puntaje >= 1.1 or (len(evs) == 1 and puntaje >= 0.6)):
        return mejor
    print(f"[odds] no encontré {local} vs {visita} en {sport} (mejor puntaje {puntaje:.2f})")
    return None


# ---------------- cuotas ----------------
def _sel(market, nombre, ev):
    n = (nombre or "").lower()
    if market == "h2h":
        if n == "draw":
            return ("1X2", None, "X")
        if nombre == ev["home_team"]:
            return ("1X2", None, "1")
        if nombre == ev["away_team"]:
            return ("1X2", None, "2")
    if market == "btts":
        return ("BTTS", None, "si" if n == "yes" else "no")
    return None


def obtener(gid, g, nombres_en=None):
    """Devuelve {"cuotas": {clave: {...}}, "n_casas", "ref", "edad_min"} o None.
    nombres_en: (local, visita) en inglés para emparejar mejor."""
    if not KEY:
        return None
    sport = sport_de(g)
    if not sport:
        return None
    cache = s365._c()
    ck = f"odds:{gid}"
    if ck in cache and time.time() - cache[ck]["t"] < 10 * 60:
        return cache[ck]["d"]
    q = leer_quota()
    if q and q.get("restantes", 999) < C.ODDS_RESERVA:
        print(f"[odds] solo quedan {q['restantes']} créditos: no consulto")
        return None
    local, visita = nombres_en or (g["homeCompetitor"]["name"], g["awayCompetitor"]["name"])
    ev = buscar_evento(sport, g["startTime"], local, visita)
    if not ev:
        return None
    data = _get(f"sports/{sport}/events/{ev['id']}/odds", regions="eu",
                markets=C.ODDS_MERCADOS, oddsFormat="decimal")
    if not data or not data.get("bookmakers"):
        return None

    # precios por clave y casa
    precios, grupos, ultimo = {}, {}, {}
    for bk in data["bookmakers"]:
        if bk["key"] in C.CASAS_EXCLUIR:
            continue
        for mk in bk.get("markets", []):
            for o in mk.get("outcomes", []):
                if mk["key"] == "totals":
                    if o.get("point") is None or abs(float(o["point"]) % 1 - 0.5) > 1e-6:
                        continue   # solo líneas .5 (las .25/.75/enteras tienen devolución parcial)
                    clave = ("OU", float(o["point"]), "over" if o["name"].lower() == "over" else "under")
                    grupo = ("OU", float(o["point"]))
                else:
                    clave = _sel(mk["key"], o["name"], ev)
                    grupo = (clave[0],) if clave else None
                if not clave or not o.get("price") or o["price"] <= 1:
                    continue
                precios.setdefault(clave, {})[bk["key"]] = (float(o["price"]), bk["title"])
                grupos.setdefault((bk["key"],) + grupo, {})[clave] = float(o["price"])
                if bk["key"] == "pinnacle":
                    ultimo[clave] = mk.get("last_update")

    # probabilidad justa: Pinnacle sin margen; si no, consenso (mediana sin margen)
    justa_pin, justas = {}, {}
    for (casa, *grupo), sels in grupos.items():
        imp = sum(1 / p for p in sels.values())
        if len(sels) < 2 or imp <= 0:
            continue
        for clave, p in sels.items():
            fair = (1 / p) / imp
            justas.setdefault(clave, []).append(fair)
            if casa == "pinnacle":
                justa_pin[clave] = fair

    out = {}
    for clave, casas in precios.items():
        if clave in justa_pin:
            fair, ref = justa_pin[clave], "Pinnacle"
        elif len(justas.get(clave, [])) >= 3:
            v = sorted(justas[clave])
            fair, ref = v[len(v) // 2], "consenso"
        else:
            continue
        mejor_key = max((k for k in casas if k != "pinnacle"), key=lambda k: casas[k][0], default=None)
        if not mejor_key:
            mejor_key = "pinnacle"
        out[clave] = {"cuota": casas[mejor_key][0], "casa": casas[mejor_key][1], "justa_p": fair,
                      "ref": ref, "pinnacle": casas.get("pinnacle", (None,))[0],
                      "n": len(casas)}
    if not out:
        return None
    edad = None
    fechas = []
    for x in ultimo.values():
        try:
            fechas.append(datetime.fromisoformat(str(x).replace("Z", "+00:00")))
        except ValueError:
            pass
    if fechas:
        edad = max(0, int((datetime.now(timezone.utc) - max(fechas)).total_seconds() // 60))
    res = {"cuotas": {"|".join("" if x is None else str(x) for x in k): v for k, v in out.items()},
           "n_casas": len({b["key"] for b in data["bookmakers"]} - set(C.CASAS_EXCLUIR)),
           "ref": "Pinnacle" if justa_pin else "consenso", "edad_min": edad}
    cache[ck] = {"t": time.time(), "d": res}
    return res


def a_clave(s):
    t, l, sel = s.split("|")
    return (t, float(l) if l else None, sel)
