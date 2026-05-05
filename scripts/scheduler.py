"""
Scheduler de datos de mercado — HedgePoint MX.

Ejecuta cada hora:
  - fetch_usdmxn_banxico()  →  inserta el tipo de cambio más reciente en SQLite
  - get_all_commodities()   →  inserta WTI en SQLite

Al iniciar corre una descarga inmediata antes de esperar el primer ciclo.

Uso:
    python scripts/scheduler.py
"""

import sys
import time
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import schedule
from dotenv import load_dotenv

from core.database import init_db, insert_fx_rate, insert_interest_rate
from core.data.market_data import fetch_usdmxn_banxico, fetch_tiie_banxico, fetch_sofr_fred
from core.market_data import get_all_commodities

load_dotenv()
init_db()

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def job_fx() -> None:
    """Descarga USD/MXN desde Banxico e inserta el dato más reciente en SQLite."""
    try:
        df = fetch_usdmxn_banxico(days=7)
        if df.empty:
            logger.warning("[FX] Banxico devolvio DataFrame vacio")
            return

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
        logger.info("[FX]  USDMXN bid=%.4f  fecha=%s  [OK]", tasa, fecha_str)

    except Exception as e:
        logger.error("[FX]  ERROR — %s", e)


def job_commodities() -> None:
    """Descarga WTI desde Alpha Vantage e inserta en SQLite."""
    try:
        results = get_all_commodities()
        for r in results:
            if "error" in r:
                logger.error("[COMM]  %s  ERROR — %s", r["symbol"], r["error"])
            else:
                logger.info(
                    "[COMM]  %s price=%.4f  fecha=%s  [OK]",
                    r["symbol"], r["price"], r["fecha"],
                )
    except Exception as e:
        logger.error("[COMM]  ERROR — %s", e)


def job_interest_rates() -> None:
    """Descarga TIIE 28d (Banxico) y SOFR (FRED) e inserta en SQLite."""
    now = datetime.now()
    fecha_str = now.strftime("%Y-%m-%d")
    hora_str = now.strftime("%H:%M:%S")

    try:
        tiie = fetch_tiie_banxico(days=7)
        insert_interest_rate(fecha_str, hora_str, "TIIE28D", tiie, "Banxico")
        logger.info("[IR]  TIIE28D rate=%.4f  [OK]", tiie)
    except Exception as e:
        logger.error("[IR]  TIIE28D ERROR — %s", e)

    try:
        sofr = fetch_sofr_fred()
        insert_interest_rate(fecha_str, hora_str, "SOFR", sofr, "FRED")
        logger.info("[IR]  SOFR rate=%.4f  [OK]", sofr)
    except Exception as e:
        logger.error("[IR]  SOFR ERROR — %s", e)


def run_all() -> None:
    """Ejecuta todos los jobs en secuencia."""
    logger.info("--- Inicio de ciclo de descarga ---")
    job_fx()
    job_commodities()
    job_interest_rates()
    logger.info("--- Ciclo completado ---")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    logger.info("Scheduler HedgePoint MX iniciado.")
    logger.info("Frecuencia: cada hora. Ctrl+C para detener.")

    # Descarga inmediata al arrancar
    run_all()

    # Programa el job cada hora a partir de ahora
    schedule.every(1).hours.do(run_all)
    logger.info("Proxima ejecucion programada en ~60 minutos.")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("Scheduler detenido por el usuario.")
        sys.exit(0)


if __name__ == "__main__":
    main()
