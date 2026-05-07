#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULE_SRC="$SCRIPT_DIR/udev/99-srs-usbtmc.rules"
RULE_DST="/etc/udev/rules.d/99-srs-usbtmc.rules"

echo "Instalando regla udev para el SR860/SR865 USBTMC..."
sudo cp "$RULE_SRC" "$RULE_DST"
sudo udevadm control --reload-rules
sudo udevadm trigger

echo
echo "Desconecta y vuelve a conectar el instrumento."
echo "Luego verifica con:"
echo "  ls -l /dev/usbtmc0"
echo "Debe aparecer con grupo plugdev y permisos 660."
