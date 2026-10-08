"""Genera site/fixtures.json con los partidos de los próximos días de tus ligas."""
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import config as C
import s365

TZ = ZoneInfo(C.ZONA)
SALIDA = os.path.join(os.path.dirname(__file__), "site", "fixtures.json")


def grupo_de(cid, comp):
    if cid in C.LIGAS:
        return C.LIGAS[cid]
    nombre = (comp.get("name") or "").lower()
    if comp.get("isInternational") and any(k in nombre for k in C.SELECCIONES_INCLUIR) \
            and not any(k in nombre for k in C.SELECCIONES_EXCLUIR):
        return comp.get("name"), "Selecciones"
    return None


def usuario_bot():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        return ""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20).json()
        return r.get("result", {}).get("username", "")
    except (requests.RequestException, ValueError):
        return ""


def main():
    hoy = datetime.now(TZ).date()
    partidos, vistos = [], set()
    for d in range(C.DIAS_CALENDARIO):
        dia = hoy + timedelta(days=d)
        j = s365.partidos_del_dia(dia.strftime("%d/%m/%Y"))
        if not j:
            print("sin datos para", dia)
            continue
        comps = {c["id"]: c for c in j.get("competitions") or []}
        for g in j.get("games") or []:
            if g["id"] in vistos or g.get("statusGroup") != 2:
                continue
            cid = g.get("competitionId")
            info = grupo_de(cid, comps.get(cid, {}))
            if not info:
                continue
            liga, grupo = info
            h, a = g["homeCompetitor"], g["awayCompetitor"]
            vistos.add(g["id"])
            partidos.append({
                "id": g["id"], "inicio": g["startTime"], "liga": liga, "grupo": grupo,
                "liga_id": cid, "ronda": g.get("roundName", "") + (f" {g['roundNum']}" if g.get("roundNum") else ""),
                "local": h.get("name"), "local_id": h.get("id"), "local_v": h.get("imageVersion", 1),
                "visita": a.get("name"), "visita_id": a.get("id"), "visita_v": a.get("imageVersion", 1),
            })
    partidos.sort(key=lambda p: (p["inicio"], p["liga"]))
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump({"generado": datetime.now(TZ).isoformat(timespec="minutes"),
                   "bot": usuario_bot(), "partidos": partidos}, f, ensure_ascii=False)
    print(f"{len(partidos)} partidos guardados")


if __name__ == "__main__":
    main()
