"""Bot de picks: lee comandos de Telegram, vigila los partidos que sigues y
envía los picks cuando salen las alineaciones. Luego liquida en paper trading.

Uso:
  python picks_bot.py              -> una pasada (lo que hace GitHub Actions)
  python picks_bot.py --loop       -> en tu PC, cada 2 minutos
  python picks_bot.py --probar ID  -> analiza un partido ya y lo manda a Telegram (no se registra)
"""
import csv
import html
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config as C
import modelo as M
import s365

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))
PAGINA = os.getenv("PAGINA_URL", "")
TZ = ZoneInfo(C.ZONA)

DIR = os.path.join(os.path.dirname(__file__), "data")
ESTADO = os.path.join(DIR, "estado.json")
SEGUIDOS_PUB = os.path.join(DIR, "seguidos.json")
LOG = os.path.join(DIR, "picks.csv")
CAMPOS = ["fecha", "game_id", "partido", "liga", "tipo", "mercado", "clave", "prob", "cuota",
          "cuota_justa", "ev", "stake", "alineacion", "resultado", "ganancia"]


# ======================= utilidades =======================
def ahora():
    return datetime.now(TZ)


def iso(s):
    return datetime.fromisoformat(s).astimezone(TZ)


def esc(t):
    return html.escape(str(t))


def tg(metodo, **datos):
    if not TOKEN:
        print("[tg] sin TELEGRAM_TOKEN:", datos.get("text", "")[:300])
        return None
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{metodo}", json=datos, timeout=30)
        return r.json()
    except requests.RequestException as e:
        print("[tg] error", e)
        return None


def enviar(texto):
    for i in range(0, len(texto), 3900):
        tg("sendMessage", chat_id=CHAT_ID, text=texto[i:i + 3900], parse_mode="HTML",
           disable_web_page_preview=True)


def cargar_estado():
    try:
        with open(ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"offset": 0, "seguidos": {}}


def guardar_estado(e):
    os.makedirs(DIR, exist_ok=True)
    with open(ESTADO, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, indent=1)
    pub = {gid: {"partido": s["partido"], "estado": s["estado"]} for gid, s in e["seguidos"].items()}
    with open(SEGUIDOS_PUB, "w", encoding="utf-8") as f:
        json.dump(pub, f, ensure_ascii=False)


def leer_log():
    try:
        with open(LOG, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def escribir_log(filas):
    os.makedirs(DIR, exist_ok=True)
    with open(LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)


def clave_str(c):
    return "|".join("" if x is None else str(x) for x in c)


def clave_de(s):
    t, l, sel = s.split("|")
    return (t, float(l) if l else None, sel)


# ======================= comandos de Telegram =======================
AYUDA = (
    "🤖 <b>Bot de picks</b>\n\n"
    "• Toca <b>Seguir</b> en el calendario web o escribe <code>/seguir ID</code>\n"
    "• <code>/lista</code> partidos que sigues\n"
    "• <code>/quitar ID</code> dejar de seguir\n"
    "• <code>/resumen</code> resultados del paper trading\n\n"
    "Los picks llegan cuando se confirman las alineaciones (normalmente ~1 hora antes).\n"
    "Ojo: el bot revisa cada ~5-10 min, así que tus comandos se responden con ese retraso."
)


def seguir(estado, gid):
    if gid in estado["seguidos"]:
        return f"Ya sigues ese partido: {esc(estado['seguidos'][gid]['partido'])}"
    g, _ = s365.detalle(gid)
    if not g:
        return f"No encontré el partido {gid}."
    inicio = iso(g["startTime"])
    if inicio < ahora():
        return "Ese partido ya empezó o terminó."
    partido = f"{g['homeCompetitor']['name']} vs {g['awayCompetitor']['name']}"
    estado["seguidos"][gid] = {
        "partido": partido, "liga": g.get("competitionDisplayName", ""),
        "inicio": g["startTime"], "estado": "pendiente", "ultimo": 0,
    }
    return (f"✅ Siguiendo <b>{esc(partido)}</b>\n🏆 {esc(g.get('competitionDisplayName', ''))}"
            f" · 🗓 {inicio:%d/%m %H:%M}\nTe mando los picks cuando salgan las alineaciones.")


def procesar_comandos(estado):
    r = tg("getUpdates", offset=estado.get("offset", 0), timeout=0)
    if not r or not r.get("ok"):
        return
    for u in r["result"]:
        estado["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != CHAT_ID:
            continue
        texto = (msg.get("text") or "").strip()
        partes = texto.split()
        if not partes:
            continue
        cmd = partes[0].split("@")[0].lower()
        arg = partes[1] if len(partes) > 1 else ""
        if cmd == "/start" and arg.startswith("f"):
            enviar(seguir(estado, arg[1:]))
        elif cmd == "/seguir" and arg.isdigit():
            enviar(seguir(estado, arg))
        elif cmd == "/quitar" and arg in estado["seguidos"]:
            p = estado["seguidos"].pop(arg)
            enviar(f"🗑 Dejaste de seguir {esc(p['partido'])}")
        elif cmd == "/lista":
            enviar(texto_lista(estado))
        elif cmd == "/resumen":
            enviar(texto_resumen())
        else:
            enviar(AYUDA + (f"\n\n📅 Calendario: {PAGINA}" if PAGINA else ""))


def texto_lista(estado):
    if not estado["seguidos"]:
        return "No sigues ningún partido todavía."
    filas = sorted(estado["seguidos"].items(), key=lambda x: x[1]["inicio"])
    iconos = {"pendiente": "⏳", "enviado": "📨", "liquidado": "✔️", "cancelado": "❌"}
    out = ["📋 <b>Partidos que sigues</b>"]
    for gid, s in filas:
        out.append(f"{iconos.get(s['estado'], '•')} {iso(s['inicio']):%d/%m %H:%M} "
                   f"{esc(s['partido'])} <code>{gid}</code>")
    return "\n".join(out)


def texto_resumen():
    filas = [f for f in leer_log() if f["resultado"] in ("G", "P")]
    if not filas:
        return "Todavía no hay picks liquidados."
    out = ["📈 <b>Paper trading</b>"]
    for tipo, icono in (("seguro", "🟢"), ("arriesgado", "🔴"), ("apostable", "✅")):
        fs = [f for f in filas if f["tipo"] == tipo]
        if not fs:
            continue
        ac = sum(f["resultado"] == "G" for f in fs)
        pm = sum(float(f["prob"]) for f in fs) / len(fs)
        out.append(f"\n{icono} <b>{tipo.capitalize()}s</b>: {ac}/{len(fs)} aciertos "
                   f"({ac / len(fs):.0%}) · el modelo esperaba {pm:.0%}")
        if tipo == "apostable":
            st = sum(float(f["stake"] or 0) for f in fs)
            gan = sum(float(f["ganancia"] or 0) for f in fs)
            out.append(f"   Apostado S/ {st:.2f} · Ganancia S/ {gan:+.2f} · "
                       f"Yield {gan / st:+.1%}" if st else "")
    n_ap = len([f for f in filas if f["tipo"] == "apostable"])
    out.append(f"\nMuestra: {n_ap}/200 apostables liquidados antes de pensar en dinero real.")
    out.append("Si los aciertos reales quedan muy por debajo de lo esperado, el modelo está "
               "sobreestimando y hay que corregirlo.")
    return "\n".join(out)


# ======================= análisis =======================
def stats_recientes(team_id, resultados, n=5):
    lista = []
    for gid, *_ in resultados[:n]:
        st = s365.stats_partido(gid)
        if team_id in st and len(st) == 2:
            rival = [k for k in st if k != team_id][0]
            yo, el = st[team_id], st[rival]
            d = {}
            if "c" in yo and "c" in el:
                d["c"], d["c_contra"] = yo["c"], el["c"]
                s1 = s365.stats_partido(gid, primer_tiempo=True)
                if team_id in s1 and rival in s1 and "c" in s1[team_id] and "c" in s1[rival]:
                    d["c1"], d["c1_contra"] = s1[team_id]["c"], s1[rival]["c"]
            if "y" in yo:
                d["k"] = yo["y"] + yo.get("r", 0)
            lista.append(d)
    return lista


def analizar(gid, g, nombres, con_alineacion):
    h, a = g["homeCompetitor"], g["awayCompetitor"]
    local, visita = h["name"], a["name"]
    res_l = s365.resultados_equipo(h["id"])
    res_v = s365.resultados_equipo(a["id"])
    lh, la, nl, nv = M.goles_esperados(res_l, res_v, h["id"], a["id"])

    notas = []
    conf_l, form_l, xi_l, _ = s365.alineacion(h)
    conf_v, form_v, xi_v, _ = s365.alineacion(a)
    if con_alineacion:
        resum_l = [s365.resumen_partido(x[0]) for x in res_l[:5]]
        resum_v = [s365.resumen_partido(x[0]) for x in res_v[:5]]
        fa_l, fr_l, inf_l = M.ajuste_alineacion(h["id"], xi_l, resum_l, nombres)
        fa_v, fr_v, inf_v = M.ajuste_alineacion(a["id"], xi_v, resum_v, nombres)
        lh *= fa_l * fr_v
        la *= fa_v * fr_l
        for eq, inf in ((local, inf_l), (visita, inf_v)):
            if inf["faltan"]:
                notas.append(f"⚠️ {esc(eq)} sin: " + ", ".join(esc(x) for x in inf["faltan"][:4]))
            if inf["cambios"] >= 5:
                notas.append(f"🔄 {esc(eq)}: {inf['cambios']} cambios vs su último partido")

    esp = M.corners_tarjetas(stats_recientes(h["id"], res_l), stats_recientes(a["id"], res_v))
    mu_c, mu_k = esp["total"], esp["tarjetas"]
    mercado = M.leer_cuotas(s365.cuotas(gid), local, visita)
    cands, _ = M.candidatos(lh, la, esp, mercado, local, visita)
    seguros, arriesgados, apostables = M.seleccionar(cands)

    inicio = iso(g["startTime"])
    lin = [f"⚽ <b>{esc(local)} vs {esc(visita)}</b>",
           f"🏆 {esc(g.get('competitionDisplayName', ''))} · 🕐 {inicio:%d/%m %H:%M}"]
    if con_alineacion:
        lin.append(f"📋 Alineaciones confirmadas ({esc(form_l or '?')} / {esc(form_v or '?')})")
    else:
        lin.append("📋 <b>Sin alineación confirmada</b> a minutos del inicio: picks solo con datos previos")
    lin += notas
    extra = []
    if mu_c:
        extra.append(f"Córners {mu_c:.1f}" + (f" ({esp['local']:.1f}-{esp['visita']:.1f})" if esp["local"] else ""))
    if mu_k:
        extra.append(f"Tarjetas {mu_k:.1f}")
    lin.append(f"📊 Goles esperados {lh:.2f} – {la:.2f}" + (" · " + " · ".join(extra) if extra else ""))
    if min(nl, nv) < 5:
        lin.append("ℹ️ Pocos partidos recientes de algún equipo: el modelo es menos confiable.")
    if not mercado:
        lin.append("ℹ️ Sin cuotas de Bet365 para este partido: probabilidades solo del modelo.")

    def fila(c, con_stake=False):
        t = f"• {esc(c['texto'])} — <b>{c['p']:.0%}</b>"
        if c["cuota"]:
            t += f" · cuota {c['cuota']:.2f} · EV {c['ev']:+.0%}"
        t += f" · justa {c['justa']:.2f}"
        if con_stake:
            t += f" · stake S/ {c['stake']:.2f}"
        return t

    lin.append("\n🟢 <b>SEGUROS</b> (lo más probable)")
    lin += [fila(c) for c in seguros] or ["• Ninguno claro"]
    lin.append("\n🔴 <b>ARRIESGADOS</b> (difíciles pero posibles)")
    lin += [fila(c) for c in arriesgados] or ["• Ninguno"]
    lin.append(f"\n✅ <b>APOSTABLES</b> (EV ≥ {C.EV_MIN:.0%}, cuota ≥ {C.CUOTA_MIN:.2f})")
    if apostables:
        lin += [fila(c, True) for c in apostables]
    else:
        lin.append("• Ninguno: el mercado no deja valor aquí. No apostar también es una decisión.")
    lin.append("\n<i>“justa” = cuota mínima que deberías aceptar en tu casa de apuestas. "
               "Seguros/arriesgados sin valor son informativos, no apuestas.</i>")
    lin.append("🧪 Paper trading · cuotas Bet365 vía 365Scores")

    filas = []
    fecha = ahora().strftime("%Y-%m-%d %H:%M")
    for tipo, lista in (("seguro", seguros), ("arriesgado", arriesgados), ("apostable", apostables)):
        for c in lista:
            filas.append({
                "fecha": fecha, "game_id": gid, "partido": f"{local} vs {visita}",
                "liga": g.get("competitionDisplayName", ""), "tipo": tipo, "mercado": c["texto"],
                "clave": clave_str(c["clave"]), "prob": f"{c['p']:.4f}",
                "cuota": f"{c['cuota']:.2f}" if c["cuota"] else "", "cuota_justa": f"{c['justa']:.2f}",
                "ev": f"{c['ev']:.4f}" if c["ev"] is not None else "",
                "stake": f"{c.get('stake', 0):.2f}" if tipo == "apostable" else "",
                "alineacion": "si" if con_alineacion else "no", "resultado": "", "ganancia": "",
            })
    return "\n".join(lin), filas


def liquidar(gid, s, log):
    g, _ = s365.detalle(gid)
    if not g:
        return False
    if s365.anulado(g):
        s["estado"] = "cancelado"
        enviar(f"❌ {esc(s['partido'])}: partido {esc(g.get('statusText', 'anulado'))}. Picks anulados.")
        for f in log:
            if f["game_id"] == gid and not f["resultado"]:
                f["resultado"], f["ganancia"] = "N", "0"
        return True
    if not s365.terminado(g):
        return False
    st = s365.stats_partido(gid)
    st1 = s365.stats_partido(gid, primer_tiempo=True)
    hid, aid = g["homeCompetitor"]["id"], g["awayCompetitor"]["id"]

    def corner(d, cid):
        return d.get(cid, {}).get("c")

    ch, ca = corner(st, hid), corner(st, aid)
    c1h, c1a = corner(st1, hid), corner(st1, aid)
    r = {"hg": int(g["homeCompetitor"]["score"]), "ag": int(g["awayCompetitor"]["score"]),
         "ht": s365.marcador_1t(g),
         "corners": ch + ca if None not in (ch, ca) else None,
         "corners_h": ch, "corners_a": ca,
         "corners_1t": c1h + c1a if None not in (c1h, c1a) else None,
         "cards": sum(v.get("y", 0) + v.get("r", 0) for v in st.values()) if len(st) == 2 else None}
    out = [f"🏁 <b>{esc(s['partido'])}</b> terminó {r['hg']}-{r['ag']}"]
    iconos = {"G": "✅", "P": "❌", "N": "↩️", "?": "❔"}
    for f in log:
        if f["game_id"] != gid or f["resultado"]:
            continue
        res = M.evaluar(clave_de(f["clave"]), r)
        f["resultado"] = res if res != "?" else ""
        if f["tipo"] == "apostable" and res in ("G", "P", "N"):
            stake = float(f["stake"] or 0)
            f["ganancia"] = f"{stake * (float(f['cuota']) - 1):.2f}" if res == "G" else (
                f"{-stake:.2f}" if res == "P" else "0")
        out.append(f"{iconos[res]} [{f['tipo']}] {esc(f['mercado'])}"
                   + (f" · S/ {float(f['ganancia']):+.2f}" if f["ganancia"] else ""))
        if res == "?":
            f["resultado"] = "?"
    s["estado"] = "liquidado"
    enviar("\n".join(out))
    return True


# ======================= ciclo principal =======================
def pasada():
    estado = cargar_estado()
    procesar_comandos(estado)
    log = leer_log()
    t = ahora()
    for gid, s in list(estado["seguidos"].items()):
        inicio = iso(s["inicio"])
        mins = (inicio - t).total_seconds() / 60
        if s["estado"] == "pendiente":
            if mins > C.VENTANA_MIN:
                continue
            urgente = mins <= C.ENVIAR_SIN_ALINEACION_MIN
            if time.time() - s.get("ultimo", 0) < C.REVISAR_CADA_MIN * 60 and not urgente:
                continue
            s["ultimo"] = time.time()
            g, nombres = s365.detalle(gid)
            if not g:
                continue
            if s365.anulado(g):
                s["estado"] = "cancelado"
                enviar(f"❌ {esc(s['partido'])}: {esc(g.get('statusText'))}")
                continue
            if g.get("startTime") and g["startTime"] != s["inicio"]:
                s["inicio"] = g["startTime"]
                if (iso(s["inicio"]) - t).total_seconds() / 60 > C.VENTANA_MIN:
                    continue
            ok_l = s365.alineacion(g["homeCompetitor"])[0]
            ok_v = s365.alineacion(g["awayCompetitor"])[0]
            if (ok_l and ok_v) or urgente:
                if mins < -20:
                    s["estado"] = "cancelado"
                    enviar(f"⌛ {esc(s['partido'])}: ya empezó y no alcancé a enviar picks.")
                    continue
                texto, filas = analizar(gid, g, nombres, ok_l and ok_v)
                enviar(texto)
                log += filas
                s["estado"] = "enviado"
        elif s["estado"] == "enviado" and mins < -115:
            if time.time() - s.get("ultimo", 0) >= 10 * 60:
                s["ultimo"] = time.time()
                liquidar(gid, s, log)
        elif s["estado"] in ("liquidado", "cancelado") and mins < -2 * 24 * 60:
            del estado["seguidos"][gid]
    escribir_log(log)
    guardar_estado(estado)
    s365.guardar_cache()


def probar(gid):
    g, nombres = s365.detalle(gid)
    if not g:
        print("No se pudo leer el partido", gid)
        return
    ok = s365.alineacion(g["homeCompetitor"])[0] and s365.alineacion(g["awayCompetitor"])[0]
    texto, _ = analizar(gid, g, nombres, ok)
    print(texto)
    enviar("🧪 <b>PRUEBA</b> (no se registra)\n\n" + texto)
    s365.guardar_cache()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--probar":
        probar(sys.argv[2])
    elif "--loop" in sys.argv:
        while True:
            try:
                pasada()
            except Exception as e:  # noqa: BLE001
                print("error en pasada:", e)
            time.sleep(120)
    else:
        pasada()
