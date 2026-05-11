#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICES=(
    hedgepoint-dashboard
    hedgepoint-monitor
    hedgepoint-scheduler
)

if [[ $EUID -ne 0 ]]; then
    echo "Error: ejecutar como root (sudo $0)" >&2
    exit 1
fi

echo "Copiando archivos de servicio..."
for svc in "${SERVICES[@]}"; do
    cp "$SCRIPT_DIR/systemd/${svc}.service" /etc/systemd/system/
    echo "  -> /etc/systemd/system/${svc}.service"
done

echo "Recargando systemd..."
systemctl daemon-reload

echo "Habilitando e iniciando servicios..."
for svc in "${SERVICES[@]}"; do
    systemctl enable "$svc"
    systemctl start "$svc"
    echo "  -> $svc: $(systemctl is-active "$svc")"
done

echo ""
echo "Estado final:"
for svc in "${SERVICES[@]}"; do
    systemctl status "$svc" --no-pager -l
    echo "---"
done
