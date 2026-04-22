"""
demo_mode.py — Modo demo de HedgePoint MX para presentaciones a prospectos.

Dispara una alerta de WhatsApp + email idéntica a la del agente de monitoreo,
pero con datos hardcodeados y sin depender del scheduler ni de datos en tiempo real.

Uso:
    python scripts/demo_mode.py
    python scripts/demo_mode.py --canal whatsapp
    python scripts/demo_mode.py --canal email
    python scripts/demo_mode.py --precio 21.05 --trigger 20.75
    python scripts/demo_mode.py --auto          # skip confirmación (modo presentación)

Credenciales: las mismas de .env (RESEND_API_KEY, TWILIO_*)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Asegurar que el root del proyecto esté en sys.path
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from agents.monitor.triggers import FiredTrigger, Trigger, TriggerType
from agents.monitor.notifier import send_alert_email, send_alert_whatsapp

# ---------------------------------------------------------------------------
# Configuración demo por defecto
# ---------------------------------------------------------------------------

_DEMO_DEFAULTS = {
    "par": "USDMXN",
    "precio": 20.85,
    "trigger_level": 20.50,
    "cambio_dia_pct": 1.2,
    "volatilidad_30d_pct": 14.8,
    "recomendacion": (
        "Ejecutar 50% de cobertura pendiente. "
        "Forward a 90 días cotiza 21.12, volatilidad implícita favorable."
    ),
}

_TRIGGERS_YAML = _ROOT / "config" / "triggers.yaml"

console = Console()


# ---------------------------------------------------------------------------
# Construcción del FiredTrigger demo
# ---------------------------------------------------------------------------

def _build_demo_fired(precio: float, trigger_level: float) -> FiredTrigger:
    """Construye un FiredTrigger sintético con los datos de la demo."""
    trigger = Trigger(
        name="usdmxn_nivel_alto",
        trigger_type=TriggerType.PRICE_ABOVE,
        symbol="USDMXN",
        threshold=trigger_level,
        active=True,
        description=f"USD/MXN superó el nivel de alerta ${trigger_level:.2f}",
    )

    cambio = _DEMO_DEFAULTS["cambio_dia_pct"]
    vol = _DEMO_DEFAULTS["volatilidad_30d_pct"]
    rec = _DEMO_DEFAULTS["recomendacion"]

    message = (
        f"USD/MXN cotiza en {precio:.4f}, superando el umbral de {trigger_level:.4f}. "
        f"Cambio del día: +{cambio}% | Volatilidad 30d: {vol}%. "
        f"Recomendación: {rec}"
    )

    return FiredTrigger(trigger=trigger, observed_value=precio, message=message)


# ---------------------------------------------------------------------------
# Preview en consola con Rich
# ---------------------------------------------------------------------------

def _show_preview(fired: FiredTrigger, canales: list[str]) -> None:
    """Muestra un preview visual del trigger y el mensaje a enviar."""
    t = fired.trigger
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Encabezado
    console.print()
    console.print(
        Panel.fit(
            "[bold white]HedgePoint MX[/] — [yellow]Modo Demo[/]\n"
            f"[dim]{timestamp}[/]",
            border_style="yellow",
            padding=(0, 2),
        )
    )

    # Tabla de datos del trigger
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold #1a3c5e on white")
    table.add_column("Campo", style="bold", min_width=20)
    table.add_column("Valor", min_width=40)

    table.add_row("Par", t.symbol)
    table.add_row("Tipo de trigger", t.trigger_type.value)
    table.add_row("Precio actual", f"[bold green]{fired.observed_value:.4f}[/]")
    table.add_row("Umbral disparado", f"{t.threshold:.4f}")
    table.add_row("Cambio del día", f"+{_DEMO_DEFAULTS['cambio_dia_pct']}%")
    table.add_row("Volatilidad 30d", f"{_DEMO_DEFAULTS['volatilidad_30d_pct']}%")

    console.print(table)

    # Mensaje que se enviará
    console.print()
    console.print(
        Panel(
            f"[italic]{fired.message}[/]",
            title="[bold]Mensaje de alerta[/]",
            border_style="dim",
            padding=(1, 2),
        )
    )

    # Canales de envío
    canales_str = " + ".join(f"[bold cyan]{c}[/]" for c in canales)
    console.print(f"\n[dim]Canales:[/] {canales_str}\n")


# ---------------------------------------------------------------------------
# Carga de destinatarios desde triggers.yaml
# ---------------------------------------------------------------------------

def _load_recipients() -> dict:
    """
    Lee recipients y whatsapp desde config/triggers.yaml.
    Devuelve {'emails': [...], 'whatsapp': [...]}.
    """
    if not _TRIGGERS_YAML.exists():
        console.print(
            f"[red]No se encontró {_TRIGGERS_YAML}. "
            "Asegúrate de ejecutar desde la raíz del proyecto.[/]"
        )
        sys.exit(1)

    with open(_TRIGGERS_YAML, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    return {
        "emails": cfg.get("recipients", []),
        "whatsapp": cfg.get("whatsapp", []),
    }


# ---------------------------------------------------------------------------
# Envío
# ---------------------------------------------------------------------------

def _send(fired: FiredTrigger, canales: list[str], recipients: dict) -> None:
    """Despacha el trigger por los canales indicados."""
    fired_list = [fired]

    if "email" in canales:
        emails = recipients["emails"]
        if not emails:
            console.print("[yellow]⚠ No hay recipients configurados en triggers.yaml[/]")
        else:
            console.print(f"[dim]Enviando email a: {', '.join(emails)}…[/]")
            ok = send_alert_email(fired_list, emails)
            if ok:
                console.print("[green]✓ Email enviado correctamente.[/]")
            else:
                console.print("[red]✗ Error al enviar email. Revisa logs y credenciales.[/]")

    if "whatsapp" in canales:
        numbers = recipients["whatsapp"]
        if not numbers:
            console.print("[yellow]⚠ No hay números WhatsApp configurados en triggers.yaml[/]")
        else:
            console.print(f"[dim]Enviando WhatsApp a: {', '.join(numbers)}…[/]")
            send_alert_whatsapp(fired_list, numbers)
            console.print("[green]✓ WhatsApp despachado (verifica entrega en Twilio).[/]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="HedgePoint MX — Demo mode: dispara alerta de prueba.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/demo_mode.py
  python scripts/demo_mode.py --canal whatsapp --auto
  python scripts/demo_mode.py --precio 21.05 --trigger 20.75
  python scripts/demo_mode.py --canal ambos --auto
""",
    )
    parser.add_argument(
        "--canal",
        choices=["whatsapp", "email", "ambos"],
        default="ambos",
        help="Canal(es) de notificación (default: ambos)",
    )
    parser.add_argument(
        "--precio",
        type=float,
        default=_DEMO_DEFAULTS["precio"],
        help=f"Override del precio actual USD/MXN (default: {_DEMO_DEFAULTS['precio']})",
    )
    parser.add_argument(
        "--trigger",
        type=float,
        default=_DEMO_DEFAULTS["trigger_level"],
        help=f"Override del nivel de trigger (default: {_DEMO_DEFAULTS['trigger_level']})",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Skip la confirmación interactiva (modo presentación)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolver canales
    if args.canal == "ambos":
        canales = ["email", "whatsapp"]
    else:
        canales = [args.canal]

    # Construir trigger demo
    fired = _build_demo_fired(precio=args.precio, trigger_level=args.trigger)

    # Cargar destinatarios
    recipients = _load_recipients()

    # Mostrar preview
    _show_preview(fired, canales)

    # Confirmación (salvo --auto)
    if not args.auto:
        try:
            respuesta = console.input(
                "[bold yellow]¿Enviar alerta demo? [y/N]:[/] "
            ).strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelado.[/]")
            sys.exit(0)

        if respuesta != "y":
            console.print("[dim]Envío cancelado.[/]")
            sys.exit(0)

    console.print()
    _send(fired, canales, recipients)
    console.print()


if __name__ == "__main__":
    main()
