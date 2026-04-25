"""
Script para generación y envío de reportes semanales de coberturas — HedgePoint MX.

Modos de uso:

  Demo (BD temporal, datos ficticios, sin envíos):
      python scripts/run_reports.py --demo

  Todos los clientes, envío inmediato:
      python scripts/run_reports.py --now

  Solo un cliente:
      python scripts/run_reports.py --now --cliente-id 3

  Simular envíos sin ejecutarlos:
      python scripts/run_reports.py --now --dry-run

  Con WhatsApp además de email:
      python scripts/run_reports.py --now --canales email,whatsapp

  Loop semanal (lunes 7am):
      python scripts/run_reports.py --schedule
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console(highlight=False)
logger = logging.getLogger(__name__)

_DEMO_DB   = Path("/tmp/hedgepoint_demo.db")
_DEMO_SEED = 42


# ---------------------------------------------------------------------------
# Datos demo
# ---------------------------------------------------------------------------

def _crear_bd_demo(db_path: Path) -> int:
    """Inicializa la BD demo y retorna el prospect_id insertado."""
    from core.database import init_db, insert_prospect, insert_fx_rate, insert_hedge

    if db_path.exists():
        db_path.unlink()

    init_db(db_path)

    prospect_id = insert_prospect(
        {
            "nombre_enc":          "Carlos Demo",
            "empresa_enc":         "Importadora Demo S.A.",
            "sector":              "Importador",
            "volumen_usd_mensual": 300_000,
            "margen_utilidad":     0.15,
            "status":              "diagnosticado",
        },
        db_path=db_path,
    )

    rng = random.Random(_DEMO_SEED)
    hoy = date.today()
    bid = 20.00
    for i in range(29, -1, -1):
        fecha = (hoy - timedelta(days=i)).isoformat()
        bid  += rng.uniform(-0.06, 0.06)
        bid   = max(19.50, min(20.50, bid))
        insert_fx_rate(
            fecha=fecha, hora="12:00:00", par="USDMXN",
            bid=round(bid, 4), ask=round(bid + 0.05, 4),
            source="demo", db_path=db_path,
        )

    hoy_iso = hoy.isoformat()
    insert_hedge(
        {"prospect_id": prospect_id, "tipo": "forward", "monto_usd": 100_000.0,
         "strike": 20.10, "spot_entrada": 20.00, "tasa_forward": 20.10,
         "prima_pagada_mxn": 0.0, "fecha_inicio": hoy_iso,
         "fecha_vencimiento": (hoy + timedelta(days=45)).isoformat()},
        db_path=db_path,
    )
    insert_hedge(
        {"prospect_id": prospect_id, "tipo": "put", "monto_usd": 80_000.0,
         "strike": 20.00, "spot_entrada": 19.90, "prima_pagada_mxn": 12_000.0,
         "fecha_inicio": hoy_iso,
         "fecha_vencimiento": (hoy + timedelta(days=15)).isoformat()},
        db_path=db_path,
    )
    insert_hedge(
        {"prospect_id": prospect_id, "tipo": "collar", "monto_usd": 120_000.0,
         "strike": 19.80, "strike_call": 20.80, "spot_entrada": 20.05,
         "prima_pagada_mxn": 8_000.0, "fecha_inicio": hoy_iso,
         "fecha_vencimiento": (hoy + timedelta(days=60)).isoformat()},
        db_path=db_path,
    )

    return prospect_id


# ---------------------------------------------------------------------------
# Output rich
# ---------------------------------------------------------------------------

def _imprimir_resumen(datos: dict, path_pdf: str) -> None:
    rm   = datos.get("resumen_mercado", {})
    pnl  = datos.get("pnl", {})
    prox = datos.get("proximos_vencimientos", [])

    t_merc = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t_merc.add_column("Campo", style="bold cyan")
    t_merc.add_column("Valor")
    t_merc.add_row("Spot USD/MXN",      f"${rm.get('spot', 0):.4f}")
    var       = rm.get("variacion_semanal", 0)
    color_var = "red" if var > 0 else "green"
    t_merc.add_row("Variacion semanal", f"[{color_var}]{var:+.2f}%[/{color_var}]")
    t_merc.add_row("Volatilidad 30d",   f"{rm.get('volatilidad_30d', 0):.2f}%")
    console.print(Panel(t_merc, title="[bold blue]Mercado[/bold blue]", expand=False))

    mtm       = pnl.get("total_mtm_mxn", 0)
    color_mtm = "green" if mtm >= 0 else "red"
    t_pos = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t_pos.add_column("Campo", style="bold cyan")
    t_pos.add_column("Valor")
    t_pos.add_row("Total cubierto USD",  f"${pnl.get('total_cubierto_usd', 0):,.0f}")
    t_pos.add_row("MTM total MXN",       f"[{color_mtm}]${mtm:,.0f}[/{color_mtm}]")
    t_pos.add_row("Exposicion residual", f"${pnl.get('exposicion_residual_usd') or 0:,.0f} USD")
    t_pos.add_row("Coberturas activas",  str(pnl.get("num_coberturas", 0)))
    t_pos.add_row("Vencen en 30 dias",   str(len(prox)))
    console.print(Panel(t_pos, title="[bold blue]Posicion del Cliente[/bold blue]", expand=False))

    console.print()
    console.print(Panel(
        f"[bold green]PDF generado correctamente[/bold green]\n\n[white]{path_pdf}[/white]",
        title="[bold]Reporte Semanal de Coberturas[/bold]",
        border_style="green", expand=False,
    ))


def _imprimir_resultado_envio(resultado: dict) -> None:
    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("Canal", style="bold cyan")
    t.add_column("Estado")
    for canal, estado in resultado.items():
        if estado is None:
            continue
        if estado == "dry_run":
            t.add_row(canal.upper(), "[yellow]dry-run[/yellow]")
        elif estado is True:
            t.add_row(canal.upper(), "[green]Enviado[/green]")
        else:
            t.add_row(canal.upper(), "[red]Error[/red]")
    console.print(Panel(t, title="[bold]Envio[/bold]", expand=False))


# ---------------------------------------------------------------------------
# Lógica de generación + envío para un cliente
# ---------------------------------------------------------------------------

def _procesar_cliente(
    prospect_id: int,
    db_path: Path,
    output_dir: Path,
    canales: list[str],
    dry_run: bool,
) -> str | None:
    """Genera PDF y envía reporte para un cliente. Retorna path del PDF o None."""
    from agents.reports.report_generator import generar_datos_reporte, generar_pdf_reporte
    from agents.reports.report_sender import enviar_reporte
    from core.database import get_prospect

    datos = generar_datos_reporte(prospect_id, db_path=db_path)
    datos["_db_path"] = db_path

    fecha_str = date.today().isoformat()
    out_path  = str(output_dir / str(prospect_id) / fecha_str / "reporte.pdf")

    with console.status(f"[cyan]Generando PDF prospect_id={prospect_id}…[/cyan]"):
        path_pdf = generar_pdf_reporte(datos, output_path=out_path)

    _imprimir_resumen(datos, path_pdf)

    prospect = get_prospect(prospect_id, db_path=db_path) or {}
    resultado = enviar_reporte(
        datos_reporte=datos,
        pdf_path=path_pdf,
        prospect=prospect,
        canales=canales,
        dry_run=dry_run,
        db_path=db_path,
    )
    _imprimir_resultado_envio(resultado)
    return path_pdf


# ---------------------------------------------------------------------------
# Modo demo
# ---------------------------------------------------------------------------

def run_demo(output_dir: Path) -> None:
    from agents.reports.report_generator import generar_datos_reporte, generar_pdf_reporte

    console.print(Panel(
        "[bold yellow]Modo DEMO[/bold yellow] — BD temporal, sin envios reales",
        border_style="yellow", expand=False,
    ))

    with console.status("[cyan]Creando BD demo y datos ficticios…[/cyan]"):
        prospect_id = _crear_bd_demo(_DEMO_DB)
    console.print(f"[green]OK[/green] BD demo: [dim]{_DEMO_DB}[/dim]  "
                  f"(prospect_id={prospect_id})")

    with console.status("[cyan]Calculando datos del reporte…[/cyan]"):
        datos = generar_datos_reporte(prospect_id, db_path=_DEMO_DB)
        datos["_db_path"] = _DEMO_DB

    fecha_str = date.today().isoformat()
    out_path  = str(output_dir / str(prospect_id) / fecha_str / "reporte.pdf")

    with console.status("[cyan]Generando PDF…[/cyan]"):
        path_pdf = generar_pdf_reporte(datos, output_path=out_path)

    _imprimir_resumen(datos, path_pdf)


# ---------------------------------------------------------------------------
# Modo --now
# ---------------------------------------------------------------------------

def run_now(
    cliente_id: int | None,
    output_dir: Path,
    canales: list[str],
    dry_run: bool,
) -> None:
    from core.database import DB_PATH, get_connection

    db_path = DB_PATH

    if cliente_id is not None:
        prospect_ids = [cliente_id]
    else:
        with get_connection(db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT prospect_id FROM hedges WHERE estado = 'activa'"
            ).fetchall()
        prospect_ids = [r["prospect_id"] for r in rows]

    if not prospect_ids:
        console.print("[yellow]No hay clientes con coberturas activas en la BD.[/yellow]")
        return

    label = "dry-run" if dry_run else "envio"
    console.print(
        f"[cyan]Procesando {len(prospect_ids)} cliente(s) "
        f"| canales: {', '.join(canales)} | modo: {label}[/cyan]"
    )

    for pid in prospect_ids:
        try:
            _procesar_cliente(pid, db_path, output_dir, canales, dry_run)
        except Exception as exc:
            console.print(f"[red]Error en prospect_id={pid}: {exc}[/red]")
            logger.exception("Error procesando prospect_id=%d", pid)


# ---------------------------------------------------------------------------
# Modo --schedule
# ---------------------------------------------------------------------------

def run_dashboard(port: int = 8000, demo: bool = False) -> None:
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Falta la libreria 'uvicorn'. "
            "Instalala con: pip install uvicorn[/red]"
        )
        sys.exit(1)

    if demo:
        with console.status("[cyan]Creando BD demo para el dashboard…[/cyan]"):
            _crear_bd_demo(_DEMO_DB)
        console.print(f"[green]OK[/green] BD demo: [dim]{_DEMO_DB}[/dim]")

        # Inyectar la BD demo en el módulo dashboard antes de arrancar
        import agents.reports.dashboard as _dash
        _dash.DB_PATH = _DEMO_DB

        db_info = f"BD demo: [dim]{_DEMO_DB}[/dim]\n"
    else:
        db_info = ""

    console.print(Panel(
        f"[bold green]Dashboard interno activo[/bold green]\n\n"
        f"URL: [white]http://localhost:{port}[/white]\n"
        f"{db_info}"
        f"Password: variable de entorno [white]DASHBOARD_PASSWORD[/white] "
        f"(default: hedgepoint2026)\n"
        f"Ctrl+C para detener.",
        title="[bold]Dashboard HedgePoint MX[/bold]", border_style="blue", expand=False,
    ))
    uvicorn.run(
        "agents.reports.dashboard:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


def run_schedule(output_dir: Path, canales: list[str], dry_run: bool) -> None:
    try:
        import schedule
        import time
    except ImportError:
        console.print(
            "[red]Falta la libreria 'schedule'. "
            "Instalala con: pip install schedule[/red]"
        )
        sys.exit(1)

    def _job():
        console.print(f"[cyan]Ejecutando envio programado — {date.today().isoformat()}[/cyan]")
        run_now(cliente_id=None, output_dir=output_dir, canales=canales, dry_run=dry_run)

    schedule.every().monday.at("07:00").do(_job)

    proxima = schedule.next_run()
    console.print(Panel(
        f"[bold green]Loop semanal activo[/bold green]\n\n"
        f"Proxima ejecucion: [white]{proxima}[/white]\n"
        f"Canales: {', '.join(canales)}\n"
        f"Ctrl+C para detener.",
        title="[bold]Schedule[/bold]", border_style="blue", expand=False,
    ))

    try:
        while True:
            schedule.run_pending()
            import time as _time
            _time.sleep(30)
    except KeyboardInterrupt:
        console.print("\n[yellow]Loop detenido por el usuario.[/yellow]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generacion y envio de reportes semanales de coberturas — HedgePoint MX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scripts/run_reports.py --demo\n"
            "  python scripts/run_reports.py --now\n"
            "  python scripts/run_reports.py --now --cliente-id 3\n"
            "  python scripts/run_reports.py --now --dry-run\n"
            "  python scripts/run_reports.py --now --canales email,whatsapp\n"
            "  python scripts/run_reports.py --schedule\n"
        ),
    )

    parser.add_argument("--demo",       action="store_true",
                        help="BD temporal + datos ficticios, sin envios")
    parser.add_argument("--now",        action="store_true",
                        help="Genera y envia reportes inmediatamente")
    parser.add_argument("--schedule",   action="store_true",
                        help="Loop semanal: ejecuta cada lunes a las 7am")
    parser.add_argument("--dashboard",  action="store_true",
                        help="Levanta el dashboard web en el puerto 8000")
    parser.add_argument("--cliente-id", type=int, default=None, metavar="ID",
                        help="Limita el procesamiento a un solo cliente")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Genera PDFs pero no envia (imprime a quien enviaria)")
    parser.add_argument("--canales",    type=str, default="email", metavar="LISTA",
                        help="Canales separados por coma: email,whatsapp (default: email)")
    parser.add_argument("--output-dir", type=Path, default=Path("output/reports"),
                        metavar="DIR",
                        help="Directorio base para PDFs (default: output/reports/)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Activa logging DEBUG")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    canales = [c.strip().lower() for c in args.canales.split(",") if c.strip()]

    # Advertencia si falta RESEND_API_KEY y no es demo ni dry-run
    dry_run = args.dry_run
    if not args.demo and not dry_run:
        if not os.getenv("RESEND_API_KEY", "").strip():
            console.print(
                "[yellow]ADVERTENCIA: RESEND_API_KEY no configurada. "
                "Forzando --dry-run automatico.[/yellow]"
            )
            dry_run = True

    try:
        if args.dashboard:
            run_dashboard(demo=args.demo)
        elif args.demo:
            run_demo(output_dir)
        elif args.schedule:
            run_schedule(output_dir, canales, dry_run)
        else:
            # --now es el modo por defecto si no se pasa ningun flag de modo
            run_now(
                cliente_id=args.cliente_id,
                output_dir=output_dir,
                canales=canales,
                dry_run=dry_run,
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrumpido por el usuario.[/yellow]")
        sys.exit(0)
    except Exception as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}")
        if args.verbose:
            console.print_exception()
        sys.exit(1)


if __name__ == "__main__":
    main()
