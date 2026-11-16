"""Historial del radar de rotaciones: cada alerta fuerte con la cuota del momento y lo que pasó después.
Sirve para saber si las alertas valen algo. Lo usan rotaciones.py (escribe) y picks_bot.py (/rotaciones)."""
import csv
import os

RUTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "rotaciones.csv")
CAMPOS = ["fecha", "game_id", "inicio", "partido", "liga", "equipo", "lado", "tipo", "faltan", "habituales",
          "nuevos", "peso", "motivo", "cuota_gana", "cuota_orig", "p_gana", "cuota_contra", "rival_rota",
          "goles_f", "goles_c", "resultado"]
STAKE = 10.0
NOMBRES = {"masiva": "Rotación masiva", "fuerte": "Rotación fuerte", "clave": "Baja clave"}


def leer():
    try:
        with open(RUTA, encoding="utf-8", newline="") as f:
            return [{k: r.get(k, "") or "" for k in CAMPOS} for r in csv.DictReader(f)]
    except OSError:
        return []


def escribir(filas):
    os.makedirs(os.path.dirname(RUTA), exist_ok=True)
    with open(RUTA, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _bloque(nombre, fs):
    n = len(fs)
    g = sum(f["resultado"] == "G" for f in fs)
    e = sum(f["resultado"] == "E" for f in fs)
    lin = [f"<b>{nombre}</b>: {n} alertas · el equipo que rotó ganó {g} ({g / n:.0%}), empató {e}, perdió {n - g - e}"]
    con_p = [f for f in fs if _num(f["p_gana"]) is not None]
    if con_p:
        esp = sum(_num(f["p_gana"]) for f in con_p) / len(con_p)
        real = sum(f["resultado"] == "G" for f in con_p) / len(con_p)
        lin.append(f"   Las cuotas decían que ganaría {esp:.0%} · ganó {real:.0%} ({len(con_p)} con cuota)")
    con_c = [f for f in fs if _num(f["cuota_contra"])]
    if con_c:
        gan = sum(STAKE * (_num(f["cuota_contra"]) - 1) if f["resultado"] != "G" else -STAKE for f in con_c)
        lin.append(f"   Apostando S/ {STAKE:.0f} en contra (rival o empate) en cada una: S/ {gan:+.2f} "
                   f"({gan / (len(con_c) * STAKE):+.1%}) en {len(con_c)}")
    return lin


def resumen(filas=None):
    filas = leer() if filas is None else filas
    hechas = [f for f in filas if f["resultado"] in ("G", "E", "P")]
    pend = sum(1 for f in filas if not f["resultado"])
    if not hechas:
        return ("🔄 <b>Radar de rotaciones</b>\nAún no hay alertas con resultado"
                + (f" ({pend} esperando a que termine el partido)." if pend else "."))
    out = ["🔄 <b>Radar de rotaciones: ¿sirven las alertas?</b>"]
    out += _bloque("Todas", hechas)
    for tipo, nombre in NOMBRES.items():
        fs = [f for f in hechas if f["tipo"] == tipo]
        if fs and len(fs) < len(hechas):
            out += _bloque(nombre, fs)
    solo = [f for f in hechas if f["rival_rota"] != "1"]
    if solo and len(solo) < len(hechas):
        out += _bloque("Solo cuando el rival NO rotó", solo)
    if pend:
        out.append(f"⏳ {pend} alertas esperando resultado.")
    out.append("<i>Si el equipo que rota gana claramente menos de lo que decían las cuotas, las alertas tienen valor. "
               "Con menos de ~40 alertas todavía es azar.</i>")
    return "\n".join(out)
