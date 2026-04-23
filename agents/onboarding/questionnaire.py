"""
Interactive CLI questionnaire for HedgePoint MX prospect onboarding.

Collects prospect data via a guided Rich-powered terminal interface.
Returns a plain dict — no encryption, no database writes.

Usage:
    from agents.onboarding.questionnaire import ProspectQuestionnaire

    data = ProspectQuestionnaire().run()
    # data keys: nombre, empresa, email, telefono, sector,
    #            volumen_usd_mensual, frecuencia_compra, plazo_pago_dias,
    #            margen_utilidad, usa_coberturas, moneda_principal
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table
from rich import box

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SECTORES: list[tuple[str, str]] = [
    ("1", "Importador"),
    ("2", "Exportador"),
    ("3", "Agroexportador"),
    ("4", "Constructora"),
    ("5", "Maquiladora"),
    ("6", "Logística/Transporte"),
    ("7", "Empaque/Plásticos"),
    ("8", "Otro"),
]

_FRECUENCIAS: list[tuple[str, str]] = [
    ("1", "semanal"),
    ("2", "quincenal"),
    ("3", "mensual"),
    ("4", "trimestral"),
]

_RE_EMAIL = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"
)
# 10 consecutive digits, ignoring spaces/dashes already stripped
_RE_PHONE = re.compile(r"^\d{10}$")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _strip_phone(raw: str) -> str:
    """Remove spaces, dashes, and a leading +52 from a phone string."""
    s = raw.strip()
    s = re.sub(r"[\s\-\.]", "", s)
    if s.startswith("+52"):
        s = s[3:]
    elif s.startswith("52") and len(s) == 12:
        s = s[2:]
    return s


def _ask_text(
    console: Console,
    prompt: str,
    *,
    optional: bool = False,
    default: str = "",
) -> str:
    """Ask for free text, re-prompting if empty and not optional."""
    while True:
        suffix = " [dim](opcional, Enter para omitir)[/dim]" if optional else ""
        value = Prompt.ask(f"  [cyan]{prompt}{suffix}[/cyan]", default=default, console=console)
        value = value.strip()
        if value or optional:
            return value
        console.print("  [red]Este campo es obligatorio.[/red]")


def _ask_email(console: Console) -> str:
    """Ask for an email address, validating format."""
    while True:
        value = Prompt.ask("  [cyan]Correo electrónico[/cyan]", console=console).strip()
        if _RE_EMAIL.match(value):
            return value
        console.print("  [red]Formato inválido. Ejemplo: nombre@empresa.com[/red]")


def _ask_phone(console: Console) -> str:
    """Ask for an optional 10-digit Mexican phone number."""
    while True:
        raw = Prompt.ask(
            "  [cyan]Teléfono [dim](10 dígitos, opcional)[/dim][/cyan]",
            default="",
            console=console,
        ).strip()
        if not raw:
            return ""
        digits = _strip_phone(raw)
        if _RE_PHONE.match(digits):
            return digits
        console.print("  [red]Ingresa 10 dígitos. Ejemplo: 5512345678 o +52 55 1234 5678[/red]")


def _ask_sector(console: Console) -> str:
    """Show numbered sector menu, return sector string."""
    console.print()
    console.print("  [bold]Sector de la empresa:[/bold]")
    for key, label in _SECTORES:
        console.print(f"    [yellow]{key}[/yellow]) {label}")
    while True:
        choice = Prompt.ask("  [cyan]Selecciona una opción[/cyan]", console=console).strip()
        match = next((label for key, label in _SECTORES if key == choice), None)
        if match == "Otro":
            return _ask_text(console, "¿Cuál sector?")
        if match:
            return match
        console.print(f"  [red]Opción inválida. Ingresa un número del 1 al {len(_SECTORES)}.[/red]")


def _ask_frecuencia(console: Console) -> str:
    """Show numbered frequency menu, return frequency string."""
    console.print()
    console.print("  [bold]Frecuencia de compra/venta en USD:[/bold]")
    for key, label in _FRECUENCIAS:
        console.print(f"    [yellow]{key}[/yellow]) {label.capitalize()}")
    while True:
        choice = Prompt.ask("  [cyan]Selecciona una opción[/cyan]", console=console).strip()
        match = next((label for key, label in _FRECUENCIAS if key == choice), None)
        if match:
            return match
        console.print(f"  [red]Opción inválida. Ingresa un número del 1 al {len(_FRECUENCIAS)}.[/red]")


def _ask_positive_float(console: Console, prompt: str, *, default: Optional[float] = None) -> float:
    """Ask for a positive number, re-prompting on invalid input."""
    default_str = str(default) if default is not None else ""
    while True:
        raw = Prompt.ask(f"  [cyan]{prompt}[/cyan]", default=default_str, console=console).strip()
        try:
            value = float(raw.replace(",", ""))
            if value > 0:
                return value
            console.print("  [red]El valor debe ser mayor a cero.[/red]")
        except ValueError:
            console.print("  [red]Ingresa un número válido (p.ej. 250000 o 250,000).[/red]")


def _ask_percent(console: Console, prompt: str) -> float:
    """Ask for a percentage between 1 and 100."""
    while True:
        raw = Prompt.ask(f"  [cyan]{prompt}[/cyan]", console=console).strip().replace("%", "")
        try:
            value = float(raw)
            if 1.0 <= value <= 100.0:
                return value / 100.0   # store as decimal
            console.print("  [red]Ingresa un porcentaje entre 1 y 100.[/red]")
        except ValueError:
            console.print("  [red]Ingresa un número válido. Ejemplo: 12 para 12%.[/red]")


def _ask_int(console: Console, prompt: str, *, default: int) -> int:
    """Ask for a positive integer with a default."""
    while True:
        raw = Prompt.ask(f"  [cyan]{prompt}[/cyan]", default=str(default), console=console).strip()
        try:
            value = int(raw)
            if value > 0:
                return value
            console.print("  [red]El valor debe ser un número entero positivo.[/red]")
        except ValueError:
            console.print("  [red]Ingresa un número entero válido. Ejemplo: 30[/red]")


def _summary_table(console: Console, data: dict) -> None:
    """Render a Rich table summarising the collected data."""
    table = Table(
        title="Resumen del prospecto",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        min_width=55,
    )
    table.add_column("Campo", style="dim", min_width=28)
    table.add_column("Valor", style="white")

    rows = [
        ("Nombre",              data["nombre"]),
        ("Empresa",             data["empresa"]),
        ("Email",               data["email"]),
        ("Teléfono",            data["telefono"] or "[dim]—[/dim]"),
        ("Sector",              data["sector"]),
        ("Volumen mensual USD", f"${data['volumen_usd_mensual']:,.0f}"),
        ("Frecuencia",          data["frecuencia_compra"].capitalize()),
        ("Plazo de pago",       f"{data['plazo_pago_dias']} días"),
        ("Margen de utilidad",  f"{data['margen_utilidad']*100:.1f}%"),
        ("Ha usado coberturas", "Sí" if data["usa_coberturas"] else "No"),
        ("Moneda principal",    data["moneda_principal"]),
    ]
    for campo, valor in rows:
        table.add_row(campo, valor)

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class ProspectQuestionnaire:
    """
    Interactive CLI questionnaire for a new HedgePoint MX prospect.

    Collects contact details and FX exposure parameters through a
    step-by-step terminal interview, then presents a summary table
    for confirmation before returning the data dict.

    The returned dict contains plain-text values ready to be encrypted
    and persisted by the onboarding orchestrator.

    Returns
    -------
    dict with keys:
        nombre, empresa, email, telefono, sector, volumen_usd_mensual,
        frecuencia_compra, plazo_pago_dias, margen_utilidad,
        usa_coberturas, moneda_principal

    Examples
    --------
    ::

        data = ProspectQuestionnaire().run()
    """

    def __init__(self, console: Optional[Console] = None) -> None:
        self._console = console or Console()

    def run(self) -> dict:
        """Execute the questionnaire and return the collected data dict."""
        c = self._console

        c.print()
        c.print(Panel(
            "[bold white]Diagnóstico gratuito de exposición cambiaria[/bold white]\n"
            "[dim]Responde las siguientes preguntas para generar tu reporte personalizado.[/dim]",
            title="[bold green]HedgePoint MX[/bold green]",
            border_style="green",
            padding=(1, 4),
        ))

        # ------------------------------------------------------------------
        # Section 1 — Contact info
        # ------------------------------------------------------------------
        c.print()
        c.print(Rule("[bold cyan]1 / 3  —  Información de contacto[/bold cyan]", style="cyan"))
        c.print()

        nombre   = _ask_text(c, "Nombre completo del contacto")
        empresa  = _ask_text(c, "Nombre de la empresa")
        email    = _ask_email(c)
        telefono = _ask_phone(c)

        # ------------------------------------------------------------------
        # Section 2 — Business profile
        # ------------------------------------------------------------------
        c.print()
        c.print(Rule("[bold cyan]2 / 3  —  Perfil del negocio[/bold cyan]", style="cyan"))

        sector = _ask_sector(c)

        c.print()
        volumen = _ask_positive_float(
            c,
            "Volumen mensual en USD [dim](p.ej. 300000)[/dim]",
        )

        frecuencia   = _ask_frecuencia(c)
        plazo_dias   = _ask_int(c, "Plazo promedio de pago en días [dim](default: 30)[/dim]", default=30)

        c.print()
        margen = _ask_percent(c, "Margen de utilidad aproximado en % [dim](p.ej. 12 para 12%)[/dim]")

        # ------------------------------------------------------------------
        # Section 3 — Hedging history
        # ------------------------------------------------------------------
        c.print()
        c.print(Rule("[bold cyan]3 / 3  —  Experiencia con coberturas[/bold cyan]", style="cyan"))
        c.print()

        usa_coberturas = Confirm.ask(
            "  [cyan]¿Han utilizado coberturas cambiarias antes?[/cyan]",
            default=False,
            console=c,
        )

        # ------------------------------------------------------------------
        # Summary + confirmation
        # ------------------------------------------------------------------
        data = {
            "nombre":               nombre,
            "empresa":              empresa,
            "email":                email,
            "telefono":             telefono,
            "sector":               sector,
            "volumen_usd_mensual":  volumen,
            "frecuencia_compra":    frecuencia,
            "plazo_pago_dias":      plazo_dias,
            "margen_utilidad":      margen,
            "usa_coberturas":       int(usa_coberturas),
            "moneda_principal":     "USD",
        }

        while True:
            _summary_table(c, data)
            c.print()
            confirmar = Confirm.ask(
                "  [bold green]¿Los datos son correctos?[/bold green]",
                default=True,
                console=c,
            )
            if confirmar:
                break

            # Let the user pick which field to correct
            c.print()
            c.print("  [bold]¿Qué deseas corregir?[/bold]")
            opciones_correccion = [
                ("1", "Nombre"),
                ("2", "Empresa"),
                ("3", "Email"),
                ("4", "Teléfono"),
                ("5", "Sector"),
                ("6", "Volumen mensual USD"),
                ("7", "Frecuencia de compra"),
                ("8", "Plazo de pago"),
                ("9", "Margen de utilidad"),
                ("10", "Uso previo de coberturas"),
            ]
            for key, label in opciones_correccion:
                c.print(f"    [yellow]{key}[/yellow]) {label}")

            campo = Prompt.ask("  [cyan]Número de campo[/cyan]", console=c).strip()
            if campo == "1":
                data["nombre"]              = _ask_text(c, "Nombre completo del contacto")
            elif campo == "2":
                data["empresa"]             = _ask_text(c, "Nombre de la empresa")
            elif campo == "3":
                data["email"]               = _ask_email(c)
            elif campo == "4":
                data["telefono"]            = _ask_phone(c)
            elif campo == "5":
                data["sector"]              = _ask_sector(c)
            elif campo == "6":
                data["volumen_usd_mensual"] = _ask_positive_float(c, "Volumen mensual en USD")
            elif campo == "7":
                data["frecuencia_compra"]   = _ask_frecuencia(c)
            elif campo == "8":
                data["plazo_pago_dias"]     = _ask_int(c, "Plazo promedio de pago en días", default=30)
            elif campo == "9":
                data["margen_utilidad"]     = _ask_percent(c, "Margen de utilidad en %")
            elif campo == "10":
                data["usa_coberturas"]      = int(Confirm.ask(
                    "  [cyan]¿Han utilizado coberturas cambiarias antes?[/cyan]",
                    default=False,
                    console=c,
                ))
            else:
                c.print("  [red]Opción no reconocida.[/red]")

        c.print()
        c.print(Panel(
            "[bold green]¡Listo![/bold green] Generando tu diagnóstico de exposición cambiaria...",
            border_style="green",
            padding=(0, 4),
        ))
        c.print()

        return data


# ---------------------------------------------------------------------------
# Exposure calculator
# ---------------------------------------------------------------------------

# Fallback FX rate used when the live Banxico feed is unavailable.
# TODO: replace with a cached value from core.database once the monitor
#       is running and populating fx_rates with recent data.
_FX_FALLBACK = 17.5


def _get_current_usdmxn() -> float:
    """Return the most recent USD/MXN FIX rate from Banxico.

    Falls back to ``_FX_FALLBACK`` if the API is unavailable or the key
    is not configured — logs a warning so the caller is aware.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        from core.data.market_data import fetch_usdmxn_banxico
        df = fetch_usdmxn_banxico(days=5)
        rate = float(df["tipo_cambio"].iloc[-1])
        log.debug("TC USD/MXN obtenido de Banxico: %.4f", rate)
        return rate
    except Exception as exc:
        log.warning(
            "No se pudo obtener el TC de Banxico (%s). Usando fallback %.2f.",
            exc, _FX_FALLBACK,
        )
        return _FX_FALLBACK


def calculate_exposure(data: dict) -> dict:
    """Calculate the FX exposure metrics for a prospect.

    Takes the dict returned by :meth:`ProspectQuestionnaire.run` and
    produces all derived exposure figures needed for the diagnostic report.
    No database writes or encryption take place here.

    Parameters
    ----------
    data : dict
        Prospect data with at least the keys ``volumen_usd_mensual`` and
        ``margen_utilidad`` (as a decimal, e.g. ``0.12`` for 12 %).

    Returns
    -------
    dict
        exposicion_anual_usd        — annual USD volume (12 × monthly)
        tipo_cambio_usado           — FX rate used for MXN conversion
        exposicion_anual_mxn        — annual exposure in MXN
        perdida_potencial_5pct      — MXN loss if USD/MXN moves +5 %
        perdida_potencial_10pct     — MXN loss if USD/MXN moves +10 %
        perdida_potencial_15pct     — MXN loss if USD/MXN moves +15 %
        margen_en_riesgo            — True when a 10 % move wipes out the margin
        costo_estimado_forward_mensual — estimated monthly hedging cost in MXN

    Examples
    --------
    ::

        data = {
            "volumen_usd_mensual": 300_000,
            "margen_utilidad": 0.12,
        }
        metrics = calculate_exposure(data)
        # metrics["exposicion_anual_usd"]  -> 3_600_000
        # metrics["margen_en_riesgo"]      -> False  (10% of 3.6M USD < 12% margin)
    """
    volumen_mensual: float = data["volumen_usd_mensual"]
    margen: float          = data["margen_utilidad"]   # already a decimal

    tc = _get_current_usdmxn()

    exposicion_anual_usd = volumen_mensual * 12
    exposicion_anual_mxn = exposicion_anual_usd * tc

    perdida_5pct  = exposicion_anual_mxn * 0.05
    perdida_10pct = exposicion_anual_mxn * 0.10
    perdida_15pct = exposicion_anual_mxn * 0.15

    # Margin is expressed as a fraction of annual MXN revenue
    margen_mxn = exposicion_anual_mxn * margen
    margen_en_riesgo = perdida_10pct > margen_mxn

    # Spread estimate: $0.03 MXN per USD (bank spread for unhedged spot purchase)
    costo_forward_mensual = volumen_mensual * 0.03

    return {
        "exposicion_anual_usd":           exposicion_anual_usd,
        "tipo_cambio_usado":              tc,
        "exposicion_anual_mxn":           exposicion_anual_mxn,
        "perdida_potencial_5pct":         perdida_5pct,
        "perdida_potencial_10pct":        perdida_10pct,
        "perdida_potencial_15pct":        perdida_15pct,
        "margen_en_riesgo":               margen_en_riesgo,
        "costo_estimado_forward_mensual": costo_forward_mensual,
    }
