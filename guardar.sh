#!/usr/bin/env bash
# Guarda el estado del bot en el repo y SOLO si se guardó, envía los mensajes a Telegram.
git config user.name "picks-bot"
git config user.email "picks-bot@users.noreply.github.com"
git add data
if git diff --cached --quiet; then exit 0; fi
git commit -m "estado del bot" -q

guardado=0
for i in 1 2 3; do
  if git push -q 2>/dev/null; then guardado=1; break; fi
  if git pull --rebase -q 2>/dev/null; then
    continue
  fi
  git rebase --abort 2>/dev/null || true
  echo "conflicto al guardar: descarto esta pasada"
  git reset -q --hard origin/main
  exit 0
done
[ "$guardado" = 1 ] || { echo "no se pudo guardar"; exit 0; }

python picks_bot.py --enviar
git add data
if ! git diff --cached --quiet; then
  git commit -m "mensajes enviados" -q
  for i in 1 2 3; do
    git push -q 2>/dev/null && break
    git pull --rebase -X theirs -q 2>/dev/null || git rebase --abort 2>/dev/null || true
    sleep 3
  done
fi
