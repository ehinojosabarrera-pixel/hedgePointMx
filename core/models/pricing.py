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
from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np

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


# ---------------------------------------------------------------------------
# Simulación Monte Carlo — VaR USD/MXN
# ---------------------------------------------------------------------------

@dataclass
class ResultadoMC:
    """Resultados de la simulación Monte Carlo para USD/MXN."""
    spot: float
    plazo_dias: int
    n_trayectorias: int
    vol: float
    drift: float
    precios_finales: np.ndarray = field(repr=False)
    trayectorias: np.ndarray = field(repr=False)   # shape (n_trayectorias, pasos+1)

    @property
    def var_95(self) -> float:
        """Pérdida máxima esperada al 95% de confianza (en MXN por USD, desde spot)."""
        return float(self.spot - np.percentile(self.precios_finales, 5))

    @property
    def var_99(self) -> float:
        """Pérdida máxima esperada al 99% de confianza."""
        return float(self.spot - np.percentile(self.precios_finales, 1))

    @property
    def precio_p5(self) -> float:
        return float(np.percentile(self.precios_finales, 5))

    @property
    def precio_p50(self) -> float:
        return float(np.percentile(self.precios_finales, 50))

    @property
    def precio_p95(self) -> float:
        return float(np.percentile(self.precios_finales, 95))

    def __str__(self) -> str:
        sep = "-" * 55
        return (
            f"\n{'=' * 55}\n"
            f"  Monte Carlo USD/MXN — {self.n_trayectorias:,} trayectorias\n"
            f"{sep}\n"
            f"  Spot inicial:  {self.spot:.4f}\n"
            f"  Plazo:         {self.plazo_dias} días\n"
            f"  Volatilidad:   {self.vol*100:.2f}%\n"
            f"  Drift anual:   {self.drift*100:.2f}%  (TIIE − SOFR)\n"
            f"{sep}\n"
            f"  Distribución de precios finales:\n"
            f"    Percentil  1%:  {np.percentile(self.precios_finales, 1):.4f}\n"
            f"    Percentil  5%:  {self.precio_p5:.4f}\n"
            f"    Mediana (50%):  {self.precio_p50:.4f}\n"
            f"    Percentil 95%:  {self.precio_p95:.4f}\n"
            f"    Percentil 99%:  {np.percentile(self.precios_finales, 99):.4f}\n"
            f"    Media:          {self.precios_finales.mean():.4f}\n"
            f"    Desv. estándar: {self.precios_finales.std():.4f}\n"
            f"{sep}\n"
            f"  VaR 95% (pérdida vs spot): {self.var_95:.4f} MXN/USD\n"
            f"  VaR 99% (pérdida vs spot): {self.var_99:.4f} MXN/USD\n"
            f"{'=' * 55}"
        )


def simular_monte_carlo(
    spot: float,
    dias: int = 90,
    vol: float = 0.12,
    tiie: float = TIIE_ANUAL,
    sofr: float = SOFR_ANUAL,
    n_trayectorias: int = 10_000,
    semilla: int | None = 42,
) -> ResultadoMC:
    """
    Simula trayectorias GBM del tipo de cambio USD/MXN usando movimiento
    browniano geométrico (Geometric Brownian Motion).

    Proceso:
        S(t+dt) = S(t) · exp[(μ − σ²/2)·dt + σ·√dt·Z]
        donde μ = TIIE − SOFR  (drift bajo medida real)
              Z ~ N(0,1)
              dt = 1/252  (día hábil)

    Args:
        spot:           Tipo de cambio spot inicial USD/MXN.
        dias:           Horizonte en días naturales (se convierte a ~dias*252/365 pasos).
        vol:            Volatilidad anualizada (decimal).
        tiie:           Tasa doméstica anualizada.
        sofr:           Tasa extranjera anualizada.
        n_trayectorias: Número de simulaciones.
        semilla:        Semilla numpy para reproducibilidad (None = aleatoria).

    Returns:
        ResultadoMC con trayectorias y estadísticas.

    Raises:
        ValueError: si spot, vol o dias no son positivos.
    """
    if spot <= 0:
        raise ValueError(f"spot debe ser positivo, recibido: {spot}")
    if vol <= 0:
        raise ValueError(f"volatilidad debe ser positiva, recibido: {vol}")
    if dias <= 0:
        raise ValueError(f"dias debe ser positivo, recibido: {dias}")

    rng = np.random.default_rng(semilla)

    # Pasos diarios hábiles proporcionales al horizonte
    pasos = max(1, round(dias * 252 / 365))
    dt = 1.0 / 252.0
    drift = tiie - sofr

    # Término de deriva y difusión
    deriva = (drift - 0.5 * vol**2) * dt
    difusion = vol * math.sqrt(dt)

    # Incrementos browniano: shape (n_trayectorias, pasos)
    Z = rng.standard_normal((n_trayectorias, pasos))
    log_retornos = deriva + difusion * Z

    # Trayectorias acumuladas: shape (n_trayectorias, pasos+1)
    log_paths = np.concatenate(
        [np.zeros((n_trayectorias, 1)), np.cumsum(log_retornos, axis=1)],
        axis=1,
    )
    trayectorias = spot * np.exp(log_paths)

    precios_finales = trayectorias[:, -1]

    return ResultadoMC(
        spot=spot,
        plazo_dias=dias,
        n_trayectorias=n_trayectorias,
        vol=vol,
        drift=drift,
        precios_finales=precios_finales,
        trayectorias=trayectorias,
    )


def plot_monte_carlo(res: ResultadoMC) -> None:
    """Genera dos gráficas: trayectorias simuladas e histograma de precios finales."""
    pasos = res.trayectorias.shape[1]
    eje_tiempo = np.linspace(0, res.plazo_dias, pasos)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        f"Monte Carlo USD/MXN — {res.n_trayectorias:,} trayectorias · {res.plazo_dias} días",
        fontsize=13, fontweight="bold",
    )

    # --- Panel izquierdo: trayectorias ---
    muestra = min(300, res.n_trayectorias)
    idx = np.random.default_rng(0).choice(res.n_trayectorias, muestra, replace=False)
    for i in idx:
        ax1.plot(eje_tiempo, res.trayectorias[i], color="#1a6b3c", alpha=0.07, linewidth=0.7)

    # Percentiles sobre todas las trayectorias
    p5  = np.percentile(res.trayectorias, 5,  axis=0)
    p50 = np.percentile(res.trayectorias, 50, axis=0)
    p95 = np.percentile(res.trayectorias, 95, axis=0)

    ax1.plot(eje_tiempo, p50, color="#1a6b3c", linewidth=2, label="Mediana")
    ax1.fill_between(eje_tiempo, p5, p95, alpha=0.2, color="#1a6b3c", label="Banda P5–P95")
    ax1.axhline(res.spot, color="gray", linestyle="--", linewidth=1, label=f"Spot {res.spot:.4f}")
    ax1.set_xlabel("Días")
    ax1.set_ylabel("USD/MXN")
    ax1.set_title("Trayectorias simuladas")
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", linestyle="--", alpha=0.4)

    # --- Panel derecho: histograma ---
    ax2.hist(res.precios_finales, bins=80, color="#1a6b3c", alpha=0.75, edgecolor="none")

    p1_val  = np.percentile(res.precios_finales, 1)
    p5_val  = np.percentile(res.precios_finales, 5)

    ax2.axvline(res.spot,  color="gray",   linestyle="--", linewidth=1.5, label=f"Spot {res.spot:.4f}")
    ax2.axvline(p5_val,   color="#e67e00", linestyle="-",  linewidth=1.5, label=f"VaR 95%: {res.var_95:.4f}")
    ax2.axvline(p1_val,   color="#c0392b", linestyle="-",  linewidth=1.5, label=f"VaR 99%: {res.var_99:.4f}")

    ax2.set_xlabel("Precio final USD/MXN")
    ax2.set_ylabel("Frecuencia")
    ax2.set_title("Distribución de precios a 90 días")
    ax2.legend(fontsize=8)
    ax2.grid(axis="y", linestyle="--", alpha=0.4)

    plt.tight_layout()
    plt.show()


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

    # --- Monte Carlo ---
    print("\nEjecutando simulación Monte Carlo (10,000 trayectorias)...")
    resultado_mc = simular_monte_carlo(
        spot=spot,
        dias=90,
        vol=0.12,
        tiie=TIIE_ANUAL,
        sofr=SOFR_ANUAL,
        n_trayectorias=10_000,
    )
    print(resultado_mc)
    plot_monte_carlo(resultado_mc)
