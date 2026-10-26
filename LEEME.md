# Bot de Picks con calendario

Calendario web con partidos de Europa (ligas top y UEFA), Libertadores/Sudamericana, Brasileirão, Portugal,
Liga 1 Perú y selecciones. Tocas **Seguir** en un partido y, cuando se confirman las alineaciones, el bot te
manda el análisis por Telegram. Todo corre gratis en GitHub Actions y empieza en **paper trading**: registra
cada pick, lo liquida solo y `/resumen` te dice si acierta lo que promete.

## Qué trae cada mensaje
| Sección | Qué es |
|---|---|
| 🔎 Análisis | forma, localía, tabla, H2H, goles recientes, remates, descanso, altura, movimiento de cuotas y alineaciones |
| 🟢 Seguros | las 2 más probables (55–88%), solo resultado y goles con cuota real. Informativos: a cuota 1.20–1.30 casi nunca hay ganancia |
| 📐 Tarjetas/córners | solo modelo, compara la “justa” con tu casa |
| ✅ Apostables | **lo único pensado para apostar**: tu casa paga ≥ 3% más que la cuota justa de Pinnacle, cuota 1.60–2.60, el modelo no lo contradice. Stake ¼ Kelly (⅛ si prob. < 40%), máximo 3% de la banca |
| 🎟 Ticket del partido | 1 o 2 selecciones con cuota total 1.70–2.30 que paguen al menos su justa |
| 🧱 Crear apuesta | 2–4 selecciones del mismo partido (goles, resultado, 1T, córners) con su historial. **Solo aparece cuando conviene** a la cuota que suele pagar Bet365 (~1.45, `BUILDER_CUOTA_BET365`); la probabilidad se corrige con lo que acertaron las anteriores |

Se quitaron (octubre 2026): arriesgados (bien calibrados pero sin ganancia), valor entre casas (repetía los
apostables sin filtros) y ticket de 2 partidos (acertaba 49% vs 57% prometido, −12%).

Solo se recomiendan casas donde puedes apostar (`CASAS_PERMITIDAS` en `config.py`: Bet365, 1xBet, Betsson,
Betano, Apuesta Total, Doradobet). Las demás se usan solo como referencia de precio.

## Comandos en Telegram
| Comando | Qué hace |
|---|---|
| `/seguir ID` | seguir un partido por su ID (también desde el calendario) |
| `/lista` | partidos que sigues y su estado |
| `/quitar ID` | dejar de seguir |
| `/resumen` | aciertos reales vs. esperados, ganancia y yield por tipo |
| `/cuota ID 1.80` | opcional: anota lo que te pagó Bet365 por el “Crear apuesta” (si no, se usa ~1.45) |

## Cómo funciona por dentro
1. **Calendario** (cada 2 h): descarga de 365Scores los partidos de hoy y los 3 días siguientes y publica la página.
   La página guarda lo que sigues en `data/seleccion.json` con tu token (⚙️).
2. **Picks** (siempre encendido): una pasada cada 2 min durante ~5.5 h y se relanza sola. Si cambias algo en el
   calendario, lo aplica en menos de 1 min. Si subes código nuevo, se reinicia sola con esa versión.
   - Desde 75 min antes revisa si las alineaciones están **confirmadas**; a 40 min envía con la probable y avisa si cambia.
   - Goles esperados: 75% lo que implican las cuotas (1X2 + más/menos 2.5) y 25% forma y remates a puerta.
     Poisson con corrección Dixon-Coles.
   - Alineación: compara el XI con los titulares habituales. Si falta quien hace los goles, baja el ataque; si faltan
     portero o defensas, sube el del rival; 5+ cambios = rotación.
   - Córners y tarjetas: binomial negativa con los últimos 5 partidos. Las tarjetas se corrigen hacia 50%
     (`TARJ_ENCOGER`) porque el modelo era demasiado optimista.
   - Cuotas: Bet365 vía 365Scores + The Odds API (500 créditos gratis/mes, 2 por partido: resultado y goles) con Pinnacle como referencia.
     Tope de 14 créditos por día (`ODDS_CREDITOS_DIA`) para que alcancen todo el mes; si se acaban, te avisa por
     Telegram y revisa cada 6 h (gratis) si ya se renovaron. Control: si Pinnacle y Bet365 difieren más de 8 puntos
     en el 1X2 (posible partido mal emparejado), descarta las cuotas externas.
   - **CLV**: 5 min antes del inicio pide la cuota de cierre de Pinnacle para cada apostable y calcula cuánto valía
     la cuota que se tomó. Sale en el mensaje de resultado y en `/resumen`. CLV positivo y constante = ventaja real.
3. ~2 h después del inicio **liquida** los picks y te manda el resultado (espera hasta 8 h si faltan estadísticas).

## Archivos
| Archivo | Para qué |
|---|---|
| `config.py` | ligas, casas, reglas y tiempos. **Aquí se ajusta todo.** |
| `s365.py` | conexión a 365Scores (reintentos y caché) |
| `cuotas_ext.py` | cuotas de varias casas (The Odds API) |
| `modelo.py` | matemáticas y selección de picks, tickets y “Crear apuesta” |
| `criterios.py` | criterios del análisis y respaldo ✔/✖ de cada pick |
| `picks_bot.py` | Telegram, vigilante, envío y liquidación |
| `guardar.sh` | guarda el estado en el repo y solo entonces envía los mensajes (evita duplicados) |
| `calendario.py` + `site/index.html` | calendario web |
| `data/picks.csv` | registro de todos los picks (se abre en Excel) |

## Secretos (Settings → Secrets and variables → Actions)
`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `ODDS_API_KEY`. Opcional en Variables: `BANCA` (banca virtual, por defecto 1000).

## Límites honestos
- “justa” = la cuota mínima que deberías aceptar. Si tu casa paga menos, no hay valor.
- Que algo sea muy probable no significa que convenga apostarlo: las casas ponen más margen en lo “seguro”.
- En “Crear apuesta” Bet365 suele pagar bastante menos que la multiplicación de las cuotas.
- Cada tarjeta cuenta 1 (algunas casas cuentan la roja como 2).
- No pases a dinero real hasta tener unos **200 apostables** con yield positivo y aciertos cercanos a los esperados.
