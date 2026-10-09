"""Criterios del análisis: forma, localía, tabla, H2H, remates, descanso, altura, movimiento
de cuotas y alineación. Cada criterio da una señal (local / visita / parejo, y más / menos goles)
y cada pick muestra cuántos criterios lo respaldan."""
import time
import unicodedata
from datetime import datetime

import s365

# Equipos de Liga 1 Perú que juegan en altura (>2.000 m)
ALTURA = ["cienciano", "cusco", "garcilaso", "adt", "tarma", "sport huancayo", "binacional",
          "los chankas", "utc", "cajamarca", "ayacucho", "real garcilaso", "alfonso ugarte"]


def _n(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def en_altura(nombre):
    n = _n(nombre)
    return any(a in n for a in ALTURA)


# ---------------------------------------------------------------- datos
def h2h(gid, hid, aid, n=6):
    c = s365._c()
    k = f"h2h:{gid}"
    if k in c:
        return c[k]["d"]
    j = s365.get("games/h2h", gameId=gid)
    out = []
    for g in ((j or {}).get("game") or {}).get("h2hGames") or []:
        if str(g.get("id")) == str(gid) or not s365.terminado(g):
            continue
        h, a = g.get("homeCompetitor") or {}, g.get("awayCompetitor") or {}
        if {h.get("id"), a.get("id")} != {hid, aid}:
            continue
        out.append([h["id"], a["id"], h.get("score"), a.get("score"), g.get("startTime", "")])
    out.sort(key=lambda x: x[4], reverse=True)
    out = out[:n]
    c[k] = {"t": time.time(), "d": out}
    return out


def tabla(comp_id):
    c = s365._c()
    k = f"tabla:{comp_id}"
    if k in c and time.time() - c[k]["t"] < 6 * 3600:
        return c[k]["d"]
    j = s365.get("standings", competitions=comp_id)
    filas = {}
    for st in (j or {}).get("standings") or []:
        rows = st.get("rows") or []
        for r in rows:
            comp = r.get("competitor") or {}
            filas[str(comp.get("id"))] = {"pos": r.get("position"), "pts": r.get("points"),
                                          "pj": r.get("gamePlayed"), "n": len(rows)}
    c[k] = {"t": time.time(), "d": filas}
    return filas


# ---------------------------------------------------------------- cálculos
def _desde(res, tid):
    """[(gf, gc, local?, fecha)] desde el punto de vista del equipo."""
    out = []
    for _, fecha, hid, aid, sh, sa in res:
        if sh is None or sa is None or sh < 0:
            continue
        out.append((sh, sa, True, fecha) if hid == tid else (sa, sh, False, fecha))
    return out


def _ppg(lista):
    if not lista:
        return None
    return sum(3 if gf > gc else 1 if gf == gc else 0 for gf, gc, *_ in lista) / len(lista)


def _dias(fecha_iso, hasta_iso):
    try:
        return (datetime.fromisoformat(hasta_iso) - datetime.fromisoformat(fecha_iso)).days
    except (ValueError, TypeError):
        return None


def evaluar(g, gid, res_l, res_v, st_l, st_v, lineas365, impacto):
    """Devuelve (lineas_texto, señales) donde señales = {"res": [(nombre, +1 local/-1 visita/0)],
    "gol": [(nombre, +1 más goles/-1 menos/0)]}"""
    h, a = g["homeCompetitor"], g["awayCompetitor"]
    hid, aid, local, visita = h["id"], a["id"], h["name"], a["name"]
    dl, dv = _desde(res_l, hid), _desde(res_v, aid)
    lin, res, gol = [], [], []

    # 1) Forma (últimos 5)
    pl, pv = _ppg(dl[:5]), _ppg(dv[:5])
    if pl is not None and pv is not None:
        s = 1 if pl - pv >= 0.5 else -1 if pv - pl >= 0.5 else 0
        res.append(("Forma últimos 5", s))
        lin.append(f"• Forma (últ. 5): {local} {pl:.1f} pts/partido · {visita} {pv:.1f}")

    # 2) Localía: local en casa vs visitante fuera
    casa = [x for x in dl if x[2]][:5]
    fuera = [x for x in dv if not x[2]][:5]
    pc, pf = _ppg(casa), _ppg(fuera)
    if pc is not None and pf is not None and len(casa) >= 3 and len(fuera) >= 3:
        s = 1 if pc - pf >= 0.6 else -1 if pf - pc >= 0.6 else 0
        res.append(("Local en casa vs visita fuera", s))
        lin.append(f"• {local} en casa: {pc:.1f} pts/partido · {visita} de visita: {pf:.1f}")

    # 3) Tabla
    t = tabla(g.get("competitionId")) if g.get("competitionId") else {}
    tl, tv = t.get(str(hid)), t.get(str(aid))
    if tl and tv and tl.get("pos") and tv.get("pos"):
        dif = tv["pos"] - tl["pos"]
        umbral = max(3, (tl.get("n") or 20) // 5)
        s = 1 if dif >= umbral else -1 if dif <= -umbral else 0
        res.append(("Posición en la tabla", s))
        lin.append(f"• Tabla: {local} {tl['pos']}° ({tl['pts']:.0f} pts) · {visita} {tv['pos']}° ({tv['pts']:.0f} pts)")

    # 4) H2H
    hh = h2h(gid, hid, aid)
    if len(hh) >= 3:
        gl = sum(1 for x in hh if (x[2] > x[3] and x[0] == hid) or (x[3] > x[2] and x[1] == hid))
        gv = sum(1 for x in hh if (x[2] > x[3] and x[0] == aid) or (x[3] > x[2] and x[1] == aid))
        em = len(hh) - gl - gv
        tot = sum(x[2] + x[3] for x in hh) / len(hh)
        s = 1 if gl - gv >= 2 else -1 if gv - gl >= 2 else 0
        res.append(("Historial directo", s))
        gol.append(("Goles en el historial directo", 1 if tot >= 2.8 else -1 if tot <= 2.0 else 0))
        lin.append(f"• H2H (últ. {len(hh)}): {local} {gl} · empates {em} · {visita} {gv} · {tot:.1f} goles/partido")

    # 5) Goles y ambos marcan en partidos recientes
    rec = dl[:8] + dv[:8]
    if len(rec) >= 8:
        tot = sum(gf + gc for gf, gc, *_ in rec) / len(rec)
        btts = sum(1 for gf, gc, *_ in rec if gf > 0 and gc > 0) / len(rec)
        gol.append(("Goles en sus partidos recientes", 1 if tot >= 2.8 else -1 if tot <= 2.2 else 0))
        gol.append(("Ambos marcan en partidos recientes", 1 if btts >= 0.6 else -1 if btts <= 0.35 else 0))
        lin.append(f"• Sus últimos partidos: {tot:.1f} goles/partido · ambos marcan {btts:.0%}")

    # 6) Remates a puerta (calidad de ataque/defensa)
    def prom(lista, campo):
        v = [x[campo] for x in lista if campo in x]
        return (sum(v) / len(v), len(v)) if v else (None, 0)
    sl, nl = prom(st_l, "s")
    sv, nv = prom(st_v, "s")
    slc, _ = prom(st_l, "s_contra")
    svc, _ = prom(st_v, "s_contra")
    if None not in (sl, sv, slc, svc) and min(nl, nv) >= 3:
        neto_l, neto_v = sl - slc, sv - svc
        s = 1 if neto_l - neto_v >= 1.5 else -1 if neto_v - neto_l >= 1.5 else 0
        res.append(("Remates a puerta (a favor − en contra)", s))
        total = (sl + svc) / 2 + (sv + slc) / 2
        gol.append(("Remates a puerta esperados", 1 if total >= 9.5 else -1 if total <= 7 else 0))
        lin.append(f"• Remates a puerta: {local} {sl:.1f} a favor/{slc:.1f} en contra · "
                   f"{visita} {sv:.1f}/{svc:.1f}")

    # 7) Descanso
    inicio = g.get("startTime")
    dsl = _dias(dl[0][3], inicio) if dl else None
    dsv = _dias(dv[0][3], inicio) if dv else None
    if dsl is not None and dsv is not None:
        s = 0
        if min(dsl, dsv) <= 3 and abs(dsl - dsv) >= 2:
            s = 1 if dsl > dsv else -1
        res.append(("Días de descanso", s))
        lin.append(f"• Descanso: {local} {dsl} días · {visita} {dsv} días")

    # 8) Altura (Liga 1 Perú)
    if en_altura(local) and not en_altura(visita):
        res.append(("Local en altura", 1))
        lin.append(f"• ⛰️ {local} juega en altura y {visita} no: ventaja local")

    # 9) Movimiento de cuotas (apertura → ahora)
    for ln in lineas365 or []:
        lt = ln.get("lineTypeId")
        if lt == 1:
            ops = {o.get("name"): o for o in ln.get("options") or []}
            def mov(o):
                r = (o.get("rate") or {}).get("decimal")
                r0 = (o.get("originalRate") or {}).get("decimal")
                return (r0, r, r / r0 - 1) if r and r0 else None
            m1, m2 = mov(ops.get("1", {})), mov(ops.get("2", {}))
            if m1 and m2:
                s = 1 if m1[2] <= -0.05 and m1[2] < m2[2] else -1 if m2[2] <= -0.05 and m2[2] < m1[2] else 0
                res.append(("Movimiento de la cuota 1X2", s))
                if s:
                    quien, m = (local, m1) if s == 1 else (visita, m2)
                    lin.append(f"• 📉 La cuota de {quien} bajó {m[0]:.2f} → {m[1]:.2f}: el dinero va hacia {quien}")
        if lt == 3 and str(ln.get("internalOptionValue")) in ("2.5", "2.50"):
            ops = {o.get("name", "").lower(): o for o in ln.get("options") or []}
            o_ = next((v for k, v in ops.items() if k.startswith("más") or k.startswith("mas")), None)
            u_ = next((v for k, v in ops.items() if k.startswith("menos")), None)
            def ch(o):
                r = ((o or {}).get("rate") or {}).get("decimal")
                r0 = ((o or {}).get("originalRate") or {}).get("decimal")
                return r / r0 - 1 if r and r0 else 0
            co, cu = ch(o_), ch(u_)
            s = 1 if co <= -0.05 else -1 if cu <= -0.05 else 0
            gol.append(("Movimiento de la cuota de goles", s))
            if s:
                lin.append(f"• 📉 El mercado empuja hacia {'MÁS' if s == 1 else 'MENOS'} de 2.5 goles")

    # 10) Alineación
    if impacto:
        rl, rv = impacto
        s = 1 if rl - rv >= 0.06 else -1 if rv - rl >= 0.06 else 0
        res.append(("Alineaciones confirmadas", s))
        tot = (rl + rv) / 2
        gol.append(("Alineaciones (efecto en goles)", 1 if tot >= 0.05 else -1 if tot <= -0.05 else 0))

    return lin, {"res": res, "gol": gol}


def lectura(señales, local, visita):
    def conteo(lista):
        a = sum(1 for _, s in lista if s > 0)
        b = sum(1 for _, s in lista if s < 0)
        return a, b, len(lista)
    a, b, n = conteo(señales["res"])
    if n:
        if a >= b + 2:
            r = f"señales a favor de <b>{local}</b> ({a} de {n} criterios)"
        elif b >= a + 2:
            r = f"señales a favor de <b>{visita}</b> ({b} de {n} criterios)"
        else:
            r = f"<b>partido parejo</b> ({a} criterios para {local}, {b} para {visita})"
    else:
        r = "sin datos suficientes"
    c, d, m = conteo(señales["gol"])
    if m:
        if c >= d + 2:
            gtx = f"tendencia a <b>muchos goles</b> ({c} de {m})"
        elif d >= c + 2:
            gtx = f"tendencia a <b>pocos goles</b> ({d} de {m})"
        else:
            gtx = "goles sin tendencia clara"
    else:
        gtx = "sin datos de goles"
    return f"🧭 <b>Lectura</b>: {r} · {gtx}"


def direccion(clave):
    """('res', +1/-1) o ('gol', +1/-1) según hacia dónde apunta el pick; None si no aplica."""
    t, l, s = clave
    if t == "1X2":
        return {"1": ("res", 1), "2": ("res", -1)}.get(s)
    if t == "DC":
        return {"1X": ("res", 1), "X2": ("res", -1)}.get(s)
    if t in ("AH", "DNB"):
        return ("res", 1 if s == "local" else -1)
    if t in ("OU", "OU1T"):
        return ("gol", 1 if s == "over" else -1)
    if t == "BTTS":
        return ("gol", 1 if s == "si" else -1)
    if t == "CS":
        eq, sn = s.split("_")
        return ("gol", -1 if sn == "si" else 1)
    return None


def respaldo(clave, señales):
    """(a favor, en contra) de los criterios para este pick."""
    d = direccion(clave)
    if not d:
        return None
    grupo, signo = d
    lista = señales[grupo]
    return (sum(1 for _, s in lista if s == signo), sum(1 for _, s in lista if s == -signo))
