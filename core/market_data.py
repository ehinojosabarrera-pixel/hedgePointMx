"""
Módulo de datos de mercado para HedgePoint MX.

Fuentes actuales:
- Banxico: tipo de cambio USD/MXN
- Alpha Vantage: commodities (WTI, NATURAL_GAS)
"""

import os
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from core.database import insert_commodity

load_dotenv()

logger = logging.getLogger(__name__)

# La variable en .env es ALPHA_VANTAGE_API_KEY
_AV_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY") or os.getenv("ALPHA_VANTAGE_KEY")
_AV_BASE_URL = "https://www.alphavantage.co/query"

# Símbolos soportados y su nombre legible
SUPPORTED_COMMODITIES = {
    "WTI": "Crude Oil WTI",
    "NATURAL_GAS": "Henry Hub Natural Gas",
}

# Segundos de espera entre requests para respetar el límite de 5 llamadas/minuto
_RATE_LIMIT_WAIT = 15


def get_commodity_price(symbol: str) -> dict:
    """
    Obtiene el precio diario más reciente de un commodity desde Alpha Vantage.

    Parámetros
    ----------
    symbol : str
        'WTI' o 'NATURAL_GAS'

    Retorna
    -------
    dict con claves: fecha, hora, symbol, price, source

    Lanza
    -----
    ValueError  – símbolo no soportado o API key ausente
    RuntimeError – error de red, HTTP, o respuesta inesperada de Alpha Vantage
    """
    symbol = symbol.upper()
    if symbol not in SUPPORTED_COMMODITIES:
        raise ValueError(
            f"Símbolo '{symbol}' no soportado. Usa: {list(SUPPORTED_COMMODITIES)}"
        )

    if not _AV_API_KEY:
        raise ValueError(
            "API key de Alpha Vantage no encontrada. "
            "Define ALPHA_VANTAGE_API_KEY en tu archivo .env"
        )

    params = {
        "function": symbol,
        "interval": "daily",
        "apikey": _AV_API_KEY,
    }

    logger.info("Consultando Alpha Vantage: %s (daily)", symbol)
    try:
        response = requests.get(_AV_BASE_URL, params=params, timeout=15)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Error de conexión con Alpha Vantage: {e}") from e
    except requests.exceptions.Timeout:
        raise RuntimeError("Timeout al conectar con Alpha Vantage (>15s)")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(
            f"Error HTTP {response.status_code} en Alpha Vantage: {e}"
        ) from e

    data = response.json()

    # Alpha Vantage señala errores dentro del JSON con status 200
    if "Error Message" in data:
        raise RuntimeError(f"Alpha Vantage error: {data['Error Message']}")
    if "Note" in data:
        raise RuntimeError(
            f"Límite de llamadas Alpha Vantage alcanzado: {data['Note']}"
        )
    if "Information" in data:
        raise RuntimeError(f"Alpha Vantage aviso: {data['Information']}")

    series = data.get("data", [])
    if not series:
        raise RuntimeError(
            f"Respuesta inesperada de Alpha Vantage para '{symbol}': "
            f"no se encontró la clave 'data'. Respuesta: {data}"
        )

    latest = series[0]
    fecha = latest.get("date", "")
    precio_str = latest.get("value", "")

    try:
        price = float(precio_str)
    except (ValueError, TypeError) as e:
        raise RuntimeError(
            f"No se pudo convertir el precio '{precio_str}' a float: {e}"
        ) from e

    hora = datetime.now().strftime("%H:%M:%S")
    source = "AlphaVantage"

    result = {
        "fecha": fecha,
        "hora": hora,
        "symbol": symbol,
        "price": price,
        "source": source,
    }

    insert_commodity(
        fecha=fecha,
        hora=hora,
        symbol=symbol,
        price=price,
        source=source,
    )
    logger.info("Guardado en BD: %s = %.4f (%s)", symbol, price, fecha)

    return result


def get_all_commodities() -> list[dict]:
    """
    Obtiene el precio de WTI desde Alpha Vantage.

    Retorna
    -------
    Lista con un dict (WTI). Si falla, incluye {"symbol": "WTI", "error": ...}.
    """
    symbols = ["WTI"]
    results = []

    for i, symbol in enumerate(symbols):
        if i > 0:
            logger.info(
                "Esperando %ds para respetar rate limit de Alpha Vantage...",
                _RATE_LIMIT_WAIT,
            )
            time.sleep(_RATE_LIMIT_WAIT)

        try:
            result = get_commodity_price(symbol)
            results.append(result)
            logger.info("%s: $%.4f (%s)", symbol, result["price"], result["fecha"])
        except (ValueError, RuntimeError) as e:
            logger.error("Error obteniendo %s: %s", symbol, e)
            results.append({"symbol": symbol, "error": str(e)})

    return results
