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
SERIE_TIIE_28D = "SF43783"

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


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


def fetch_tiie_banxico(days: int = 7) -> float:
    """
    Descarga la TIIE 28 días más reciente de Banxico (serie SF43783).

    Returns:
        TIIE 28d como decimal (ej: 0.0702 para 7.02%).

    Raises:
        EnvironmentError: si BANXICO_API_KEY no está configurada.
        ConnectionError: si la API no responde.
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
        f"{BANXICO_BASE_URL}/series/{SERIE_TIIE_28D}/datos/"
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
            f"Error HTTP al consultar Banxico TIIE: {e.response.status_code} {e.response.reason}"
        )

    payload = response.json()

    try:
        datos = payload["bmx"]["series"][0]["datos"]
    except (KeyError, IndexError):
        raise ValueError(
            f"La respuesta de Banxico TIIE no tiene el formato esperado: {payload}"
        )

    if not datos:
        raise ValueError(
            f"Banxico no devolvió datos de TIIE para el período solicitado."
        )

    # Tomar el dato más reciente (el último de la lista, que ya viene ordenado)
    ultimo = datos[-1]
    try:
        valor_pct = float(ultimo["dato"])
    except (KeyError, ValueError):
        raise ValueError(f"No se pudo parsear el dato de TIIE: {ultimo}")

    return valor_pct / 100.0


def fetch_sofr_fred() -> float:
    """
    Descarga el SOFR más reciente desde la API de FRED (Federal Reserve de St. Louis).

    Requiere FRED_API_KEY en .env (gratis en https://fred.stlouisfed.org/docs/api/api_key.html).

    Returns:
        SOFR como decimal (ej: 0.0366 para 3.66%).

    Raises:
        EnvironmentError: si FRED_API_KEY no está configurada.
        ConnectionError: si la API no responde.
        ValueError: si la respuesta no contiene datos válidos.
    """
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "FRED_API_KEY no encontrada. Regístrate gratis en "
            "https://fred.stlouisfed.org/docs/api/api_key.html y agrégala al .env."
        )

    params = {
        "series_id": "SOFR",
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": "1",
    }

    try:
        response = requests.get(FRED_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "No se pudo conectar a la API de FRED. Verifica tu conexión a internet."
        )
    except requests.exceptions.Timeout:
        raise ConnectionError(
            "La API de FRED no respondió a tiempo (timeout de 10s)."
        )
    except requests.exceptions.HTTPError as e:
        raise ConnectionError(
            f"Error HTTP al consultar FRED SOFR: {e.response.status_code} {e.response.reason}"
        )

    payload = response.json()

    try:
        observations = payload["observations"]
    except KeyError:
        raise ValueError(f"La respuesta de FRED no tiene el formato esperado: {payload}")

    if not observations:
        raise ValueError("FRED no devolvió observaciones de SOFR.")

    try:
        valor_pct = float(observations[0]["value"])
    except (KeyError, ValueError, IndexError):
        raise ValueError(f"No se pudo parsear el valor de SOFR: {observations}")

    return valor_pct / 100.0


if __name__ == "__main__":
    print("Descargando tipo de cambio USD/MXN de Banxico (últimos 30 días)...")
    try:
        df = fetch_usdmxn_banxico(days=30)
        print(f"  {len(df)} observaciones obtenidas.")
        print(df.tail(5).to_string(index=False))
        plot_usdmxn(df)
    except (EnvironmentError, ConnectionError, ValueError) as e:
        print(f"[ERROR] {e}")
