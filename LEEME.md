# Bot de Picks con calendario

Calendario web de partidos (Europa top, UEFA, Libertadores/Sudamericana, Liga 1 y selecciones).
Tocas **Seguir** en un partido y, cuando se confirman las alineaciones, el bot te manda por Telegram:

- 🟢 **Seguros**: lo más probable (55–88%).
- 🔴 **Arriesgados**: difíciles pero posibles (10–40%), incluido el marcador exacto más probable.
- ✅ **Apostables**: solo si EV ≥ 8% y cuota ≥ 1.60, con stake de ¼ Kelly (⅛ si la probabilidad es menor a 40%).

Todo corre gratis en GitHub Actions con datos y cuotas de Bet365 tomados de 365Scores.
Empieza en **paper trading**: registra cada pick, lo liquida solo y `/resumen` te dice si el modelo acierta lo que promete.

---

## Instalación paso a paso (unos 20 minutos)

### Paso 1 — Crear un bot nuevo de Telegram
Usa un bot nuevo para no chocar con el radar bot.

1. En Telegram abre **@BotFather** y escribe `/newbot`.
2. Ponle un nombre, por ejemplo `Picks Jose`, y un usuario que termine en `bot`, por ejemplo `picks_jose_bot`.
3. Copia el **token** que te da.
4. Tu **chat id** es el mismo que usas en el radar bot. Si no lo tienes: escríbele algo a tu bot nuevo y abre
   `https://api.telegram.org/bot<TOKEN>/getUpdates`. El número que aparece en `"chat":{"id": ...}` es tu chat id.

### Paso 2 — Crear el repositorio
1. En GitHub: **New repository** → nombre `picks-bot` → **Public** → **Create repository**.
   El repositorio debe ser público para que GitHub Pages sea gratis.
2. Pulsa **uploading an existing file** y arrastra **todo el contenido** de la carpeta `picks-bot`, incluida la carpeta `.github`.
   - Si Windows no te deja ver `.github`: en el Explorador, **Vista → Mostrar → Elementos ocultos**.
   - Si la carpeta `.github` no se sube, créala a mano: **Add file → Create new file**, escribe como nombre
     `.github/workflows/picks.yml`, pega el contenido del archivo y guarda. Repite con `calendario.yml`.
3. Pulsa **Commit changes**.

### Paso 3 — Secretos
**Settings → Secrets and variables → Actions → New repository secret**:

| Nombre | Valor |
|---|---|
| `TELEGRAM_TOKEN` | el token del bot nuevo |
| `TELEGRAM_CHAT_ID` | tu chat id |

Opcional, en la pestaña **Variables**: `BANCA` = tu banca virtual en soles (por defecto 1000).

### Paso 4 — Permisos y página web
1. **Settings → Actions → General → Workflow permissions** → marca **Read and write permissions** → Save.
2. **Settings → Pages → Build and deployment → Source** → elige **GitHub Actions**.

### Paso 5 — Primer arranque
1. Pestaña **Actions** → si pregunta, pulsa **I understand… enable them**.
2. Abre **Calendario** → **Run workflow**. Espera el ✅ (1–2 min).
3. Tu calendario queda en: `https://TU_USUARIO.github.io/picks-bot/`. Guárdalo en tu celular.
4. Abre **Picks** → **Run workflow** una vez. Desde ahí corre solo cada 5 minutos.

### Paso 6 — Prueba con un partido real
1. En el calendario, toca **Seguir** en un partido → se abre Telegram → **Iniciar**.
2. Para ver un análisis al instante sin esperar: **Actions → Picks → Run workflow** y en el campo
   *ID de partido* pon el número del partido. Es el que aparece después de `start=f` en el enlace del botón Seguir.
   Te llega un mensaje marcado 🧪 PRUEBA que no se registra.

---

## Comandos en Telegram
| Comando | Qué hace |
|---|---|
| `/seguir ID` | seguir un partido por su ID |
| `/lista` | partidos que sigues y su estado |
| `/quitar ID` | dejar de seguir |
| `/resumen` | aciertos reales vs. esperados, ganancia y yield del paper trading |

El bot revisa cada ~5 minutos, pero GitHub a veces se retrasa en horas pico, así que las respuestas pueden tardar entre 5 y 15 minutos.

## Cómo funciona por dentro
1. **Calendario** (3 veces al día): descarga de 365Scores los partidos de hoy y los 3 días siguientes de tus ligas.
2. **Picks** (cada 5 min): desde 75 minutos antes del inicio revisa si las dos alineaciones están **Confirmadas**.
   - Calcula goles esperados con los últimos 10 partidos de cada equipo (Poisson con corrección Dixon-Coles).
   - Compara el XI de hoy con los titulares habituales de los últimos 5 partidos. Si falta alguien que aporta goles,
     baja el ataque de ese equipo. Si faltan defensas, sube el del rival. Si hay 5 o más cambios, lo marca como rotación.
   - Calcula córners y tarjetas con las estadísticas de los últimos 5 partidos (binomial negativa).
   - **Mezcla el modelo con la cuota de Bet365 sin margen**: el modelo pesa 35% en goles y resultado y 50% en córners.
     Las tarjetas son solo modelo porque no hay cuota.
   - Si a 12 minutos del inicio no hay alineación confirmada, envía igual, avisando.
3. ~2 horas después del inicio **liquida** los picks y te manda el resultado.

## Archivos
| Archivo | Para qué |
|---|---|
| `config.py` | ligas, reglas (EV, cuota mínima, Kelly), tiempos. **Aquí ajustas todo.** |
| `s365.py` | conexión a 365Scores |
| `modelo.py` | matemáticas y selección de picks |
| `picks_bot.py` | bot de Telegram, envío y liquidación |
| `calendario.py` + `site/index.html` | calendario web |
| `data/picks.csv` | registro de todos los picks (se abre en Excel) |

## Límites honestos
- La columna "justa" es la cuota mínima que deberías aceptar. Si tu casa paga menos, no hay valor.
- Los seguros y arriesgados sin ✅ son informativos: que algo sea probable no significa que convenga apostarlo.
- Las líneas de tarjetas de cada casa cuentan distinto (a veces la roja vale 2). Aquí cada tarjeta cuenta 1.
- No pases a dinero real hasta tener unos **200 apostables** con yield positivo y aciertos reales cercanos a los esperados en `/resumen`.
