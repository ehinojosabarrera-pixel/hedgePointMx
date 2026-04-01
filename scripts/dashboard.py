"""
Dashboard de consola para HedgePoint MX.

Muestra en tiempo real:
- Tipo de cambio USD/MXN (bid/ask) con cambio porcentual vs registro anterior
- Precio WTI más reciente
- Fecha/hora de última actualización

Colores: verde si el peso se apreció (ask bajó), rojo si se depreció (ask subió).

Uso:
    python scripts/dashboard.py
    python scripts/dashboard.py --watch      # refresca cada 60s
    python scripts/dashboard.py --interval 30
"""

import sys
import time
import argparse
from pathlib import Path

# Permite ejecutar desde la raíz del proyecto o desde scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box
from rich.live import Live
from rich.layout import Layout

from core.database import get_latest_fx_rates, get_latest_commodities

console = Console()


def _pct_change(current: float, previous: float) -> float:
    """Cambio porcentual de previous a current."""
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100


def _arrow(pct: float) -> str:
    if pct > 0:
        return "▲"
    if pct < 0:
        return "▼"
    return "─"


def build_fx_panel(fx_rows: list[dict]) -> Panel:
    """Construye el panel de tipo de cambio USD/MXN."""
    if not fx_rows:
        return Panel("[dim]Sin datos de FX[/dim]", title="USD/MXN", border_style="yellow")

    latest = fx_rows[0]
    bid = latest["bid"]
    ask = latest["ask"]
    mid = (bid + ask) / 2
    spread = ask - bid
    timestamp = f"{latest['fecha']}  {latest['hora']}"
    source = latest.get("source", "—")

    # Cambio porcentual — comparamos el ask actual vs el anterior
    if len(fx_rows) >= 2:
        prev = fx_rows[1]
        pct_bid = _pct_change(bid, prev["bid"])
        pct_ask = _pct_change(ask, prev["ask"])
    else:
        pct_bid = pct_ask = 0.0

    # El peso se aprecia cuando el ask BAJA (menos pesos por dólar)
    appreciated = pct_ask < 0
    color = "green" if appreciated else ("red" if pct_ask > 0 else "white")

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("Campo", style="dim", width=14)
    table.add_column("Valor", justify="right", width=14)
    table.add_column("Cambio %", justify="right", width=12)

    arrow_bid = _arrow(pct_bid)
    arrow_ask = _arrow(pct_ask)

    # Bid
    bid_color = "green" if pct_bid < 0 else ("red" if pct_bid > 0 else "white")
    table.add_row(
        "Bid",
        f"[bold]{bid:.4f}[/bold]",
        f"[{bid_color}]{arrow_bid} {pct_bid:+.3f}%[/{bid_color}]",
    )

    # Ask
    table.add_row(
        "Ask",
        f"[bold {color}]{ask:.4f}[/bold {color}]",
        f"[{color}]{arrow_ask} {pct_ask:+.3f}%[/{color}]",
    )

    # Mid y spread sin cambio %
    table.add_row("Mid", f"{mid:.4f}", "")
    table.add_row("Spread", f"{spread:.4f}", "")

    sentiment = (
        f"[green]Peso APRECIADO[/green]" if appreciated
        else (f"[red]Peso DEPRECIADO[/red]" if pct_ask > 0 else "[white]Sin cambio[/white]")
    )
    status_line = Text.from_markup(f"  {sentiment}   [dim]{source}[/dim]")

    from rich.console import Group
    content = Group(table, status_line)

    return Panel(
        content,
        title=f"[bold cyan]USD/MXN[/bold cyan]",
        subtitle=f"[dim]{timestamp}[/dim]",
        border_style=color,
        padding=(0, 1),
    )


def build_wti_panel(wti_rows: list[dict]) -> Panel:
    """Construye el panel de precio WTI."""
    if not wti_rows:
        return Panel("[dim]Sin datos de WTI[/dim]", title="WTI Crude Oil", border_style="yellow")

    latest = wti_rows[0]
    price = latest["price"]
    timestamp = f"{latest['fecha']}  {latest['hora']}"
    source = latest.get("source", "—")

    if len(wti_rows) >= 2:
        prev_price = wti_rows[1]["price"]
        pct = _pct_change(price, prev_price)
    else:
        pct = 0.0

    color = "green" if pct < 0 else ("red" if pct > 0 else "white")
    arrow = _arrow(pct)

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Campo", style="dim", width=14)
    table.add_column("Valor", justify="right", width=14)

    table.add_row("Precio (USD/bbl)", f"[bold {color}]{price:.2f}[/bold {color}]")
    table.add_row("Cambio vs anterior", f"[{color}]{arrow} {pct:+.3f}%[/{color}]")
    table.add_row("Fuente", f"[dim]{source}[/dim]")

    return Panel(
        table,
        title="[bold yellow]WTI Crude Oil[/bold yellow]",
        subtitle=f"[dim]{timestamp}[/dim]",
        border_style=color,
        padding=(0, 1),
    )


def build_history_table(fx_rows: list[dict]) -> Table:
    """Tabla con los últimos N registros de USD/MXN."""
    table = Table(
        title="Historial reciente  USD/MXN",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("Fecha", style="dim", width=12)
    table.add_column("Hora", style="dim", width=10)
    table.add_column("Bid", justify="right", width=10)
    table.add_column("Ask", justify="right", width=10)
    table.add_column("Mid", justify="right", width=10)
    table.add_column("Δ Ask %", justify="right", width=10)

    for i, row in enumerate(fx_rows):
        bid = row["bid"]
        ask = row["ask"]
        mid = (bid + ask) / 2

        if i < len(fx_rows) - 1:
            pct = _pct_change(ask, fx_rows[i + 1]["ask"])
            color = "green" if pct < 0 else ("red" if pct > 0 else "white")
            pct_str = f"[{color}]{_arrow(pct)} {pct:+.3f}%[/{color}]"
        else:
            pct_str = "[dim]—[/dim]"

        row_style = "bold" if i == 0 else ""
        table.add_row(
            row["fecha"],
            row["hora"],
            f"[{row_style}]{bid:.4f}[/{row_style}]" if row_style else f"{bid:.4f}",
            f"[{row_style}]{ask:.4f}[/{row_style}]" if row_style else f"{ask:.4f}",
            f"{mid:.4f}",
            pct_str,
        )

    return table


def render_dashboard() -> None:
    """Renderiza el dashboard completo una vez."""
    fx_rows = get_latest_fx_rates("USDMXN", n=10)
    wti_rows = get_latest_commodities("WTI", n=5)

    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print()
    console.rule(f"[bold white]HedgePoint MX — Dashboard[/bold white]  [dim]{now}[/dim]")
    console.print()

    # Paneles superiores en columnas
    fx_panel = build_fx_panel(fx_rows)
    wti_panel = build_wti_panel(wti_rows)
    console.print(Columns([fx_panel, wti_panel], equal=True, expand=True))
    console.print()

    # Historial
    if fx_rows:
        console.print(build_history_table(fx_rows))
    else:
        console.print("[dim]No hay registros en la base de datos aún. Ejecuta el scheduler primero.[/dim]")

    console.print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard de consola HedgePoint MX")
    parser.add_argument("--watch", action="store_true", help="Refrescar periódicamente")
    parser.add_argument("--interval", type=int, default=60, metavar="SEG",
                        help="Segundos entre actualizaciones (default: 60)")
    args = parser.parse_args()

    if args.watch:
        try:
            while True:
                console.clear()
                render_dashboard()
                console.print(f"[dim]Próxima actualización en {args.interval}s — Ctrl+C para salir[/dim]")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard cerrado.[/dim]")
    else:
        render_dashboard()


if __name__ == "__main__":
    main()
