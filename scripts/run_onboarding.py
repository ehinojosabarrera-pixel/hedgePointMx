"""
HedgePoint MX — Onboarding CLI

Launches the full prospect diagnostic flow:
  interactive questionnaire → exposure calc → LLM insights → PDF report

Usage:
    python scripts/run_onboarding.py           # interactive mode
    python scripts/run_onboarding.py --demo    # demo with fictional prospect
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()

_DEMO_PROSPECT = {
    "nombre":              "Carlos Mendoza",
    "empresa":             "Importadora del Norte SA de CV",
    "email":               "carlos@importnorte.mx",
    "telefono":            "8112345678",
    "sector":              "Importador",
    "volumen_usd_mensual": 100_000.0,
    "frecuencia_compra":   "Mensual",
    "plazo_pago_dias":     30,
    "margen_utilidad":     0.20,
    "usa_coberturas":      0,
    "moneda_principal":    "USD",
}


def _print_summary(result: dict, pdf_path: str) -> None:
    """Print a Rich summary panel after the diagnostic completes."""
    prospect = result.get("prospect_data", {})
    exposure = result.get("exposure",      {})

    empresa         = prospect.get("empresa", "—")
    exp_usd         = exposure.get("exposicion_anual_usd", 0)
    en_riesgo       = exposure.get("margen_en_riesgo", False)
    estrategia      = result.get("prospect_data", {})   # filled by DB; use insights fallback
    estrategia_txt  = _extract_strategy_label(result.get("insights", ""))

    # Summary table
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Campo",  style="dim",   min_width=28)
    t.add_column("Valor",  style="white")

    t.add_row("Empresa",              empresa)
    t.add_row("Exposición anual USD", f"${exp_usd:,.0f}")
    t.add_row("Margen en riesgo",
              "[red]Sí ⚠[/red]" if en_riesgo else "[green]No[/green]")
    t.add_row("Estrategia recomendada", estrategia_txt.capitalize())
    t.add_row("PDF generado",          pdf_path)

    console.print()
    console.print(Panel(
        t,
        title="[bold green]Diagnóstico completado[/bold green]",
        border_style="green",
        padding=(1, 3),
    ))
    console.print()
    console.print(
        "  [bold green]OK[/bold green] "
        "[bold]PDF listo para enviar al prospecto.[/bold]"
    )
    console.print()


def _extract_strategy_label(insights: str) -> str:
    """Pull a strategy name from the insights text for the summary."""
    import re
    m = re.search(
        r"\b(forward|opciones?|collar|mix|combinaci[oó]n)\b",
        insights, re.IGNORECASE,
    )
    if not m:
        return "forward"
    word = m.group(1).lower()
    if word.startswith("opci"):
        return "opciones"
    if word in ("mix", "combinación", "combinacion"):
        return "mix"
    return word


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HedgePoint MX — Diagnóstico de exposición cambiaria",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/run_onboarding.py          # cuestionario interactivo\n"
            "  python scripts/run_onboarding.py --demo   # prospecto ficticio de demostración"
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Usa datos de un importador ficticio en vez del cuestionario interactivo.",
    )
    args = parser.parse_args()

    try:
        from agents.onboarding.diagnostic import DiagnosticOrchestrator
        from agents.onboarding.pdf_diagnostic import generar_pdf_diagnostico
        from core.database import init_db
        init_db()   # ensure prospects table exists
    except ImportError as exc:
        console.print(f"[red]Error al importar módulos:[/red] {exc}")
        console.print("Asegúrate de ejecutar desde la raíz del proyecto con el venv activo.")
        sys.exit(1)

    if args.demo:
        console.print()
        console.print(Panel(
            "[bold]Modo demo[/bold] — usando datos de un importador ficticio.",
            border_style="yellow",
            padding=(0, 3),
        ))
        prospect_data = _DEMO_PROSPECT
    else:
        prospect_data = None   # triggers interactive questionnaire

    try:
        orch   = DiagnosticOrchestrator(console=console)
        result = orch.run_full_diagnostic(prospect_data=prospect_data)
    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]Diagnóstico cancelado por el usuario.[/yellow]")
        sys.exit(0)
    except Exception as exc:
        console.print()
        console.print(Panel(
            f"[red]Error durante el diagnóstico:[/red]\n{exc}\n\n"
            "Verifica que las variables de entorno HEDGEPOINT_ENCRYPTION_KEY "
            "y ANTHROPIC_API_KEY estén configuradas en el archivo .env.",
            title="[red]Error[/red]",
            border_style="red",
        ))
        sys.exit(1)

    try:
        pdf_path = generar_pdf_diagnostico(result)
    except Exception as exc:
        console.print(f"[red]Error al generar el PDF:[/red] {exc}")
        console.print("[yellow]El diagnóstico fue completado pero el PDF no pudo generarse.[/yellow]")
        sys.exit(1)

    _print_summary(result, pdf_path)


if __name__ == "__main__":
    main()
