# Avisos de prensa (tarea diaria de Claude)

- `noticias.txt`: el último aviso. Cada vez que cambia, el workflow "Aviso de prensa" lo manda a Telegram.
- `historial.md`: lo que ya se avisó, para no repetir.

La tarea lee `data/rot_agenda.json` (lo deja el radar de rotaciones) para saber qué partidos vienen
y qué equipos ya no se juegan nada.
