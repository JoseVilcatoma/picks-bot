"""Configuración del bot de picks. Cambia aquí ligas, reglas y montos."""
import os

# ---------------------------------------------------------------
# LIGAS (IDs de 365Scores, verificados)
# ---------------------------------------------------------------
LIGAS = {
    7: ("Premier League", "Europa"),
    11: ("LaLiga", "Europa"),
    17: ("Serie A", "Europa"),
    25: ("Bundesliga", "Europa"),
    35: ("Ligue 1", "Europa"),
    572: ("Champions League", "UEFA"),
    573: ("Europa League", "UEFA"),
    7685: ("Conference League", "UEFA"),
    102: ("Copa Libertadores", "Sudamérica"),
    389: ("Copa Sudamericana", "Sudamérica"),
    583: ("Liga 1 Perú", "Perú"),
    113: ("Brasileirão Serie A", "Sudamérica"),
}

# Selecciones: se detectan por nombre de la competición
SELECCIONES_INCLUIR = ["amistoso", "nations", "eliminatoria", "clasificaci",
                       "mundial", "copa del mundo", "copa américa", "copa america",
                       "eurocopa", "euro "]
SELECCIONES_EXCLUIR = ["femen", "sub", "juvenil", "youth", "u17", "u19", "u20",
                       "u21", "u23", "olímp", "olimp", "playa", "futsal"]

DIAS_CALENDARIO = 4          # hoy + 3 días

# ---------------------------------------------------------------
# REGLAS DE PICKS (tus reglas)
# ---------------------------------------------------------------
BANCA = float(os.getenv("BANCA") or 1000)   # banca virtual en soles (paper trading)
EV_MIN = 0.08                # EV mínimo para "apostable"
CUOTA_MIN = 1.60             # cuota mínima para "apostable"
MAX_APOSTABLES = 3           # máximo de picks apostables por partido
KELLY_SEGURO = 0.25          # 1/4 Kelly si la probabilidad es >= 40%
KELLY_ARRIESGADO = 0.125     # 1/8 Kelly si la probabilidad es < 40%
STAKE_MAX = 0.03             # nunca más del 3% de la banca en un pick

# Cuánto pesa el modelo frente al mercado (el resto es la cuota de Bet365 sin margen).
# Tu backtest mostró que el mercado predice mejor: por eso el modelo pesa poco.
PESO_MODELO = {"resultado": 0.35, "goles": 0.35, "corners": 0.50, "tarjetas": 1.0}

# ---------------------------------------------------------------
# TIEMPOS
# ---------------------------------------------------------------
VENTANA_MIN = 75             # empieza a buscar alineaciones 75 min antes
REVISAR_CADA_MIN = 8         # no consultar el mismo partido más seguido que esto
ENVIAR_SIN_ALINEACION_MIN = 40  # si a 40 min del inicio 365Scores no confirma, envía con la alineación probable
                                # (y si luego se confirma distinta, manda una actualización)

ZONA = "America/Lima"
