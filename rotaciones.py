"""Radar de rotaciones: avisa por Telegram
  1) qué equipos ya NO se juegan nada (por la tabla),
  2) titulares de prensa que hablan de rotación / suplentes,
  3) al confirmarse la alineación, si faltan MUCHOS titulares habituales
     (comparado con los últimos partidos del equipo, no solo con el anterior).

Usa las mismas ligas y selecciones del calendario (config.py). No toca los datos del bot de picks.
"""
import html
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

import config as C
import s365
from calendario import grupo_de

# ------------------------------------------------------------------ ajustes
N_PARTIDOS = 7          # partidos anteriores para saber quién es titular habitual
MIN_PARTIDOS = 4        # con menos historial que esto no se opina
HABITUAL = 0.6          # titular habitual = fue titular en ≥ 60% de esos partidos
UMBRAL_FALTAN = 4       # alerta si faltan al menos 4 habituales...
SOBRE_LO_NORMAL = 1.5   # ...y eso supera en 1.5 lo que le suele faltar a ESE equipo
VENTANA_MIN = 75        # empieza a mirar alineaciones 75 min antes
HORAS_ADELANTE = 30     # el resumen cubre los partidos de las próximas 30 horas
HORAS_RESUMEN = range(7, 24)   # hora Lima en que puede salir el resumen
CADA_HORAS = 3          # el resumen se revisa cada 3 h y solo manda lo NUEVO
HORAS_NOTICIAS = (8, 20)       # las noticias se buscan 2 veces al día (son muchas consultas)
PASADA_SEG = 180
# Partidos que juega cada equipo en la fase de liga (si no está aquí: ida y vuelta, 2×(n−1))
JUEGOS_TOTALES = {572: 8, 573: 8, 7685: 6}
UNA_VUELTA = ["mundial", "copa del mundo", "eurocopa", "copa américa", "copa america"]

CLAVES_ES = re.compile(r"rotaci|suplente|alternativ|dar[áa]n? descanso|descanso a |sin sus figuras|"
                       r"equipo b\b|once alternativo|cambios en el once|reservar[áa]|guardar[áa] a", re.I)
CLAVES_EN = re.compile(r"rotat|rested|to rest |second[- ]string|much[- ]changed|wholesale changes|"
                       r"rings? the changes|weakened (side|team|xi)|fringe players", re.I)
NO_NOTICIAS = re.compile(r"rugby|wallabies|cricket|nba|nfl|b[aá]squet|women|femenin|sub-?\d|u-?\d\d|v[oó]ley|tenis", re.I)
# Competiciones que el radar ignora (en amistosos rotar es lo normal: no es noticia)
EXCLUIR_LIGAS = ["amistoso"]
NUEVOS_MIN = 5          # también alerta si entran 5+ jugadores que casi nunca son titulares (y no es lo normal)

TZ = ZoneInfo(C.ZONA)
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rot")
ESTADO = os.path.join(DIR, "estado.json")
s365.CACHE_PATH = os.path.join(DIR, "cache.json")     # caché propia: no pisa la del bot de picks
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ROTACIONES") or os.getenv("TELEGRAM_CHAT_ID")
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


# ------------------------------------------------------------------ utilidades
def ahora():
    return datetime.now(TZ)


def esc(t):
    return html.escape(str(t))


def norm(t):
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def enviar(texto):
    if not TOKEN or not CHAT:
        print("[tg sin configurar]\n" + texto + "\n", flush=True)
        return
    for i in range(0, len(texto), 3900):
        try:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", timeout=30,
                          json={"chat_id": CHAT, "text": texto[i:i + 3900], "parse_mode": "HTML",
                                "disable_web_page_preview": True})
        except requests.RequestException as e:
            print("[tg] error", e)


def cargar():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            e = json.load(f)
    except (OSError, ValueError):
        e = {}
    for k in ("enviados", "xi", "marcados", "noticias"):
        e.setdefault(k, {})
    return e


def guardar(e):
    limite = time.time() - 5 * 86400
    for k in ("enviados", "xi", "marcados"):
        e[k] = {a: b for a, b in e[k].items() if (b if isinstance(b, (int, float)) else b.get("t", 0)) > limite}
    os.makedirs(DIR, exist_ok=True)
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False)
    s365.guardar_cache()


# ------------------------------------------------------------------ partidos
def partidos(estados=(2,), dias=(0, 1)):
    """Partidos de tus ligas y selecciones (hoy y mañana). estados: 2 = por jugar, 4 = terminado."""
    hoy = ahora().date()
    out, vistos = [], set()
    for d in dias:
        fecha = (hoy + timedelta(days=d)).strftime("%d/%m/%Y")
        j = s365.partidos_del_dia(fecha)
        if not j:
            continue
        ingles = {}
        je = s365.get("games/allscores", sports=1, startDate=fecha, endDate=fecha, showOdds="false", langId=1)
        for g in (je or {}).get("games") or []:
            for lado in ("homeCompetitor", "awayCompetitor"):
                c = g.get(lado) or {}
                ingles[c.get("id")] = c.get("name")
        comps = {c["id"]: c for c in j.get("competitions") or []}
        for g in j.get("games") or []:
            cid = g.get("competitionId")
            info = grupo_de(cid, comps.get(cid, {}))
            if not info or any(x in info[0].lower() for x in EXCLUIR_LIGAS) or g["id"] in vistos or g.get("statusGroup") not in estados or s365.anulado(g):
                continue
            vistos.add(g["id"])
            h, a = g["homeCompetitor"], g["awayCompetitor"]
            out.append({"id": g["id"], "inicio": datetime.fromisoformat(g["startTime"]).astimezone(TZ),
                        "liga": info[0], "liga_id": cid,
                        "eq": [{"id": h.get("id"), "n": h.get("name"), "en": ingles.get(h.get("id"))},
                               {"id": a.get("id"), "n": a.get("name"), "en": ingles.get(a.get("id"))}]})
    out.sort(key=lambda p: p["inicio"])
    return out


def titulo(p):
    return f"{p['eq'][0]['n']} vs {p['eq'][1]['n']}"


# ------------------------------------------------------------------ 1) nada en juego
def tabla(comp_id):
    """Lista de grupos; cada grupo = filas {id, pos, pts, pj, zona}. Caché de 2 h."""
    c = s365._c()
    k = f"rtab:{comp_id}"
    if k in c and time.time() - c[k]["t"] < 2 * 3600:
        return c[k]["d"]
    j = s365.get("standings", competitions=comp_id)
    grupos = []
    for st in (j or {}).get("standings") or []:
        destinos = {d.get("num"): d.get("name") for d in st.get("destinations") or []}
        porg = {}
        for r in st.get("rows") or []:
            comp = r.get("competitor") or {}
            if r.get("points") is None or r.get("gamePlayed") is None:
                continue
            porg.setdefault(str(r.get("groupNum") or 0), []).append({
                "id": comp.get("id"), "pos": r.get("position"), "pts": float(r["points"]),
                "pj": int(r["gamePlayed"]), "zona": destinos.get(r.get("destinationNum")) or ""})
        grupos.extend(porg.values())
    if grupos:
        c[k] = {"t": time.time(), "d": grupos}
    return grupos


def nada_en_juego(team_id, comp_id, liga):
    """Texto si el equipo ya no puede cambiar de zona/puesto pase lo que pase; si no, None."""
    grupo = None
    for g in tabla(comp_id):
        if any(f["id"] == team_id for f in g):
            grupo = g                                   # si aparece en varias fases, vale la última
    if not grupo or len(grupo) < 3:
        return None
    m = len(grupo)
    if comp_id in JUEGOS_TOTALES:
        total = JUEGOS_TOTALES[comp_id]
    elif m >= 30:
        total = 8
    elif any(x in liga.lower() for x in UNA_VUELTA) and "clasif" not in liga.lower() and "eliminat" not in liga.lower():
        total = m - 1
    else:
        total = 2 * (m - 1)
    if any(f["pj"] > total for f in grupo):
        return None                                     # formato distinto al supuesto: mejor callar
    yo = next(f for f in grupo if f["id"] == team_id)
    rest = total - yo["pj"]
    if rest < 1:
        return None
    con_zonas = len({f["zona"] for f in grupo}) > 1
    for f in grupo:
        if f["id"] == team_id or (con_zonas and f["zona"] == yo["zona"]):
            continue
        arriba = (f["pts"], -(f["pos"] or 0)) > (yo["pts"], -(yo["pos"] or 0))
        if arriba and yo["pts"] + 3 * rest >= f["pts"]:
            return None                                 # todavía puedo alcanzarlo
        if not arriba and f["pts"] + 3 * (total - f["pj"]) >= yo["pts"]:
            return None                                 # todavía me puede alcanzar
    que = f"de zona ({yo['zona']})" if con_zonas and yo["zona"] else ("de zona" if con_zonas else "de puesto")
    return f"{yo['pos']}.º con {yo['pts']:g} pts y {rest} por jugar: ya no puede cambiar {que}"


# ------------------------------------------------------------------ 2) noticias
def noticias(nombre, ingles):
    """Titulares de las últimas 48 h que nombran al equipo y hablan de rotación."""
    out = []
    consultas = [(nombre, 'rotación OR suplentes OR rotaciones OR alternativo OR descanso', "es-419", "PE", CLAVES_ES)]
    if ingles and norm(ingles) != norm(nombre):
        consultas.append((ingles, 'rotate OR rotation OR rested OR "second string" OR changes', "en", "US", CLAVES_EN))
    for eq, extra, hl, gl, patron in consultas:
        try:
            r = requests.get("https://news.google.com/rss/search", timeout=15,
                             params={"q": f'"{eq}" {extra} when:2d', "hl": hl, "gl": gl,
                                     "ceid": f"{gl}:{hl.split('-')[0]}"})
            raiz = ET.fromstring(r.content)
        except (requests.RequestException, ET.ParseError):
            continue
        for it in raiz.iter("item"):
            t, link = it.findtext("title") or "", it.findtext("link") or ""
            try:
                viejo = (datetime.now(TZ) - parsedate_to_datetime(it.findtext("pubDate"))).total_seconds() > 48 * 3600
            except (TypeError, ValueError):
                viejo = True
            if viejo or norm(eq) not in norm(t) or not patron.search(t) or NO_NOTICIAS.search(t):
                continue
            out.append((t, link))
        time.sleep(0.4)
    return out[:2]


def resumen(e, lista, forzar=False):
    t = ahora()
    hora = t.hour
    ranura = f"{t:%Y%m%d}-{hora // CADA_HORAS}"
    if not forzar and (hora not in HORAS_RESUMEN or e.get("ranura") == ranura):
        return
    con_noticias = forzar or (hora in range(HORAS_NOTICIAS[0], HORAS_NOTICIAS[0] + CADA_HORAS)
                              or hora in range(HORAS_NOTICIAS[1], HORAS_NOTICIAS[1] + CADA_HORAS))
    con_noticias = (con_noticias or "ranura_noticias" not in e) and e.get("ranura_noticias") != ranura
    bloques = []
    for p in lista:
        if not timedelta(0) < p["inicio"] - t <= timedelta(hours=HORAS_ADELANTE):
            continue
        lineas = []
        for eq in p["eq"]:
            try:
                motivo = nada_en_juego(eq["id"], p["liga_id"], p["liga"])
            except Exception:  # noqa: BLE001
                motivo = None
                print("[tabla] error:\n" + traceback.format_exc(), flush=True)
            k = f"nada:{p['id']}:{eq['id']}"
            if motivo and (forzar or k not in e["enviados"]):
                e["enviados"][k] = time.time()
                e["marcados"][f"{p['id']}:{eq['id']}"] = {"t": time.time(), "m": motivo}
                lineas.append(f"  🔒 <b>{esc(eq['n'])}</b>: {esc(motivo)}")
            if con_noticias:
                for tit, link in noticias(eq["n"], eq["en"]):
                    k = "noti:" + norm(tit)[:90]
                    if forzar or k not in e["enviados"]:
                        e["enviados"][k] = time.time()
                        e["marcados"].setdefault(f"{p['id']}:{eq['id']}", {"t": time.time(), "m": ""})
                        lineas.append(f"  📰 <a href=\"{esc(link)}\">{esc(tit)}</a>")
        if lineas:
            cuando = f"{DIAS[p['inicio'].weekday()]} {p['inicio']:%H:%M}"
            bloques.append(f"⚽ <b>{esc(titulo(p))}</b> · {cuando} · {esc(p['liga'])}\n" + "\n".join(lineas))
    e["ranura"] = ranura
    if con_noticias:
        e["ranura_noticias"] = ranura
    if bloques:
        enviar("📋 <b>Radar de rotaciones</b>\n🔒 = ya no se juega nada · 📰 = lo que dice la prensa\n\n"
               + "\n\n".join(bloques)
               + "\n\n<i>Cuando salga la alineación te aviso si de verdad rotan.</i>")
    print(f"[resumen] {len(bloques)} partidos con novedades (noticias: {con_noticias})", flush=True)


# ------------------------------------------------------------------ 3) alineaciones
def analizar_xi(team_id, comp, nombres, gid, antes_de=None):
    """Compara el XI con los titulares habituales de los últimos N_PARTIDOS. Devuelve dict o None."""
    conf, _, xi, _ = s365.alineacion(comp)
    if len(xi) != 11:
        return None
    previos = [r for r in s365.resultados_equipo(team_id, 12)
               if r[0] != gid and (antes_de is None or r[1] < antes_de)][:N_PARTIDOS]
    hist, nom, pos = [], {}, {}
    for r in previos:
        d = s365.resumen_partido(r[0])
        eq = (d or {}).get("eq", {}).get(str(team_id))
        if eq and len(eq["xi"]) == 11:
            hist.append({str(x) for x in eq["xi"]})
            nom.update(d.get("n", {}))
            pos.update(eq.get("pos", {}))
    n = len(hist)
    if n < MIN_PARTIDOS:
        return None
    veces = {}
    for h in hist:
        for pid in h:
            veces[pid] = veces.get(pid, 0) + 1
    minimo = max(2, math.ceil(n * HABITUAL))
    habituales = {pid for pid, c in veces.items() if c >= minimo}
    hoy = {str(x) for x in xi}
    faltan = sorted(habituales - hoy, key=lambda x: -veces[x])
    normal = sum(len(habituales - h) for h in hist) / n          # lo que le suele faltar a este equipo
    lista = {str(m.get("id")) for m in (comp.get("lineups") or {}).get("members") or []}
    raro = max(1, n // 5)                                         # "casi nunca titular" = 0 o 1 vez
    nuevos = [p for p in hoy if veces.get(p, 0) <= raro]
    # cuántos "raros" suele poner: un equipo que siempre prueba gente no dispara la alerta
    normal_nuevos = sum(sum(1 for p in h if veces[p] <= raro) for h in hist) / n

    def nombre(pid):
        return nom.get(pid) or nombres.get(int(pid), "") or "?"
    return {
        "faltan": len(faltan), "habituales": len(habituales), "normal": normal, "n": n,
        "fuerte": (len(faltan) >= UMBRAL_FALTAN and len(faltan) >= normal + SOBRE_LO_NORMAL)
                  or (len(nuevos) >= NUEVOS_MIN and len(nuevos) >= normal_nuevos + 2),
        "n_nuevos": len(nuevos), "normal_nuevos": normal_nuevos,
        "banca": [f"{nombre(p)} ({veces[p]}/{n})" for p in faltan if p in lista],
        "fuera": [f"{nombre(p)} ({veces[p]}/{n})" for p in faltan if p not in lista],
        "nuevos": [f"{nombre(p)} ({veces.get(p, 0)}/{n})" for p in nuevos],
        "portero": any(pos.get(p) == "Portero" for p in faltan),
    }


def texto_xi(p, eq, a, motivo=""):
    cab = f"{esc(titulo(p))} · {p['inicio']:%H:%M} · {esc(p['liga'])}"
    if not a["fuerte"]:
        return (f"✅ <b>{esc(eq['n'])} sale con su equipo habitual</b>\n{cab}\n"
                f"Faltan {a['faltan']} de {a['habituales']} titulares habituales (lo normal en este equipo: {a['normal']:.1f}).")
    nivel = "Rotación masiva" if a["faltan"] >= 6 or a["n_nuevos"] >= 7 else "Rotación fuerte"
    lin = [f"🚨 <b>{nivel}: {esc(eq['n'])}</b>", cab,
           f"Faltan <b>{a['faltan']} de {a['habituales']}</b> titulares habituales "
           f"(lo normal en este equipo: {a['normal']:.1f})."]
    if a["banca"]:
        lin.append("🪑 En la banca: " + esc(", ".join(a["banca"])))
    if a["fuera"]:
        lin.append("❌ Fuera de la lista: " + esc(", ".join(a["fuera"])))
    if a["nuevos"]:
        lin.append(f"🆕 Entran {a['n_nuevos']} poco habituales (lo normal: {a['normal_nuevos']:.1f}): " + esc(", ".join(a["nuevos"])))
    if a["portero"]:
        lin.append("🧤 Cambia el portero habitual.")
    if motivo:
        lin.append("🔒 " + esc(motivo))
    lin.append(f"<i>(n/{a['n']}) = veces titular en sus últimos {a['n']} partidos.</i>")
    return "\n".join(lin)


def alineaciones(e, lista):
    t = ahora()
    for p in lista:
        falta = (p["inicio"] - t).total_seconds() / 60
        if not -10 <= falta <= VENTANA_MIN:
            continue
        pend = [eq for eq in p["eq"] if f"{p['id']}:{eq['id']}" not in e["xi"]]
        if not pend:
            continue
        g, nombres = s365.detalle(p["id"])
        if not g:
            continue
        for eq, lado in zip(p["eq"], ("homeCompetitor", "awayCompetitor")):
            k = f"{p['id']}:{eq['id']}"
            if k in e["xi"]:
                continue
            comp = g.get(lado) or {}
            conf, _, xi, _ = s365.alineacion(comp)
            if not conf or len(xi) != 11:
                if falta < -5:
                    e["xi"][k] = time.time()            # empezó y nunca se confirmó
                continue
            e["xi"][k] = time.time()
            a = analizar_xi(eq["id"], comp, nombres, p["id"])
            if not a:
                continue
            marca = e["marcados"].get(k)
            print(f"[xi] {eq['n']}: faltan {a['faltan']}/{a['habituales']} (normal {a['normal']:.1f}), "
                  f"nuevos {a['n_nuevos']} (normal {a['normal_nuevos']:.1f})", flush=True)
            if a["fuerte"] or marca:                    # el ✅ solo para equipos que ya te había avisado
                enviar(texto_xi(p, eq, a, (marca or {}).get("m", "")))


# ------------------------------------------------------------------ ejecución
def pasada(e):
    lista = partidos()
    try:
        resumen(e, lista)
    except Exception:  # noqa: BLE001
        print("[resumen] error:\n" + traceback.format_exc(), flush=True)
    try:
        alineaciones(e, lista)
    except Exception:  # noqa: BLE001
        print("[xi] error:\n" + traceback.format_exc(), flush=True)
    guardar(e)
    return len(lista)


def diagnostico():
    """Prueba sin enviar nada: muestra la tabla leída, el resumen y simula las alineaciones de partidos ya jugados."""
    global TOKEN
    TOKEN = None
    j = s365.get("standings", competitions=7)
    st = ((j or {}).get("standings") or [{}])[0]
    print("CLAVES standings:", sorted(st.keys()))
    print("FILA:", json.dumps((st.get("rows") or [{}])[0], ensure_ascii=False)[:900])
    print("DESTINOS:", json.dumps(st.get("destinations"), ensure_ascii=False)[:500])
    lista = partidos()
    print(f"\n{len(lista)} partidos por jugar (hoy y mañana)")
    for p in lista[:60]:
        print(f"  {p['inicio']:%d/%m %H:%M} {titulo(p)} [{p['liga']} #{p['liga_id']}] en={p['eq'][0]['en']} / {p['eq'][1]['en']}")
        g = tabla(p["liga_id"])
        print(f"     tabla: {len(g)} grupos, tamaños {[len(x) for x in g][:16]}, zonas {sorted({f['zona'] for x in g for f in x})[:8]}")
    print("\n===== RESUMEN (forzado) =====")
    resumen(cargar(), lista, forzar=True)
    print("\n===== SIMULACIÓN con partidos ya jugados (ayer y hoy) =====")
    for p in partidos(estados=(4,), dias=(-3, -2, -1, 0))[:40]:
        g, nombres = s365.detalle(p["id"])
        if not g:
            continue
        for eq, lado in zip(p["eq"], ("homeCompetitor", "awayCompetitor")):
            comp = g.get(lado) or {}
            lu = comp.get("lineups") or {}
            a = analizar_xi(eq["id"], comp, nombres, p["id"], antes_de=g.get("startTime"))
            print(f"-- {eq['n']} ({titulo(p)}) estado='{lu.get('status')}' "
                  f"status_miembros={sorted({m.get('status') for m in lu.get('members') or []})}")
            if a:
                a2 = dict(a, fuerte=True)
                print(("ALERTA " if a["fuerte"] else "normal ") + texto_xi(p, eq, a2).replace("\n", " | "))
    s365.guardar_cache()


if __name__ == "__main__":
    if "--diag" in sys.argv:
        diagnostico()
    elif "--vigilar" in sys.argv:
        fin = time.time() + float(sys.argv[sys.argv.index("--vigilar") + 1]) * 60
        estado = cargar()
        while time.time() < fin:
            try:
                n = pasada(estado)
                print(f"[rotaciones] pasada lista ({ahora():%H:%M}), {n} partidos en la mira", flush=True)
            except Exception:  # noqa: BLE001
                print("[rotaciones] error:\n" + traceback.format_exc(), flush=True)
            time.sleep(max(0, min(PASADA_SEG, fin - time.time())))
            r = subprocess.run("git fetch -q origin main && git diff --quiet HEAD origin/main -- rotaciones.py s365.py config.py",
                               shell=True)
            if r.returncode == 1:
                print("[rotaciones] código nuevo en el repo: me reinicio", flush=True)
                break
    else:
        pasada(cargar())
