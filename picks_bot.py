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
import re
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config as C
import criterios
import cuotas_ext
import modelo as M
import s365

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))
PAGINA = os.getenv("PAGINA_URL", "")
TZ = ZoneInfo(C.ZONA)

DIR = os.path.join(os.path.dirname(__file__), "data")
ESTADO = os.path.join(DIR, "estado.json")
SEGUIDOS_PUB = os.path.join(DIR, "seguidos.json")
SELECCION = os.path.join(DIR, "seleccion.json")   # lo escribe la página web
LOG = os.path.join(DIR, "picks.csv")
CAMPOS = ["fecha", "game_id", "partido", "liga", "tipo", "mercado", "clave", "prob", "cuota", "casa",
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


COLA = os.getenv("COLA") == "1"       # en GitHub: los mensajes se envían solo si el estado se guardó
SALIDA = os.path.join(DIR, "salida.json")
_cola = []


def _enviar_ya(texto):
    for i in range(0, len(texto), 3900):
        tg("sendMessage", chat_id=CHAT_ID, text=texto[i:i + 3900], parse_mode="HTML",
           disable_web_page_preview=True)


def enviar(texto):
    if COLA:
        _cola.append(texto)
    else:
        _enviar_ya(texto)


def guardar_cola():
    global _cola
    if not _cola:
        return
    try:
        with open(SALIDA, encoding="utf-8") as f:
            pend = json.load(f)
    except (OSError, ValueError):
        pend = []
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(pend + _cola, f, ensure_ascii=False)
    _cola = []


def enviar_cola():
    """Envía la bandeja de salida (se llama solo después de guardar el estado)."""
    try:
        with open(SALIDA, encoding="utf-8") as f:
            pend = json.load(f)
    except (OSError, ValueError):
        return
    if not pend:
        return
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump([], f)
    for t in pend:
        _enviar_ya(t)
    print(f"[tg] {len(pend)} mensajes enviados")


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
    pub = {gid: {"partido": r["partido"], "estado": "rechazado", "motivo": r["motivo"], "t": r["t"]}
           for gid, r in e.get("rechazados", {}).items()}
    pub.update({gid: {"partido": s["partido"], "estado": s["estado"]} for gid, s in e["seguidos"].items()})
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


def seguir(estado, gid, corto=False):
    if gid in estado["seguidos"]:
        return f"Ya sigues ese partido: {esc(estado['seguidos'][gid]['partido'])}"
    g, _ = s365.detalle(gid)
    if not g:
        return None          # falla temporal de 365Scores: se reintenta en la próxima pasada
    inicio = iso(g["startTime"])
    if inicio < ahora():
        return (f"{esc(g['homeCompetitor']['name'])} vs {esc(g['awayCompetitor']['name'])} ya empezó "
                f"({inicio:%H:%M}): no se puede analizar. Elige los partidos antes del inicio, "
                f"idealmente 1 hora antes.")
    partido = f"{g['homeCompetitor']['name']} vs {g['awayCompetitor']['name']}"
    estado["seguidos"][gid] = {
        "partido": partido, "liga": g.get("competitionDisplayName", ""),
        "inicio": g["startTime"], "estado": "pendiente", "ultimo": 0,
    }
    huella_365(gid, s365.cuotas(gid))      # foto de las cuotas para detectar si luego no se mueven
    if corto:
        return f"✅ {inicio:%d/%m %H:%M} · <b>{esc(partido)}</b> · {esc(g.get('competitionDisplayName', ''))}"
    return (f"✅ Siguiendo <b>{esc(partido)}</b>\n🏆 {esc(g.get('competitionDisplayName', ''))}"
            f" · 🗓 {inicio:%d/%m %H:%M}\nTe mando los picks cuando salgan las alineaciones.")


def procesar_comandos(estado):
    r = tg("getUpdates", offset=estado.get("offset", 0), timeout=0)
    if not r or not r.get("ok"):
        print("[tg] getUpdates falló:", r)
        return
    print(f"[tg] {len(r['result'])} mensajes nuevos")
    for u in r["result"]:
        estado["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id"))
        if chat != CHAT_ID:
            print(f"[tg] mensaje de chat {chat} ignorado (TELEGRAM_CHAT_ID = '{CHAT_ID}')")
            tg("sendMessage", chat_id=chat,
               text=f"⚠️ Este chat no está autorizado.\nTu chat id es: {chat}\n"
                    f"Ponlo en GitHub → Settings → Secrets → TELEGRAM_CHAT_ID y vuelve a enviar el partido.")
            continue
        texto = (msg.get("text") or "").strip()
        partes = texto.split()
        if not partes:
            continue
        cmd = partes[0].split("@")[0].lower()
        arg = partes[1] if len(partes) > 1 else ""
        if cmd == "/start" and arg.startswith("f"):
            enviar(seguir(estado, arg[1:]) or "No pude leer el partido ahora, intenta de nuevo en unos minutos.")
        elif cmd == "/seguir" and arg.isdigit():
            enviar(seguir(estado, arg) or "No pude leer el partido ahora, intenta de nuevo en unos minutos.")
        elif cmd == "/quitar" and arg in estado["seguidos"]:
            p = estado["seguidos"].pop(arg)
            enviar(f"🗑 Dejaste de seguir {esc(p['partido'])}")
        elif cmd == "/lista":
            enviar(texto_lista(estado))
        elif cmd == "/resumen":
            enviar(texto_resumen())
        else:
            enviar(AYUDA + (f"\n\n📅 Calendario: {PAGINA}" if PAGINA else ""))


def sincronizar_seleccion(estado):
    """Aplica lo que elegiste en la página web (data/seleccion.json)."""
    try:
        with open(SELECCION, encoding="utf-8") as f:
            sel = json.load(f)
    except (OSError, ValueError):
        return
    hechos = estado.setdefault("web", {})        # gid -> marca de tiempo ya aplicada
    nuevos, quitados, fallos = [], [], []
    for gid, d in sel.items():
        t = d.get("t", 0)
        if hechos.get(gid) == t:
            continue
        if d.get("accion") == "seguir":
            if gid in estado["seguidos"]:
                hechos[gid] = t
                continue
            txt = seguir(estado, gid, corto=True)
            if txt is None:
                print(f"[web] no pude leer el partido {gid}: reintento luego")
                continue
            hechos[gid] = t
            if gid in estado["seguidos"]:
                nuevos.append(txt)
                estado.get("rechazados", {}).pop(gid, None)
            else:
                fallos.append(txt)
                estado.setdefault("rechazados", {})[gid] = {
                    "partido": re.sub(r"<[^>]+>", "", txt)[:80], "motivo": re.sub(r"<[^>]+>", "", txt), "t": t}
        elif d.get("accion") == "quitar" and gid not in estado["seguidos"]:
            hechos[gid] = t
            estado.get("rechazados", {}).pop(gid, None)
        elif d.get("accion") == "quitar" and gid in estado["seguidos"]:
            hechos[gid] = t
            if estado["seguidos"][gid]["estado"] == "pendiente":
                quitados.append(esc(estado["seguidos"].pop(gid)["partido"]))
    corte = (time.time() - 7 * 86400) * 1000
    for gid in [g for g, t in hechos.items() if t < corte]:
        del hechos[gid]
    rech = estado.get("rechazados", {})
    for gid in [g for g, r in rech.items() if r["t"] < corte]:
        del rech[gid]
    partes = []
    if nuevos:
        partes.append(f"📌 <b>Siguiendo {len(nuevos)} partido(s)</b>\n" + "\n".join(nuevos)
                      + "\n\nTe mando los picks cuando salgan las alineaciones.")
    if quitados:
        partes.append("🗑 Dejaste de seguir: " + ", ".join(quitados))
    if fallos:
        partes.append("⚠️ " + "\n⚠️ ".join(fallos))
    if partes:
        enviar("\n\n".join(partes))


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
    for tipo, icono in (("seguro", "🟢"), ("arriesgado", "🔴"), ("modelo", "📐"), ("apostable", "✅")):
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
    q = cuotas_ext.leer_quota()
    if q:
        out.append(f"\n💱 Créditos The Odds API: {q.get('restantes')} restantes este mes")
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
            if "s" in yo and "s" in el:
                d["s"], d["s_contra"] = yo["s"], el["s"]
            lista.append(d)
    return lista


def huella_365(gid, lineas):
    """Registra las cuotas de 365Scores y devuelve cuántas horas llevan sin moverse."""
    fp = json.dumps(sorted((ln.get("lineTypeId"), ln.get("internalOptionValue"),
                            tuple((o.get("rate") or {}).get("decimal") for o in ln.get("options") or []))
                           for ln in lineas), default=str)
    cache = s365._c()
    k = f"fp365:{gid}"
    prev = cache.get(k)
    if not prev or prev["fp"] != fp:
        cache[k] = {"t": time.time(), "fp": fp}
        return 0.0
    return (time.time() - prev["t"]) / 3600


def nombres_ingles(gid):
    j = s365.get("game", gameId=gid, langId=1)
    g = (j or {}).get("game") or {}
    try:
        return g["homeCompetitor"]["name"], g["awayCompetitor"]["name"]
    except KeyError:
        return None


def firma_xi(g):
    """Huella de los dos XI (para saber si cambió la alineación)."""
    xl = sorted(s365.alineacion(g["homeCompetitor"])[2])
    xv = sorted(s365.alineacion(g["awayCompetitor"])[2])
    return ",".join(map(str, xl)) + "|" + ",".join(map(str, xv)), len(xl) == 11 and len(xv) == 11


def analizar(gid, g, nombres, modo):
    """modo: 'confirmada' | 'probable' (XI de 365Scores sin confirmar) | 'ninguna'"""
    con_alineacion = modo in ("confirmada", "probable")
    h, a = g["homeCompetitor"], g["awayCompetitor"]
    local, visita = h["name"], a["name"]
    res_l = s365.resultados_equipo(h["id"])
    res_v = s365.resultados_equipo(a["id"])
    lh, la, nl, nv = M.goles_esperados(res_l, res_v, h["id"], a["id"])
    st_l, st_v = stats_recientes(h["id"], res_l), stats_recientes(a["id"], res_v)

    # Remates a puerta: medida más estable que los goles → se mezcla con los goles esperados
    def prom(lista, campo):
        v = [x[campo] for x in lista if campo in x]
        return sum(v) / len(v) if len(v) >= 3 else None
    sl, slc, sv, svc = prom(st_l, "s"), prom(st_l, "s_contra"), prom(st_v, "s"), prom(st_v, "s_contra")
    if None not in (sl, slc, sv, svc):
        lh_s = (sl + svc) / 2 * C.GOLES_POR_REMATE * M.HA ** 0.5
        la_s = (sv + slc) / 2 * C.GOLES_POR_REMATE / M.HA ** 0.5
        lh = (1 - C.PESO_REMATES) * lh + C.PESO_REMATES * lh_s
        la = (1 - C.PESO_REMATES) * la + C.PESO_REMATES * la_s
    # Altura (Liga 1 Perú): el visitante de llano rinde menos
    altura = criterios.en_altura(local) and not criterios.en_altura(visita)
    if altura:
        lh *= 1.08
        la *= 0.85
    lh_forma, la_forma = lh, la
    f_h = f_a = 1.0          # factores de la alineación (se aplican sobre la base final)

    notas = []
    conf_l, form_l, xi_l, _ = s365.alineacion(h)
    conf_v, form_v, xi_v, _ = s365.alineacion(a)
    if con_alineacion:
        resum_l = [s365.resumen_partido(x[0]) for x in res_l[:5]]
        resum_v = [s365.resumen_partido(x[0]) for x in res_v[:5]]
        fa_l, fr_l, inf_l = M.ajuste_alineacion(h["id"], xi_l, resum_l, nombres)
        fa_v, fr_v, inf_v = M.ajuste_alineacion(a["id"], xi_v, resum_v, nombres)
        f_h = fa_l * fr_v
        f_a = fa_v * fr_l
        for eq, inf in ((local, inf_l), (visita, inf_v)):
            if inf["faltan"]:
                notas.append(f"⚠️ {esc(eq)} sin: " + ", ".join(esc(x) for x in inf["faltan"][:4]))
            if inf["cambios"] >= 5:
                notas.append(f"🔄 {esc(eq)}: {inf['cambios']} cambios vs su último partido")

    esp = M.corners_tarjetas(st_l, st_v)
    mu_c, mu_k = esp["total"], esp["tarjetas"]
    lineas = s365.cuotas(gid)
    mercado = M.leer_cuotas(lineas, local, visita)
    horas_quietas = huella_365(gid, lineas) if lineas else 0
    viejas = horas_quietas >= C.CUOTAS_VIEJAS_HORAS
    casa_de = {k: "Bet365" for k in mercado}
    ext = cuotas_ext.obtener(gid, g, nombres_ingles(gid))
    valor_casas = []
    if ext:
        for ks, v in ext["cuotas"].items():
            k = cuotas_ext.a_clave(ks)
            precio, casa = v["cuota"], v["casa"]
            if k in mercado and not viejas and mercado[k][0] > precio:
                precio, casa = mercado[k][0], "Bet365"
            mercado[k] = (precio, v["justa_p"])
            casa_de[k] = casa
            ev_c = precio * v["justa_p"] - 1
            if v["ref"] == "Pinnacle" and ev_c >= C.VALOR_CASAS_MIN and v["justa_p"] >= 0.15:
                valor_casas.append((ev_c, k, precio, casa, 1 / v["justa_p"]))
        valor_casas.sort(reverse=True)
    # Base = lo que implica el mercado (75%) + forma/remates (25%); si no hay 1X2, solo forma
    lam_m = M.lambdas_mercado(mercado)
    if lam_m:
        pf = C.PESO_FORMA
        lh0 = lam_m[0] ** (1 - pf) * lh_forma ** pf
        la0 = lam_m[1] ** (1 - pf) * la_forma ** pf
    else:
        lh0, la0 = lh_forma, la_forma
    lh, la = lh0 * f_h, la0 * f_a
    base = (lh0, la0) if con_alineacion and (abs(lh / lh0 - 1) >= 0.02 or abs(la / la0 - 1) >= 0.02) else None
    cands, _ = M.candidatos(lh, la, esp, mercado, local, visita, base=base)
    impacto = (lh / lh0 - 1, la / la0 - 1) if con_alineacion else None
    crit_lin, señales = criterios.evaluar(g, gid, res_l, res_v, st_l, st_v, lineas, impacto)
    for c in cands:
        c["casa"] = casa_de.get(c["clave"], "")
        c["respaldo"] = criterios.respaldo(c["clave"], señales)
    seguros, arriesgados, apostables = M.seleccionar(cands)
    modelos = M.solo_modelo(cands, {c["clave"] for c in seguros + arriesgados + apostables})
    bloqueados = []
    if viejas and not ext:
        bloqueados = [c for c in apostables if c["casa"] == "Bet365"]
        apostables = [c for c in apostables if c["casa"] != "Bet365"]

    inicio = iso(g["startTime"])
    lin = [f"⚽ <b>{esc(local)} vs {esc(visita)}</b>",
           f"🏆 {esc(g.get('competitionDisplayName', ''))} · 🕐 {inicio:%d/%m %H:%M}"]
    if modo == "confirmada":
        lin.append(f"📋 Alineaciones confirmadas ({esc(form_l or '?')} / {esc(form_v or '?')})")
    elif modo == "probable":
        lin.append(f"📋 <b>Alineación probable</b> ({esc(form_l or '?')} / {esc(form_v or '?')}): "
                   "365Scores aún no la confirma. Si cambia, te mando actualización.")
    else:
        lin.append("📋 <b>Sin alineación confirmada</b> a minutos del inicio: picks solo con datos previos")
    lin += notas
    extra = []
    if mu_c:
        extra.append(f"Córners {mu_c:.1f}" + (f" ({esp['local']:.1f}-{esp['visita']:.1f})" if esp["local"] else ""))
    if mu_k:
        extra.append(f"Tarjetas {mu_k:.1f}")
    def forma(res, tid):
        letras, gf, gc = [], [], []
        for _, _, hid, aid, sh, sa in res:
            if sh is None or sa is None or sh < 0:
                continue
            a_favor, en_contra = (sh, sa) if hid == tid else (sa, sh)
            gf.append(a_favor); gc.append(en_contra)
            letras.append("G" if a_favor > en_contra else "E" if a_favor == en_contra else "P")
        if not gf:
            return "sin datos"
        return (f"{'-'.join(letras[:5])} · {sum(gf)/len(gf):.1f} goles a favor / "
                f"{sum(gc)/len(gc):.1f} en contra (últ. {len(gf)})")
    lin.append("\n🔎 <b>ANÁLISIS</b>")
    lin.append(f"📈 {esc(local)}: {forma(res_l, h['id'])}")
    lin.append(f"📈 {esc(visita)}: {forma(res_v, a['id'])}")
    lin += [esc(x) for x in crit_lin if not x.startswith("• Forma")]
    lin.append(criterios.lectura(señales, esc(local), esc(visita)))
    lin.append(f"📊 Goles esperados {lh:.2f} – {la:.2f}" + (" · " + " · ".join(extra) if extra else ""))
    if lam_m:
        lin.append(f"   <i>(mercado {lam_m[0]:.2f}–{lam_m[1]:.2f} · forma/remates {lh_forma:.2f}–{la_forma:.2f})</i>")
    if con_alineacion:
        if base:
            def cambio(x0, x1):
                return f"{x0:.2f}→{x1:.2f} ({(x1 / x0 - 1):+.0%})"
            lin.append(f"🧩 <b>Impacto de la alineación</b>: {esc(local)} {cambio(lh0, lh)} · "
                       f"{esc(visita)} {cambio(la0, la)}")
        else:
            lin.append("🧩 Alineaciones habituales: sin impacto relevante en el análisis")
    if min(nl, nv) < 5:
        lin.append("ℹ️ Pocos partidos recientes de algún equipo: el modelo es menos confiable.")
    if ext:
        edad = f" · actualizadas hace {ext['edad_min']} min" if ext.get("edad_min") is not None else ""
        lin.append(f"💱 Cuotas frescas de {ext['n_casas']} casas · referencia {ext['ref']}{edad}")
    elif viejas:
        lin.append(f"⚠️ <b>Cuotas de Bet365 sin moverse hace {horas_quietas:.0f} h</b>: probablemente "
                   "desactualizadas. Verifica en tu app y guíate por la “justa”.")
    elif mercado:
        lin.append("💱 Cuotas: solo Bet365 (vía 365Scores) · verifica en tu app antes de apostar")
    if not mercado:
        lin.append("ℹ️ Sin cuotas para este partido: probabilidades solo del modelo.")

    def fila(c, con_stake=False):
        t = f"• {esc(c['texto'])} — <b>{c['p']:.0%}</b>"
        if c.get("p_mercado") is not None:
            extra_al = f" · alineación {c['delta']:+.0%}" if abs(c.get("delta") or 0) >= 0.015 else ""
            t += f" <i>(mercado {c['p_mercado']:.0%} · modelo {c['p_modelo']:.0%}{extra_al})</i>"
        if c["cuota"]:
            casa = f" ({esc(c['casa'])})" if c.get("casa") else ""
            t += f" · cuota {c['cuota']:.2f}{casa} · EV {c['ev']:+.0%}"
        t += f" · justa {c['justa']:.2f}"
        r = c.get("respaldo")
        if r and (r[0] or r[1]):
            t += f" · respaldo ✔{r[0]} ✖{r[1]}"
        if con_stake:
            t += f" · stake S/ {c['stake']:.2f}"
        return t

    lin.append("\n🟢 <b>SEGUROS</b> (lo más probable)")
    lin += [fila(c) for c in seguros] or ["• Ninguno claro"]
    lin.append("\n🔴 <b>ARRIESGADOS</b> (difíciles pero posibles)")
    lin += [fila(c) for c in arriesgados] or ["• Ninguno"]
    if modelos:
        lin.append("\n📐 <b>SOLO MODELO</b> (sin cuota de mercado: menos fiables, compara la “justa” en tu casa)")
        lin += [fila(c) for c in modelos]
    lin.append(f"\n✅ <b>APOSTABLES</b> (EV ≥ {C.EV_MIN:.0%}, cuota ≥ {C.CUOTA_MIN:.2f})")
    if apostables:
        lin += [fila(c, True) for c in apostables]
    elif bloqueados:
        lin.append("• Habría valor con las cuotas de 365Scores, pero están desactualizadas: "
                   "revísalo tú en la app (" + ", ".join(f"{esc(c['texto'])} ≥ {c['justa']:.2f}"
                                                      for c in bloqueados) + ")")
    else:
        lin.append("• Ninguno: el mercado no deja valor aquí. No apostar también es una decisión.")
    contra = [c for c in cands if M.contradicho(c) and not c.get("discrepa") and c["cuota"]
              and c["ev"] is not None and c["ev"] >= C.EV_MIN and c["cuota"] >= C.CUOTA_MIN and c["p"] >= 0.20]
    if contra:
        lin.append("⚠️ <i>Con valor pero descartados porque la mayoría de criterios van en contra: "
                   + ", ".join(esc(c["texto"]) for c in sorted(contra, key=lambda c: -c["ev"])[:3]) + "</i>")
    dudosos = [c for c in cands if c.get("discrepa") and c["cuota"] and c["ev"] is not None
               and c["ev"] >= C.EV_MIN and c["cuota"] >= C.CUOTA_MIN]
    if dudosos:
        lin.append("⚠️ <i>Descartados por discrepar mucho del mercado (el modelo puede estar equivocado): "
                   + ", ".join(esc(c["texto"]) for c in sorted(dudosos, key=lambda c: -c["ev"])[:3]) + "</i>")
    if valor_casas:
        lin.append("\n💎 <b>VALOR ENTRE CASAS</b> (mejor cuota vs. Pinnacle sin margen)")
        for ev_c, k, precio, casa, justa in valor_casas[:3]:
            lin.append(f"• {esc(M.texto_mercado(k, local, visita))} — {esc(casa)} {precio:.2f} "
                       f"vs justa {justa:.2f} · EV {ev_c:+.1%}")
    lin.append("\n<i>“justa” = cuota mínima que deberías aceptar en tu casa de apuestas. "
               "Seguros/arriesgados sin valor son informativos, no apuestas.</i>")
    lin.append("🧪 Paper trading · cuotas: " + ("The Odds API + Bet365 (365Scores)" if ext else "Bet365 vía 365Scores"))

    filas = []
    fecha = ahora().strftime("%Y-%m-%d %H:%M")
    for tipo, lista in (("seguro", seguros), ("arriesgado", arriesgados), ("modelo", modelos),
                        ("apostable", apostables)):
        for c in lista:
            filas.append({
                "fecha": fecha, "game_id": gid, "partido": f"{local} vs {visita}",
                "liga": g.get("competitionDisplayName", ""), "tipo": tipo, "mercado": c["texto"],
                "clave": clave_str(c["clave"]), "prob": f"{c['p']:.4f}",
                "cuota": f"{c['cuota']:.2f}" if c["cuota"] else "", "casa": c.get("casa", ""),
                "cuota_justa": f"{c['justa']:.2f}",
                "ev": f"{c['ev']:.4f}" if c["ev"] is not None else "",
                "stake": f"{c.get('stake', 0):.2f}" if tipo == "apostable" else "",
                "alineacion": {"confirmada": "si", "probable": "probable"}.get(modo, "no"), "resultado": "", "ganancia": "",
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
    sincronizar_seleccion(estado)
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
                mins = (iso(s["inicio"]) - t).total_seconds() / 60
                if mins > C.VENTANA_MIN:
                    continue
                urgente = mins <= C.ENVIAR_SIN_ALINEACION_MIN
            ok_l = s365.alineacion(g["homeCompetitor"])[0]
            ok_v = s365.alineacion(g["awayCompetitor"])[0]
            confirmada = ok_l and ok_v
            if confirmada or urgente:
                if mins < -20:
                    s["estado"] = "cancelado"
                    enviar(f"⌛ {esc(s['partido'])}: ya empezó y no alcancé a enviar picks.")
                    continue
                firma, completa = firma_xi(g)
                modo = "confirmada" if confirmada else ("probable" if completa else "ninguna")
                texto, filas = analizar(gid, g, nombres, modo)
                enviar(texto)
                log += filas
                s["estado"] = "enviado"
                s["xi"], s["conf"] = firma, confirmada
        elif s["estado"] == "enviado" and mins < -115:
            if time.time() - s.get("ultimo", 0) >= 10 * 60:
                s["ultimo"] = time.time()
                liquidar(gid, s, log)
        elif s["estado"] == "enviado" and not s.get("conf", True) and mins > -3:
            # Se envió con alineación probable: vigilar si se confirma y si cambió
            if time.time() - s.get("ultimo", 0) < 5 * 60:
                continue
            s["ultimo"] = time.time()
            g, nombres = s365.detalle(gid)
            if not g:
                continue
            if not (s365.alineacion(g["homeCompetitor"])[0] and s365.alineacion(g["awayCompetitor"])[0]):
                continue
            s["conf"] = True
            firma, _ = firma_xi(g)
            if firma == s.get("xi"):
                enviar(f"✅ <b>{esc(s['partido'])}</b>: alineaciones confirmadas, "
                       "iguales a la probable. Los picks se mantienen.")
                continue
            texto, filas = analizar(gid, g, nombres, "confirmada")
            log[:] = [f for f in log if not (f["game_id"] == gid and not f["resultado"])]
            log += filas
            s["xi"] = firma
            enviar("🔄 <b>ACTUALIZACIÓN: la alineación confirmada cambió</b>. "
                   "Estos picks reemplazan a los anteriores.\n\n" + texto)
        elif s["estado"] in ("liquidado", "cancelado") and mins < -2 * 24 * 60:
            del estado["seguidos"][gid]
    escribir_log(log)
    guardar_estado(estado)
    guardar_cola()
    s365.guardar_cache()


def probar(gid):
    g, nombres = s365.detalle(gid)
    if not g:
        print("No se pudo leer el partido", gid)
        return
    ok = s365.alineacion(g["homeCompetitor"])[0] and s365.alineacion(g["awayCompetitor"])[0]
    modo = "confirmada" if ok else ("probable" if firma_xi(g)[1] else "ninguna")
    texto, _ = analizar(gid, g, nombres, modo)
    print(texto)
    _enviar_ya("🧪 <b>PRUEBA</b> (no se registra)\n\n" + texto)
    s365.guardar_cache()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--probar":
        probar(sys.argv[2])
    elif "--enviar" in sys.argv:
        enviar_cola()
    elif "--vigilar" in sys.argv:
        # Modo vigilante (GitHub Actions): una pasada cada 2 min durante N minutos.
        import subprocess
        minutos = float(sys.argv[sys.argv.index("--vigilar") + 1])
        COLA = True
        fin = time.time() + minutos * 60
        n = 0
        while time.time() < fin:
            n += 1
            subprocess.run("git pull --rebase -q || git rebase --abort", shell=True)
            try:
                pasada()
            except Exception as e:  # noqa: BLE001
                print("error en pasada:", e)
            subprocess.run("bash guardar.sh", shell=True)
            print(f"[vigilante] pasada {n} lista ({ahora():%H:%M})", flush=True)
            time.sleep(max(0, min(C.PASADA_SEG, fin - time.time())))
    elif "--loop" in sys.argv:
        while True:
            try:
                pasada()
            except Exception as e:  # noqa: BLE001
                print("error en pasada:", e)
            time.sleep(120)
    else:
        pasada()
