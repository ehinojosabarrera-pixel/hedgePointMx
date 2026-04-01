"""
Prueba de conectividad con Alpha Vantage — precios mensuales del WTI.

Uso:
    python tests/test_alpha_vantage.py
"""

import sys
from pathlib import Path

# Permite ejecutar el script desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv
import os

load_dotenv()

API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
URL = "https://www.alphavantage.co/query"


def fetch_wti_monthly(api_key: str, n: int = 3) -> None:
    if not api_key:
        print("ERROR: ALPHA_VANTAGE_API_KEY no encontrada en .env")
        sys.exit(1)

    params = {
        "function": "WTI",
        "interval": "monthly",
        "apikey": api_key,
    }

    print(f"Consultando Alpha Vantage — WTI mensual (últimos {n} registros)...")
    try:
        response = requests.get(URL, params=params, timeout=15)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        print(f"ERROR de conexión: {e}")
        sys.exit(1)
    except requests.exceptions.Timeout:
        print("ERROR: timeout al conectar con Alpha Vantage (>15s)")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR HTTP {response.status_code}: {e}")
        sys.exit(1)

    data = response.json()

    # Alpha Vantage devuelve un mensaje de error dentro del JSON con status 200
    if "Error Message" in data:
        print(f"ERROR de Alpha Vantage: {data['Error Message']}")
        sys.exit(1)
    if "Note" in data:
        print(f"AVISO (límite de llamadas): {data['Note']}")
        sys.exit(1)
    if "Information" in data:
        print(f"AVISO: {data['Information']}")
        sys.exit(1)

    series = data.get("data", [])
    if not series:
        print("ERROR: respuesta inesperada — no se encontró la clave 'data'")
        print("Respuesta completa:", data)
        sys.exit(1)

    print(f"\n{'Fecha':<12}  {'Precio WTI (USD/bbl)':>20}")
    print("-" * 36)
    for entry in series[:n]:
        fecha = entry.get("date", "N/A")
        precio = entry.get("value", "N/A")
        print(f"{fecha:<12}  {precio:>20}")


if __name__ == "__main__":
    fetch_wti_monthly(API_KEY, n=3)
