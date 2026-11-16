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
import rot_hist
import s365
from calendario import grupo_de

# ------------------------------------------------------------------ ajustes
N_PARTIDOS = 7          # partidos anteriores para saber quién es titular habitual
MIN_PARTIDOS = 4        # con menos historial que esto no se opina
MIN_HABITUALES = 5      # con menos titulares fijos que esto el equipo no tiene un once estable: no se opina
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
NO_NOTICIAS = re.compile(r"rugby|wallabies|cricket|nba|nfl|knicks|b[aá]squet|basket|women|femenin|sub-?\d|u-?\d\d|v[oó]ley|tenis|"
                         r"tropas|troop|soldier|soldado|militar|military|ej[eé]rcito|army|presidente|president\b", re.I)
# Competiciones que el radar ignora (en amistosos rotar es lo normal: no es noticia)
EXCLUIR_LIGAS = ["amistoso"]
NUEVOS_MIN = 5          # también alerta si entran 5+ jugadores que casi nunca son titulares (y no es lo normal)
N_LARGO = 15            # quien fue titular 3+ veces en los últimos 15 no es "nuevo": vuelve (lesión, sanción)
N_PROD = 12             # partidos para medir quién hace los goles y asistencias
PROD_MIN = 8            # con menos goles+asistencias registrados que esto no se opina del peso de las bajas
FIGURA = 0.15           # ⭐ = el jugador aporta ≥ 15% de los goles+asistencias del equipo
CLAVE_MIN = 0.35        # "baja clave": los habituales ausentes suman ≥ 35% de la producción...
CLAVE_NUEVA = 0.15      # ...y al menos 15% es de alguien que SÍ jugó el partido anterior (no es noticia vieja)
DIAS_APRETADO = 4       # calendario apretado: otro partido más importante dentro de 4 días
SEG_CALENDARIO = 20     # segundos por pasada para averiguar próximos partidos (365Scores tarda; no frenar las alineaciones)
PESO_COPA = {572: 3, 573: 2, 7685: 2, 102: 3, 389: 2}    # el resto de torneos pesa 1
MOV_CUOTA = 0.04        # la cuota "se movió" si cambió 4% o más desde la apertura
PUBLICAR = os.getenv("ROT_PUBLICAR") == "1"               # solo el radar real guarda el historial en el repo

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
    for k in ("enviados", "xi", "marcados", "noticias", "calres"):
        e.setdefault(k, {})
    return e


def guardar(e):
    limite = time.time() - 5 * 86400
    for k in ("enviados", "xi", "marcados", "calres"):
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


# ------------------------------------------------------------------ cuotas (Bet365 vía 365Scores, gratis)
def cuotas_partido(gid):
    """{'1': (apertura, ahora), 'X': …, '2': …, '1X': …, 'X2': …} o {} si no hay."""
    out = {}
    try:
        lineas = s365.cuotas(gid)
    except Exception:  # noqa: BLE001
        return out
    for ln in lineas:
        if ln.get("lineTypeId") not in (1, 14):
            continue
        for o in ln.get("options") or []:
            r = (o.get("rate") or {}).get("decimal")
            r0 = (o.get("originalRate") or {}).get("decimal")
            if r and r > 1 and o.get("name") in ("1", "X", "2", "1X", "X2", "12"):
                out[o["name"]] = (float(r0) if r0 and r0 > 1 else None, float(r))
    return out


def linea_cuota(eq, lado, cu):
    """Una línea que dice si la cuota del equipo ya se movió (lado: 0 local, 1 visita)."""
    c = cu.get("1" if lado == 0 else "2")
    if not c:
        return ""
    r0, r = c
    if not r0:
        return f"💱 Bet365: gana {eq['n']} paga {r:.2f} (verifica en tu app)"
    mov = r / r0 - 1
    if mov >= MOV_CUOTA:
        lectura = "ya subió: las casas lo están descontando"
    elif mov <= -MOV_CUOTA:
        lectura = "bajó: las casas no lo castigan"
    else:
        lectura = "casi no se movió"
    return f"💱 Bet365: gana {eq['n']} {r0:.2f} → {r:.2f} · {lectura} (verifica en tu app)"


# ------------------------------------------------------------------ calendario apretado
def proximo_partido(team_id, gid, despues):
    """Siguiente partido del equipo después del partido `gid`: dict {inicio, rival, liga, liga_id} o None. Caché 6 h.
    (games/fixtures solo devuelve 1 partido; se usa su misma paginación: los que vienen después de `gid`.)"""
    c = s365._c()
    k = f"rfix:{team_id}:{gid}"
    if k in c and time.time() - c[k]["t"] < 6 * 3600:
        lista = c[k]["d"]
    else:
        j = s365.get("games", competitors=team_id, games=3, aftergame=gid, direction=1)
        lista = []
        for g in (j or {}).get("games") or []:
            h, a = g.get("homeCompetitor") or {}, g.get("awayCompetitor") or {}
            if not g.get("startTime") or g.get("id") == gid or s365.anulado(g):
                continue
            lista.append([g["startTime"], h.get("id"), h.get("name"), a.get("name"),
                          g.get("competitionDisplayName") or "", g.get("competitionId")])
        lista.sort()
        if j is not None:
            c[k] = {"t": time.time(), "d": lista}
    for ini, hid, hn, an, liga, cid in lista:
        try:
            t = datetime.fromisoformat(ini).astimezone(TZ)
        except ValueError:
            continue
        if t > despues + timedelta(hours=3):
            return {"inicio": t, "rival": an if hid == team_id else hn, "liga": liga, "liga_id": cid}
    return None


def revisar_calendarios(e, lista):
    """Averigua de a pocos (SEG_CALENDARIO por pasada) el siguiente partido de cada club que juega pronto.
    Solo clubes: las selecciones juegan sus dos partidos de la fecha FIFA y ambos cuentan."""
    t0, t = time.time(), ahora()
    for p in lista:
        if p["liga_id"] not in C.LIGAS or not timedelta(0) < p["inicio"] - t <= timedelta(hours=HORAS_ADELANTE):
            continue
        for eq in p["eq"]:
            k = f"{p['id']}:{eq['id']}"
            if k in e["calres"]:
                continue
            if time.time() - t0 > SEG_CALENDARIO:
                return
            sig = proximo_partido(eq["id"], p["id"], p["inicio"])
            e["calres"][k] = {"t": time.time(), "sig": sig and dict(sig, inicio=sig["inicio"].isoformat())}


def calendario_apretado(e, eq, p, sin_nada_en_juego):
    """Texto si al equipo le viene pronto un partido más importante (o cualquiera, si aquí no se juega nada)."""
    sig = (e["calres"].get(f"{p['id']}:{eq['id']}") or {}).get("sig")
    if not sig:
        return None
    inicio = datetime.fromisoformat(sig["inicio"]).astimezone(TZ)
    dias = (inicio - p["inicio"]).total_seconds() / 86400
    if dias > DIAS_APRETADO or any(x in sig["liga"].lower() for x in EXCLUIR_LIGAS):
        return None
    mas_importante = PESO_COPA.get(sig["liga_id"], 1) > PESO_COPA.get(p["liga_id"], 1)
    if not (mas_importante or sin_nada_en_juego):
        return None
    liga = sig["liga"].split(",")[-1].strip() or sig["liga"]
    return (f"juega {liga} vs {sig['rival']} el {DIAS[inicio.weekday()]} {inicio:%d/%m} "
            f"({math.ceil(dias)} día{'s' if math.ceil(dias) != 1 else ''} después)")


# ------------------------------------------------------------------ historial (¿sirven las alertas?)
def publicar_historial():
    """Sube data/rotaciones.csv al repo (solo el radar real). Si falla, lo reintenta en la próxima alerta."""
    if not PUBLICAR:
        return
    cmd = ("git add data/rotaciones.csv && (git diff --cached --quiet || git commit -q -m 'historial de rotaciones') "
           "&& (git pull -q --rebase || (git rebase --abort; false)) && git push -q")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode:
        print("[historial] no se pudo subir ahora: " + (r.stderr or r.stdout).strip()[-200:], flush=True)


def registrar(p, lado, eq, a, motivo, cu, rival_rota):
    filas = rot_hist.leer()
    if any(f["game_id"] == str(p["id"]) and f["equipo"] == eq["n"] for f in filas):
        return
    gana = cu.get("1" if lado == 0 else "2")
    p_gana = ""
    if all(k in cu for k in ("1", "X", "2")):
        inv = [1 / cu[k][1] for k in ("1", "X", "2")]
        p_gana = f"{inv[0 if lado == 0 else 2] / sum(inv):.4f}"
    contra = cu.get("X2" if lado == 0 else "1X")
    filas.append({
        "fecha": f"{ahora():%Y-%m-%d %H:%M}", "game_id": str(p["id"]), "inicio": p["inicio"].isoformat(),
        "partido": titulo(p), "liga": p["liga"], "equipo": eq["n"], "lado": "local" if lado == 0 else "visita",
        "tipo": a["tipo"], "faltan": a["faltan"], "habituales": a["habituales"], "nuevos": a["n_nuevos"],
        "peso": f"{a['peso']:.2f}" if a["peso"] is not None else "", "motivo": motivo or "",
        "cuota_gana": f"{gana[1]:.2f}" if gana else "", "cuota_orig": f"{gana[0]:.2f}" if gana and gana[0] else "",
        "p_gana": p_gana, "cuota_contra": f"{contra[1]:.2f}" if contra else "",
        "rival_rota": "1" if rival_rota else "0", "goles_f": "", "goles_c": "", "resultado": ""})
    if rival_rota:
        for f in filas:
            if f["game_id"] == str(p["id"]):
                f["rival_rota"] = "1"
    rot_hist.escribir(filas)


def liquidar_historial():
    """Completa el resultado de las alertas cuyos partidos ya terminaron."""
    filas = rot_hist.leer()
    t, cambio = ahora(), False
    for gid in {f["game_id"] for f in filas if not f["resultado"]}:
        mias = [f for f in filas if f["game_id"] == gid and not f["resultado"]]
        horas = (t - datetime.fromisoformat(mias[0]["inicio"])).total_seconds() / 3600
        if horas < 1.9:
            continue
        g, _ = s365.detalle(int(gid))
        if g and s365.terminado(g):
            hg, ag = int(g["homeCompetitor"]["score"]), int(g["awayCompetitor"]["score"])
            for f in mias:
                gf, gc = (hg, ag) if f["lado"] == "local" else (ag, hg)
                f["goles_f"], f["goles_c"] = gf, gc
                f["resultado"] = "G" if gf > gc else "E" if gf == gc else "P"
            cambio = True
        elif horas > 30 or (g and s365.anulado(g)):
            for f in mias:
                f["resultado"] = "?"
            cambio = True
    if cambio:
        rot_hist.escribir(filas)
    return cambio


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
def traducir(texto):
    """Pasa un titular al español (traductor gratis de Google). Si falla, lo deja como está."""
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single", timeout=10,
                         params={"client": "gtx", "sl": "auto", "tl": "es", "dt": "t", "q": texto})
        t = "".join(x[0] for x in r.json()[0] if x and x[0]).strip()
        return t or texto
    except (requests.RequestException, ValueError, TypeError, IndexError, KeyError):
        return texto


def noticias(nombre, ingles):
    """Titulares de las últimas 48 h que nombran al equipo y hablan de rotación."""
    out = []
    consultas = [(nombre, 'fútbol (rotación OR suplentes OR rotaciones OR alternativo OR descanso)', "es-419", "PE", CLAVES_ES)]
    if ingles and norm(ingles) != norm(nombre):
        consultas.append((ingles, 'football (rotate OR rotation OR rested OR "second string" OR changes)', "en", "US", CLAVES_EN))
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
            cuerpo = t.rsplit(" - ", 1)[0]                  # sin el nombre del medio
            if viejo or norm(eq) not in norm(cuerpo) or not patron.search(cuerpo) or NO_NOTICIAS.search(t) \
                    or any(norm(cuerpo) == norm(o[2].rsplit(" - ", 1)[0]) for o in out):
                continue
            out.append((t if hl.startswith("es") else traducir(t), link, t))
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
            k = f"cal:{p['id']}:{eq['id']}"
            if forzar or k not in e["enviados"]:
                try:
                    apretado = calendario_apretado(e, eq, p, bool(motivo))
                except Exception:  # noqa: BLE001
                    apretado = None
                    print("[calendario] error:\n" + traceback.format_exc(), flush=True)
                if apretado:
                    e["enviados"][k] = time.time()
                    marca = e["marcados"].setdefault(f"{p['id']}:{eq['id']}", {"t": time.time(), "m": ""})
                    marca["cal"] = apretado
                    lineas.append(f"  📅 <b>{esc(eq['n'])}</b>: {esc(apretado)}")
            if con_noticias:
                for tit, link, original in noticias(eq["n"], eq["en"]):
                    k = "noti:" + norm(original)[:90]
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
        enviar("📋 <b>Radar de rotaciones</b>\n🔒 = ya no se juega nada · 📅 = le viene un partido más importante · "
               "📰 = lo que dice la prensa\n\n"
               + "\n\n".join(bloques)
               + "\n\n<i>Cuando salga la alineación te aviso si de verdad rotan.</i>")
    print(f"[resumen] {len(bloques)} partidos con novedades (noticias: {con_noticias})", flush=True)


# ------------------------------------------------------------------ 3) alineaciones
def analizar_xi(team_id, comp, nombres, gid, antes_de=None):
    """Compara el XI con los titulares habituales de los últimos N_PARTIDOS. Devuelve dict o None."""
    conf, _, xi, _ = s365.alineacion(comp)
    if len(xi) != 11:
        return None
    previos = [r for r in s365.resultados_equipo(team_id, 20)
               if r[0] != gid and (antes_de is None or r[1] < antes_de)][:N_LARGO]
    todos, nom, pos = [], {}, {}                    # todos = [(titulares, goles, asistencias)] del más nuevo al más viejo
    for r in previos:
        d = s365.resumen_partido(r[0])
        eq = (d or {}).get("eq", {}).get(str(team_id))
        if eq and len(eq["xi"]) == 11:
            todos.append(({str(x) for x in eq["xi"]}, eq.get("g") or {}, eq.get("a") or {}))
            nom.update(d.get("n", {}))
            pos.update(eq.get("pos", {}))
    hist = [x[0] for x in todos[:N_PARTIDOS]]
    n = len(hist)
    if n < MIN_PARTIDOS:
        return None
    veces, veces_largo = {}, {}
    for h in hist:
        for pid in h:
            veces[pid] = veces.get(pid, 0) + 1
    for h, _, _ in todos:
        for pid in h:
            veces_largo[pid] = veces_largo.get(pid, 0) + 1
    minimo = max(2, math.ceil(n * HABITUAL))
    habituales = {pid for pid, c in veces.items() if c >= minimo}
    if len(habituales) < MIN_HABITUALES:
        return None                                 # cambia de once todo el tiempo (típico en selecciones chicas)
    hoy = {str(x) for x in xi}
    faltan = sorted(habituales - hoy, key=lambda x: -veces[x])
    normal = sum(len(habituales - h) for h in hist) / n          # lo que le suele faltar a este equipo
    lista = {str(m.get("id")) for m in (comp.get("lineups") or {}).get("members") or []}
    raro = max(1, n // 5)                                         # "casi nunca titular" = 0 o 1 vez
    pocos = [p for p in hoy if veces.get(p, 0) <= raro]
    vuelven = [p for p in pocos if veces_largo.get(p, 0) >= 3]    # titulares de antes que regresan
    nuevos = [p for p in pocos if p not in vuelven]
    # cuántos "raros" suele poner: un equipo que siempre prueba gente no dispara la alerta
    normal_nuevos = sum(sum(1 for p in h if veces[p] <= raro) for h in hist) / n

    # ¿quién hace los goles? (goles + asistencias de los últimos N_PROD partidos)
    prod, total = {}, 0.0
    for _, g_, a_ in todos[:N_PROD]:
        for d_ in (g_, a_):
            for pid, v in d_.items():
                prod[pid] = prod.get(pid, 0) + v
                total += v
    parte = (lambda pid: prod.get(pid, 0) / total) if total >= PROD_MIN else (lambda pid: 0.0)
    peso = sum(parte(p) for p in faltan) if total >= PROD_MIN else None
    peso_nuevo = sum(parte(p) for p in faltan if p in hist[0])    # bajas que sí jugaron el partido anterior
    fuerte = ((len(faltan) >= UMBRAL_FALTAN and len(faltan) >= normal + SOBRE_LO_NORMAL)
              or (len(nuevos) >= NUEVOS_MIN and len(nuevos) >= normal_nuevos + 2))
    clave = peso is not None and peso >= CLAVE_MIN and peso_nuevo >= CLAVE_NUEVA

    def nombre(pid):
        return nom.get(pid) or nombres.get(int(pid), "") or "?"

    def ficha(pid):
        t = f"{nombre(pid)} ({veces.get(pid, 0)}/{n})"
        return t + (f" ⭐{parte(pid):.0%}" if parte(pid) >= FIGURA else "")
    return {
        "faltan": len(faltan), "habituales": len(habituales), "normal": normal, "n": n,
        "fuerte": fuerte, "clave": clave, "peso": peso,
        "tipo": ("masiva" if len(faltan) >= 6 or len(nuevos) >= 7 else "fuerte") if fuerte else "clave",
        "n_nuevos": len(nuevos), "normal_nuevos": normal_nuevos,
        "banca": [ficha(p) for p in faltan if p in lista],
        "fuera": [ficha(p) for p in faltan if p not in lista],
        "nuevos": [f"{nombre(p)} ({veces.get(p, 0)}/{n})" for p in nuevos],
        "vuelven": [nombre(p) for p in vuelven],
        "portero": any(pos.get(p) == "Portero" for p in faltan),
    }


def _suele_faltar(x):
    """Promedio de habituales que le faltan por partido, dicho en palabras."""
    if x < 0.75:
        return "este equipo casi nunca cambia su once"
    if x < 1.5:
        return "normalmente le falta 1"
    return f"normalmente le faltan {round(x)}"


def _suele_meter(x):
    if x < 0.5:
        return "normalmente no mete ninguno"
    if x < 1.5:
        return "normalmente mete 1"
    return f"normalmente mete {round(x)}"


def texto_xi(p, eq, a, motivo="", cuota="", apretado=""):
    cab = f"{esc(titulo(p))} · {p['inicio']:%H:%M} · {esc(p['liga'])}"
    if not (a["fuerte"] or a["clave"]):
        return (f"✅ <b>{esc(eq['n'])} sale con su equipo habitual</b>\n{cab}\n"
                f"Faltan {a['faltan']} de sus {a['habituales']} titulares habituales ({_suele_faltar(a['normal'])}).")
    if a["fuerte"]:
        lin = [f"🚨 <b>{rot_hist.NOMBRES[a['tipo']]}: {esc(eq['n'])}</b>", cab,
               f"Faltan <b>{a['faltan']} de sus {a['habituales']}</b> titulares habituales "
               f"({_suele_faltar(a['normal'])})."]
    else:
        lin = [f"⚠️ <b>Baja clave: {esc(eq['n'])}</b>", cab,
               f"Faltan {a['faltan']} de sus {a['habituales']} titulares habituales, pero son de los que hacen los goles."]
    if a["peso"] is not None and a["peso"] >= FIGURA:
        lin.append(f"⭐ Los ausentes hicieron el <b>{a['peso']:.0%}</b> de los goles y asistencias del equipo.")
    if a["banca"]:
        lin.append("🪑 En la banca: " + esc(", ".join(a["banca"])))
    if a["fuera"]:
        lin.append("❌ Fuera de la lista: " + esc(", ".join(a["fuera"])))
    if a["nuevos"] and a["fuerte"]:
        lin.append(f"🆕 Entran {a['n_nuevos']} que casi nunca son titulares ({_suele_meter(a['normal_nuevos'])}): "
                   + esc(", ".join(a["nuevos"])))
    if a["vuelven"] and a["fuerte"]:
        lin.append("↩️ Vuelven al once (ya eran titulares antes): " + esc(", ".join(a["vuelven"])))
    if a["portero"]:
        lin.append("🧤 Cambia el portero habitual.")
    if motivo:
        lin.append("🔒 " + esc(motivo))
    if apretado:
        lin.append("📅 " + esc(apretado[0].upper() + apretado[1:]))
    if cuota:
        lin.append(esc(cuota))
    lin.append(f"<i>(n/{a['n']}) = veces titular en sus últimos {a['n']} partidos"
               + (" · ⭐ = su parte de los goles y asistencias" if "⭐" in "".join(a["banca"] + a["fuera"]) else "")
               + ".</i>")
    return "\n".join(lin)


def _ya_roto(e, k):
    v = e["xi"].get(k)
    return isinstance(v, dict) and bool(v.get("f"))


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
        avisos = []                                     # (lado, equipo, análisis, marca)
        for lado, (eq, clave_lado) in enumerate(zip(p["eq"], ("homeCompetitor", "awayCompetitor"))):
            k = f"{p['id']}:{eq['id']}"
            if k in e["xi"]:
                continue
            comp = g.get(clave_lado) or {}
            conf, _, xi, _ = s365.alineacion(comp)
            if not conf or len(xi) != 11:
                if falta < -5:
                    e["xi"][k] = time.time()            # empezó y nunca se confirmó
                continue
            a = analizar_xi(eq["id"], comp, nombres, p["id"])
            e["xi"][k] = {"t": time.time(), "f": bool(a and (a["fuerte"] or a["clave"]))}
            if not a:
                continue
            marca = e["marcados"].get(k)
            print(f"[xi] {eq['n']}: faltan {a['faltan']}/{a['habituales']} (normal {a['normal']:.1f}), "
                  f"nuevos {a['n_nuevos']} (normal {a['normal_nuevos']:.1f}), peso {a['peso']}", flush=True)
            if a["fuerte"] or a["clave"] or marca:      # el ✅ solo para equipos que ya te había avisado
                avisos.append((lado, eq, a, marca or {}))
        if not avisos:
            continue
        alertas = [x for x in avisos if x[2]["fuerte"] or x[2]["clave"]]
        cu = cuotas_partido(p["id"]) if alertas else {}
        textos = []
        for lado, eq, a, marca in avisos:
            es_alerta = a["fuerte"] or a["clave"]
            textos.append(texto_xi(p, eq, a, marca.get("m", ""),
                                   linea_cuota(eq, lado, cu) if es_alerta else "", marca.get("cal", "")))
            if es_alerta:
                otro = p["eq"][1 - lado]
                rival_rota = len(alertas) == 2 or _ya_roto(e, f"{p['id']}:{otro['id']}")
                if rival_rota and len(alertas) == 1:
                    textos[-1] += f"\nℹ️ {esc(otro['n'])} también rota (te avisé antes): el efecto se compensa en parte."
                try:
                    registrar(p, lado, eq, a, marca.get("m", "") or marca.get("cal", ""), cu, rival_rota)
                except Exception:  # noqa: BLE001
                    print("[historial] error:\n" + traceback.format_exc(), flush=True)
        if len(alertas) == 2:                           # rotan los dos: un solo mensaje
            enviar("\n\n".join(textos) + "\n\nℹ️ <b>Rotan los dos equipos</b>: el efecto se compensa en parte.")
        else:
            for x in textos:
                enviar(x)
        if alertas:
            publicar_historial()


def resumen_semanal(e):
    """Los lunes a las 9 manda cómo les fue a las alertas."""
    t = ahora()
    semana = f"{t:%G-%V}"
    if t.weekday() != 0 or t.hour < 9 or e.get("semanal") == semana:
        return
    e["semanal"] = semana
    if any(f["resultado"] in ("G", "E", "P") for f in rot_hist.leer()):
        enviar(rot_hist.resumen())


# ------------------------------------------------------------------ ejecución
def pasada(e):
    lista = partidos()
    try:
        revisar_calendarios(e, lista)
    except Exception:  # noqa: BLE001
        print("[calendario] error:\n" + traceback.format_exc(), flush=True)
    try:
        resumen(e, lista)
    except Exception:  # noqa: BLE001
        print("[resumen] error:\n" + traceback.format_exc(), flush=True)
    try:
        alineaciones(e, lista)
    except Exception:  # noqa: BLE001
        print("[xi] error:\n" + traceback.format_exc(), flush=True)
    try:
        if liquidar_historial():
            publicar_historial()
        resumen_semanal(e)
    except Exception:  # noqa: BLE001
        print("[historial] error:\n" + traceback.format_exc(), flush=True)
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
    print("\n===== NUEVO: próximos partidos (calendario apretado) y cuotas =====")
    ed = cargar()
    t0 = time.time()
    revisar_calendarios(ed, lista)
    print(f"calendarios: {len(ed['calres'])} equipos revisados en {time.time() - t0:.0f} s (tope {SEG_CALENDARIO} s por pasada)")
    for p in lista[:25]:
        cu = cuotas_partido(p["id"])
        for lado, eq in enumerate(p["eq"]):
            sig = (ed["calres"].get(f"{p['id']}:{eq['id']}") or {}).get("sig")
            print(f"  {eq['n']}: siguiente={sig and (sig['inicio'][:16], sig['rival'], sig['liga'], sig['liga_id'])} "
                  f"apretado={calendario_apretado(ed, eq, p, True)!r} | {linea_cuota(eq, lado, cu)!r}")
    print("\n===== RESUMEN (forzado) =====")
    resumen(ed, lista, forzar=True)
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
                marca = "ALERTA " if a["fuerte"] else "CLAVE  " if a["clave"] else "normal "
                a2 = a if (a["fuerte"] or a["clave"]) else dict(a, fuerte=True, tipo="fuerte")
                lado = 0 if lado == "homeCompetitor" else 1
                print(marca + f"[peso {a['peso']}] " + texto_xi(p, eq, a2, "", linea_cuota(eq, lado, cuotas_partido(p["id"]))).replace("\n", " | "))
    s365.guardar_cache()


if __name__ == "__main__":
    if "--diag" in sys.argv:
        diagnostico()
    elif "--vigilar" in sys.argv:
        fin = time.time() + float(sys.argv[sys.argv.index("--vigilar") + 1]) * 60
        estado = cargar()
        ARCHIVOS = "rotaciones.py rot_hist.py s365.py config.py calendario.py"

        def version(ref):
            return subprocess.run("git rev-parse " + " ".join(f"{ref}:{a}" for a in ARCHIVOS.split()),
                                  shell=True, capture_output=True, text=True).stdout
        codigo = version("HEAD")
        while time.time() < fin:
            try:
                n = pasada(estado)
                print(f"[rotaciones] pasada lista ({ahora():%H:%M}), {n} partidos en la mira", flush=True)
            except Exception:  # noqa: BLE001
                print("[rotaciones] error:\n" + traceback.format_exc(), flush=True)
            time.sleep(max(0, min(PASADA_SEG, fin - time.time())))
            subprocess.run("git fetch -q origin main", shell=True)
            nueva = version("origin/main")
            if nueva and nueva != codigo:
                print("[rotaciones] código nuevo en el repo: me reinicio", flush=True)
                break
    else:
        pasada(cargar())
