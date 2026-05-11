#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/hedgepoint/app"
SERVICES=(hedgepoint-dashboard hedgepoint-monitor hedgepoint-scheduler)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
fail() { echo -e "${RED}✗${NC} $*" >&2; exit 1; }

echo "=============================="
echo " HedgePoint MX — Deploy"
echo " $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "=============================="
echo ""

# 1. Actualizar código
echo "[1/5] git pull..."
cd "$APP_DIR"
git pull origin master
ok "Código actualizado"

# 2. Dependencias
echo ""
echo "[2/5] Instalando dependencias..."
source "$APP_DIR/venv/bin/activate"
pip install -r requirements.txt --quiet
ok "Dependencias instaladas"

# 3. Tests
echo ""
echo "[3/5] Ejecutando tests..."
if ! python -m pytest tests/ -x -q; then
    echo ""
    fail "Tests fallaron — deploy abortado. Corrige los errores antes de reintentar."
fi
ok "Todos los tests pasaron"

# 4. Reiniciar servicios
echo ""
echo "[4/5] Reiniciando servicios..."
sudo systemctl restart "${SERVICES[@]}"
ok "Servicios reiniciados"

# 5. Verificar estado
echo ""
echo "[5/5] Verificando estado (espera 3s)..."
sleep 3

all_ok=true
for svc in "${SERVICES[@]}"; do
    status=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
    if [[ "$status" == "active" ]]; then
        ok "$svc → $status"
    else
        warn "$svc → $status"
        all_ok=false
    fi
done

# Resumen
echo ""
echo "=============================="
echo " Resumen del deploy"
echo "=============================="
echo "  Fecha:        $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  Último commit: $(git log -1 --format='%h %s')"
echo "  Autor:        $(git log -1 --format='%an')"
echo ""

if $all_ok; then
    ok "Deploy completado — todos los servicios activos"
else
    fail "Deploy completado con advertencias — revisa los servicios marcados con '!'"
fi
