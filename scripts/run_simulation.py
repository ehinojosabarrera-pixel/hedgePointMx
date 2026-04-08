"""
Script ejecutable para correr la simulación de ahorro por cobertura forward.

Descarga el histórico si es necesario, ejecuta el backtesting y genera el PDF.

Uso interactivo:
    python scripts/run_simulation.py

Uso con argumentos (ejemplo importador mediano):
    python scripts/run_simulation.py \
        --volumen 300000 \
        --margen 0.12 \
        --frecuencia mensual \
        --output output/reporte_importador_demo.pdf

Ejemplo rápido con datos demo:
    python scripts/run_simulation.py --demo
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Asegurar que el root del proyecto esté en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _verificar_dependencias() -> None:
    """Verifica que las dependencias necesarias estén instaladas."""
    faltantes = []
    try:
        import reportlab  # noqa: F401
    except ImportError:
        faltantes.append("reportlab")
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        faltantes.append("matplotlib")
    try:
        import pandas  # noqa: F401
    except ImportError:
        faltantes.append("pandas")

    if faltantes:
        print(f"\n[ERROR] Dependencias faltantes: {', '.join(faltantes)}")
        print("Instala con: pip install " + " ".join(faltantes))
        sys.exit(1)


def _verificar_o_descargar_historico(years: int = 2) -> None:
    """
    Verifica si hay datos históricos en la DB; si no, los descarga.

    Args:
        years: Años de histórico requeridos.
    """
    from core.database import get_connection, DB_PATH, init_db
    from datetime import date, timedelta

    init_db(DB_PATH)

    with get_connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT MIN(fecha) AS f_min, MAX(fecha) AS f_max, COUNT(*) AS total "
            "FROM fx_rates WHERE par = 'USD/MXN'"
        ).fetchone()

    total = row["total"] if row else 0
    fecha_min = row["f_min"] if row else None
    fecha_max = row["f_max"] if row else None

    hoy = date.today()
    fecha_requerida = hoy - timedelta(days=years * 365 + 35)  # 35d extra

    necesita_descarga = False
    if total == 0:
        print("\nNo hay datos históricos en la base de datos.")
        necesita_descarga = True
    elif fecha_min and fecha_min > str(fecha_requerida):
        print(
            f"\nDatos disponibles desde {fecha_min}, "
            f"pero se requieren desde {fecha_requerida}."
        )
        necesita_descarga = True
    else:
        print(
            f"\nDatos históricos disponibles: {total:,} registros "
            f"({fecha_min} → {fecha_max})"
        )

    if necesita_descarga:
        print("Descargando histórico de Banxico (esto puede tardar unos segundos)...")
        from scripts.fetch_historical import fetch_historico, verificar_cobertura
        try:
            insertados = fetch_historico(years=years + 1)  # +1 para margen en forward
            print(f"  {insertados:,} registros nuevos descargados.")
            verificar_cobertura()
        except EnvironmentError as e:
            print(f"\n[ERROR] {e}")
            print(
                "Agrega BANXICO_API_KEY a tu archivo .env y vuelve a ejecutar.\n"
                "Obtén tu token gratuito en: https://www.banxico.org.mx/SieAPIRest/"
            )
            sys.exit(1)
        except (ConnectionError, ValueError) as e:
            print(f"\n[ERROR] No se pudo descargar el histórico: {e}")
            sys.exit(1)


def _solicitar_parametros_interactivo() -> dict:
    """Solicita los parámetros al usuario de forma interactiva."""
    print("\n" + "=" * 60)
    print("  SIMULADOR DE AHORRO — HedgePoint MX")
    print("=" * 60)
    print("Ingresa los datos de tu empresa (Enter para usar el valor por defecto):\n")

    def _pedir_float(prompt: str, default: float) -> float:
        while True:
            entrada = input(f"  {prompt} [{default:,.0f}]: ").strip()
            if not entrada:
                return default
            try:
                valor = float(entrada.replace(",", "").replace("$", ""))
                if valor <= 0:
                    print("  El valor debe ser positivo. Intenta de nuevo.")
                    continue
                return valor
            except ValueError:
                print("  Ingresa un número válido.")

    def _pedir_porcentaje(prompt: str, default: float) -> float:
        while True:
            entrada = input(f"  {prompt} [{default*100:.0f}%]: ").strip()
            if not entrada:
                return default
            try:
                valor = float(entrada.replace("%", "")) / 100
                if valor <= 0 or valor > 1:
                    print("  Ingresa un porcentaje entre 1 y 100.")
                    continue
                return valor
            except ValueError:
                print("  Ingresa un número válido (p.ej. 12 para 12%).")

    def _pedir_opcion(prompt: str, opciones: list[str], default: str) -> str:
        opciones_str = "/".join(opciones)
        while True:
            entrada = input(f"  {prompt} [{opciones_str}] (default: {default}): ").strip().lower()
            if not entrada:
                return default
            if entrada in opciones:
                return entrada
            print(f"  Opciones válidas: {opciones_str}")

    volumen = _pedir_float("Volumen mensual en USD", 300_000)
    margen = _pedir_porcentaje("Margen de utilidad (%)", 0.12)
    frecuencia = _pedir_opcion(
        "Frecuencia de compra",
        ["mensual", "quincenal", "semanal"],
        "mensual",
    )

    nombre_empresa = input("\n  Nombre de la empresa (opcional, para el nombre del PDF): ").strip()
    nombre_archivo = (
        nombre_empresa.lower().replace(" ", "_") if nombre_empresa
        else "cliente_demo"
    )

    return {
        "volumen": volumen,
        "margen": margen,
        "frecuencia": frecuencia,
        "output": f"output/reporte_{nombre_archivo}.pdf",
    }


def ejecutar_simulacion(
    volumen: float,
    margen: float,
    frecuencia: str,
    output: str,
    years: int = 5,
    spread: float = 0.05,
    markup: float = 0.04,
    fee: float = 15_000.0,
    con_plazos: bool = False,
) -> None:
    """
    Ejecuta la simulación completa y genera el PDF.

    Args:
        volumen: Volumen mensual en USD.
        margen: Margen de utilidad como decimal (0.12 = 12%).
        frecuencia: Frecuencia de compra ('mensual', 'quincenal', 'semanal').
        output: Ruta del PDF de salida.
        years: Años de histórico a simular.
        spread: Spread del banco en MXN/USD (default: 0.05).
        markup: Markup HedgePoint en MXN/USD (default: 0.04).
        fee: Fee mensual HedgePoint en MXN (default: 15,000).
        con_plazos: Si True, ejecuta simulación multi-plazo (30/60/90d).
    """
    from agents.simulator.savings_simulator import (
        ParametrosCliente, SimuladorAhorro, simular_multi_plazo,
    )
    from agents.simulator.pdf_generator import generar_pdf

    print("\n" + "-" * 60)
    print("  Configuración de la simulación:")
    print(f"    Volumen mensual:    USD ${volumen:,.0f}")
    print(f"    Margen de utilidad: {margen*100:.1f}%")
    print(f"    Frecuencia:         {frecuencia}")
    print(f"    Período:            {years} años")
    print(f"    Spread banco:       ${spread:.2f} MXN/USD")
    print(f"    Markup HedgePoint:  ${markup:.2f} MXN/USD")
    print(f"    Fee mensual HP:     ${fee:,.0f} MXN")
    print(f"    Comparativa plazos: {'Sí (30/60/90d)' if con_plazos else 'No (solo 30d)'}")
    print(f"    Archivo de salida:  {output}")
    print("-" * 60)

    # 1. Parámetros del cliente
    params = ParametrosCliente(
        volumen_mensual_usd=volumen,
        margen_utilidad=margen,
        frecuencia=frecuencia,  # type: ignore[arg-type]
        spread_banco=spread,
        markup_hedgepoint=markup,
        fee_mensual=fee,
    )

    # 2. Ejecutar simulación principal (plazo 30d)
    print("\nEjecutando simulación de backtesting (plazo 30 días)...")
    sim = SimuladorAhorro(params, years=years)
    try:
        resultado = sim.ejecutar()
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

    print("\n" + resultado.resumen())

    # 3. Simulación multi-plazo (opcional)
    multi_plazo = None
    if con_plazos:
        print("\nEjecutando comparativa multi-plazo (30/60/90 días) en paralelo...")
        try:
            multi_plazo = simular_multi_plazo(params, years=years)
            print("\n" + multi_plazo.resumen())
        except ValueError as e:
            print(f"\n[AVISO] No se pudo generar la comparativa multi-plazo: {e}")

    # 4. Generar PDF
    print("\nGenerando PDF profesional...")
    try:
        ruta_pdf = generar_pdf(resultado, output, multi_plazo=multi_plazo)
        print(f"\n  PDF generado exitosamente:")
        print(f"  → {ruta_pdf.resolve()}")
    except ImportError as e:
        print(f"\n[ERROR] Dependencia faltante para generar el PDF: {e}")
        print("Instala con: pip install reportlab matplotlib")
        sys.exit(1)
    except Exception as e:
        logger.exception("Error al generar el PDF")
        print(f"\n[ERROR] No se pudo generar el PDF: {e}")
        sys.exit(1)

    print("\n¡Simulación completada!")


def main() -> None:
    """Punto de entrada principal del script."""
    _verificar_dependencias()

    parser = argparse.ArgumentParser(
        description="Simulador de ahorro por cobertura forward — HedgePoint MX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/run_simulation.py --demo
  python scripts/run_simulation.py --volumen 300000 --margen 12 --frecuencia mensual --plazos
  python scripts/run_simulation.py --volumen 500000 --margen 8 --spread 0.05 --markup 0.04 --fee 15000 --plazos
  python scripts/run_simulation.py --volumen 300000 --margen 12 --spread 0 --markup 0 --fee 0
        """,
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Ejecutar con datos demo: importador mediano $300k USD/mes, 12%% margen",
    )
    parser.add_argument(
        "--volumen",
        type=float,
        default=None,
        help="Volumen mensual en USD (p.ej. 300000)",
    )
    parser.add_argument(
        "--margen",
        type=float,
        default=None,
        help="Margen de utilidad en %% (p.ej. 12 para 12%%)",
    )
    parser.add_argument(
        "--frecuencia",
        type=str,
        choices=["mensual", "quincenal", "semanal"],
        default="mensual",
        help="Frecuencia de compra de divisas (default: mensual)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Ruta del PDF de salida (default: output/reporte_simulacion.pdf)",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Años de histórico a simular (default: 5)",
    )
    parser.add_argument(
        "--spread",
        type=float,
        default=0.05,
        help="Spread del banco en MXN/USD (default: 0.05)",
    )
    parser.add_argument(
        "--markup",
        type=float,
        default=0.04,
        help="Markup HedgePoint en MXN/USD (default: 0.04)",
    )
    parser.add_argument(
        "--fee",
        type=float,
        default=15_000.0,
        help="Fee mensual HedgePoint en MXN (default: 15000)",
    )
    parser.add_argument(
        "--plazos",
        action="store_true",
        help="Activar comparativa multi-plazo (30, 60 y 90 días)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Omitir verificación/descarga de datos históricos",
    )

    args = parser.parse_args()

    _ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Modo demo: importador mediano de referencia, --plazos activado por defecto
    if args.demo:
        print("\n[DEMO] Usando datos de ejemplo: importador mediano ($300k USD/mes, 12% margen)")
        volumen = 300_000.0
        margen = 0.12
        frecuencia = "mensual"
        spread = args.spread      # respeta override del usuario
        markup = args.markup
        fee = args.fee
        con_plazos = True         # siempre activo en modo demo
        output = args.output or f"output/reporte_demo_importador_{_ts}.pdf"
    elif args.volumen is not None and args.margen is not None:
        volumen = args.volumen
        margen = args.margen / 100.0 if args.margen > 1 else args.margen
        frecuencia = args.frecuencia
        spread = args.spread
        markup = args.markup
        fee = args.fee
        con_plazos = args.plazos
        output = args.output or f"output/reporte_simulacion_{_ts}.pdf"
    else:
        # Modo interactivo
        params_i = _solicitar_parametros_interactivo()
        volumen = params_i["volumen"]
        margen = params_i["margen"]
        frecuencia = params_i["frecuencia"]
        spread = args.spread
        markup = args.markup
        fee = args.fee
        con_plazos = args.plazos
        # El nombre interactivo también lleva timestamp
        base = params_i["output"].replace(".pdf", "")
        output = args.output or f"{base}_{_ts}.pdf"

    # Verificar / descargar histórico
    if not args.skip_download:
        _verificar_o_descargar_historico(years=args.years)

    # Ejecutar simulación y generar PDF
    ejecutar_simulacion(
        volumen=volumen,
        margen=margen,
        frecuencia=frecuencia,
        output=output,
        years=args.years,
        spread=spread,
        markup=markup,
        fee=fee,
        con_plazos=con_plazos,
    )


if __name__ == "__main__":
    main()
