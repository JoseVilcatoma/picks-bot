"""Modelo estadístico: goles (Poisson Dixon-Coles), córners y tarjetas (binomial negativa),
ajuste por alineación confirmada, mezcla con el mercado y selección de picks."""
import math

import config as C

HA = 1.10          # ventaja de local
RHO = -0.06        # corrección Dixon-Coles para marcadores bajos
FRAC_1T = 0.45     # proporción de goles que caen en el 1er tiempo


# =================================================================
# Distribuciones
# =================================================================
def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def nb_pmf(k, mu, r):
    p = r / (r + mu)
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
                    + r * math.log(p) + k * math.log(1 - p))


def nb_over(mu, linea, r):
    return 1 - sum(nb_pmf(k, mu, r) for k in range(int(math.floor(linea)) + 1))


def matriz(lh, la, n=11, rho=RHO):
    m = [[pois(i, lh) * pois(j, la) for j in range(n)] for i in range(n)]
    m[0][0] *= 1 - lh * la * rho
    m[0][1] *= 1 + lh * rho
    m[1][0] *= 1 + la * rho
    m[1][1] *= 1 - rho
    s = sum(map(sum, m))
    return [[x / s for x in fila] for fila in m]


def suma(m, cond):
    return sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if cond(i, j))


# =================================================================
# Fuerza de los equipos (últimos 10 partidos, más peso a los recientes)
# =================================================================
def promedio_pond(valores, prior, k=3.0, decay=0.88):
    w = [decay ** i for i in range(len(valores))]
    return (sum(a * b for a, b in zip(w, valores)) + k * prior) / (sum(w) + k)


def goles_esperados(res_local, res_visita, id_local, id_visita):
    def gf_ga(res, tid):
        gf, ga = [], []
        for _, _, h, a, sh, sa in res:
            if sh is None or sa is None or sh < 0:
                continue
            if h == tid:
                gf.append(sh); ga.append(sa)
            else:
                gf.append(sa); ga.append(sh)
        return gf, ga

    gfl, gal = gf_ga(res_local, id_local)
    gfv, gav = gf_ga(res_visita, id_visita)
    todos = gfl + gal + gfv + gav
    L = sum(todos) / len(todos) if todos else 1.3
    L = min(max(L, 0.9), 1.7)
    ataque_l = promedio_pond(gfl, L) / L
    defensa_l = promedio_pond(gal, L) / L
    ataque_v = promedio_pond(gfv, L) / L
    defensa_v = promedio_pond(gav, L) / L
    lh = L * ataque_l * defensa_v * HA
    la = L * ataque_v * defensa_l / HA
    return lh, la, len(gfl), len(gfv)


def lambdas_mercado(mercado):
    """Goles esperados (local, visita) que implican las cuotas sin margen: 1X2 y, si hay, más/menos 2.5.
    Devuelve None si no hay 1X2."""
    p1 = (mercado.get(("1X2", None, "1")) or (None, None))[1]
    p2 = (mercado.get(("1X2", None, "2")) or (None, None))[1]
    if p1 is None or p2 is None:
        return None
    po = (mercado.get(("OU", 2.5, "over")) or (None, None))[1]

    def error(lh, la):
        m = matriz(lh, la, n=9)
        e = (suma(m, lambda i, j: i > j) - p1) ** 2 + (suma(m, lambda i, j: i < j) - p2) ** 2
        if po is not None:
            e += (suma(m, lambda i, j: i + j > 2.5) - po) ** 2
        else:  # sin línea de goles: anclar el total a un promedio típico
            e += 0.02 * ((lh + la) - 2.6) ** 2
        return e

    mejor = min(((error(x / 10, y / 10), x / 10, y / 10) for x in range(2, 41, 2) for y in range(2, 41, 2)))
    _, bh, ba = mejor
    for paso in (0.05, 0.02, 0.01):
        rango = [k * paso for k in range(-4, 5)]
        mejor = min(((error(max(0.05, bh + dx), max(0.05, ba + dy)), max(0.05, bh + dx), max(0.05, ba + dy))
                     for dx in rango for dy in rango))
        _, bh, ba = mejor
    return bh, ba


FRAC_C1T = 0.46   # si no hay datos del 1T, ~46% de los córners caen en el 1er tiempo


def corners_tarjetas(st_local, st_visita):
    """Cada lista: [{'c','c_contra','c1','c1_contra','k'}] de partidos recientes.
    Devuelve córners esperados (total, por equipo, 1er tiempo) y tarjetas."""
    def prom(lista, campo):
        v = [x[campo] for x in lista if campo in x]
        return sum(v) / len(v) if v else None

    out = {"total": None, "local": None, "visita": None, "primer_tiempo": None, "tarjetas": None}
    cf_l, cc_l = prom(st_local, "c"), prom(st_local, "c_contra")
    cf_v, cc_v = prom(st_visita, "c"), prom(st_visita, "c_contra")
    if None not in (cf_l, cc_l, cf_v, cc_v):
        out["local"] = (cf_l + cc_v) / 2
        out["visita"] = (cf_v + cc_l) / 2
        out["total"] = out["local"] + out["visita"]
        c1 = [prom(st_local, "c1"), prom(st_local, "c1_contra"),
              prom(st_visita, "c1"), prom(st_visita, "c1_contra")]
        if None not in c1:
            out["primer_tiempo"] = (c1[0] + c1[3]) / 2 + (c1[2] + c1[1]) / 2
        else:
            out["primer_tiempo"] = out["total"] * FRAC_C1T
    k_l, k_v = prom(st_local, "k"), prom(st_visita, "k")
    if None not in (k_l, k_v):
        out["tarjetas"] = k_l + k_v
    # Tarjetas por equipo (para "ambos reciben tarjeta"): lo que recibe + lo que provoca el rival,
    # encogido hacia el nivel general de ambos equipos porque son pocos partidos.
    kc_l, kc_v = prom(st_local, "k_contra"), prom(st_visita, "k_contra")
    if None not in (k_l, k_v, kc_l, kc_v):
        nivel = (k_l + k_v + kc_l + kc_v) / 4
        n_l = len([x for x in st_local if "k" in x])
        n_v = len([x for x in st_visita if "k" in x])
        enc = lambda v, n: (v * n + nivel * 4) / (n + 4)
        out["k_local"] = enc((k_l + kc_v) / 2, n_l)
        out["k_visita"] = enc((k_v + kc_l) / 2, n_v)
    return out


# =================================================================
# Ajuste por alineación confirmada
# =================================================================
def ajuste_alineacion(team_id, xi_hoy, resumenes, nombres_hoy):
    """Compara el XI de hoy con los últimos partidos del equipo.
    Devuelve (factor_ataque_propio, factor_ataque_rival, notas[])"""
    tid = str(team_id)
    partidos = [r["eq"][tid] for r in resumenes if r and tid in r.get("eq", {})]
    if not partidos or not xi_hoy:
        return 1.0, 1.0, {"faltan": [], "cambios": 0}
    n = len(partidos)
    titular, aporte, pos, nombres = {}, {}, {}, {}
    goles_eq = 0.0
    for r in resumenes:
        if r:
            nombres.update(r.get("n", {}))
    for p in partidos:
        for pid in p["xi"]:
            titular[str(pid)] = titular.get(str(pid), 0) + 1
        for pid, v in p["g"].items():
            aporte[pid] = aporte.get(pid, 0) + v
            goles_eq += v
        for pid, v in p["a"].items():
            aporte[pid] = aporte.get(pid, 0) + 0.5 * v
            goles_eq += 0.5 * v
        pos.update(p["pos"])
    hoy = {str(x) for x in xi_hoy}
    habituales = [pid for pid, c in titular.items() if c >= max(2, math.ceil(n * 0.6))]
    faltan = [pid for pid in habituales if pid not in hoy]

    notas, f_atk, f_riv = [], 1.0, 1.0
    perdida = 0.0
    for pid in faltan:
        share = min(aporte.get(pid, 0) / goles_eq, 0.6) if goles_eq else 0
        perdida += share
        nombre = nombres.get(pid) or (nombres_hoy.get(int(pid), "") if pid.isdigit() else "")
        etiqueta = f"{nombre or 'Titular'}"
        if share >= 0.10:
            etiqueta += f" ({share:.0%} de la producción ofensiva)"
        elif pos.get(pid) in ("Portero", "Defensa"):
            etiqueta += f" ({pos.get(pid).lower()})"
        notas.append(etiqueta)
        if pos.get(pid) == "Portero":
            f_riv += 0.06
        elif pos.get(pid) == "Defensa":
            f_riv += 0.03
    f_atk -= min(0.25, 0.6 * perdida)
    f_riv = min(f_riv, 1.15)

    ultimo = {str(x) for x in partidos[0]["xi"]}
    cambios = len(hoy - ultimo) if ultimo else 0
    if cambios >= 5:
        f_atk *= 0.93
        f_riv *= 1.04
    return f_atk, f_riv, {"faltan": notas, "cambios": cambios}


# =================================================================
# Cuotas de Bet365 -> {clave: (cuota, prob_justa_mercado)}
# =================================================================
def _sel(nombre):
    n = (nombre or "").lower()
    if n.startswith("más") or n.startswith("mas") or n.startswith("over"):
        return "over"
    if n.startswith("menos") or n.startswith("under"):
        return "under"
    if n in ("sí", "si", "yes"):
        return "si"
    if n == "no":
        return "no"
    if n.startswith("local") or n == "1":
        return "1" if n == "1" else "local"
    if n.startswith("visit") or n == "2":
        return "2" if n == "2" else "visita"
    return nombre


def _tipo_corner(nombre_lt, local, visita):
    n = nombre_lt
    if not any(x in n for x in ("corner", "córner", "esquina")):
        return None
    if any(x in n for x in ("hándicap", "handicap", "primero", "último", "ultimo", "carrera", "race")):
        return None
    if any(x in n for x in ("primer", "1er", "1ª", "mitad", "first half", "1st")):
        if any(x in n for x in ("local", "visit", local.lower(), visita.lower())):
            return None
        return "CORN1T"
    if "local" in n or (local and local.lower() in n):
        return "CORN_H"
    if "visit" in n or (visita and visita.lower() in n):
        return "CORN_A"
    return "CORN"


def leer_cuotas(lineas, local="", visita=""):
    out = {}
    for ln in lineas:
        lt = ln.get("lineTypeId")
        nombre_lt = ((ln.get("lineType") or {}).get("name") or "").lower()
        try:
            val = float(ln.get("internalOptionValue")) if ln.get("internalOptionValue") not in (None, "") else None
        except ValueError:
            val = None
        ops = []
        for o in ln.get("options") or []:
            dec = (o.get("rate") or {}).get("decimal")
            if dec and dec > 1:
                ops.append((_sel(o.get("name")), o.get("name"), float(dec)))
        if len(ops) < 2:
            continue
        imp = sum(1 / d for _, _, d in ops)
        for sel, nombre, dec in ops:
            clave = None
            if lt == 1:
                clave = ("1X2", None, nombre if nombre in ("1", "X", "2") else sel)
            elif lt == 14:
                clave = ("DC", None, nombre)
            elif lt == 3 and val is not None and abs(val % 1 - 0.5) < 1e-6:
                clave = ("OU", val, sel)
            elif lt == 12:
                clave = ("BTTS", None, sel)
            elif lt == 11 and val is not None and abs(val * 2 % 2 - 1) < 1e-6:
                clave = ("AH", val, sel)          # solo líneas .5 (sin devolución)
            elif lt == 15:
                clave = ("DNB", None, sel)
            elif lt == 9 and val is not None and abs(val % 1 - 0.5) < 1e-6:   # solo .5 (sin devolución)
                clave = ("OU1T", val, sel)
            elif val is not None and sel in ("over", "under") and (
                    lt == 137 or _tipo_corner(nombre_lt, local, visita)):
                clave = (_tipo_corner(nombre_lt, local, visita) or "CORN", val, sel)
            elif "tarjeta" in nombre_lt and val is not None:
                clave = ("CARDS", val, sel)
            elif lt == 144:
                clave = ("CS", None, "local_" + sel)
            elif lt == 145:
                clave = ("CS", None, "visita_" + sel)
            if clave:
                total = 2.0 if lt == 14 else 1.0   # doble oportunidad: las 3 opciones suman 200%
                out[clave] = (dec, min(0.99, (1 / dec) / imp * total))
    return out


# =================================================================
# Probabilidades del modelo para cada mercado
# =================================================================
FAMILIA = {"1X2": "resultado", "DC": "resultado", "AH": "resultado", "DNB": "resultado",
           "OU": "goles", "BTTS": "goles", "OU1T": "goles", "CS": "goles", "EXACT": "goles",
           "CORN": "corners", "CORN_H": "corners", "CORN_A": "corners", "CORN1T": "corners",
           "CARDS": "tarjetas", "CARDS_BOTH": "tarjetas"}

# Mercados que SOLO se muestran si hay cuota real y valor (EV >= mínimo)
SOLO_CON_VALOR = {"CORN_H", "CORN_A", "CORN1T"}


def texto_mercado(clave, local, visita):
    t, l, s = clave
    if t == "1X2":
        return {"1": f"Gana {local}", "X": "Empate", "2": f"Gana {visita}"}[s]
    if t == "DC":
        return {"1X": f"{local} o empate", "X2": f"{visita} o empate", "12": "No hay empate"}[s]
    if t == "OU":
        return f"{'Más' if s == 'over' else 'Menos'} de {l:g} goles"
    if t == "OU1T":
        return f"{'Más' if s == 'over' else 'Menos'} de {l:g} goles en el 1T"
    if t == "BTTS":
        return "Ambos marcan: Sí" if s == "si" else "Ambos marcan: No"
    if t == "AH":
        h = l if s == "local" else -l
        return f"{local if s == 'local' else visita} {h:+g} (hándicap)"
    if t == "DNB":
        return f"{local if s == 'local' else visita} (empate no vale)"
    if t == "CS":
        eq, sn = s.split("_")
        quien = local if eq == "local" else visita
        return f"{quien} deja el arco en cero" if sn == "si" else f"{quien} recibe gol"
    if t == "EXACT":
        return f"Marcador exacto {s}"
    if t == "CORN":
        return f"{'Más' if s == 'over' else 'Menos'} de {l:g} córners"
    if t == "CARDS":
        return f"{'Más' if s == 'over' else 'Menos'} de {l:g} tarjetas"
    if t == "CARDS_BOTH":
        return "Ambos equipos reciben tarjeta" if s == "si" else "Algún equipo sin tarjeta"
    if t in ("CORN_H", "CORN_A"):
        quien = local if t == "CORN_H" else visita
        return f"{quien}: {'más' if s == 'over' else 'menos'} de {l:g} córners"
    if t == "CORN1T":
        return f"{'Más' if s == 'over' else 'Menos'} de {l:g} córners en el 1T"
    return str(clave)


def prob_modelo(clave, m, m1t, esp):
    t, l, s = clave
    mu_c, mu_k = esp["total"], esp["tarjetas"]
    mu_eq = {"CORN_H": esp["local"], "CORN_A": esp["visita"], "CORN1T": esp["primer_tiempo"]}
    if t in mu_eq:
        mu = mu_eq[t]
        if not mu:
            return None
        p = nb_over(mu, l, 10)
        return p if s == "over" else 1 - p
    if t == "1X2":
        return {"1": suma(m, lambda i, j: i > j), "X": suma(m, lambda i, j: i == j),
                "2": suma(m, lambda i, j: i < j)}.get(s)
    if t == "DC":
        return {"1X": suma(m, lambda i, j: i >= j), "X2": suma(m, lambda i, j: i <= j),
                "12": suma(m, lambda i, j: i != j)}.get(s)
    if t == "OU":
        p = suma(m, lambda i, j: i + j > l)
        return p if s == "over" else 1 - p
    if t == "OU1T":
        p = suma(m1t, lambda i, j: i + j > l)
        return p if s == "over" else 1 - p
    if t == "BTTS":
        p = suma(m, lambda i, j: i > 0 and j > 0)
        return p if s == "si" else 1 - p
    if t == "AH":
        if s == "local":
            return suma(m, lambda i, j: i - j + l > 0)
        return suma(m, lambda i, j: i - j + l < 0)
    if t == "DNB":
        pe = suma(m, lambda i, j: i == j)
        pw = suma(m, lambda i, j: i > j) if s == "local" else suma(m, lambda i, j: i < j)
        return pw / (1 - pe) if pe < 1 else None      # prob. condicionada (sin empate)
    if t == "CS":
        eq, sn = s.split("_")
        p = suma(m, lambda i, j: j == 0) if eq == "local" else suma(m, lambda i, j: i == 0)
        return p if sn == "si" else 1 - p
    if t == "EXACT":
        a, b = map(int, s.split("-"))
        return m[a][b]
    if t == "CORN" and mu_c:
        p = nb_over(mu_c, l, 12)
        return p if s == "over" else 1 - p
    if t == "CARDS" and mu_k:
        p = nb_over(mu_k, l, 8)
        p = p if s == "over" else 1 - p
        return 0.5 + (p - 0.5) * C.TARJ_ENCOGER
    return None


# =================================================================
# Candidatos y selección
# =================================================================
def candidatos(lh, la, esp, mercado, local, visita, base=None, frescas=None):
    """base = (lh0, la0) goles esperados SIN alineación. La diferencia que causa la alineación
    se aplica también sobre la probabilidad del mercado (que suele ser previa a las alineaciones)."""
    mu_c, mu_k = esp["total"], esp["tarjetas"]
    m = matriz(lh, la)
    m1t = matriz(lh * FRAC_1T, la * FRAC_1T, rho=0)
    if base:
        m0 = matriz(*base)
        m0_1t = matriz(base[0] * FRAC_1T, base[1] * FRAC_1T, rho=0)
    pe = suma(m, lambda i, j: i == j)

    claves = set(mercado.keys())
    for s in ("1", "X", "2"):
        claves.add(("1X2", None, s))
    for s in ("1X", "X2", "12"):
        claves.add(("DC", None, s))
    for l in (1.5, 2.5, 3.5):
        claves |= {("OU", l, "over"), ("OU", l, "under")}
    for l in (0.5, 1.5):
        claves |= {("OU1T", l, "over"), ("OU1T", l, "under")}
    claves |= {("BTTS", None, "si"), ("BTTS", None, "no")}
    claves |= {("CS", None, "local_si"), ("CS", None, "visita_si")}
    if mu_c:
        lc = math.floor(mu_c) + 0.5
        for l in (lc - 2, lc - 1, lc, lc + 1, lc + 2):
            if l > 0:
                claves |= {("CORN", l, "over"), ("CORN", l, "under")}
    if mu_k:
        for l in (2.5, 3.5, 4.5, 5.5, 6.5):
            claves |= {("CARDS", l, "over"), ("CARDS", l, "under")}

    out = []
    for clave in claves:
        if clave[0] == "DNB":
            continue
        pm = prob_modelo(clave, m, m1t, esp)
        if pm is None:
            continue
        fam = FAMILIA[clave[0]]
        cuota, pmk = mercado.get(clave, (None, None))
        if clave[0] in SOLO_CON_VALOR and not cuota:
            continue
        w = C.PESO_MODELO[fam]
        delta = 0.0
        if base and fam in ("resultado", "goles"):
            pm0 = prob_modelo(clave, m0, m0_1t, esp)
            delta = (pm - pm0) if pm0 is not None else 0.0
        if pmk is not None:
            # Cuotas pedidas justo después de las alineaciones: el mercado ya las descontó en parte
            aj = C.AJUSTE_ALINEACION_FRESCAS if frescas and clave in frescas else C.AJUSTE_ALINEACION
            pmk_aj = min(max(pmk + aj * delta, 0.01), 0.99)
            p = w * pm + (1 - w) * pmk_aj
        else:
            p = pm
        p = min(max(p, 0.001), 0.999)
        if clave[0] == "DNB":
            # p es condicionada a que no haya empate; EV con devolución en empate
            ev = (p * (1 - pe) * cuota + pe - 1) if cuota else None
            p_show = p * (1 - pe)
            justa = 1 / p
        else:
            ev = p * cuota - 1 if cuota else None
            p_show = p
            justa = 1 / p
        out.append({"clave": clave, "fam": fam, "p": p_show, "p_kelly": p, "cuota": cuota,
                    "justa": justa, "ev": ev, "solo_modelo": pmk is None,
                    "p_mercado": pmk, "p_modelo": pm, "delta": delta,
                    "aj": C.AJUSTE_ALINEACION_FRESCAS if frescas and clave in frescas else C.AJUSTE_ALINEACION,
                    "solo_valor": clave[0] in SOLO_CON_VALOR,
                    "texto": texto_mercado(clave, local, visita)})
    return out, m


def stake(c):
    o, p = c["cuota"], c["p_kelly"]
    if c["clave"][0] == "DNB":
        return 0.0
    f = (p * o - 1) / (o - 1)
    frac = C.KELLY_SEGURO if p >= 0.40 else C.KELLY_ARRIESGADO
    s = min(max(f * frac, 0), C.STAKE_MAX) * C.BANCA
    return round(s * 2) / 2


def contradicho(c):
    """La mayoría de los criterios del análisis van en contra de este pick."""
    r = c.get("respaldo")
    return bool(r) and r[1] > r[0]


def correlacionados(a, b, m):
    """Dos picks del mismo partido que ganan juntos mucho más de lo normal (ej. 'gana X' y 'X arco en cero')."""
    if m is None or a["clave"][0] not in TIPOS_FT or b["clave"][0] not in TIPOS_FT:
        return a["fam"] == b["fam"]
    n = len(m)
    gana = lambda c, x, y: evaluar(c["clave"], {"hg": x, "ag": y}) == "G"
    pa = sum(m[x][y] for x in range(n) for y in range(n) if gana(a, x, y))
    pb = sum(m[x][y] for x in range(n) for y in range(n) if gana(b, x, y))
    pj = sum(m[x][y] for x in range(n) for y in range(n) if gana(a, x, y) and gana(b, x, y))
    return pa > 0 and pb > 0 and pj / (pa * pb) > 1.25


def seleccionar(cands, matriz=None):
    todos = cands
    cands = [c for c in todos if not c["solo_valor"]]
    def uno_por_familia(lista, n):
        vistos, res = set(), []
        for c in lista:
            if c["fam"] in vistos:
                continue
            vistos.add(c["fam"])
            res.append(c)
            if len(res) == n:
                break
        return res

    # Solo mercados con cuota real: la probabilidad queda anclada al mercado (mucho más fiable)
    # Solo resultado y goles (tarjetas/córners rinden mal como "seguros")
    seguros = [c for c in cands if c["cuota"] and 0.55 <= c["p"] <= 0.88 and c["fam"] in ("resultado", "goles")
               and c["cuota"] >= 1.15 and c["clave"][0] != "EXACT" and not contradicho(c)]
    seguros.sort(key=lambda c: -c["p"])
    seguros = uno_por_familia(seguros, 3)

    # Arriesgados realistas: 25–45 % de probabilidad, cuota 2.00–4.00, los más probables primero
    # Sin "gana X" (1X2): en 80 casos acertó 22% cuando se esperaba 31% (las sorpresas casi no salen)
    arr = [c for c in cands if c["cuota"] and C.ARR_PROB[0] <= c["p"] <= C.ARR_PROB[1]
           and C.ARR_CUOTA[0] <= c["cuota"] <= C.ARR_CUOTA[1] and c["ev"] >= -0.10
           and c["clave"][0] != "1X2" and not contradicho(c)]
    arr.sort(key=lambda c: -c["p"])
    arriesgados = uno_por_familia(arr, 2)

    def discrepa(c):
        """El modelo se aleja demasiado del mercado (sin que la alineación lo explique): no confiar."""
        if c.get("p_mercado") is None:
            return False
        ref = c["p_mercado"] + c.get("aj", C.AJUSTE_ALINEACION) * (c.get("delta") or 0)
        return abs(c["p_modelo"] - ref) > C.DISCREPANCIA_MAX
    for c in todos:
        c["discrepa"] = discrepa(c)
    # Apostables: SOLO con valor real contra Pinnacle (sin margen y corregido por la alineación).
    # El modelo no crea el valor: solo puede vetarlo si apunta en contra.
    for c in todos:
        c["ev_ref"] = None
        if c.get("ref_sharp") and c["cuota"] and c.get("p_mercado") is not None:
            p_ref = min(max(c["p_mercado"] + c.get("aj", C.AJUSTE_ALINEACION) * (c.get("delta") or 0), 0.01), 0.99)
            c["p_ref"] = p_ref
            c["ev_ref"] = c["cuota"] * p_ref - 1
    ap = [c for c in todos if c["ev_ref"] is not None and c["ev_ref"] >= C.VALOR_APOSTABLE
          and C.CUOTA_MIN <= c["cuota"] <= C.CUOTA_MAX and c["p_ref"] >= 0.25 and c["fam"] != "tarjetas"
          and c["p_modelo"] >= c["p_ref"] - 0.02          # el modelo no lo contradice
          and c["clave"][0] != "DNB" and c["clave"] != ("1X2", None, "X") and not contradicho(c)]
    ap.sort(key=lambda c: -c["ev_ref"])
    apostables = []
    for c in ap:
        if len(apostables) >= C.MAX_APOSTABLES:
            break
        if any(correlacionados(c, x, matriz) for x in apostables):
            continue                                      # mismo escenario que otro apostable
        c["ev"], c["p_kelly"] = c["ev_ref"], c["p_ref"]
        apostables.append(c)
    for c in apostables:
        c["stake"] = stake(c)
    apostables = [c for c in apostables if c["stake"] > 0]
    ya = {c["clave"] for c in apostables}
    seguros = [c for c in seguros if c["clave"] not in ya]
    arriesgados = [c for c in arriesgados if c["clave"] not in ya]
    return seguros, arriesgados, apostables


def solo_modelo(cands, usados):
    """Mercados sin cuota (tarjetas, líneas de córners sin precio…): solo referencia, menos fiables."""
    lista = [c for c in cands if (not c["cuota"] or c["fam"] in ("tarjetas", "corners"))
             and not c["solo_valor"] and c["clave"][0] != "EXACT"
             and 0.60 <= c["p"] <= 0.88 and c["clave"] not in usados]
    lista.sort(key=lambda c: -c["p"])
    vistos, res = set(), []
    for c in lista:
        if c["fam"] not in vistos:
            vistos.add(c["fam"])
            res.append(c)
        if len(res) == 2:
            break
    return res


# =================================================================
# Liquidación
# =================================================================
def evaluar(clave, r):
    """'G' ganado, 'P' perdido, 'N' nulo/devolución, '?' sin datos."""
    t, l, s = clave
    hg, ag = r["hg"], r["ag"]
    tot = hg + ag
    if t == "1X2":
        ok = {"1": hg > ag, "X": hg == ag, "2": hg < ag}[s]
    elif t == "DC":
        ok = {"1X": hg >= ag, "X2": hg <= ag, "12": hg != ag}[s]
    elif t == "OU":
        ok = (tot > l) == (s == "over")
    elif t == "BTTS":
        ok = (hg > 0 and ag > 0) == (s == "si")
    elif t == "AH":
        d = hg - ag + l
        ok = d > 0 if s == "local" else d < 0
    elif t == "DNB":
        if hg == ag:
            return "N"
        ok = (hg > ag) == (s == "local")
    elif t == "CS":
        eq, sn = s.split("_")
        cero = ag == 0 if eq == "local" else hg == 0
        ok = cero == (sn == "si")
    elif t == "EXACT":
        ok = f"{hg}-{ag}" == s
    elif t == "OU1T":
        if r.get("ht") is None:
            return "?"
        ok = (sum(r["ht"]) > l) == (s == "over")
    elif t == "CORN":
        if r.get("corners") is None:
            return "?"
        ok = (r["corners"] > l) == (s == "over")
    elif t in ("CORN_H", "CORN_A", "CORN1T"):
        campo = {"CORN_H": "corners_h", "CORN_A": "corners_a", "CORN1T": "corners_1t"}[t]
        if r.get(campo) is None:
            return "?"
        ok = (r[campo] > l) == (s == "over")
    elif t == "CARDS":
        if r.get("cards") is None:
            return "?"
        ok = (r["cards"] > l) == (s == "over")
    elif t == "CARDS_BOTH":
        if r.get("cards_h") is None or r.get("cards_a") is None:
            return "?"
        ok = (r["cards_h"] > 0 and r["cards_a"] > 0) == (s == "si")
    else:
        return "?"
    return "G" if ok else "P"


# =================================================================
# Tickets
# =================================================================
TIPOS_FT = ("1X2", "DC", "OU", "BTTS", "CS", "AH")   # se resuelven solo con el marcador final


def evaluar_multi(claves, r):
    """Combinada: P si alguna pierde, ? si falta dato, N si todas nulas, G si todas ganan/nulas."""
    res = [evaluar(k, r) for k in claves]
    if "P" in res:
        return "P"
    if "?" in res:
        return "?"
    return "N" if all(x == "N" for x in res) else "G"


def p_prudente(c):
    """Para tickets: la menor entre la probabilidad del bot y la del mercado (Pinnacle si hay).
    El modelo inflaba algunas selecciones; así el ticket solo sale si paga incluso según el mercado."""
    q = c.get("p_ref") if c.get("p_ref") is not None else c.get("p_mercado")
    return min(c["p"], q) if q is not None else c["p"]


def _apta(c):
    return (c["cuota"] and c["fam"] in ("resultado", "goles") and not contradicho(c)
            and not c.get("discrepa") and c["clave"] != ("1X2", None, "X") and c["p"] >= 0.45)


def ticket_partido(cands, m):
    """Mejor ticket del partido (1 o 2 selecciones) con cuota total en TICKET_CUOTA y máxima probabilidad.
    Para 2 selecciones del mismo partido la probabilidad conjunta sale de la matriz de marcadores
    (están correlacionadas)."""
    lo, hi = C.TICKET_CUOTA
    aptas = [c for c in cands if _apta(c)]
    opciones = []
    for c in aptas:
        if lo <= c["cuota"] <= hi:
            # Ticket de 1 selección: acertó 46% vs 58% esperado → probabilidad prudente
            opciones.append({"piernas": [c], "p": p_prudente(c), "cuota": c["cuota"]})
    ft = [c for c in aptas if c["clave"][0] in TIPOS_FT and c["cuota"] < lo]
    n = len(m)
    for i in range(len(ft)):
        for j in range(i + 1, len(ft)):
            a, b = ft[i], ft[j]
            if a["clave"][0] == b["clave"][0]:
                continue
            cuota = a["cuota"] * b["cuota"]
            if not (lo <= cuota <= hi):
                continue
            ok = [(x, y) for x in range(n) for y in range(n)
                  if evaluar(a["clave"], {"hg": x, "ag": y}) != "P" and evaluar(b["clave"], {"hg": x, "ag": y}) != "P"]
            conj = sum(m[x][y] for x, y in ok)
            pa = sum(m[x][y] for x in range(n) for y in range(n) if evaluar(a["clave"], {"hg": x, "ag": y}) != "P")
            pb = sum(m[x][y] for x in range(n) for y in range(n) if evaluar(b["clave"], {"hg": x, "ag": y}) != "P")
            if pa <= 0 or pb <= 0:
                continue
            if conj >= 0.97 * min(pa, pb):   # una selección contiene a la otra (ej. "gana local" + "no hay empate")
                continue
            # (las de 2 selecciones del mismo partido acertaron 64% vs 56% esperado: aquí no se recorta)
            p = min(a["p"] * b["p"] * conj / (pa * pb), a["p"], b["p"])
            opciones.append({"piernas": [a, b], "p": p, "cuota": cuota})
    opciones = [o for o in opciones if o["p"] * o["cuota"] - 1 >= C.TICKET_EV_MIN]   # cuota ≥ justa
    if not opciones:
        return None
    return max(opciones, key=lambda o: (o["p"], -abs(o["cuota"] - 2.0)))


# =================================================================
# Crear apuesta (bet builder de un partido: 2-4 selecciones muy probables)
# =================================================================
R_CORNERS_CONS = 7    # dispersión más ancha que la normal (12): probabilidades más prudentes
R_TARJ_CONS = 5       # ídem tarjetas (8): el modelo de tarjetas es el más débil


def _estados_goles(lh, la, n=7):
    """Distribución conjunta (goles 1T local, 1T visita, final local, final visita)."""
    m1 = matriz(lh * FRAC_1T, la * FRAC_1T, n=n, rho=0)
    m2 = matriz(lh * (1 - FRAC_1T), la * (1 - FRAC_1T), n=n, rho=0)
    est = {}
    for h1 in range(n):
        for a1 in range(n):
            p1 = m1[h1][a1]
            if p1 < 1e-7:
                continue
            for h2 in range(n):
                for a2 in range(n):
                    p = p1 * m2[h2][a2]
                    if p < 1e-9:
                        continue
                    k = (h1, a1, h1 + h2, a1 + a2)
                    est[k] = est.get(k, 0) + p
    return [({"hg": k[2], "ag": k[3], "ht": (k[0], k[1])}, p) for k, p in est.items()]


def _estados_corners(mu):
    return [({"hg": 0, "ag": 0, "corners": k}, nb_pmf(k, mu, R_CORNERS_CONS)) for k in range(40)]


def _estados_tarjetas(mu_h, mu_a):
    ph = [nb_pmf(k, mu_h, R_TARJ_CONS) for k in range(16)]
    pa = [nb_pmf(k, mu_a, R_TARJ_CONS) for k in range(16)]
    return [({"hg": 0, "ag": 0, "cards": x + y, "cards_h": x, "cards_a": y}, ph[x] * pa[y])
            for x in range(16) for y in range(16)]


def crear_apuesta(cands, lh, la, esp, local, visita, hist):
    """Combina 3-5 selecciones de distinto tipo (resultado, goles, goles 1T, córners, tarjetas,
    ambos con tarjeta) con la mayor probabilidad conjunta posible y cuota justa en BUILDER_CUOTA.
    hist(clave) -> {'tasa','n','txt'} o None: cuántas veces se cumplió en sus partidos recientes y H2H.
    Probabilidad conjunta: goles/resultado con la distribución exacta de marcadores (1T y final);
    córners y tarjetas aparte (casi independientes de los goles)."""
    por_clave = {c["clave"]: c for c in cands}
    dist = {"goles": _estados_goles(lh, la)}
    if esp.get("total"):
        dist["corners"] = _estados_corners(esp["total"])
    if esp.get("k_local") and esp.get("k_visita"):
        dist["tarjetas"] = _estados_tarjetas(esp["k_local"], esp["k_visita"])

    def p_dist(d, claves):
        return sum(p for r, p in dist[d] if all(evaluar(k, r) == "G" for k in claves))

    # (grupo, distribución, claves posibles)
    opciones = [("resultado", "goles", [("DC", None, x) for x in ("1X", "X2", "12")]),
                ("goles", "goles", [("OU", 0.5, "over"), ("OU", 1.5, "over"), ("OU", 3.5, "under"),
                                    ("OU", 4.5, "under"), ("OU", 5.5, "under")]),
                ("goles1t", "goles", [("OU1T", 0.5, "over"), ("OU1T", 1.5, "under"),
                                      ("OU1T", 2.5, "under"), ("OU1T", 3.5, "under")])]
    if "corners" in dist:
        lc = math.floor(esp["total"]) + 0.5
        opciones.append(("corners", "corners",
                         [("CORN", lc - d, "over") for d in range(1, 6) if lc - d > 0]
                         + [("CORN", lc + d, "under") for d in range(1, 6)]))
    if "tarjetas" in dist:
        mk = esp["k_local"] + esp["k_visita"]
        lk = math.floor(mk) + 0.5
        opciones.append(("tarjetas", "tarjetas",
                         [("CARDS", lk - d, "over") for d in range(1, 4) if lk - d > 0]
                         + [("CARDS", lk + d, "under") for d in range(1, 5)]))
        opciones.append(("ambas", "tarjetas", [("CARDS_BOTH", None, "si")]))

    piernas = {}
    for grupo, d, claves in opciones:
        lista = []
        for k in claves:
            pm = p_dist(d, [k])
            c = por_clave.get(k)
            if d == "goles":
                if c and (contradicho(c) or c.get("discrepa")):
                    continue
                p = c["p"] if c and c.get("p_mercado") is not None else pm
            else:
                p = pm
                if d == "tarjetas":       # el modelo de tarjetas es demasiado optimista (ver config)
                    p = 0.5 + (p - 0.5) * C.TARJ_ENCOGER
                if c and c.get("p_mercado") is not None:
                    p = min(p, c["p"])
            h = hist(k)
            if h and h["n"] >= 6:
                if h["tasa"] < C.BUILDER_HIST_MIN:
                    continue                      # su historial lo contradice
                if d != "goles":                  # córners/tarjetas: mitad modelo, mitad historial (nunca sube)
                    p = min(p, 0.5 * p + 0.5 * (h["tasa"] * h["n"] + 1) / (h["n"] + 2))
            if p < C.BUILDER_PMIN[d] or p > C.BUILDER_PMAX:
                continue
            lista.append({"clave": k, "texto": texto_mercado(k, local, visita), "p": p, "pm": pm,
                          "dist": d, "grupo": grupo, "hist": h,
                          "cuota": c["cuota"] if c else None})
        lista.sort(key=lambda x: -x["p"])
        if lista:
            piernas[grupo] = lista[:4]

    grupos = list(piernas)
    cache = {}

    def p_claves(d, ps):
        ks = tuple(sorted(str(x["clave"]) for x in ps))
        if ks not in cache:
            cache[ks] = p_dist(d, [x["clave"] for x in ps])
        return cache[ks]

    def redundante(sel):
        """Alguna selección sale casi siempre que salen las demás de su mismo tipo (ej. menos de
        5.5 goles + menos de 2.5 en el 1T): Bet365 no la paga, solo agrega riesgo."""
        for d in {x["dist"] for x in sel}:
            ps = [x for x in sel if x["dist"] == d]
            if len(ps) < 2:
                continue
            todo = p_claves(d, ps)
            for x in ps:
                resto = p_claves(d, [y for y in ps if y is not x])
                if resto > 0 and todo / resto >= C.BUILDER_REDUNDANTE:
                    return True
        return False

    def conjunta(sel):
        p = 1.0
        for d in {x["dist"] for x in sel}:
            ps = [x for x in sel if x["dist"] == d]
            prod_m = math.prod(x["pm"] for x in ps)
            prod_p = math.prod(x["p"] for x in ps)
            if prod_m <= 0:
                return 0.0
            p *= min(prod_p * p_claves(d, ps) / prod_m, min(x["p"] for x in ps))
        return p

    lo, hi = C.BUILDER_CUOTA
    nmin, nmax = C.BUILDER_PIERNAS
    mejor = None

    def explorar(i, sel):
        nonlocal mejor
        if i == len(grupos):
            if nmin <= len(sel) <= nmax and len({x["dist"] for x in sel}) >= 2 and not redundante(sel):
                p = conjunta(sel)
                if p > 0 and lo <= 1 / p <= hi:
                    clave = (round(p, 2), -len(sel), p)     # a igual probabilidad, menos selecciones
                    if not mejor or clave > mejor[0]:
                        mejor = (clave, list(sel), p)
            return
        explorar(i + 1, sel)
        if len(sel) < nmax:
            for x in piernas[grupos[i]]:
                explorar(i + 1, sel + [x])

    explorar(0, [])
    if not mejor:
        return None
    orden = {"resultado": 0, "goles": 1, "goles1t": 2, "corners": 3, "tarjetas": 4, "ambas": 5}
    sel = sorted(mejor[1], key=lambda x: orden[x["grupo"]])
    return {"piernas": sel, "p": mejor[2], "justa": 1 / mejor[2]}
