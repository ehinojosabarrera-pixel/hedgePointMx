"""
Cálculo de P&L mark-to-market para coberturas activas — HedgePoint MX.

Usa las funciones de pricing existentes (calcular_forward, calcular_opcion_gk)
y las consultas de BD (get_active_hedges, get_client_hedges) para producir
valoraciones en tiempo real de cada cobertura.

Funciones principales:
    calcular_pnl_hedge        — P&L de una cobertura individual
    calcular_pnl_cliente      — P&L de todas las coberturas activas de un cliente
    resumen_pnl_cliente       — Resumen agregado con exposición residual
    calcular_pnl_todos_clientes — Resumen por cada cliente con coberturas activas
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from core.database import get_active_hedges, get_client_hedges, DB_PATH


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass
class HedgePnL:
    """Mark-to-market de una cobertura activa."""
    hedge_id: int
    prospect_id: int
    tipo: str
    monto_usd: float
    strike: float
    spot_actual: float
    mtm_mxn: float
    pnl_vs_spot_mxn: float
    dias_restantes: int
    exposicion_residual_usd: Optional[float]
    estado: str


# ---------------------------------------------------------------------------
# Cálculo individual
# ---------------------------------------------------------------------------

def calcular_pnl_hedge(
    hedge: dict,
    spot_actual: float,
    exposicion_total_usd: Optional[float] = None,
) -> HedgePnL:
    """Calcula el P&L mark-to-market de una cobertura.

    Para cada tipo de instrumento:

    - **forward**: mtm = (tasa_forward - spot_actual) * monto_usd
      Si strike < spot_actual la cobertura está in-the-money (el cliente
      vende USD más caro de lo que valdría hoy en el mercado).

    - **put**: mtm = max(strike - spot_actual, 0) * monto_usd - prima_pagada_mxn

    - **call**: mtm = max(spot_actual - strike, 0) * monto_usd - prima_pagada_mxn

    - **collar**: put comprado + call vendido.
      mtm = max(strike - spot_actual, 0) * monto_usd
            - max(spot_actual - strike_call, 0) * monto_usd
            - prima_pagada_mxn

    pnl_vs_spot_mxn mide cuánto gana/pierde el cliente vs no haberse cubierto
    (i.e., comprar/vender al spot actual):

    - forward / put / collar: (strike - spot_actual) * monto_usd
      (positivo = el cliente vendió más caro que el spot actual)
    - call: (spot_actual - strike) * monto_usd

    Args:
        hedge: Fila de la tabla ``hedges`` como dict (devuelta por get_hedge, etc.).
        spot_actual: Tipo de cambio spot USD/MXN en el momento del cálculo.
        exposicion_total_usd: Exposición total del cliente en USD.  Si se
            proporciona, se calcula ``exposicion_residual_usd``; si no, queda None.

    Returns:
        HedgePnL con todos los campos calculados.

    Raises:
        ValueError: Si el tipo de cobertura no es reconocido o faltan campos.
    """
    tipo = hedge["tipo"]
    monto = hedge["monto_usd"]
    strike = hedge["strike"]
    prima = hedge.get("prima_pagada_mxn") or 0.0
    fecha_venc_str = hedge["fecha_vencimiento"]

    # Días restantes
    fecha_venc = date.fromisoformat(fecha_venc_str)
    dias_restantes = max((fecha_venc - date.today()).days, 0)

    # MTM según tipo de instrumento
    if tipo == "forward":
        tasa_fwd = hedge.get("tasa_forward") or strike
        mtm_mxn = (tasa_fwd - spot_actual) * monto
        pnl_vs_spot_mxn = (strike - spot_actual) * monto

    elif tipo == "put":
        mtm_mxn = max(strike - spot_actual, 0.0) * monto - prima
        pnl_vs_spot_mxn = (strike - spot_actual) * monto

    elif tipo == "call":
        mtm_mxn = max(spot_actual - strike, 0.0) * monto - prima
        pnl_vs_spot_mxn = (spot_actual - strike) * monto

    elif tipo == "collar":
        strike_call = hedge.get("strike_call")
        if strike_call is None:
            raise ValueError(f"Collar hedge_id={hedge['id']} no tiene strike_call.")
        put_value = max(strike - spot_actual, 0.0) * monto
        call_cost = max(spot_actual - strike_call, 0.0) * monto
        mtm_mxn = put_value - call_cost - prima
        pnl_vs_spot_mxn = (strike - spot_actual) * monto

    else:
        raise ValueError(f"Tipo de cobertura desconocido: '{tipo}'")

    # Exposición residual (monto sin cubrir)
    exposicion_residual: Optional[float] = None
    if exposicion_total_usd is not None:
        exposicion_residual = max(exposicion_total_usd - monto, 0.0)

    return HedgePnL(
        hedge_id=hedge["id"],
        prospect_id=hedge["prospect_id"],
        tipo=tipo,
        monto_usd=monto,
        strike=strike,
        spot_actual=spot_actual,
        mtm_mxn=mtm_mxn,
        pnl_vs_spot_mxn=pnl_vs_spot_mxn,
        dias_restantes=dias_restantes,
        exposicion_residual_usd=exposicion_residual,
        estado=hedge["estado"],
    )


# ---------------------------------------------------------------------------
# Cálculo por cliente
# ---------------------------------------------------------------------------

def calcular_pnl_cliente(
    prospect_id: int,
    spot_actual: float,
    db_path: Path = DB_PATH,
) -> list[HedgePnL]:
    """Calcula el P&L mark-to-market de todas las coberturas activas de un cliente.

    Args:
        prospect_id: ID del prospecto/cliente.
        spot_actual: Tipo de cambio spot USD/MXN actual.
        db_path: Ruta a la base de datos.

    Returns:
        Lista de HedgePnL, una por cobertura activa.
    """
    hedges = get_client_hedges(prospect_id, estado="activa", db_path=db_path)
    return [calcular_pnl_hedge(h, spot_actual) for h in hedges]


# ---------------------------------------------------------------------------
# Resumen por cliente
# ---------------------------------------------------------------------------

def resumen_pnl_cliente(
    prospect_id: int,
    spot_actual: float,
    exposicion_total_usd: Optional[float] = None,
    db_path: Path = DB_PATH,
) -> dict:
    """Resumen agregado de P&L mark-to-market para un cliente.

    Args:
        prospect_id: ID del prospecto/cliente.
        spot_actual: Tipo de cambio spot USD/MXN actual.
        exposicion_total_usd: Exposición cambiaria total del cliente en USD.
            Si se proporciona, se calcula la exposición residual (no cubierta).
        db_path: Ruta a la base de datos.

    Returns:
        Dict con los siguientes campos:

        - ``prospect_id`` (int)
        - ``spot_actual`` (float)
        - ``num_coberturas`` (int)
        - ``total_cubierto_usd`` (float): suma de monto_usd de coberturas activas
        - ``total_mtm_mxn`` (float): suma del MTM de todas las coberturas
        - ``total_pnl_vs_spot_mxn`` (float): ganancia/pérdida acumulada vs no cubrirse
        - ``exposicion_residual_usd`` (float | None): exposicion_total - total_cubierto
        - ``proximos_vencimientos`` (list[HedgePnL]): coberturas que vencen en ≤30 días
        - ``coberturas`` (list[HedgePnL]): todas las coberturas del cliente
    """
    hedges_raw = get_client_hedges(prospect_id, estado="activa", db_path=db_path)
    coberturas = [calcular_pnl_hedge(h, spot_actual) for h in hedges_raw]

    total_cubierto = sum(c.monto_usd for c in coberturas)
    total_mtm = sum(c.mtm_mxn for c in coberturas)
    total_pnl = sum(c.pnl_vs_spot_mxn for c in coberturas)

    exposicion_residual: Optional[float] = None
    if exposicion_total_usd is not None:
        exposicion_residual = max(exposicion_total_usd - total_cubierto, 0.0)

    proximos = [c for c in coberturas if c.dias_restantes <= 30]

    return {
        "prospect_id": prospect_id,
        "spot_actual": spot_actual,
        "num_coberturas": len(coberturas),
        "total_cubierto_usd": total_cubierto,
        "total_mtm_mxn": total_mtm,
        "total_pnl_vs_spot_mxn": total_pnl,
        "exposicion_residual_usd": exposicion_residual,
        "proximos_vencimientos": proximos,
        "coberturas": coberturas,
    }


# ---------------------------------------------------------------------------
# Todos los clientes
# ---------------------------------------------------------------------------

def calcular_pnl_todos_clientes(
    spot_actual: float,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Calcula el resumen de P&L para cada cliente con coberturas activas.

    Agrupa las coberturas activas de la BD por prospect_id y llama
    a resumen_pnl_cliente para cada uno.

    Args:
        spot_actual: Tipo de cambio spot USD/MXN actual.
        db_path: Ruta a la base de datos.

    Returns:
        Lista de dicts (uno por cliente), el mismo formato que
        resumen_pnl_cliente, ordenados por total_mtm_mxn descendente.
    """
    all_hedges = get_active_hedges(db_path=db_path)

    # Deduplicar prospect_ids preservando orden
    seen: set[int] = set()
    prospect_ids: list[int] = []
    for h in all_hedges:
        pid = h["prospect_id"]
        if pid not in seen:
            seen.add(pid)
            prospect_ids.append(pid)

    resumenes = [
        resumen_pnl_cliente(pid, spot_actual, db_path=db_path)
        for pid in prospect_ids
    ]
    resumenes.sort(key=lambda r: r["total_mtm_mxn"], reverse=True)
    return resumenes
