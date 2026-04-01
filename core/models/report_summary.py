"""
Reporte ejecutivo HedgePoint MX — consola.

Integra:
- Tipo de cambio spot USD/MXN (Banxico FIX)
- Forwards teóricos 30/60/90 días (paridad de tasas)
- Pricing de opciones europeas USD/MXN (Garman-Kohlhagen)
- VaR Monte Carlo a 90 días (95% y 99%)
"""

from __future__ import annotations

from datetime import datetime

from core.data.market_data import fetch_usdmxn_banxico
from core.models.pricing import (
    SOFR_ANUAL,
    TIIE_ANUAL,
    calcular_forwards_estandar,
    calcular_opcion_gk,
    simular_monte_carlo,
)

# Parámetros del reporte
STRIKE = 18.50
PLAZO_OPCION_DIAS = 90
VOL = 0.12
N_SIMS = 10_000

_W = 62  # ancho total de línea


def _linea(char: str = "─") -> str:
    return char * _W


def _titulo(texto: str, char: str = "═") -> str:
    return f"{char * _W}\n  {texto}\n{char * _W}"


def _fila(etiqueta: str, valor: str, unidad: str = "") -> str:
    col_val = f"{valor} {unidad}".strip()
    puntos = "." * max(2, _W - 4 - len(etiqueta) - len(col_val))
    return f"  {etiqueta} {puntos} {col_val}"


def _seccion(titulo: str) -> str:
    pad = (_W - len(titulo) - 4) // 2
    return f"\n  {'─' * pad}  {titulo}  {'─' * pad}"


def generar_reporte(spot: float, fecha_spot: str) -> None:
    """Imprime el reporte ejecutivo completo en consola."""

    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

    print()
    print(_titulo(f"REPORTE EJECUTIVO  ·  HedgePoint MX"))
    print(_fila("Generado", ts))
    print(_linea())

    # ── TIPO DE CAMBIO SPOT ───────────────────────────────────────────────────
    print(_seccion("TIPO DE CAMBIO  USD / MXN"))
    print(_fila("Fuente", "Banxico — Tipo de cambio FIX"))
    print(_fila("Fecha del dato", fecha_spot))
    print(_fila("Spot USD/MXN", f"{spot:.4f}", "MXN"))
    print(_linea("─"))

    # ── FORWARDS TEÓRICOS ────────────────────────────────────────────────────
    print(_seccion("FORWARDS TEÓRICOS  (paridad de tasas)"))
    print(_fila("TIIE (tasa doméstica MXN)", f"{TIIE_ANUAL*100:.2f}", "%"))
    print(_fila("SOFR (tasa extranjera USD)", f"{SOFR_ANUAL*100:.2f}", "%"))
    print()

    encabezado = f"  {'Plazo':>8}  {'Forward':>10}  {'Pts fwd':>9}  {'Prima %':>8}"
    print(encabezado)
    print("  " + "─" * (_W - 2))

    forwards = calcular_forwards_estandar(spot)
    for fwd in forwards:
        puntos = (fwd.forward - fwd.spot) * 10_000
        prima_pct = (fwd.forward / fwd.spot - 1) * 100
        print(
            f"  {fwd.plazo_dias:>5} días"
            f"  {fwd.forward:>10.4f}"
            f"  {puntos:>+9.1f}"
            f"  {prima_pct:>+7.2f}%"
        )

    print(_linea("─"))

    # ── OPCIONES EUROPEAS (GARMAN-KOHLHAGEN) ─────────────────────────────────
    print(_seccion("OPCIONES EUROPEAS  (Garman-Kohlhagen)"))
    print(_fila("Strike", f"{STRIKE:.4f}", "MXN/USD"))
    print(_fila("Plazo", f"{PLAZO_OPCION_DIAS}", "días"))
    print(_fila("Volatilidad implícita", f"{VOL*100:.2f}", "%"))
    print()

    opcion = calcular_opcion_gk(
        spot=spot,
        strike=STRIKE,
        dias=PLAZO_OPCION_DIAS,
        vol=VOL,
        tiie=TIIE_ANUAL,
        sofr=SOFR_ANUAL,
    )

    encabezado2 = f"  {'Instrumento':16}  {'Precio':>10}  {'Delta':>9}  {'Vega/1%vol':>10}"
    print(encabezado2)
    print("  " + "─" * (_W - 2))
    print(
        f"  {'CALL (compra USD)':16}"
        f"  {opcion.call:>10.4f}"
        f"  {opcion.delta_call:>+9.4f}"
        f"  {opcion.vega:>10.4f}"
    )
    print(
        f"  {'PUT  (venta USD)':16}"
        f"  {opcion.put:>10.4f}"
        f"  {opcion.delta_put:>+9.4f}"
        f"  {opcion.vega:>10.4f}"
    )

    # Paridad put-call (verificación implícita)
    fwd_90 = next(f for f in forwards if f.plazo_dias == 90)
    print()
    print(_fila("Put-Call parity check  (C − P)", f"{opcion.call - opcion.put:+.4f}", "MXN"))
    print(_linea("─"))

    # ── MONTE CARLO / VaR ─────────────────────────────────────────────────────
    print(_seccion("VALUE AT RISK  (Monte Carlo GBM)"))
    print(_fila("Trayectorias simuladas", f"{N_SIMS:,}"))
    print(_fila("Horizonte", "90 días"))
    print(_fila("Volatilidad anualizada", f"{VOL*100:.2f}", "%"))
    print(_fila("Drift (TIIE − SOFR)", f"{(TIIE_ANUAL - SOFR_ANUAL)*100:.2f}", "%"))
    print()

    mc = simular_monte_carlo(
        spot=spot,
        dias=PLAZO_OPCION_DIAS,
        vol=VOL,
        tiie=TIIE_ANUAL,
        sofr=SOFR_ANUAL,
        n_trayectorias=N_SIMS,
    )

    import numpy as np  # noqa: PLC0415 — import local para no contaminar módulo

    encabezado3 = f"  {'Métrica':30}  {'Valor':>10}  {'vs Spot':>9}"
    print(encabezado3)
    print("  " + "─" * (_W - 2))

    metricas = [
        ("Precio esperado (media)",     mc.precios_finales.mean()),
        ("Mediana  (P50)",              mc.precio_p50),
        ("Percentil  5%  (P5)",         mc.precio_p5),
        ("Percentil  1%  (P1)",         float(np.percentile(mc.precios_finales, 1))),
        ("Desv. estándar",              mc.precios_finales.std()),
    ]
    for nombre, val in metricas:
        diff = val - spot
        print(f"  {nombre:30}  {val:>10.4f}  {diff:>+9.4f}")

    print()
    print(_fila("VaR 95%  (pérdida máx. esperada)", f"{mc.var_95:.4f}", "MXN/USD"))
    print(_fila("VaR 99%  (pérdida máx. esperada)", f"{mc.var_99:.4f}", "MXN/USD"))
    print()
    print(_linea("═"))
    print()


if __name__ == "__main__":
    print("Obteniendo tipo de cambio spot desde Banxico...")
    try:
        df = fetch_usdmxn_banxico(days=5)
        spot = float(df["tipo_cambio"].iloc[-1])
        fecha_spot = df["fecha"].iloc[-1].strftime("%d/%m/%Y")
    except Exception as e:
        print(f"  [AVISO] No se pudo obtener el spot de Banxico: {e}")
        spot = 17.0
        fecha_spot = "referencia"

    generar_reporte(spot=spot, fecha_spot=fecha_spot)
