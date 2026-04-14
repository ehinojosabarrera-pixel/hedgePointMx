"""
Monitor de mercado — HedgePoint MX.

Ciclo cada 15 minutos:
  1. Descarga USD/MXN desde Banxico y persiste en SQLite.
  2. Construye market_data con los 2 registros más recientes de cada activo.
  3. Evalúa los triggers definidos en config/triggers.yaml.
  4. Imprime los triggers activados y envía alerta por email (Gmail SMTP).

Al iniciar ejecuta un primer ciclo inmediato antes de esperar el primer intervalo.

Uso:
    python agents/monitor/monitor.py
    python agents/monitor/monitor.py --interval 5   # ciclos cada 5 minutos
"""

import sys
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

# Permite ejecutar desde la raíz del proyecto o directamente
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import yaml
import schedule
from dotenv import load_dotenv

from core.database import init_db, insert_fx_rate
from core.data.market_data import fetch_usdmxn_banxico
from agents.monitor.triggers import build_market_data_from_db, evaluate_triggers
from agents.monitor.notifier import send_alert_email, send_alert_whatsapp

load_dotenv()
init_db()

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "triggers.yaml"

# ---------------------------------------------------------------------------
# Logging con timestamps
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("hedgepoint.monitor")


def _load_notification_config() -> dict:
    """
    Lee la configuración de notificaciones desde config/triggers.yaml.

    Returns dict con claves:
        recipients  — list[str]  emails destino
        whatsapp    — list[str]  números WhatsApp destino
        canales     — list[str]  canales activos (default: ["email"])
    """
    try:
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        recipients = [str(r).strip() for r in (raw.get("recipients") or []) if str(r).strip()]
        whatsapp = [str(n).strip() for n in (raw.get("whatsapp") or []) if str(n).strip()]
        raw_canales = raw.get("canales") or []
        canales = [str(c).strip() for c in raw_canales] if raw_canales else ["email"]
        return {"recipients": recipients, "whatsapp": whatsapp, "canales": canales}
    except Exception as exc:
        logger.warning("[CONFIG] No se pudo cargar configuración de notificaciones: %s", exc)
        return {"recipients": [], "whatsapp": [], "canales": ["email"]}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def _fetch_and_store_fx() -> bool:
    """
    Descarga el tipo de cambio USD/MXN desde Banxico y lo persiste en SQLite.

    Returns:
        True si la inserción fue exitosa, False en caso de error.
    """
    try:
        df = fetch_usdmxn_banxico(days=7)
        if df.empty:
            logger.warning("[FX] Banxico devolvió DataFrame vacío — sin inserción")
            return False

        latest = df.iloc[-1]
        fecha_str = latest["fecha"].strftime("%Y-%m-%d")
        tasa = float(latest["tipo_cambio"])
        hora_str = datetime.now().strftime("%H:%M:%S")

        insert_fx_rate(
            fecha=fecha_str,
            hora=hora_str,
            par="USDMXN",
            bid=tasa,
            ask=round(tasa + 0.02, 4),
            source="Banxico",
        )
        logger.info("[FX] USDMXN bid=%.4f  ask=%.4f  fecha=%s  [OK]",
                    tasa, tasa + 0.02, fecha_str)
        return True

    except Exception as exc:
        logger.error("[FX] Error al obtener/persistir datos: %s", exc, exc_info=True)
        return False


def _check_triggers() -> None:
    """
    Construye market_data desde SQLite, evalúa triggers y reporta los activados.
    """
    try:
        market_data = build_market_data_from_db(
            fx_pairs=["USDMXN"],
            commodity_symbols=["WTI"],
        )
    except Exception as exc:
        logger.error("[TRIGGERS] Error al construir market_data: %s", exc, exc_info=True)
        return

    try:
        fired = evaluate_triggers(market_data)
    except Exception as exc:
        logger.error("[TRIGGERS] Error al evaluar triggers: %s", exc, exc_info=True)
        return

    if not fired:
        logger.info("[TRIGGERS] Ningún trigger activado en este ciclo.")
        return

    logger.warning("[TRIGGERS] %d trigger(s) activado(s):", len(fired))
    for result in fired:
        logger.warning("  *** %s", result.message)

    notif = _load_notification_config()

    # Determinar canales efectivos por trigger:
    # si el trigger define canales propios los usa; si no, hereda el global.
    def _canales(ft) -> list[str]:
        return ft.trigger.canales if ft.trigger.canales is not None else notif["canales"]

    fired_email = [ft for ft in fired if "email" in _canales(ft)]
    fired_whatsapp = [ft for ft in fired if "whatsapp" in _canales(ft)]

    if fired_email:
        send_alert_email(fired_email, recipients=notif["recipients"])

    if fired_whatsapp:
        # Combinar números globales con números específicos de cada trigger
        wa_numbers: list[str] = list(notif["whatsapp"])
        for ft in fired_whatsapp:
            if ft.trigger.whatsapp_numbers:
                for num in ft.trigger.whatsapp_numbers:
                    if num not in wa_numbers:
                        wa_numbers.append(num)
        send_alert_whatsapp(fired_whatsapp, whatsapp_numbers=wa_numbers)


def run_cycle() -> None:
    """Ejecuta un ciclo completo: descarga FX → evalúa triggers."""
    logger.info("=" * 60)
    logger.info("Inicio de ciclo — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    _fetch_and_store_fx()
    _check_triggers()

    logger.info("Ciclo completado.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor de mercado HedgePoint MX")
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        metavar="MIN",
        help="Minutos entre ciclos (default: 15)",
    )
    args = parser.parse_args()

    logger.info("Monitor HedgePoint MX iniciado.")
    logger.info("Intervalo: cada %d minutos. Ctrl+C para detener.", args.interval)

    # Primer ciclo inmediato al arrancar
    run_cycle()

    schedule.every(args.interval).minutes.do(run_cycle)
    logger.info("Próximo ciclo en %d minutos.", args.interval)

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Monitor detenido por el usuario.")
        sys.exit(0)


if __name__ == "__main__":
    main()
