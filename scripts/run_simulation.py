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
        "output": f"output/simulaciones/reporte_{nombre_archivo}.pdf",
    }


def ejecutar_simulacion_forward(
    volumen: float,
    margen: float,
    frecuencia: str,
    output: str,
    years: int = 1,
    spread: float = 0.05,
    markup: float = 0.00,
    fee: float = 15_000.0,
    con_plazos: bool = False,
    cobertura: float = 100.0,
    anio: int | None = None,
) -> None:
    """
    Ejecuta la simulación de forwards y genera el PDF.

    Args:
        volumen: Volumen mensual en USD.
        margen: Margen de utilidad como decimal (0.12 = 12%).
        frecuencia: Frecuencia de compra ('mensual', 'quincenal', 'semanal').
        output: Ruta del PDF de salida.
        years: Años de histórico a simular (ignorado si anio se especifica).
        spread: Spread del banco en MXN/USD (default: 0.05).
        markup: Markup HedgePoint en MXN/USD (default: 0.00 — sin markup en fase inicial).
        fee: Fee mensual HedgePoint en MXN (default: 15,000).
        con_plazos: Si True, ejecuta simulación multi-plazo (30/60/90d).
        cobertura: Porcentaje del volumen mensual cubierto con forward (default: 100).
        anio: Año calendario específico a analizar (p.ej. 2024). Si se especifica,
              sobrescribe years y simula solo ese año.
    """
    from agents.simulator.savings_simulator import (
        ParametrosCliente, SimuladorAhorro, simular_multi_plazo,
    )
    from agents.simulator.pdf_generator import generar_pdf

    _periodo_str = str(anio) if anio else f"{years} año(s)"
    print("\n" + "-" * 60)
    print("  Configuración de la simulación:")
    print(f"    Volumen mensual:    USD ${volumen:,.0f}")
    print(f"    Margen de utilidad: {margen*100:.1f}%")
    print(f"    Frecuencia:         {frecuencia}")
    print(f"    Período:            {_periodo_str}")
    print(f"    Spread banco:       ${spread:.2f} MXN/USD")
    print(f"    Markup HedgePoint:  ${markup:.2f} MXN/USD")
    print(f"    Fee mensual HP:     ${fee:,.0f} MXN")
    print(f"    Comparativa plazos: {'Sí (30/60/90d)' if con_plazos else 'No (solo 30d)'}")
    print(f"    Nivel de cobertura: {cobertura:.0f}%")
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
        cobertura_pct=cobertura,
    )

    # 2. Ejecutar simulación principal (plazo 30d)
    print("\nEjecutando simulación de backtesting (plazo 30 días)...")
    sim = SimuladorAhorro(params, years=years, anio=anio)
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


def ejecutar_simulacion_opciones(
    volumen: float,
    margen: float,
    frecuencia: str,
    output: str,
    years: int = 1,
    markup: float = 0.00,
    fee: float = 15_000.0,
    markup_banco_pct: float = 0.15,
) -> None:
    """
    Ejecuta la simulación de cobertura con opciones put y genera el PDF.

    Args:
        volumen: Volumen mensual en USD.
        margen: Margen de utilidad como decimal (0.12 = 12%).
        frecuencia: Frecuencia de compra ('mensual', 'quincenal', 'semanal').
        output: Ruta del PDF de salida.
        years: Años de histórico a simular.
        markup: Markup HedgePoint en MXN/USD (default: 0.00 — sin markup en fase inicial).
        fee: Fee mensual HedgePoint en MXN (default: 15,000).
        markup_banco_pct: Markup del banco sobre la prima teórica GK (default: 15%).
    """
    from agents.simulator.savings_simulator import (
        ParametrosCliente, simulate_options_strategy,
    )
    from agents.simulator.pdf_generator import generar_pdf_opciones

    print("\n" + "-" * 60)
    print("  Configuración de la simulación (Opciones Put):")
    print(f"    Volumen mensual:      USD ${volumen:,.0f}")
    print(f"    Margen de utilidad:   {margen*100:.1f}%")
    print(f"    Frecuencia:           {frecuencia}")
    print(f"    Período:              {years} años")
    print(f"    Markup HedgePoint:    ${markup:.2f} MXN/USD")
    print(f"    Fee mensual HP:       ${fee:,.0f} MXN")
    print(f"    Markup banco (prima): {markup_banco_pct*100:.0f}%")
    print(f"    Instrumento:          Put ATM Garman-Kohlhagen")
    print(f"    Archivo de salida:    {output}")
    print("-" * 60)

    params = ParametrosCliente(
        volumen_mensual_usd=volumen,
        margen_utilidad=margen,
        frecuencia=frecuencia,  # type: ignore[arg-type]
        spread_banco=0.0,       # no aplica para opciones (el banco cobra vía prima)
        markup_hedgepoint=markup,
        fee_mensual=fee,
    )

    print("\nEjecutando simulación de opciones put (backtesting)...")
    try:
        resultado = simulate_options_strategy(
            parametros=params,
            years=years,
            markup_banco_pct=markup_banco_pct,
        )
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

    print("\n" + resultado.resumen())

    print("\nGenerando PDF profesional (opciones)...")
    try:
        ruta_pdf = generar_pdf_opciones(resultado, output)
        print(f"\n  PDF generado exitosamente:")
        print(f"  → {ruta_pdf.resolve()}")
    except ImportError as e:
        print(f"\n[ERROR] Dependencia faltante para generar el PDF: {e}")
        print("Instala con: pip install reportlab matplotlib")
        sys.exit(1)
    except Exception as e:
        logger.exception("Error al generar el PDF de opciones")
        print(f"\n[ERROR] No se pudo generar el PDF: {e}")
        sys.exit(1)

    print("\n¡Simulación de opciones completada!")


def ejecutar_simulacion_collar(
    volumen: float,
    margen: float,
    frecuencia: str,
    output: str,
    years: int = 1,
    markup: float = 0.00,
    fee: float = 15_000.0,
    markup_banco_pct: float = 0.15,
    call_otm_pct: float = 0.03,
) -> None:
    """
    Ejecuta la simulación de cobertura con collar (put ATM + call OTM vendido) y genera el PDF.

    Args:
        volumen: Volumen mensual en USD.
        margen: Margen de utilidad como decimal (0.12 = 12%).
        frecuencia: Frecuencia de compra ('mensual', 'quincenal', 'semanal').
        output: Ruta del PDF de salida.
        years: Años de histórico a simular.
        markup: Markup HedgePoint en MXN/USD (default: 0.00 — sin markup en fase inicial).
        fee: Fee mensual HedgePoint en MXN (default: 15,000).
        markup_banco_pct: Markup del banco sobre las primas teóricas GK (default: 15%).
        call_otm_pct: Distancia OTM del call vendido como fracción (default: 3%).
    """
    from agents.simulator.savings_simulator import (
        ParametrosCliente, simulate_collar_strategy,
    )
    from agents.simulator.pdf_generator import generar_pdf_collar

    print("\n" + "-" * 60)
    print("  Configuración de la simulación (Collar):")
    print(f"    Volumen mensual:      USD ${volumen:,.0f}")
    print(f"    Margen de utilidad:   {margen*100:.1f}%")
    print(f"    Frecuencia:           {frecuencia}")
    print(f"    Período:              {years} años")
    print(f"    Markup HedgePoint:    ${markup:.2f} MXN/USD")
    print(f"    Fee mensual HP:       ${fee:,.0f} MXN")
    print(f"    Markup banco (prima): {markup_banco_pct*100:.0f}%")
    print(f"    Call OTM strike:      +{call_otm_pct*100:.1f}% sobre spot")
    print(f"    Instrumento:          Put ATM + Call OTM vendido (Garman-Kohlhagen)")
    print(f"    Archivo de salida:    {output}")
    print("-" * 60)

    params = ParametrosCliente(
        volumen_mensual_usd=volumen,
        margen_utilidad=margen,
        frecuencia=frecuencia,  # type: ignore[arg-type]
        spread_banco=0.0,       # no aplica para opciones (el banco cobra vía prima)
        markup_hedgepoint=markup,
        fee_mensual=fee,
    )

    print("\nEjecutando simulación de collar (backtesting)...")
    try:
        resultado = simulate_collar_strategy(
            parametros=params,
            years=years,
            markup_banco_pct=markup_banco_pct,
            call_otm_pct=call_otm_pct,
        )
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

    print("\n" + resultado.resumen())

    print("\nGenerando PDF profesional (collar)...")
    try:
        ruta_pdf = generar_pdf_collar(resultado, output)
        print(f"\n  PDF generado exitosamente:")
        print(f"  → {ruta_pdf.resolve()}")
    except ImportError as e:
        print(f"\n[ERROR] Dependencia faltante para generar el PDF: {e}")
        print("Instala con: pip install reportlab matplotlib")
        sys.exit(1)
    except Exception as e:
        logger.exception("Error al generar el PDF de collar")
        print(f"\n[ERROR] No se pudo generar el PDF: {e}")
        sys.exit(1)

    print("\n¡Simulación de collar completada!")


def ejecutar_simulacion_comparativa(
    volumen: float,
    margen: float,
    frecuencia: str,
    output: str,
    years: int = 1,
    spread: float = 0.05,
    markup: float = 0.00,
    fee: float = 15_000.0,
    markup_banco_pct: float = 0.15,
    call_otm_pct: float = 0.03,
) -> None:
    """
    Ejecuta las 3 estrategias base, calcula la mezcla óptima y genera el PDF comparativo.

    Args:
        volumen: Volumen mensual en USD.
        margen: Margen de utilidad como decimal (0.12 = 12%).
        frecuencia: Frecuencia de compra ('mensual', 'quincenal', 'semanal').
        output: Ruta del PDF de salida.
        years: Años de histórico a simular.
        spread: Spread del banco en MXN/USD (aplica al forward).
        markup: Markup HedgePoint en MXN/USD.
        fee: Fee mensual HedgePoint en MXN.
        markup_banco_pct: Markup del banco sobre primas teóricas GK (opciones y collar).
        call_otm_pct: Distancia OTM del put vendido en el collar.
    """
    from agents.simulator.savings_simulator import (
        ParametrosCliente, SimuladorAhorro, simulate_options_strategy,
        simulate_collar_strategy, find_optimal_mix,
    )
    from agents.simulator.pdf_generator import generar_pdf

    print("\n" + "-" * 60)
    print("  Configuración de la simulación (Comparativa de Estrategias):")
    print(f"    Volumen mensual:      USD ${volumen:,.0f}")
    print(f"    Margen de utilidad:   {margen*100:.1f}%")
    print(f"    Frecuencia:           {frecuencia}")
    print(f"    Período:              {years} años")
    print(f"    Spread banco:         ${spread:.2f} MXN/USD")
    print(f"    Markup HedgePoint:    ${markup:.2f} MXN/USD")
    print(f"    Fee mensual HP:       ${fee:,.0f} MXN")
    print(f"    Markup banco (prima): {markup_banco_pct*100:.0f}%")
    print(f"    Put OTM strike:       -{call_otm_pct*100:.1f}% bajo spot (collar)")
    print(f"    Estrategias:          Forward, Opciones Put ATM, Collar")
    print(f"    Archivo de salida:    {output}")
    print("-" * 60)

    params = ParametrosCliente(
        volumen_mensual_usd=volumen,
        margen_utilidad=margen,
        frecuencia=frecuencia,  # type: ignore[arg-type]
        spread_banco=spread,
        markup_hedgepoint=markup,
        fee_mensual=fee,
        cobertura_pct=100.0,
    )

    # 1. Simulación forward base (para el PDF principal y resumen)
    print("\nEjecutando simulación forward (base)...")
    try:
        sim_fwd = SimuladorAhorro(params, years=years)
        resultado_fwd = sim_fwd.ejecutar()
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    print("  Forward: OK  —", resultado_fwd.resumen().splitlines()[0])

    # 2. Simulación opciones
    print("\nEjecutando simulación de opciones put...")
    try:
        resultado_op = simulate_options_strategy(
            parametros=params,
            years=years,
            markup_banco_pct=markup_banco_pct,
        )
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    print("  Opciones: OK  —", resultado_op.resumen().splitlines()[0])

    # 3. Simulación collar
    print("\nEjecutando simulación de collar...")
    try:
        resultado_col = simulate_collar_strategy(
            parametros=params,
            years=years,
            markup_banco_pct=markup_banco_pct,
            call_otm_pct=call_otm_pct,
        )
    except ValueError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    print("  Collar: OK  —", resultado_col.resumen().splitlines()[0])

    # 4. Calcular mezcla óptima
    print("\nCalculando mezcla óptima (evaluando combinaciones en 25%)...")
    try:
        comparativa = find_optimal_mix(
            parametros=params,
            years=years,
            markup_banco_pct=markup_banco_pct,
            call_otm_pct=call_otm_pct,
        )
    except Exception as e:
        logger.exception("Error en find_optimal_mix")
        print(f"\n[ERROR] No se pudo calcular la mezcla óptima: {e}")
        sys.exit(1)

    mix = comparativa.mix_optimo
    print(f"\n  Mix óptimo: {mix.instrumento_principal}")
    print(f"    Forward: {mix.pct_forward:.0f}%  |  Opciones: {mix.pct_opcion:.0f}%  |  Collar: {mix.pct_collar:.0f}%  |  Spot: {mix.pct_sin_cubrir:.0f}%")
    print(f"    Costo total:        ${mix.costo_total_mxn:,.0f} MXN")
    print(f"    Ahorro vs spot:     ${mix.costo_vs_spot_mxn:,.0f} MXN")
    print(f"    Ratio costo/prot.:  {mix.ratio_costo_proteccion:,.0f}")

    # 5. Generar PDF con sección comparativa integrada
    print("\nGenerando PDF comparativo...")
    try:
        ruta_pdf = generar_pdf(resultado_fwd, output, comparativa=comparativa)
        print(f"\n  PDF generado exitosamente:")
        print(f"  → {ruta_pdf.resolve()}")
    except ImportError as e:
        print(f"\n[ERROR] Dependencia faltante para generar el PDF: {e}")
        print("Instala con: pip install reportlab matplotlib")
        sys.exit(1)
    except Exception as e:
        logger.exception("Error al generar el PDF comparativo")
        print(f"\n[ERROR] No se pudo generar el PDF: {e}")
        sys.exit(1)

    print("\n¡Simulación comparativa completada!")


def main() -> None:
    """Punto de entrada principal del script."""
    _verificar_dependencias()

    parser = argparse.ArgumentParser(
        description="Simulador de cobertura cambiaria — HedgePoint MX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python scripts/run_simulation.py --demo
  python scripts/run_simulation.py --volumen 300000 --margen 12 --year 2024
  python scripts/run_simulation.py --volumen 300000 --margen 12 --estrategia forward --plazos
  python scripts/run_simulation.py --volumen 300000 --margen 12 --estrategia opcion
  python scripts/run_simulation.py --volumen 300000 --margen 12 --estrategia opcion --markup-banco 20
  python scripts/run_simulation.py --volumen 500000 --margen 8 --spread 0.05 --markup 0.00 --fee 15000 --year 2023
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
        default=1,
        help="Años de histórico a simular (default: 1)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        dest="year",
        help=(
            "Año calendario específico a analizar (p.ej. 2024). "
            "Si se especifica, sobrescribe --years y simula solo ese año. "
            "Default: último año con datos completos."
        ),
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
        default=0.00,
        help="Markup HedgePoint en MXN/USD (default: 0.00 — sin markup en fase inicial)",
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
        "--cobertura",
        type=float,
        default=100.0,
        help="Porcentaje del volumen mensual cubierto con forward (default: 100)",
    )
    parser.add_argument(
        "--estrategia",
        type=str,
        choices=["forward", "opcion", "collar", "comparativa", "optima"],
        default="forward",
        help=(
            "Estrategia de cobertura a simular (default: forward). "
            "'forward': forwards a 30d. "
            "'opcion': puts ATM Garman-Kohlhagen. "
            "'collar': call ATM comprado + put OTM vendido. "
            "'comparativa'/'optima': compara las 3 estrategias y encuentra la mezcla óptima."
        ),
    )
    parser.add_argument(
        "--markup-banco",
        type=float,
        default=15.0,
        dest="markup_banco",
        help=(
            "Markup del banco sobre la prima teórica de la opción, en %% "
            "(aplica con --estrategia opcion y collar, default: 15)"
        ),
    )
    parser.add_argument(
        "--call-otm",
        type=float,
        default=3.0,
        dest="call_otm",
        help=(
            "Distancia OTM del call vendido en el collar, en %% sobre spot "
            "(solo aplica con --estrategia collar, default: 3)"
        ),
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Omitir verificación/descarga de datos históricos",
    )

    args = parser.parse_args()

    _ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Comparativa y optima comparten el mismo sufijo de archivo
    _sufijo = "comparativa" if args.estrategia in ("comparativa", "optima") else args.estrategia

    # Modo demo: importador mediano de referencia, --plazos activado por defecto
    if args.demo:
        print(
            f"\n[DEMO] Usando datos de ejemplo: importador mediano "
            f"($300k USD/mes, 12% margen, estrategia: {_sufijo})"
        )
        volumen = 300_000.0
        margen = 0.12
        frecuencia = "mensual"
        spread = args.spread      # respeta override del usuario
        markup = args.markup
        fee = args.fee
        con_plazos = True         # siempre activo en modo demo
        cobertura = args.cobertura
        output = args.output or f"output/simulaciones/reporte_demo_{_sufijo}_{_ts}.pdf"
    elif args.volumen is not None and args.margen is not None:
        volumen = args.volumen
        margen = args.margen / 100.0 if args.margen > 1 else args.margen
        frecuencia = args.frecuencia
        spread = args.spread
        markup = args.markup
        fee = args.fee
        con_plazos = args.plazos
        cobertura = args.cobertura
        output = args.output or f"output/simulaciones/reporte_{_sufijo}_{_ts}.pdf"
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
        cobertura = args.cobertura
        # El nombre interactivo también lleva timestamp y estrategia
        base = params_i["output"].replace(".pdf", "")
        output = args.output or f"{base}_{_sufijo}_{_ts}.pdf"

    # Verificar / descargar histórico
    if not args.skip_download:
        from datetime import date as _date
        _hoy = _date.today()
        _years_para_descarga = (
            (_hoy.year - args.year + 1) if args.year else args.years
        )
        _verificar_o_descargar_historico(years=_years_para_descarga)

    # Dispatcher por estrategia
    estrategia = args.estrategia

    if estrategia == "forward":
        ejecutar_simulacion_forward(
            volumen=volumen,
            margen=margen,
            frecuencia=frecuencia,
            output=output,
            years=args.years,
            spread=spread,
            markup=markup,
            fee=fee,
            con_plazos=con_plazos,
            cobertura=cobertura,
            anio=args.year,
        )

    elif estrategia == "opcion":
        ejecutar_simulacion_opciones(
            volumen=volumen,
            margen=margen,
            frecuencia=frecuencia,
            output=output,
            years=args.years,
            markup=markup,
            fee=fee,
            markup_banco_pct=args.markup_banco / 100.0,
        )

    elif estrategia == "collar":
        ejecutar_simulacion_collar(
            volumen=volumen,
            margen=margen,
            frecuencia=frecuencia,
            output=output,
            years=args.years,
            markup=markup,
            fee=fee,
            markup_banco_pct=args.markup_banco / 100.0,
            call_otm_pct=args.call_otm / 100.0,
        )

    elif estrategia in ("comparativa", "optima"):
        ejecutar_simulacion_comparativa(
            volumen=volumen,
            margen=margen,
            frecuencia=frecuencia,
            output=output,
            years=args.years,
            spread=spread,
            markup=markup,
            fee=fee,
            markup_banco_pct=args.markup_banco / 100.0,
            call_otm_pct=args.call_otm / 100.0,
        )


if __name__ == "__main__":
    main()
