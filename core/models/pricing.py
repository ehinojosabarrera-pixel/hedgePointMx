"""
Modelos de pricing financiero para HedgePoint MX.

Incluira:
- Calculo de forward teorico USD/MXN (paridad de tasas TIIE vs SOFR)
- Black-Scholes para opciones sobre tipo de cambio
- Griegas (Delta, Gamma, Vega, Theta)
- Simulacion Monte Carlo para VaR

Se construye en Sprint 0.
"""

from __future__ import annotations

from dataclasses import dataclass

# Tasas de interés anualizadas (valores provisionales — Sprint 1)
TIIE_ANUAL = 0.1025   # TIIE 28 días vigente
SOFR_ANUAL = 0.0430   # SOFR overnight compuesto


@dataclass
class ForwardUSDMXN:
    """Precio forward teórico USD/MXN calculado por paridad de tasas de interés."""
    plazo_dias: int
    spot: float
    tiie: float
    sofr: float
    forward: float

    def __str__(self) -> str:
        return (
            f"Forward {self.plazo_dias:>3}d | "
            f"Spot: {self.spot:.4f} | "
            f"TIIE: {self.tiie*100:.2f}% | "
            f"SOFR: {self.sofr*100:.2f}% | "
            f"Forward teórico: {self.forward:.4f}"
        )


def calcular_forward(spot: float, dias: int,
                     tiie: float = TIIE_ANUAL,
                     sofr: float = SOFR_ANUAL) -> ForwardUSDMXN:
    """
    Calcula el precio forward teórico USD/MXN usando paridad cubierta de tasas.

    Fórmula:
        F = S * (1 + r_MXN * t) / (1 + r_USD * t)

    donde t = dias / 360 (convención del mercado mexicano).

    Args:
        spot:  Tipo de cambio spot USD/MXN.
        dias:  Plazo en días naturales (p.ej. 30, 60, 90).
        tiie:  Tasa de interés mexicana anualizada (decimal). Default: TIIE_ANUAL.
        sofr:  Tasa de interés estadounidense anualizada (decimal). Default: SOFR_ANUAL.

    Returns:
        ForwardUSDMXN con el resultado y los parámetros usados.

    Raises:
        ValueError: si spot o días son no positivos.
    """
    if spot <= 0:
        raise ValueError(f"El tipo de cambio spot debe ser positivo, recibido: {spot}")
    if dias <= 0:
        raise ValueError(f"El plazo debe ser positivo, recibido: {dias}")

    t = dias / 360
    forward = spot * (1 + tiie * t) / (1 + sofr * t)

    return ForwardUSDMXN(
        plazo_dias=dias,
        spot=spot,
        tiie=tiie,
        sofr=sofr,
        forward=forward,
    )


def calcular_forwards_estandar(spot: float,
                                tiie: float = TIIE_ANUAL,
                                sofr: float = SOFR_ANUAL) -> list[ForwardUSDMXN]:
    """
    Calcula forwards a 30, 60 y 90 días para un spot dado.

    Returns:
        Lista de ForwardUSDMXN ordenada por plazo.
    """
    return [calcular_forward(spot, dias, tiie, sofr) for dias in (30, 60, 90)]


if __name__ == "__main__":
    from core.data.market_data import fetch_usdmxn_banxico

    print("Obteniendo tipo de cambio spot desde Banxico...")
    try:
        df = fetch_usdmxn_banxico(days=5)
        spot = float(df["tipo_cambio"].iloc[-1])
        fecha_spot = df["fecha"].iloc[-1].strftime("%d/%m/%Y")
        print(f"  Spot USD/MXN FIX al {fecha_spot}: {spot:.4f}\n")
    except Exception as e:
        print(f"  [AVISO] No se pudo obtener el spot de Banxico: {e}")
        spot = 17.0
        print(f"  Usando spot de referencia: {spot:.4f}\n")

    print(f"Parámetros: TIIE = {TIIE_ANUAL*100:.2f}%  |  SOFR = {SOFR_ANUAL*100:.2f}%")
    print("-" * 65)

    forwards = calcular_forwards_estandar(spot)
    for fwd in forwards:
        puntos = (fwd.forward - fwd.spot) * 10_000
        print(f"{fwd}  |  Puntos fwd: {puntos:+.1f}")

    print("-" * 65)
