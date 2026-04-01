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

import math
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


# ---------------------------------------------------------------------------
# Garman-Kohlhagen (Black-Scholes para divisas)
# ---------------------------------------------------------------------------

@dataclass
class OpcionGK:
    """Resultado de pricing Garman-Kohlhagen para una opción sobre USD/MXN."""
    spot: float
    strike: float
    plazo_dias: int
    vol: float
    tiie: float
    sofr: float
    call: float
    put: float
    delta_call: float
    delta_put: float
    vega: float          # misma para call y put (en MXN por 1% de vol)

    def __str__(self) -> str:
        sep = "-" * 55
        return (
            f"\n{'=' * 55}\n"
            f"  Opción USD/MXN — Garman-Kohlhagen\n"
            f"{sep}\n"
            f"  Spot:    {self.spot:.4f}   Strike: {self.strike:.4f}\n"
            f"  Plazo:   {self.plazo_dias} días        Vol:    {self.vol*100:.2f}%\n"
            f"  TIIE:    {self.tiie*100:.2f}%         SOFR:   {self.sofr*100:.2f}%\n"
            f"{sep}\n"
            f"  Precio CALL:  {self.call:.4f} MXN\n"
            f"  Precio PUT:   {self.put:.4f} MXN\n"
            f"{sep}\n"
            f"  Delta CALL:   {self.delta_call:+.4f}\n"
            f"  Delta PUT:    {self.delta_put:+.4f}\n"
            f"  Vega (1%vol): {self.vega:.4f} MXN\n"
            f"{'=' * 55}"
        )


def _norm_cdf(x: float) -> float:
    """CDF de la distribución normal estándar."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """PDF de la distribución normal estándar."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calcular_opcion_gk(
    spot: float,
    strike: float,
    dias: int,
    vol: float,
    tiie: float = TIIE_ANUAL,
    sofr: float = SOFR_ANUAL,
) -> OpcionGK:
    """
    Precio de opción europea sobre USD/MXN usando el modelo Garman-Kohlhagen.

    Fórmulas:
        d1 = [ln(S/K) + (r_d - r_f + σ²/2)·t] / (σ·√t)
        d2 = d1 - σ·√t

        Call = S·e^(-r_f·t)·N(d1) - K·e^(-r_d·t)·N(d2)
        Put  = K·e^(-r_d·t)·N(-d2) - S·e^(-r_f·t)·N(-d1)

        Delta_call =  e^(-r_f·t)·N(d1)
        Delta_put  = -e^(-r_f·t)·N(-d1)
        Vega       =  S·e^(-r_f·t)·N'(d1)·√t  (por unidad de vol)

    Convención de tiempo: t = días / 365.

    Args:
        spot:   Tipo de cambio spot USD/MXN.
        strike: Precio de ejercicio (USD/MXN).
        dias:   Plazo en días naturales.
        vol:    Volatilidad implícita anualizada (decimal, p.ej. 0.12).
        tiie:   Tasa doméstica (MXN) anualizada en decimal.
        sofr:   Tasa extranjera (USD) anualizada en decimal.

    Returns:
        OpcionGK con precios y griegas.

    Raises:
        ValueError: si algún parámetro es no positivo.
    """
    if spot <= 0:
        raise ValueError(f"spot debe ser positivo, recibido: {spot}")
    if strike <= 0:
        raise ValueError(f"strike debe ser positivo, recibido: {strike}")
    if dias <= 0:
        raise ValueError(f"dias debe ser positivo, recibido: {dias}")
    if vol <= 0:
        raise ValueError(f"volatilidad debe ser positiva, recibido: {vol}")

    t = dias / 365.0
    sqrt_t = math.sqrt(t)

    d1 = (math.log(spot / strike) + (tiie - sofr + 0.5 * vol**2) * t) / (vol * sqrt_t)
    d2 = d1 - vol * sqrt_t

    disc_d = math.exp(-tiie * t)   # factor descuento doméstico
    disc_f = math.exp(-sofr * t)   # factor descuento extranjero

    call = spot * disc_f * _norm_cdf(d1) - strike * disc_d * _norm_cdf(d2)
    put  = strike * disc_d * _norm_cdf(-d2) - spot * disc_f * _norm_cdf(-d1)

    delta_call =  disc_f * _norm_cdf(d1)
    delta_put  = -disc_f * _norm_cdf(-d1)
    vega = spot * disc_f * _norm_pdf(d1) * sqrt_t * 0.01  # por 1% de vol

    return OpcionGK(
        spot=spot,
        strike=strike,
        plazo_dias=dias,
        vol=vol,
        tiie=tiie,
        sofr=sofr,
        call=call,
        put=put,
        delta_call=delta_call,
        delta_put=delta_put,
        vega=vega,
    )


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

    # --- Garman-Kohlhagen ---
    print("\nPricing opción europea USD/MXN (Garman-Kohlhagen)...")
    opcion = calcular_opcion_gk(
        spot=spot,
        strike=18.50,
        dias=90,
        vol=0.12,
        tiie=TIIE_ANUAL,
        sofr=SOFR_ANUAL,
    )
    print(opcion)
