#!/usr/bin/env bash
set -euo pipefail

# ── Configuración ──────────────────────────────────────────────
APP_DIR="/home/hedgepoint/app"
DB_PATH="$APP_DIR/data/hedgepoint.db"
BACKUP_DIR="/home/hedgepoint/backups"
LOG_FILE="/var/log/hedgepoint-backup.log"
RETAIN_DAYS=7
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_NAME="hedgepoint_${TIMESTAMP}.db"
TMP_DB="/tmp/${BACKUP_NAME}"
GZ_PATH="${BACKUP_DIR}/${BACKUP_NAME}.gz"

# Cargar .env si existe (para BACKUP_REMOTE)
if [[ -f "$APP_DIR/.env" ]]; then
    set -o allexport
    # shellcheck disable=SC1091
    source "$APP_DIR/.env"
    set +o allexport
fi

# ── Helpers ────────────────────────────────────────────────────
log() {
    local level="$1"; shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $*" | tee -a "$LOG_FILE"
}

fail() {
    log "ERROR" "$*"
    exit 1
}

# ── 1. Preparar directorio de backups ──────────────────────────
mkdir -p "$BACKUP_DIR"

log "INFO" "==============================="
log "INFO" "Inicio de backup — $TIMESTAMP"

# ── 2. Backup en caliente (sin detener servicios) ──────────────
log "INFO" "Creando snapshot de la base de datos..."
sqlite3 "$DB_PATH" ".backup $TMP_DB" \
    || fail "sqlite3 .backup falló"
log "INFO" "Snapshot creado en $TMP_DB"

# ── 3. Comprimir con gzip ──────────────────────────────────────
log "INFO" "Comprimiendo → $GZ_PATH"
gzip -c "$TMP_DB" > "$GZ_PATH" \
    || fail "Compresión gzip falló"
rm -f "$TMP_DB"

SIZE=$(du -sh "$GZ_PATH" | cut -f1)
log "INFO" "Backup local listo: $GZ_PATH ($SIZE)"

# ── 4. Subir a remoto si BACKUP_REMOTE está definido ──────────
if [[ -n "${BACKUP_REMOTE:-}" ]]; then
    if command -v rclone &>/dev/null; then
        log "INFO" "Subiendo a remoto: $BACKUP_REMOTE"
        rclone copy "$GZ_PATH" "$BACKUP_REMOTE" \
            && log "INFO" "Subida completada" \
            || log "WARN" "rclone falló — backup local conservado"
    else
        log "WARN" "BACKUP_REMOTE definido pero rclone no está instalado (omitiendo subida)"
    fi
else
    log "INFO" "BACKUP_REMOTE no definido — solo backup local"
fi

# ── 5. Purgar backups locales con más de 7 días ────────────────
log "INFO" "Purgando backups con más de $RETAIN_DAYS días..."
DELETED=$(find "$BACKUP_DIR" -name "hedgepoint_*.db.gz" \
    -mtime "+$RETAIN_DAYS" -print -delete | wc -l)
log "INFO" "Archivos eliminados: $DELETED"

# ── 6. Resumen ─────────────────────────────────────────────────
TOTAL=$(find "$BACKUP_DIR" -name "hedgepoint_*.db.gz" | wc -l)
log "INFO" "Backup completado. Archivos locales retenidos: $TOTAL"
log "INFO" "==============================="

# ──────────────────────────────────────────────────────────────
# CRON — agregar con: crontab -e (usuario hedgepoint)
#
#   0 3 * * * /home/hedgepoint/app/deploy/backup.sh
#
# O como root para escribir en /var/log/:
#   0 3 * * * /home/hedgepoint/app/deploy/backup.sh 2>&1
#
# Verificar último backup:
#   tail -20 /var/log/hedgepoint-backup.log
# ──────────────────────────────────────────────────────────────
