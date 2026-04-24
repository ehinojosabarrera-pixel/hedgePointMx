"""
Visualizaciones matplotlib para el Simulador de Escenarios de HedgePoint MX.

Genera tres tipos de gráfica a partir de un ScenarioResult (dict):
- Comparativa de estrategias (barras agrupadas)
- Waterfall del impacto financiero
- Distribución Monte Carlo con el escenario marcado

Uso típico:
    from agents.simulator.scenario_charts import generar_todas_las_graficas
    from dataclasses import asdict

    resultado = engine.run(scenario_input)
    paths = generar_todas_las_graficas(asdict(resultado), output_dir="output/charts")
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.models.pricing import TIIE_ANUAL, SOFR_ANUAL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de color
# ---------------------------------------------------------------------------

COLOR_POSITIVO = "#2ecc71"   # verde  — ahorro / beneficio
COLOR_NEGATIVO = "#e74c3c"   # rojo   — pérdida / costo
COLOR_NEUTRO   = "#3498db"   # azul   — referencia
COLOR_FORWARD  = "#2980b9"   # azul oscuro
COLOR_OPCIONES = "#27ae60"   # verde oscuro
COLOR_COLLAR   = "#8e44ad"   # morado
COLOR_SPOT     = "#e67e22"   # naranja
COLOR_FONDO    = "#f8f9fa"   # gris claro

# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

plt.style.use("seaborn-v0_8-whitegrid")

_FIGSIZE = (10, 6)
_DPI     = 150


def _fmt_mxn(valor: float, pos=None) -> str:
    """Formatea un valor en MXN para los ejes (miles → K, millones → M)."""
    abs_v = abs(valor)
    if abs_v >= 1_000_000:
        return f"${valor/1_000_000:.1f}M"
    if abs_v >= 1_000:
        return f"${valor/1_000:.0f}K"
    return f"${valor:.0f}"


def _guardar_o_mostrar(fig: plt.Figure, output_path: str | None) -> str | None:
    """Guarda la figura en disco o la muestra en pantalla."""
    plt.tight_layout()
    if output_path:
        fig.savefig(output_path, dpi=_DPI, bbox_inches="tight")
        plt.close(fig)
        return output_path
    plt.show()
    plt.close(fig)
    return None


# ---------------------------------------------------------------------------
# 1. Comparativa de estrategias
# ---------------------------------------------------------------------------

def grafica_comparativa_estrategias(
    scenario_result: dict,
    output_path: str | None = None,
) -> str | None:
    """Barras agrupadas: costo adicional de cada estrategia vs sin cobertura.

    Parámetros
    ----------
    scenario_result : dict
        ScenarioResult convertido a dict con ``dataclasses.asdict()``.
    output_path : str | None
        Ruta donde guardar el PNG. Si es None, muestra con plt.show().

    Retorna
    -------
    str | None
        Path del archivo guardado, o None si se mostró en pantalla.
    """
    sin_cob = scenario_result.get("impacto_sin_cobertura", {})
    fwd     = scenario_result.get("impacto_forward", {})
    opc     = scenario_result.get("impacto_opciones", {})
    collar  = scenario_result.get("impacto_collar", {})

    spot_actual     = scenario_result.get("spot_actual", 0.0)
    spot_h          = scenario_result.get("spot_hipotetico", 0.0)
    movimiento_pct  = scenario_result.get("movimiento_pct", 0.0)
    inp             = scenario_result.get("input", {})
    volumen         = inp.get("volumen_mensual_usd", 0.0) if isinstance(inp, dict) else 0.0
    plazo           = inp.get("plazo_meses", 0) if isinstance(inp, dict) else 0

    # Costo adicional de cada estrategia (positivo = más caro, negativo = más barato)
    diferencia_base = sin_cob.get("diferencia_vs_actual_mxn", 0.0)

    # Para "sin cobertura" mostramos el costo adicional bruto
    # Para las estrategias mostramos la diferencia neta vs. escenario sin cobertura
    # (negativo = ahorra, positivo = cuesta más que sin cobertura)
    vals = [
        diferencia_base,
        diferencia_base - fwd.get("ahorro_vs_sin_cobertura_mxn", 0.0),
        diferencia_base - opc.get("ahorro_vs_sin_cobertura_mxn", 0.0),
        diferencia_base - collar.get("ahorro_vs_sin_cobertura_mxn", 0.0),
    ]
    etiquetas = ["Sin cobertura", "Forward", "Opciones", "Collar"]
    colores   = [COLOR_NEGATIVO, COLOR_FORWARD, COLOR_OPCIONES, COLOR_COLLAR]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    fig.patch.set_facecolor(COLOR_FONDO)
    ax.set_facecolor(COLOR_FONDO)

    x = np.arange(len(etiquetas))
    bars = ax.bar(x, vals, color=colores, width=0.55, zorder=3, edgecolor="white", linewidth=0.8)

    # Annotations encima/debajo de cada barra
    for bar, v in zip(bars, vals):
        offset = max(abs(v) * 0.03, abs(diferencia_base) * 0.02 if diferencia_base else 5_000)
        va = "bottom" if v >= 0 else "top"
        y_pos = v + (offset if v >= 0 else -offset)
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            y_pos,
            f"${v:,.0f}",
            ha="center", va=va,
            fontsize=9, fontweight="bold",
            color="#2c3e50",
        )

    # Línea de referencia en 0
    ax.axhline(0, color="#7f8c8d", linewidth=0.9, linestyle="--", zorder=2)

    ax.set_xticks(x)
    ax.set_xticklabels(etiquetas, fontsize=11)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_mxn))
    ax.set_ylabel("Costo adicional (MXN)", fontsize=11)

    titulo = (
        f"Impacto: USD/MXN ${spot_actual:.2f} → ${spot_h:.2f} ({movimiento_pct:+.1f}%)"
    )
    subtitulo = f"Volumen: ${volumen:,.0f} USD/mes × {plazo} meses"
    ax.set_title(titulo, fontsize=13, fontweight="bold", pad=14)
    ax.text(
        0.5, 1.01, subtitulo,
        transform=ax.transAxes, ha="center", fontsize=9, color="#7f8c8d",
    )

    return _guardar_o_mostrar(fig, output_path)


# ---------------------------------------------------------------------------
# 2. Waterfall
# ---------------------------------------------------------------------------

def grafica_waterfall(
    scenario_result: dict,
    output_path: str | None = None,
) -> str | None:
    """Waterfall chart: costo base → movimiento FX → ahorro por estrategia.

    Muestra visualmente cómo el movimiento cambiario aumenta el costo y en
    cuánto lo reduce cada estrategia de cobertura.

    Parámetros
    ----------
    scenario_result : dict
        ScenarioResult convertido a dict.
    output_path : str | None
        Ruta del PNG. None → plt.show().

    Retorna
    -------
    str | None
    """
    sin_cob = scenario_result.get("impacto_sin_cobertura", {})
    fwd     = scenario_result.get("impacto_forward", {})
    opc     = scenario_result.get("impacto_opciones", {})
    collar  = scenario_result.get("impacto_collar", {})

    spot_actual = scenario_result.get("spot_actual", 0.0)
    inp         = scenario_result.get("input", {})
    volumen     = inp.get("volumen_mensual_usd", 0.0) if isinstance(inp, dict) else 0.0
    plazo       = inp.get("plazo_meses", 0) if isinstance(inp, dict) else 0

    costo_base        = volumen * plazo * spot_actual
    movimiento_fx     = sin_cob.get("diferencia_vs_actual_mxn", 0.0)
    ahorro_forward    = fwd.get("ahorro_vs_sin_cobertura_mxn", 0.0)
    ahorro_opciones   = opc.get("ahorro_vs_sin_cobertura_mxn", 0.0)
    ahorro_collar     = collar.get("ahorro_vs_sin_cobertura_mxn", 0.0)

    # Cada entrada: (etiqueta, delta, color)
    pasos = [
        ("Costo base\n(spot actual)",  costo_base,      COLOR_NEUTRO),
        ("Movimiento FX",              movimiento_fx,   COLOR_NEGATIVO if movimiento_fx >= 0 else COLOR_POSITIVO),
        ("Ahorro\nForward",            -ahorro_forward, COLOR_FORWARD),
        ("Ahorro\nOpciones",           -ahorro_opciones, COLOR_OPCIONES),
        ("Ahorro\nCollar",             -ahorro_collar,  COLOR_COLLAR),
    ]

    # Calcular bases acumuladas para el efecto cascada
    etiquetas = [p[0] for p in pasos]
    deltas    = [p[1] for p in pasos]
    colores   = [p[2] for p in pasos]

    bases  = [0.0] * len(pasos)
    totales = [0.0] * len(pasos)
    acum = 0.0
    for i, d in enumerate(deltas):
        if i == 0:
            bases[i] = 0.0
        else:
            bases[i] = acum if d >= 0 else acum + d
        acum += d
        totales[i] = acum

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    fig.patch.set_facecolor(COLOR_FONDO)
    ax.set_facecolor(COLOR_FONDO)

    x = np.arange(len(pasos))
    bar_width = 0.55

    bars = ax.bar(
        x, [abs(d) for d in deltas],
        bottom=bases,
        color=colores, width=bar_width,
        zorder=3, edgecolor="white", linewidth=0.8,
    )

    # Conectores grises entre barras
    for i in range(len(pasos) - 1):
        top_actual = bases[i] + abs(deltas[i])
        ax.plot(
            [x[i] + bar_width / 2, x[i + 1] - bar_width / 2],
            [top_actual, top_actual],
            color="#bdc3c7", linewidth=0.8, linestyle="--", zorder=2,
        )

    # Labels encima/debajo de cada barra
    for i, (bar, d, total) in enumerate(zip(bars, deltas, totales)):
        label = f"${abs(d):,.0f}"
        y_top = bar.get_y() + bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            y_top + abs(costo_base) * 0.01,
            label,
            ha="center", va="bottom",
            fontsize=8, fontweight="bold", color="#2c3e50",
        )

    ax.set_xticks(x)
    ax.set_xticklabels(etiquetas, fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_mxn))
    ax.set_ylabel("MXN", fontsize=11)
    ax.set_title("Análisis Waterfall del Escenario", fontsize=13, fontweight="bold", pad=14)

    return _guardar_o_mostrar(fig, output_path)


# ---------------------------------------------------------------------------
# 3. Distribución Monte Carlo
# ---------------------------------------------------------------------------

def grafica_distribucion_montecarlo(
    spot_actual: float,
    spot_hipotetico: float,
    volatilidad: float = 0.12,
    plazo_dias: int = 90,
    n_sims: int = 10_000,
    output_path: str | None = None,
) -> str | None:
    """Histograma de simulación Monte Carlo con el escenario hipotético marcado.

    Usa GBM (movimiento browniano geométrico) con drift = TIIE − SOFR.

    Parámetros
    ----------
    spot_actual : float
        Tipo de cambio spot USD/MXN de partida.
    spot_hipotetico : float
        Nivel del escenario hipotético a marcar en la distribución.
    volatilidad : float
        Volatilidad anualizada. Default: 0.12.
    plazo_dias : int
        Horizonte de simulación en días. Default: 90.
    n_sims : int
        Número de trayectorias. Default: 10 000.
    output_path : str | None
        Ruta del PNG. None → plt.show().

    Retorna
    -------
    str | None
    """
    rng = np.random.default_rng(42)

    drift = TIIE_ANUAL - SOFR_ANUAL
    dt    = plazo_dias / 365.0
    vol   = volatilidad

    precios = spot_actual * np.exp(
        (drift - 0.5 * vol ** 2) * dt
        + vol * np.sqrt(dt) * rng.standard_normal(n_sims)
    )

    p5  = float(np.percentile(precios, 5))
    p95 = float(np.percentile(precios, 95))
    pct_escenario = float(np.mean(precios <= spot_hipotetico) * 100)

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    fig.patch.set_facecolor(COLOR_FONDO)
    ax.set_facecolor(COLOR_FONDO)

    # Histograma principal
    ax.hist(
        precios, bins=80,
        color=COLOR_NEUTRO, alpha=0.55, edgecolor="white", linewidth=0.4,
        zorder=3, label="Simulaciones",
    )

    # Área sombreada percentil 5–95
    mask = (precios >= p5) & (precios <= p95)
    ax.hist(
        precios[mask], bins=80,
        color="#bdc3c7", alpha=0.35, edgecolor="none",
        zorder=2, label="Percentil 5–95",
    )

    # Líneas verticales de referencia
    ax.axvline(spot_actual, color=COLOR_SPOT, linewidth=2.0, linestyle="-",
               zorder=4, label=f"Spot actual ${spot_actual:.2f}")
    ax.axvline(spot_hipotetico, color=COLOR_NEGATIVO, linewidth=2.0, linestyle="--",
               zorder=4, label=f"Escenario hipotético ${spot_hipotetico:.2f}")

    # Texto de percentil en esquina superior
    color_pct = COLOR_NEGATIVO if spot_hipotetico > spot_actual else COLOR_POSITIVO
    ax.text(
        0.97, 0.95,
        f"El escenario está en el percentil {pct_escenario:.0f}%",
        transform=ax.transAxes, ha="right", va="top",
        fontsize=10, fontweight="bold", color=color_pct,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8, edgecolor=color_pct),
    )

    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:.2f}"))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.set_xlabel("USD/MXN", fontsize=11)
    ax.set_ylabel("Frecuencia", fontsize=11)
    ax.set_title(
        f"Distribución Monte Carlo USD/MXN a {plazo_dias} días ({n_sims:,} simulaciones)",
        fontsize=13, fontweight="bold", pad=14,
    )
    ax.legend(fontsize=9, loc="upper left")

    return _guardar_o_mostrar(fig, output_path)


# ---------------------------------------------------------------------------
# 4. Generador de todas las gráficas
# ---------------------------------------------------------------------------

def generar_todas_las_graficas(
    scenario_result: dict,
    output_dir: str = "output/charts",
) -> list[str]:
    """Genera las 3 gráficas del escenario y retorna la lista de paths.

    Parámetros
    ----------
    scenario_result : dict
        ScenarioResult convertido a dict con ``dataclasses.asdict()``.
    output_dir : str
        Directorio de salida. Se crea si no existe. Default: "output/charts".

    Retorna
    -------
    list[str]
        Paths de los archivos generados (se omiten los que fallaron).
    """
    os.makedirs(output_dir, exist_ok=True)
    paths: list[str] = []

    # -- 1. Comparativa de estrategias --
    try:
        p = grafica_comparativa_estrategias(
            scenario_result,
            output_path=os.path.join(output_dir, "comparativa_estrategias.png"),
        )
        if p:
            paths.append(p)
            logger.info("Gráfica generada: %s", p)
    except Exception as exc:
        logger.error("Error generando comparativa_estrategias: %s", exc)

    # -- 2. Waterfall --
    try:
        p = grafica_waterfall(
            scenario_result,
            output_path=os.path.join(output_dir, "waterfall.png"),
        )
        if p:
            paths.append(p)
            logger.info("Gráfica generada: %s", p)
    except Exception as exc:
        logger.error("Error generando waterfall: %s", exc)

    # -- 3. Monte Carlo --
    try:
        inp      = scenario_result.get("input", {})
        vol      = inp.get("volatilidad", 0.12) if isinstance(inp, dict) else 0.12
        plazo_m  = inp.get("plazo_meses", 3)    if isinstance(inp, dict) else 3
        p = grafica_distribucion_montecarlo(
            spot_actual=scenario_result.get("spot_actual", 0.0),
            spot_hipotetico=scenario_result.get("spot_hipotetico", 0.0),
            volatilidad=vol,
            plazo_dias=plazo_m * 30,
            output_path=os.path.join(output_dir, "montecarlo.png"),
        )
        if p:
            paths.append(p)
            logger.info("Gráfica generada: %s", p)
    except Exception as exc:
        logger.error("Error generando montecarlo: %s", exc)

    logger.info("Total gráficas generadas: %d/%d", len(paths), 3)
    return paths
