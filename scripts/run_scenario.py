"""
HedgePoint MX — Simulador de Escenarios CLI

Permite al usuario describir escenarios hipotéticos de tipo de cambio en
lenguaje natural y ver el impacto financiero calculado con los modelos de
pricing del proyecto (forward, opciones, collar).

Modos de operación:
    Interactivo (default): loop de preguntas en español con Rich
    Directo (flags):       un escenario directo por línea de comandos
    Demo (--demo):         3 escenarios de ejemplo automáticos

Uso:
    python scripts/run_scenario.py                          # interactivo
    python scripts/run_scenario.py --spot 22               # spot fijo
    python scripts/run_scenario.py --cambio 10             # +10%
    python scripts/run_scenario.py --historico covid_2020  # evento histórico
    python scripts/run_scenario.py --demo                  # demo automático
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from agents.simulator.scenario_engine import (
    ESCENARIOS_HISTORICOS,
    ScenarioEngine,
    ScenarioInput,
)
from agents.simulator.scenario_charts import generar_todas_las_graficas

console = Console()

# ---------------------------------------------------------------------------
# LLM — opcional; el script funciona sin él
# ---------------------------------------------------------------------------

_llm = None

try:
    from core.llm_client import HedgePointLLM
    _llm = HedgePointLLM()
except Exception:
    pass  # sin API key o sin conexión — modo sin Claude


# ---------------------------------------------------------------------------
# Helpers de presentación
# ---------------------------------------------------------------------------

def _tabla_historicos() -> Table:
    """Construye una tabla Rich con los eventos históricos disponibles."""
    t = Table(
        title="Eventos Históricos Disponibles",
        box=box.ROUNDED,
        border_style="blue",
        show_lines=True,
    )
    t.add_column("Clave",          style="bold cyan",  min_width=24)
    t.add_column("Evento",         style="white",      min_width=28)
    t.add_column("Spot antes",     style="dim",        justify="right")
    t.add_column("Spot después",   style="bold",       justify="right")
    t.add_column("Cambio",         style="bold red",   justify="right")
    t.add_column("Descripción",    style="dim",        min_width=44)

    for clave, ev in ESCENARIOS_HISTORICOS.items():
        cambio_pct = ev["cambio_pct"]
        color = "red" if cambio_pct > 0 else "green"
        t.add_row(
            clave,
            ev["nombre"],
            f"${ev['spot_antes']:.2f}",
            f"${ev['spot_despues']:.2f}",
            f"[{color}]{cambio_pct:+.1f}%[/{color}]",
            ev["descripcion"],
        )
    return t


def _tabla_resultado(r: dict) -> Table:
    """Construye la tabla Rich principal con el resultado de un escenario."""
    sin_cob = r.get("impacto_sin_cobertura", {})
    fwd     = r.get("impacto_forward", {})
    opc     = r.get("impacto_opciones", {})
    collar  = r.get("impacto_collar", {})
    mejor   = r.get("mejor_estrategia", "")

    spot_a  = r.get("spot_actual", 0.0)
    spot_h  = r.get("spot_hipotetico", 0.0)
    mov     = r.get("movimiento_pct", 0.0)
    dir_    = r.get("direccion", "")

    dir_color = "red" if dir_ == "depreciacion" else "green"
    dir_label = "Depreciación" if dir_ == "depreciacion" else "Apreciación"

    t = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        border_style="blue",
        padding=(0, 1),
    )
    t.add_column("Concepto",   style="dim",    min_width=34)
    t.add_column("Valor",      style="white",  min_width=24, justify="right")
    t.add_column("Detalle",    style="dim",    min_width=30)

    # — Escenario base —
    t.add_row("[bold]ESCENARIO[/bold]", "", "")
    t.add_row(
        "  Spot actual",
        f"${spot_a:.4f} MXN/USD",
        "",
    )
    t.add_row(
        "  Spot hipotético",
        f"${spot_h:.4f} MXN/USD",
        f"[{dir_color}]{mov:+.2f}% ({dir_label})[/{dir_color}]",
    )

    # — Sin cobertura —
    t.add_row("", "", "")
    t.add_row("[bold red]SIN COBERTURA[/bold red]", "", "")
    diferencia = sin_cob.get("diferencia_vs_actual_mxn", 0.0)
    signo_dif  = "red" if diferencia >= 0 else "green"
    t.add_row(
        "  Exposición total",
        f"${sin_cob.get('exposicion_total_mxn', 0):,.0f} MXN",
        "",
    )
    t.add_row(
        "  Costo adicional vs. hoy",
        f"[{signo_dif}]{diferencia:+,.0f} MXN[/{signo_dif}]",
        "",
    )
    t.add_row(
        "  Impacto sobre margen",
        f"[{signo_dif}]{sin_cob.get('impacto_margen_pct', 0):.1f}% del margen[/{signo_dif}]",
        "",
    )

    # — Forward —
    t.add_row("", "", "")
    t.add_row(
        "[bold blue]FORWARD[/bold blue]"
        + (" ← Recomendado" if mejor == "forward" else ""),
        "", "",
    )
    ahorro_fwd = fwd.get("ahorro_vs_sin_cobertura_mxn", 0.0)
    c_fwd = "green" if ahorro_fwd >= 0 else "red"
    t.add_row("  Tasa forward (90d)",  f"${fwd.get('tasa_forward', 0):.4f}",  "")
    t.add_row("  Costo total",         f"${fwd.get('costo_cobertura_mxn', 0):,.0f} MXN",  "")
    t.add_row(
        "  Ahorro vs. sin cobertura",
        f"[{c_fwd}]{ahorro_fwd:+,.0f} MXN[/{c_fwd}]",
        f"Protección: {fwd.get('proteccion_pct', 0):.1f}%",
    )

    # — Opciones —
    t.add_row("", "", "")
    t.add_row(
        "[bold green]OPCIONES (put ATM)[/bold green]"
        + (" ← Recomendado" if mejor == "opciones" else ""),
        "", "",
    )
    ahorro_opc = opc.get("ahorro_vs_sin_cobertura_mxn", 0.0)
    c_opc = "green" if ahorro_opc >= 0 else "red"
    t.add_row("  Prima por USD",       f"${opc.get('prima_put_mxn_usd', 0):.4f} MXN",   "")
    t.add_row("  Prima total",         f"${opc.get('prima_total_mxn', 0):,.0f} MXN",     "Costo máximo de cobertura")
    t.add_row(
        "  Ahorro vs. sin cobertura",
        f"[{c_opc}]{ahorro_opc:+,.0f} MXN[/{c_opc}]",
        "",
    )

    # — Collar —
    t.add_row("", "", "")
    t.add_row(
        "[bold magenta]COLLAR[/bold magenta]"
        + (" ← Recomendado" if mejor == "collar" else ""),
        "", "",
    )
    ahorro_col = collar.get("ahorro_vs_sin_cobertura_mxn", 0.0)
    c_col = "green" if ahorro_col >= 0 else "red"
    t.add_row("  Prima neta por USD",  f"${collar.get('prima_neta_mxn_usd', 0):.4f} MXN",  "")
    t.add_row("  Costo neto total",    f"${collar.get('costo_neto_mxn', 0):,.0f} MXN",       "")
    t.add_row(
        "  Ahorro vs. sin cobertura",
        f"[{c_col}]{ahorro_col:+,.0f} MXN[/{c_col}]",
        f"Floor: ${collar.get('limite_beneficio', 0):.2f} | Cap: ${collar.get('proteccion_desde', 0):.2f}",
    )

    return t


def _mostrar_resultado(r: dict, con_analisis: bool = True) -> None:
    """Imprime tabla de resultado, panel de análisis Claude y paths de gráficas."""
    console.print()
    console.print(_tabla_resultado(r))
    console.print()
    console.print(
        Panel(
            r.get("resumen", ""),
            title="[bold]Resumen automático[/bold]",
            border_style="blue",
            padding=(0, 2),
        )
    )

    if con_analisis and _llm is not None:
        console.print()
        with console.status("[dim]Generando análisis con Claude...[/dim]", spinner="dots"):
            try:
                analisis = _llm.analyze_scenario(r)
            except Exception as exc:
                logger.error("analyze_scenario: %s", exc)
                analisis = None
        if analisis:
            console.print(
                Panel(
                    analisis,
                    title="[bold green]Análisis HedgePoint MX[/bold green]",
                    border_style="green",
                    padding=(1, 2),
                )
            )


def _parse_texto_a_input(
    texto: str,
    volumen: float,
    margen: float,
    plazo: int,
    spot_actual: float,
) -> ScenarioInput | None:
    """
    Convierte texto libre a ScenarioInput.

    Intenta parse via Claude API si está disponible; de lo contrario
    usa regex para extraer el primer número decimal del texto.
    """
    if _llm is not None:
        with console.status("[dim]Interpretando escenario...[/dim]", spinner="dots"):
            params = _llm.parse_scenario(texto, spot_actual)

        if params is not None:
            # Mostrar parámetros parseados y pedir confirmación
            console.print()
            t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
            t.add_column("Campo", style="dim", min_width=20)
            t.add_column("Valor", style="bold")
            t.add_row("Tipo",          params.get("tipo", "—"))
            t.add_row("Valor",         str(params.get("valor", "—")))
            t.add_row("Plazo (meses)", str(params.get("plazo_meses", plazo)))
            if params.get("nombre_evento"):
                t.add_row("Evento",    params["nombre_evento"])
            console.print(
                Panel(t, title="Escenario interpretado", border_style="yellow", padding=(0, 2))
            )
            if not Confirm.ask("¿Es correcto?", default=True):
                return None

            return ScenarioInput(
                tipo=params.get("tipo", "spot_fijo"),
                valor=float(params.get("valor", 0)),
                plazo_meses=int(params.get("plazo_meses", plazo)),
                nombre_evento=params.get("nombre_evento"),
                volumen_mensual_usd=volumen,
                margen_utilidad=margen,
                spot_actual=spot_actual,
            )

    # Regex fallback: primer número decimal en el texto
    match = re.search(r"\b(\d{1,3}(?:\.\d{1,4})?)\b", texto)
    if match:
        valor = float(match.group(1))
        console.print(
            f"[yellow]Sin API de Claude. Interpretando como spot_fijo=${valor:.2f}[/yellow]"
        )
        return ScenarioInput(
            tipo="spot_fijo",
            valor=valor,
            plazo_meses=plazo,
            volumen_mensual_usd=volumen,
            margen_utilidad=margen,
            spot_actual=spot_actual,
        )

    console.print("[red]No se pudo interpretar el escenario. Intenta ser más específico.[/red]")
    return None


# ---------------------------------------------------------------------------
# Modo interactivo
# ---------------------------------------------------------------------------

def modo_interactivo() -> None:
    """Loop interactivo principal."""
    console.print()
    console.print(
        Panel(
            "[bold white]HedgePoint MX — Simulador de Escenarios[/bold white]\n"
            "[dim]Describe cualquier situación hipotética del dólar y calcula su impacto[/dim]",
            border_style="blue",
            padding=(1, 4),
        )
    )

    if _llm is None:
        console.print(
            "[yellow]AVISO: ANTHROPIC_API_KEY no encontrada. "
            "El análisis Claude y el parseo inteligente no estarán disponibles.[/yellow]"
        )

    console.print()

    # Parámetros de posición del cliente
    volumen_str = Prompt.ask(
        "Volumen mensual en USD",
        default="500000",
    )
    try:
        volumen = float(volumen_str.replace(",", "").replace("$", ""))
    except ValueError:
        volumen = 500_000.0

    margen_str = Prompt.ask(
        "Margen de utilidad (%)",
        default="8",
    )
    try:
        margen = float(margen_str.replace("%", "")) / 100.0
    except ValueError:
        margen = 0.08

    engine = ScenarioEngine()
    # Obtener spot una sola vez para toda la sesión
    with console.status("[dim]Consultando tipo de cambio actual...[/dim]", spinner="dots"):
        spot_actual = engine._obtener_spot_actual()
    console.print(f"[dim]Spot actual: ${spot_actual:.4f} MXN/USD[/dim]")

    output_dir = "output/escenarios"  # subcarpeta organizada

    while True:
        console.print()
        texto = Prompt.ask(
            "[bold]Describe un escenario[/bold] "
            "[dim](o escribe 'historicos', 'salir')[/dim]"
        )
        texto = texto.strip()

        if texto.lower() in ("salir", "exit", "q"):
            console.print("[dim]Hasta luego.[/dim]")
            break

        if texto.lower() in ("historicos", "históricos", "historia", "h"):
            console.print()
            console.print(_tabla_historicos())
            continue

        # Intentar parsear e interpretar
        try:
            scenario_input = _parse_texto_a_input(
                texto,
                volumen=volumen,
                margen=margen,
                plazo=3,
                spot_actual=spot_actual,
            )
            if scenario_input is None:
                continue

            with console.status("[dim]Calculando escenario...[/dim]", spinner="dots"):
                resultado = engine.run(scenario_input)

            _mostrar_resultado(asdict(resultado), con_analisis=True)

            # Gráficas
            if Confirm.ask("¿Generar gráficas?", default=True):
                with console.status("[dim]Generando gráficas...[/dim]", spinner="dots"):
                    paths = generar_todas_las_graficas(asdict(resultado), output_dir=output_dir)
                if paths:
                    console.print(
                        f"[green]Gráficas guardadas en:[/green] {output_dir}"
                    )
                    for p in paths:
                        console.print(f"  [dim]→ {p}[/dim]")
                else:
                    console.print("[yellow]No se generaron gráficas.[/yellow]")

        except Exception as exc:
            logger.exception("Error en escenario interactivo")
            console.print(f"[red]Error al calcular el escenario:[/red] {exc}")

        console.print()
        if not Confirm.ask("¿Calcular otro escenario?", default=True):
            console.print("[dim]Sesión terminada.[/dim]")
            break


# ---------------------------------------------------------------------------
# Modo directo (flags)
# ---------------------------------------------------------------------------

def modo_directo(args: argparse.Namespace) -> None:
    """Ejecuta un único escenario desde flags de línea de comandos."""
    # Determinar tipo
    if args.spot is not None:
        tipo, valor, nombre_evento = "spot_fijo", args.spot, None
    elif args.cambio is not None:
        tipo, valor, nombre_evento = "cambio_porcentual", args.cambio, None
    else:
        tipo, valor, nombre_evento = "historico", 0.0, args.historico

    scenario_input = ScenarioInput(
        tipo=tipo,
        valor=valor,
        plazo_meses=args.plazo,
        volumen_mensual_usd=args.volumen,
        margen_utilidad=args.margen,
        nombre_evento=nombre_evento,
        volatilidad=args.vol,
    )

    engine = ScenarioEngine()

    try:
        with console.status("[dim]Calculando escenario...[/dim]", spinner="dots"):
            resultado = engine.run(scenario_input)
    except Exception as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    _mostrar_resultado(asdict(resultado), con_analisis=not args.sin_analisis)

    if not args.sin_graficas:
        with console.status("[dim]Generando gráficas...[/dim]", spinner="dots"):
            paths = generar_todas_las_graficas(
                asdict(resultado), output_dir=args.output_dir
            )
        if paths:
            console.print(f"\n[green]Gráficas guardadas en:[/green] {args.output_dir}")
            for p in paths:
                console.print(f"  [dim]→ {p}[/dim]")


# ---------------------------------------------------------------------------
# Modo demo
# ---------------------------------------------------------------------------

_DEMO_ESCENARIOS = [
    {
        "label": "Spot a $22.00",
        "input": ScenarioInput(
            tipo="spot_fijo",
            valor=22.0,
            volumen_mensual_usd=500_000,
            margen_utilidad=0.08,
            plazo_meses=3,
        ),
    },
    {
        "label": "Cambio +10%",
        "input": ScenarioInput(
            tipo="cambio_porcentual",
            valor=10.0,
            volumen_mensual_usd=500_000,
            margen_utilidad=0.08,
            plazo_meses=3,
        ),
    },
    {
        "label": "COVID-19 Mar 2020",
        "input": ScenarioInput(
            tipo="historico",
            valor=0.0,
            nombre_evento="covid_2020",
            volumen_mensual_usd=500_000,
            margen_utilidad=0.08,
            plazo_meses=3,
        ),
    },
]


def modo_demo() -> None:
    """Ejecuta 3 escenarios de ejemplo automáticamente."""
    console.print()
    console.print(
        Panel(
            "[bold]Modo demo[/bold] — 3 escenarios de ejemplo\n"
            "[dim]Importador ficticio: $500,000 USD/mes | Margen: 8% | Plazo: 3 meses[/dim]",
            border_style="yellow",
            padding=(1, 4),
        )
    )

    engine = ScenarioEngine()
    output_dir = "output/escenarios"

    for i, demo in enumerate(_DEMO_ESCENARIOS, start=1):
        console.print()
        console.rule(f"[bold]Escenario {i}/3 — {demo['label']}[/bold]")

        try:
            with console.status("[dim]Calculando...[/dim]", spinner="dots"):
                resultado = engine.run(demo["input"])

            _mostrar_resultado(asdict(resultado), con_analisis=False)

            with console.status("[dim]Generando gráficas...[/dim]", spinner="dots"):
                subdir = os.path.join(output_dir, f"escenario_{i:02d}_{demo['label'].lower().replace(' ', '_').replace('/', '')}")
                paths = generar_todas_las_graficas(asdict(resultado), output_dir=subdir)

            if paths:
                console.print(f"[green]Gráficas guardadas en:[/green] {subdir}")
                for p in paths:
                    console.print(f"  [dim]→ {p}[/dim]")

        except Exception as exc:
            logger.exception("Error en escenario demo %d", i)
            console.print(f"[red]Error en escenario {i}:[/red] {exc}")

    console.print()
    console.print(
        Panel(
            f"[bold green]Demo completado.[/bold green] "
            f"Gráficas en: [bold]{output_dir}[/bold]",
            border_style="green",
            padding=(0, 3),
        )
    )


# ---------------------------------------------------------------------------
# Argparse y main
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HedgePoint MX — Simulador de Escenarios de Tipo de Cambio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/run_scenario.py                           # interactivo\n"
            "  python scripts/run_scenario.py --spot 22                 # dólar a $22\n"
            "  python scripts/run_scenario.py --cambio 10               # dólar sube 10%\n"
            "  python scripts/run_scenario.py --historico covid_2020    # evento COVID\n"
            "  python scripts/run_scenario.py --demo                    # 3 escenarios demo\n"
            "\nEventos históricos disponibles:\n"
            "  crisis_2008 | trump_2016 | covid_2020 | super_peso_2023 | aranceles_trump_2025"
        ),
    )

    parser.add_argument("--demo", action="store_true",
                        help="Modo demo: 3 escenarios automáticos.")

    # Tipo de escenario (mutuamente excluyentes)
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--spot", type=float, metavar="FLOAT",
                       help="Spot hipotético en MXN/USD (ej: 22.0).")
    grupo.add_argument("--cambio", type=float, metavar="FLOAT",
                       help="Cambio porcentual (ej: 10 para +10%%, -5 para -5%%).")
    grupo.add_argument(
        "--historico", type=str, metavar="NOMBRE",
        choices=list(ESCENARIOS_HISTORICOS.keys()),
        help="Evento histórico pre-armado.",
    )

    # Parámetros de posición
    parser.add_argument("--volumen", type=float, default=500_000.0,
                        help="Volumen mensual en USD (default: 500000).")
    parser.add_argument("--margen", type=float, default=0.08,
                        help="Margen de utilidad como decimal (default: 0.08).")
    parser.add_argument("--plazo", type=int, default=3,
                        help="Plazo en meses (default: 3).")
    parser.add_argument("--vol", type=float, default=0.12,
                        help="Volatilidad anualizada (default: 0.12).")

    # Opciones de output
    parser.add_argument("--sin-graficas", action="store_true",
                        help="No generar gráficas.")
    parser.add_argument("--sin-analisis", action="store_true",
                        help="No generar análisis con Claude.")
    parser.add_argument("--output-dir", type=str, default="output/escenarios",
                        dest="output_dir",
                        help="Directorio para graficas (default: output/escenarios).")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.demo:
        modo_demo()
        return

    # Modo directo: alguno de --spot / --cambio / --historico presente
    if args.spot is not None or args.cambio is not None or args.historico is not None:
        modo_directo(args)
        return

    # Default: interactivo
    try:
        modo_interactivo()
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]Cancelado por el usuario.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
