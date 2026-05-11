#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_SRC="$SCRIPT_DIR/nginx/hedgepoint.conf"
CONF_DEST="/etc/nginx/sites-available/hedgepoint"
CONF_LINK="/etc/nginx/sites-enabled/hedgepoint"

if [[ $EUID -ne 0 ]]; then
    echo "Error: ejecutar como root (sudo $0)" >&2
    exit 1
fi

# Advertir si el dominio no fue configurado
if grep -q "TU_DOMINIO.mx" "$CONF_SRC"; then
    echo "AVISO: el archivo contiene el placeholder 'TU_DOMINIO.mx'."
    echo "  Edita deploy/nginx/hedgepoint.conf antes de continuar en producción."
    echo ""
fi

echo "Copiando configuración..."
cp "$CONF_SRC" "$CONF_DEST"
echo "  -> $CONF_DEST"

echo "Creando symlink en sites-enabled..."
ln -sf "$CONF_DEST" "$CONF_LINK"
echo "  -> $CONF_LINK"

echo "Verificando configuración de Nginx..."
nginx -t

echo "Recargando Nginx..."
systemctl reload nginx
echo "  -> nginx: $(systemctl is-active nginx)"

echo ""
echo "Listo. Para activar HTTPS ejecuta:"
echo "  sudo certbot --nginx -d TU_DOMINIO.mx -d www.TU_DOMINIO.mx"
