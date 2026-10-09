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
AJUSTE_ALINEACION = 0.8      # cuánto del efecto de la alineación se aplica también a la prob. del mercado
ARR_PROB = (0.25, 0.45)      # arriesgados: rango de probabilidad
ARR_CUOTA = (2.00, 4.00)     # arriesgados: rango de cuota
DISCREPANCIA_MAX = 0.12      # si el modelo se aleja >12 puntos del mercado, no se marca como apostable

# ---------------------------------------------------------------
# TIEMPOS
# ---------------------------------------------------------------
VENTANA_MIN = 75             # empieza a buscar alineaciones 75 min antes
REVISAR_CADA_MIN = 4         # no consultar el mismo partido más seguido que esto
PASADA_SEG = 120             # el vigilante revisa cada 2 minutos
VIGILAR_MIN = 330            # cada ejecución vigila ~5.5 h y luego se relanza sola
ENVIAR_SIN_ALINEACION_MIN = 40  # si a 40 min del inicio 365Scores no confirma, envía con la alineación probable
                                # (y si luego se confirma distinta, manda una actualización)

ZONA = "America/Lima"

# ---------------------------------------------------------------
# CUOTAS DE VARIAS CASAS (The Odds API, plan gratis 500 créditos/mes)
# ---------------------------------------------------------------
ODDS_MERCADOS = "h2h,totals,btts"   # cada mercado devuelto cuesta 1 crédito (≈3 por partido)
ODDS_RESERVA = 10                   # deja de consultar si quedan menos créditos que esto
CASAS_EXCLUIR = ["betfair_ex_eu", "betfair_ex_uk", "matchbook"]   # exchanges (cobran comisión)
VALOR_CASAS_MIN = 0.03              # "💎 valor entre casas": mejor cuota ≥ 3% sobre la justa de Pinnacle
CUOTAS_VIEJAS_HORAS = 4             # si las cuotas de 365Scores no se movieron en 4 h, se marcan como viejas
