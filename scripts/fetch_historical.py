"""
Descarga el histórico de N años del tipo de cambio USD/MXN FIX desde la API SIE de Banxico
y lo almacena en la tabla fx_rates de la base de datos SQLite.

Serie: SF43718 — Tipo de Cambio FIX USD/MXN publicado por Banxico.

Uso:
    python scripts/fetch_historical.py
    python scripts/fetch_historical.py --years 2
    python scripts/fetch_historical.py --start 2022-01-01 --end 2024-01-01
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

# Asegurar que el root del proyecto esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import get_connection, init_db, DB_PATH

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BANXICO_BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1"
SERIE_USDMXN_FIX = "SF43718"
PAR = "USD/MXN"


def _fetch_banxico_rango(fecha_inicio: str, fecha_fin: str, api_key: str) -> list[dict]:
    """
    Descarga datos de la serie SF43718 para un rango de fechas.

    Args:
        fecha_inicio: Fecha en formato YYYY-MM-DD.
        fecha_fin: Fecha en formato YYYY-MM-DD.
        api_key: Token de la API de Banxico.

    Returns:
        Lista de dicts con claves 'fecha' (str DD/MM/YYYY) y 'dato' (str).

    Raises:
        ConnectionError: Si la API no responde.
        ValueError: Si la respuesta no tiene el formato esperado.
    """
    url = (
        f"{BANXICO_BASE_URL}/series/{SERIE_USDMXN_FIX}/datos/"
        f"{fecha_inicio}/{fecha_fin}"
    )
    headers = {"Bmx-Token": api_key}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(f"No se pudo conectar a Banxico: {e}") from e
    except requests.exceptions.Timeout:
        raise ConnectionError("Timeout al conectar con Banxico (>30s)")
    except requests.exceptions.HTTPError as e:
        raise ConnectionError(
            f"Error HTTP {e.response.status_code} de Banxico: {e.response.reason}"
        ) from e

    payload = response.json()

    try:
        datos = payload["bmx"]["series"][0]["datos"]
    except (KeyError, IndexError) as e:
        raise ValueError(
            f"Respuesta de Banxico con formato inesperado: {payload}"
        ) from e

    return datos or []


def _parsear_datos(datos: list[dict]) -> list[dict]:
    """
    Convierte los datos crudos de Banxico al formato requerido por insert_fx_rates_bulk.

    Args:
        datos: Lista de dicts con claves 'fecha' (DD/MM/YYYY) y 'dato'.

    Returns:
        Lista de dicts con claves: fecha, hora, par, bid, ask, source.
        Se omiten registros con valor 'N/E' (no estimado / festivos).
    """
    rows = []
    for item in datos:
        valor_str = item.get("dato", "N/E")
        if valor_str == "N/E":
            continue
        try:
            tc = float(valor_str)
        except (ValueError, TypeError):
            logger.warning("Valor no numérico ignorado: %s", valor_str)
            continue

        # Convertir DD/MM/YYYY → YYYY-MM-DD
        fecha_raw = item.get("fecha", "")
        try:
            fecha_dt = datetime.strptime(fecha_raw, "%d/%m/%Y")
            fecha = fecha_dt.strftime("%Y-%m-%d")
        except ValueError:
            logger.warning("Fecha con formato inesperado ignorada: %s", fecha_raw)
            continue

        rows.append({
            "fecha": fecha,
            "hora": "17:00:00",  # FIX se publica al cierre
            "par": PAR,
            "bid": tc,
            "ask": tc,
            "source": "Banxico-SIE",
        })

    return rows


def _fechas_ya_en_db(db_path: Path = DB_PATH) -> set[str]:
    """Retorna el conjunto de fechas (YYYY-MM-DD) ya almacenadas para USD/MXN."""
    sql = "SELECT fecha FROM fx_rates WHERE par = ?"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (PAR,)).fetchall()
    return {row["fecha"] for row in rows}


def _insertar_rows_nuevas(rows: list[dict], fechas_existentes: set[str],
                          db_path: Path = DB_PATH) -> int:
    """
    Filtra los rows que ya existen en la DB e inserta solo los nuevos.

    Returns:
        Cantidad de registros insertados.
    """
    nuevos = [r for r in rows if r["fecha"] not in fechas_existentes]
    if not nuevos:
        return 0

    sql = """
        INSERT INTO fx_rates (fecha, hora, par, bid, ask, source)
        VALUES (:fecha, :hora, :par, :bid, :ask, :source)
    """
    with get_connection(db_path) as conn:
        cur = conn.executemany(sql, nuevos)
        return cur.rowcount


def fetch_historico(
    years: int = 2,
    fecha_inicio: str | None = None,
    fecha_fin: str | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """
    Descarga el histórico de USD/MXN FIX de Banxico y lo almacena en SQLite.

    Args:
        years: Número de años hacia atrás desde hoy (ignorado si se pasan fechas).
        fecha_inicio: Fecha de inicio en formato YYYY-MM-DD (opcional).
        fecha_fin: Fecha de fin en formato YYYY-MM-DD (opcional).
        db_path: Ruta a la base de datos SQLite.

    Returns:
        Número de registros nuevos insertados.

    Raises:
        EnvironmentError: Si BANXICO_API_KEY no está configurada.
    """
    api_key = os.getenv("BANXICO_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "BANXICO_API_KEY no encontrada. Agrega el token al archivo .env."
        )

    # Inicializar tablas si no existen
    init_db(db_path)

    hoy = datetime.today()
    if fecha_fin is None:
        fecha_fin = hoy.strftime("%Y-%m-%d")
    if fecha_inicio is None:
        fecha_inicio = (hoy - timedelta(days=years * 365)).strftime("%Y-%m-%d")

    logger.info("Descargando USD/MXN FIX: %s → %s", fecha_inicio, fecha_fin)

    # Banxico limita rangos largos; descargamos en bloques anuales
    dt_inicio = datetime.strptime(fecha_inicio, "%Y-%m-%d")
    dt_fin = datetime.strptime(fecha_fin, "%Y-%m-%d")

    fechas_existentes = _fechas_ya_en_db(db_path)
    logger.info("Registros ya en DB para USD/MXN: %d", len(fechas_existentes))

    total_insertados = 0
    cursor = dt_inicio

    while cursor <= dt_fin:
        bloque_fin = min(cursor + timedelta(days=364), dt_fin)
        bloque_ini_str = cursor.strftime("%Y-%m-%d")
        bloque_fin_str = bloque_fin.strftime("%Y-%m-%d")

        logger.info("  Bloque: %s → %s", bloque_ini_str, bloque_fin_str)
        try:
            datos = _fetch_banxico_rango(bloque_ini_str, bloque_fin_str, api_key)
        except (ConnectionError, ValueError) as e:
            logger.error("  Error en bloque %s→%s: %s", bloque_ini_str, bloque_fin_str, e)
            cursor = bloque_fin + timedelta(days=1)
            continue

        rows = _parsear_datos(datos)
        insertados = _insertar_rows_nuevas(rows, fechas_existentes, db_path)
        # Actualizar set para bloques siguientes
        fechas_existentes.update(r["fecha"] for r in rows)

        logger.info(
            "  Registros en bloque: %d brutos / %d nuevos insertados",
            len(rows), insertados,
        )
        total_insertados += insertados
        cursor = bloque_fin + timedelta(days=1)

    logger.info("Total insertados: %d registros", total_insertados)
    return total_insertados


def verificar_cobertura(db_path: Path = DB_PATH) -> None:
    """Imprime un resumen de los datos históricos disponibles en la DB."""
    sql = """
        SELECT
            MIN(fecha) AS fecha_min,
            MAX(fecha) AS fecha_max,
            COUNT(*) AS total,
            MIN(bid) AS tc_min,
            MAX(bid) AS tc_max,
            AVG(bid) AS tc_promedio
        FROM fx_rates
        WHERE par = ?
    """
    with get_connection(db_path) as conn:
        row = conn.execute(sql, (PAR,)).fetchone()

    if row and row["total"]:
        print("\n--- Cobertura de datos USD/MXN FIX en SQLite ---")
        print(f"  Desde:    {row['fecha_min']}")
        print(f"  Hasta:    {row['fecha_max']}")
        print(f"  Registros: {row['total']:,}")
        print(f"  TC mínimo: {row['tc_min']:.4f}")
        print(f"  TC máximo: {row['tc_max']:.4f}")
        print(f"  TC promedio: {row['tc_promedio']:.4f}")
    else:
        print("No hay datos de USD/MXN en la base de datos.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Descarga histórico USD/MXN FIX de Banxico a SQLite"
    )
    parser.add_argument(
        "--years", type=int, default=2,
        help="Años hacia atrás desde hoy (default: 2)"
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="Fecha de inicio YYYY-MM-DD (sobreescribe --years)"
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="Fecha de fin YYYY-MM-DD (default: hoy)"
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Solo mostrar cobertura actual sin descargar"
    )

    args = parser.parse_args()

    if args.verify:
        verificar_cobertura()
        sys.exit(0)

    try:
        insertados = fetch_historico(
            years=args.years,
            fecha_inicio=args.start,
            fecha_fin=args.end,
        )
        print(f"\nDescarga completa: {insertados} registros nuevos insertados.")
        verificar_cobertura()
    except EnvironmentError as e:
        logger.error("%s", e)
        sys.exit(1)
