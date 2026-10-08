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
        pos.update(p["pos"])
    hoy = {str(x) for x in xi_hoy}
    habituales = [pid for pid, c in titular.items() if c >= max(2, math.ceil(n * 0.6))]
    faltan = [pid for pid in habituales if pid not in hoy]

    notas, f_atk, f_riv = [], 1.0, 1.0
    perdida = 0.0
    for pid in faltan:
        share = aporte.get(pid, 0) / goles_eq if goles_eq else 0
        perdida += share
        nombre = nombres.get(pid) or (nombres_hoy.get(int(pid), "") if pid.isdigit() else "")
        etiqueta = f"{nombre or 'Titular'}"
        if share >= 0.10:
            etiqueta += f" ({share:.0%} de la producción ofensiva)"
        elif pos.get(pid) in ("Portero", "Defensa"):
            etiqueta += f" ({pos.get(pid).lower()})"
        notas.append(etiqueta)
        if pos.get(pid) in ("Portero", "Defensa"):
            f_riv += 0.03
    f_atk -= min(0.25, 0.6 * perdida)
    f_riv = min(f_riv, 1.12)

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
            elif lt == 3 and val is not None:
                clave = ("OU", val, sel)
            elif lt == 12:
                clave = ("BTTS", None, sel)
            elif lt == 11 and val is not None and abs(val * 2 % 2 - 1) < 1e-6:
                clave = ("AH", val, sel)          # solo líneas .5 (sin devolución)
            elif lt == 15:
                clave = ("DNB", None, sel)
            elif lt == 9 and val is not None:
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
                out[clave] = (dec, (1 / dec) / imp)
    return out


# =================================================================
# Probabilidades del modelo para cada mercado
# =================================================================
FAMILIA = {"1X2": "resultado", "DC": "resultado", "AH": "resultado", "DNB": "resultado",
           "OU": "goles", "BTTS": "goles", "OU1T": "goles", "CS": "goles", "EXACT": "goles",
           "CORN": "corners", "CORN_H": "corners", "CORN_A": "corners", "CORN1T": "corners",
           "CARDS": "tarjetas"}

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
        return p if s == "over" else 1 - p
    return None


# =================================================================
# Candidatos y selección
# =================================================================
def candidatos(lh, la, esp, mercado, local, visita):
    mu_c, mu_k = esp["total"], esp["tarjetas"]
    m = matriz(lh, la)
    m1t = matriz(lh * FRAC_1T, la * FRAC_1T, rho=0)
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
        base = math.floor(mu_c) + 0.5
        for l in (base - 2, base - 1, base, base + 1, base + 2):
            if l > 0:
                claves |= {("CORN", l, "over"), ("CORN", l, "under")}
    if mu_k:
        for l in (2.5, 3.5, 4.5, 5.5, 6.5):
            claves |= {("CARDS", l, "over"), ("CARDS", l, "under")}
    exactos = sorted(((m[i][j], f"{i}-{j}") for i in range(6) for j in range(6)), reverse=True)[:3]
    for _, s in exactos:
        claves.add(("EXACT", None, s))

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
        p = w * pm + (1 - w) * pmk if pmk is not None else pm
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


def seleccionar(cands):
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

    seguros = [c for c in cands if 0.55 <= c["p"] <= 0.88
               and (c["cuota"] or c["justa"]) >= 1.22 and c["clave"][0] != "EXACT"]
    seguros.sort(key=lambda c: -c["p"])
    seguros = uno_por_familia(seguros, 3)

    arr = [c for c in cands if 0.10 <= c["p"] <= 0.40 and (c["ev"] is None or c["ev"] >= -0.06)]
    arr.sort(key=lambda c: (c["ev"] is None, -(c["ev"] or 0), -c["p"]))
    arriesgados = uno_por_familia(arr, 2)
    if not any(c["clave"][0] == "EXACT" for c in arriesgados):
        ex = max((c for c in cands if c["clave"][0] == "EXACT"), key=lambda c: c["p"], default=None)
        if ex:
            arriesgados.append(ex)

    ap = [c for c in todos if c["cuota"] and c["cuota"] >= C.CUOTA_MIN and c["ev"] is not None
          and c["ev"] >= C.EV_MIN and c["p"] >= 0.20 and c["clave"][0] != "DNB"]
    ap.sort(key=lambda c: -c["ev"])
    apostables = uno_por_familia(ap, C.MAX_APOSTABLES)
    for c in apostables:
        c["stake"] = stake(c)
    apostables = [c for c in apostables if c["stake"] > 0]
    ya = {c["clave"] for c in apostables}
    seguros = [c for c in seguros if c["clave"] not in ya]
    arriesgados = [c for c in arriesgados if c["clave"] not in ya]
    return seguros, arriesgados, apostables


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
    else:
        return "?"
    return "G" if ok else "P"
