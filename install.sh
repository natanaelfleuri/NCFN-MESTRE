#!/usr/bin/env bash
# Instala o MESTRE para o usuário atual (sem sudo).
#   ./install.sh            ambiente Python, modelos de voz, serviço, lançador e atalho
# Requisitos: Linux com X11, Python 3.11+, Google Chrome, Claude Code (`claude`)
# autenticado, e de preferência uma GPU NVIDIA (sem ela tudo roda na CPU, mais devagar).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "==> Ambiente Python (.venv)"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Bibliotecas CUDA do Whisper"
.venv/bin/python - <<'PY' > .cuda_libs || true
import nvidia.cublas.lib as a, nvidia.cudnn.lib as b
print(list(a.__path__)[0] + ':' + list(b.__path__)[0])
PY

echo "==> Voz de reserva (Piper pt_BR-faber)"
mkdir -p models/piper
for f in pt_BR-faber-medium.onnx pt_BR-faber-medium.onnx.json; do
  [ -f "models/piper/$f" ] || curl -sL -o "models/piper/$f" \
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/$f"
done
# Whisper (~1,6 GB) e Kokoro (~330 MB) baixam sozinhos no primeiro uso.

[ -f config.json ] || cp config.example.json config.json
chmod +x bin/* hooks/guard.py

echo "==> Serviço, lançador e atalho"
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/bin" "$HOME/.local/share/icons" "$HOME/.local/share/applications"
sed "s|__MESTRE_DIR__|$DIR|g" install/mestre.service > "$HOME/.config/systemd/user/mestre.service"
sed "s|__MESTRE_DIR__|$DIR|g" install/mestre.launcher > "$HOME/.local/bin/mestre"
chmod +x "$HOME/.local/bin/mestre"
cp ui/icon.svg "$HOME/.local/share/icons/mestre.svg"
sed "s|__HOME__|$HOME|g" install/mestre.desktop > "$HOME/.local/share/applications/mestre.desktop"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESK" ]; then
  cp "$HOME/.local/share/applications/mestre.desktop" "$DESK/mestre.desktop"
  chmod +x "$DESK/mestre.desktop"
  gio set "$DESK/mestre.desktop" metadata::trusted true 2>/dev/null || true
fi
systemctl --user daemon-reload

echo
echo "Pronto. Clique no ícone MESTRE na área de trabalho (ou rode: mestre)."
echo "Contexto pessoal opcional: crie persona.local.md (fica fora do git)."
