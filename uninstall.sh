#!/usr/bin/env bash
# Remove o serviço, o lançador e os atalhos do MESTRE. Não apaga esta pasta.
set -u
systemctl --user stop mestre.service 2>/dev/null
rm -f "$HOME/.config/systemd/user/mestre.service" "$HOME/.local/bin/mestre" \
      "$HOME/.local/share/icons/mestre.svg" "$HOME/.local/share/applications/mestre.desktop" \
      "$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")/mestre.desktop"
systemctl --user daemon-reload
echo "MESTRE desinstalado. Para apagar tudo, remova também esta pasta (modelos e .venv)."
