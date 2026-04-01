"""
Conexiones a APIs de datos de mercado.

Incluira:
- API de Banxico (tipo de cambio USD/MXN, TIIE)
- Alpha Vantage (commodities: petroleo WTI, oro, acero)
- Almacenamiento en SQLite/PostgreSQL

Se construye en Sprint 1.
"""

import os
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

BANXICO_BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1"
SERIE_USDMXN_FIX = "SF43718"


def fetch_usdmxn_banxico(days: int = 30) -> pd.DataFrame:
    """
    Descarga el tipo de cambio FIX USD/MXN de los últimos `days` días desde Banxico.

    Returns:
        DataFrame con columnas ['fecha', 'tipo_cambio'].

    Raises:
        EnvironmentError: si BANXICO_API_KEY no está configurada.
        ConnectionError: si la API no responde o devuelve un error HTTP.
        ValueError: si la respuesta no contiene datos válidos.
    """
    api_key = os.getenv("BANXICO_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "BANXICO_API_KEY no encontrada. Agrega el token al archivo .env."
        )

    fecha_fin = datetime.today()
    fecha_inicio = fecha_fin - timedelta(days=days)
    fmt = "%Y-%m-%d"

    url = (
        f"{BANXICO_BASE_URL}/series/{SERIE_USDMXN_FIX}/datos/"
        f"{fecha_inicio.strftime(fmt)}/{fecha_fin.strftime(fmt)}"
    )
    headers = {"Bmx-Token": api_key}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "No se pudo conectar a la API de Banxico. Verifica tu conexión a internet."
        )
    except requests.exceptions.Timeout:
        raise ConnectionError(
            "La API de Banxico no respondió a tiempo (timeout de 10s)."
        )
    except requests.exceptions.HTTPError as e:
        raise ConnectionError(
            f"Error HTTP al consultar Banxico: {e.response.status_code} {e.response.reason}"
        )

    payload = response.json()

    try:
        datos = payload["bmx"]["series"][0]["datos"]
    except (KeyError, IndexError):
        raise ValueError(
            "La respuesta de Banxico no tiene el formato esperado. "
            f"Respuesta recibida: {payload}"
        )

    if not datos:
        raise ValueError(
            f"Banxico no devolvió datos para el período {fecha_inicio.strftime(fmt)} "
            f"a {fecha_fin.strftime(fmt)}."
        )

    df = pd.DataFrame(datos)
    df.rename(columns={"fecha": "fecha", "dato": "tipo_cambio"}, inplace=True)
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y")
    df["tipo_cambio"] = pd.to_numeric(df["tipo_cambio"], errors="coerce")
    df.dropna(subset=["tipo_cambio"], inplace=True)
    df.sort_values("fecha", inplace=True)
    df.reset_index(drop=True, inplace=True)

    return df


def plot_usdmxn(df: pd.DataFrame) -> None:
    """Grafica el tipo de cambio USD/MXN contenido en el DataFrame."""
    fig, ax = plt.subplots(figsize=(12, 5))

    ax.plot(
        df["fecha"],
        df["tipo_cambio"],
        color="#1a6b3c",
        linewidth=2,
        marker="o",
        markersize=3,
        label="USD/MXN FIX",
    )

    # Banda de referencia (min/max del período)
    ax.fill_between(
        df["fecha"],
        df["tipo_cambio"].min(),
        df["tipo_cambio"].max(),
        alpha=0.08,
        color="#1a6b3c",
    )

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    fig.autofmt_xdate()

    ultimo_valor = df["tipo_cambio"].iloc[-1]
    ultima_fecha = df["fecha"].iloc[-1].strftime("%d/%m/%Y")
    ax.annotate(
        f"${ultimo_valor:.4f}",
        xy=(df["fecha"].iloc[-1], ultimo_valor),
        xytext=(8, 4),
        textcoords="offset points",
        fontsize=9,
        color="#1a6b3c",
        fontweight="bold",
    )

    ax.set_title(
        f"Tipo de Cambio USD/MXN FIX — Banxico\nÚltimo: ${ultimo_valor:.4f}  ({ultima_fecha})",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Fecha")
    ax.set_ylabel("Pesos mexicanos por dólar")
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    print("Descargando tipo de cambio USD/MXN de Banxico (últimos 30 días)...")
    try:
        df = fetch_usdmxn_banxico(days=30)
        print(f"  {len(df)} observaciones obtenidas.")
        print(df.tail(5).to_string(index=False))
        plot_usdmxn(df)
    except (EnvironmentError, ConnectionError, ValueError) as e:
        print(f"[ERROR] {e}")
