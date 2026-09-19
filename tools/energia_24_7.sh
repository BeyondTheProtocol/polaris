#!/bin/bash
# energia_24_7.sh — ajustes de energía para que la caja Polaris viva 24/7.
# EJECUTAR CON SUDO:   sudo bash ~/claudecode/tools/energia_24_7.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Ejecutame con sudo:  sudo bash $0"; exit 1; }

echo "→ Reiniciar automaticamente tras un corte de luz..."
pmset -a autorestart 1
echo "→ No dormir (sistema y disco); pantalla a los 10 min..."
pmset -a sleep 0 disksleep 0 displaysleep 10
echo
echo "Estado de energia ahora:"
pmset -g | grep -E 'autorestart|disksleep| sleep|displaysleep' || true
echo
echo "AUTO-LOGIN (para que arranque sin teclear la contraseña):"
echo "  Ajustes del Sistema > Usuarios y grupos > Inicio de sesion automatico > 'polaris'."
echo "  (Requiere tu contraseña de cuenta; FileVault puede pedir igualmente el primer login.)"
