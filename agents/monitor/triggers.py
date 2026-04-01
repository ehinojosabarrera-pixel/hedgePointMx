"""
Sistema de triggers configurables para HedgePoint MX.

Cada trigger define una condición sobre datos de mercado (FX o commodity).
Los triggers se cargan desde config/triggers.yaml.

Tipos soportados:
    price_above       — precio/ask supera el umbral
    price_below       — precio/ask cae por debajo del umbral
    volatility_above  — spread bid/ask (%) supera el umbral
    daily_change_pct  — variación porcentual respecto al registro anterior
                        supera el umbral en valor absoluto

Uso básico:
    from agents.monitor.triggers import load_triggers, evaluate_triggers

    triggers = load_triggers()                    # carga desde config/triggers.yaml
    fired = evaluate_triggers(market_data)        # evalúa todos los triggers activos
    for result in fired:
        print(result["message"])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Ruta por defecto al archivo de configuración
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "triggers.yaml"


# ---------------------------------------------------------------------------
# Tipos y modelos
# ---------------------------------------------------------------------------

class TriggerType(str, Enum):
    PRICE_ABOVE = "price_above"
    PRICE_BELOW = "price_below"
    VOLATILITY_ABOVE = "volatility_above"
    DAILY_CHANGE_PCT = "daily_change_pct"


@dataclass
class Trigger:
    """Definición de un trigger de mercado."""

    name: str
    trigger_type: TriggerType
    symbol: str          # par FX (ej: USDMXN) o símbolo commodity (ej: WTI)
    threshold: float
    active: bool = True
    description: str = ""

    # Campo calculado al activarse — no forma parte de la config YAML
    _fired_value: float | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trigger":
        """Construye un Trigger desde un dict (entrada YAML)."""
        raw_type = data.get("type", "")
        try:
            t_type = TriggerType(raw_type)
        except ValueError:
            raise ValueError(
                f"Tipo de trigger desconocido: '{raw_type}'. "
                f"Opciones válidas: {[t.value for t in TriggerType]}"
            )

        return cls(
            name=data["name"],
            trigger_type=t_type,
            symbol=str(data["symbol"]).upper(),
            threshold=float(data["threshold"]),
            active=bool(data.get("active", True)),
            description=data.get("description", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.trigger_type.value,
            "symbol": self.symbol,
            "threshold": self.threshold,
            "active": self.active,
            "description": self.description,
        }


@dataclass
class FiredTrigger:
    """Resultado de un trigger activado."""

    trigger: Trigger
    observed_value: float
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.trigger.name,
            "type": self.trigger.trigger_type.value,
            "symbol": self.trigger.symbol,
            "threshold": self.trigger.threshold,
            "observed_value": self.observed_value,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Carga de configuración
# ---------------------------------------------------------------------------

def load_triggers(config_path: Path = _CONFIG_PATH) -> list[Trigger]:
    """
    Lee config/triggers.yaml y retorna la lista de Trigger definidos.

    El YAML debe tener la clave raíz ``triggers`` con una lista de objetos.
    Los triggers con ``active: false`` se incluyen pero no se evaluarán.

    Raises:
        FileNotFoundError: si el archivo no existe.
        ValueError: si algún trigger tiene campos inválidos.
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Archivo de triggers no encontrado: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    raw_triggers = raw.get("triggers", [])
    if not isinstance(raw_triggers, list):
        raise ValueError("El archivo YAML debe contener una clave 'triggers' con una lista.")

    triggers: list[Trigger] = []
    for i, item in enumerate(raw_triggers):
        try:
            triggers.append(Trigger.from_dict(item))
        except (KeyError, ValueError) as exc:
            logger.warning("Trigger #%d inválido, se omite: %s", i, exc)

    logger.debug("Cargados %d triggers desde %s", len(triggers), path)
    return triggers


# ---------------------------------------------------------------------------
# Evaluación
# ---------------------------------------------------------------------------

def evaluate_triggers(
    market_data: dict[str, Any],
    triggers: list[Trigger] | None = None,
    config_path: Path = _CONFIG_PATH,
) -> list[FiredTrigger]:
    """
    Evalúa los triggers activos contra los datos de mercado actuales.

    Parameters
    ----------
    market_data:
        Diccionario con la snapshot actual del mercado.  Estructura esperada::

            {
                "fx": {
                    "USDMXN": {
                        "bid": 20.12,
                        "ask": 20.14,
                        "prev_ask": 20.05,   # opcional — registro anterior
                        "prev_bid": 20.03,   # opcional
                    },
                    ...
                },
                "commodities": {
                    "WTI": {
                        "price": 78.50,
                        "prev_price": 79.10,  # opcional
                    },
                    ...
                },
            }

    triggers:
        Lista de Trigger a evaluar.  Si es None, se cargan desde config_path.

    config_path:
        Ruta al YAML de triggers (solo se usa cuando triggers es None).

    Returns
    -------
    list[FiredTrigger]
        Triggers activados, en el mismo orden en que aparecen en la config.
    """
    if triggers is None:
        triggers = load_triggers(config_path)

    fired: list[FiredTrigger] = []

    for t in triggers:
        if not t.active:
            continue

        result = _evaluate_single(t, market_data)
        if result is not None:
            fired.append(result)

    return fired


# ---------------------------------------------------------------------------
# Lógica interna por tipo
# ---------------------------------------------------------------------------

def _evaluate_single(
    trigger: Trigger, market_data: dict[str, Any]
) -> FiredTrigger | None:
    """Evalúa un único trigger. Retorna FiredTrigger si se activa, None si no."""

    symbol = trigger.symbol
    t_type = trigger.trigger_type

    # Obtener datos del símbolo según si es FX o commodity
    fx_entry = (market_data.get("fx") or {}).get(symbol)
    comm_entry = (market_data.get("commodities") or {}).get(symbol)

    try:
        if t_type == TriggerType.PRICE_ABOVE:
            return _check_price_above(trigger, fx_entry, comm_entry)

        elif t_type == TriggerType.PRICE_BELOW:
            return _check_price_below(trigger, fx_entry, comm_entry)

        elif t_type == TriggerType.VOLATILITY_ABOVE:
            return _check_volatility(trigger, fx_entry, comm_entry)

        elif t_type == TriggerType.DAILY_CHANGE_PCT:
            return _check_daily_change(trigger, fx_entry, comm_entry)

    except Exception as exc:  # noqa: BLE001
        logger.warning("Error evaluando trigger '%s': %s", trigger.name, exc)

    return None


def _reference_price(fx_entry: dict | None, comm_entry: dict | None) -> float:
    """Extrae el precio de referencia: ask para FX, price para commodities."""
    if fx_entry is not None:
        return float(fx_entry["ask"])
    if comm_entry is not None:
        return float(comm_entry["price"])
    raise ValueError("No hay datos para el símbolo solicitado")


def _check_price_above(
    t: Trigger, fx: dict | None, comm: dict | None
) -> FiredTrigger | None:
    price = _reference_price(fx, comm)
    if price > t.threshold:
        asset_label = "USD/MXN ask" if fx else t.symbol
        msg = (
            f"[{t.name}] {asset_label} = {price:.4f} "
            f"supera el umbral de {t.threshold:.4f}"
        )
        if t.description:
            msg += f" — {t.description}"
        return FiredTrigger(trigger=t, observed_value=price, message=msg)
    return None


def _check_price_below(
    t: Trigger, fx: dict | None, comm: dict | None
) -> FiredTrigger | None:
    price = _reference_price(fx, comm)
    if price < t.threshold:
        asset_label = "USD/MXN ask" if fx else t.symbol
        msg = (
            f"[{t.name}] {asset_label} = {price:.4f} "
            f"cae por debajo del umbral de {t.threshold:.4f}"
        )
        if t.description:
            msg += f" — {t.description}"
        return FiredTrigger(trigger=t, observed_value=price, message=msg)
    return None


def _check_volatility(
    t: Trigger, fx: dict | None, comm: dict | None
) -> FiredTrigger | None:
    """Spread bid/ask como % del mid para FX; no aplica a commodities (sin bid/ask)."""
    if fx is None:
        logger.debug(
            "Trigger '%s' tipo volatility_above requiere datos FX; '%s' no encontrado.",
            t.name, t.symbol,
        )
        return None

    bid = float(fx["bid"])
    ask = float(fx["ask"])
    if bid <= 0:
        return None

    mid = (bid + ask) / 2.0
    spread_pct = (ask - bid) / mid * 100.0

    if spread_pct > t.threshold:
        msg = (
            f"[{t.name}] Spread bid/ask de {t.symbol} = {spread_pct:.4f}% "
            f"supera umbral de {t.threshold:.4f}%"
        )
        if t.description:
            msg += f" — {t.description}"
        return FiredTrigger(trigger=t, observed_value=spread_pct, message=msg)
    return None


def _check_daily_change(
    t: Trigger, fx: dict | None, comm: dict | None
) -> FiredTrigger | None:
    """Variación porcentual respecto al registro anterior (valor absoluto)."""
    if fx is not None:
        current = float(fx["ask"])
        prev = fx.get("prev_ask")
    elif comm is not None:
        current = float(comm["price"])
        prev = comm.get("prev_price")
    else:
        raise ValueError("No hay datos para el símbolo solicitado")

    if prev is None:
        logger.debug(
            "Trigger '%s': no hay registro anterior disponible para '%s'.",
            t.name, t.symbol,
        )
        return None

    prev = float(prev)
    if prev == 0:
        return None

    change_pct = (current - prev) / prev * 100.0
    abs_change = abs(change_pct)

    if abs_change > t.threshold:
        direction = "subió" if change_pct > 0 else "bajó"
        asset_label = "USD/MXN ask" if fx else t.symbol
        msg = (
            f"[{t.name}] {asset_label} {direction} {abs_change:.3f}% "
            f"(umbral: {t.threshold:.3f}%) — "
            f"anterior: {prev:.4f} → actual: {current:.4f}"
        )
        if t.description:
            msg += f" — {t.description}"
        return FiredTrigger(trigger=t, observed_value=change_pct, message=msg)
    return None


# ---------------------------------------------------------------------------
# Helper: construir market_data desde la base de datos
# ---------------------------------------------------------------------------

def build_market_data_from_db(
    fx_pairs: list[str] | None = None,
    commodity_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """
    Construye el diccionario market_data leyendo los 2 registros más recientes
    de cada par/símbolo desde SQLite.

    Útil para llamar a evaluate_triggers() sin necesidad de datos en tiempo real.

    Example::

        from agents.monitor.triggers import build_market_data_from_db, evaluate_triggers

        data = build_market_data_from_db(fx_pairs=["USDMXN"], commodity_symbols=["WTI"])
        fired = evaluate_triggers(data)
    """
    from core.database import get_latest_fx_rates, get_latest_commodities

    fx_pairs = fx_pairs or ["USDMXN"]
    commodity_symbols = commodity_symbols or ["WTI"]

    market_data: dict[str, Any] = {"fx": {}, "commodities": {}}

    for pair in fx_pairs:
        rows = get_latest_fx_rates(pair, n=2)
        if rows:
            entry: dict[str, Any] = {"bid": rows[0]["bid"], "ask": rows[0]["ask"]}
            if len(rows) >= 2:
                entry["prev_bid"] = rows[1]["bid"]
                entry["prev_ask"] = rows[1]["ask"]
            market_data["fx"][pair] = entry

    for sym in commodity_symbols:
        rows = get_latest_commodities(sym, n=2)
        if rows:
            entry = {"price": rows[0]["price"]}
            if len(rows) >= 2:
                entry["prev_price"] = rows[1]["price"]
            market_data["commodities"][sym] = entry

    return market_data
