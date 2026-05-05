"""
CLI para generar una Orden de Cobertura Cambiaria — HedgePoint MX.

El PDF resultante es el documento pre-llenado que el cliente entrega a su
ejecutivo bancario para contratar la cobertura.  Todo el procesamiento es
local; no se llama a ninguna API externa ni a Claude/Anthropic.

Uso básico:
    python scripts/run_order.py --demo
    python scripts/run_order.py --demo --tipo opcion
    python scripts/run_order.py --demo --tipo collar --plazo 60

Uso con cliente real (requiere BD y .env con HEDGEPOINT_ENCRYPTION_KEY):
    python scripts/run_order.py \\
        --cliente-id 3 \\
        --tipo forward \\
        --monto 200000 \\
        --plazo 90 \\
        --capa "Táctica 1" \\
        --justificacion "Trigger activado: tipo de cambio superó $18.80." \\
        --output-dir outputs/ordenes
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Root del proyecto en sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def _verificar_dependencias() -> None:
    faltantes = []
    try:
        import reportlab  # noqa: F401
    except ImportError:
        faltantes.append("reportlab")
    if faltantes:
        print(f"\n[ERROR] Dependencias faltantes: {', '.join(faltantes)}")
        print("Instala con: pip install " + " ".join(faltantes))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Console output helpers
# ---------------------------------------------------------------------------

def _linea(char: str = "-", n: int = 60) -> str:
    return char * n


def _banner() -> None:
    print()
    print(_linea("="))
    print("  ORDEN DE COBERTURA CAMBIARIA - HedgePoint MX")
    print(_linea("="))
    print()


def _imprimir_resumen(datos, ruta_pdf: str) -> None:
    """Imprime un panel resumido de la orden generada."""
    d = datos
    print(_linea())
    print("  RESUMEN DE LA ORDEN")
    print(_linea())
    print(f"  Empresa         : {d.empresa}")
    print(f"  Instrumento     : {d.tipo.upper()}  |  Capa: {d.capa}")
    print(f"  Monto           : USD ${d.monto_usd:,.0f}")
    print(f"  Plazo           : {d.plazo_dias} dias")
    print(f"  Inicio          : {d.fecha_inicio.isoformat()}")
    print(f"  Vencimiento     : {d.fecha_vencimiento.isoformat()}")
    print(f"  Strike          : ${d.strike:.4f} MXN/USD")
    if d.strike_call:
        print(f"  Strike call     : ${d.strike_call:.4f} MXN/USD")
    print()
    print("  CONDICIONES DE MERCADO")
    print(_linea())
    print(f"  Spot USD/MXN    : ${d.spot:.4f}  (bid ${d.bid:.4f} / ask ${d.ask:.4f})")
    print(f"  Volatilidad 30d : {d.volatilidad_30d:.1f}% anualizada")
    print(f"  Fuente          : {d.fuente_mercado}  [{d.hora_cotizacion}]")
    print()
    print("  PRECIO TEORICO")
    print(_linea())
    if d.forward_teorico is not None:
        print(f"  Forward teorico : ${d.forward_teorico:.4f} MXN/USD")
    if d.prima_put is not None:
        print(f"  Prima put (GK)  : ${d.prima_put:.4f} MXN/USD")
    if d.prima_call is not None:
        print(f"  Prima call (GK) : ${d.prima_call:.4f} MXN/USD")
    if d.prima_neta is not None:
        lbl = "Prima neta collar" if d.tipo == "collar" else "Prima neta"
        print(f"  {lbl:<18}: ${d.prima_neta:.4f} MXN/USD")
    print()
    print("  POSICION DEL CLIENTE")
    print(_linea())
    print(f"  Coberturas activas  : {len(d.coberturas_activas)}")
    print(f"  Monto cubierto hoy  : USD ${d.monto_cubierto_usd:,.0f}  "
          f"({d.pct_cubierto_actual:.0f}% del volumen mensual)")
    monto_despues = d.monto_cubierto_usd + d.monto_usd
    pct_despues   = (monto_despues / d.volumen_mensual_usd * 100) if d.volumen_mensual_usd > 0 else 0.0
    print(f"  Cubierto post-orden : USD ${monto_despues:,.0f}  "
          f"({pct_despues:.0f}%)")
    print()
    print(_linea())
    print(f"  PDF generado exitosamente:")
    print(f"  -> {Path(ruta_pdf).resolve()}")
    print(_linea())
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    _verificar_dependencias()

    parser = argparse.ArgumentParser(
        prog="run_order.py",
        description="Genera la Orden de Cobertura Cambiaria en PDF — HedgePoint MX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  # Modo demo (sin BD ni API keys):
  python scripts/run_order.py --demo
  python scripts/run_order.py --demo --tipo opcion --plazo 60
  python scripts/run_order.py --demo --tipo collar --monto 300000

  # Orden real con cliente en BD:
  python scripts/run_order.py \\
      --cliente-id 3 \\
      --tipo forward \\
      --monto 200000 \\
      --plazo 90 \\
      --capa "Táctica 1" \\
      --justificacion "Trigger: USD/MXN superó $18.80." \\
      --output-dir outputs/ordenes

  # Strike manual:
  python scripts/run_order.py --demo --tipo forward --strike 19.10
        """,
    )

    # Modo
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Genera la orden con datos ficticios (no requiere BD ni API keys)",
    )

    # Cliente (requerido si no es --demo)
    parser.add_argument(
        "--cliente-id",
        type=int,
        default=None,
        dest="cliente_id",
        help="ID del cliente en la tabla prospects de la BD",
    )

    # Instrumento
    parser.add_argument(
        "--tipo",
        type=str,
        choices=["forward", "opcion", "collar"],
        default="forward",
        help="Tipo de instrumento de cobertura (default: forward)",
    )
    parser.add_argument(
        "--monto",
        type=float,
        default=200_000.0,
        help="Monto a cubrir en USD (default: 200000)",
    )
    parser.add_argument(
        "--plazo",
        type=int,
        default=90,
        help="Plazo de la cobertura en días naturales (default: 90)",
    )
    parser.add_argument(
        "--strike",
        type=float,
        default=None,
        help="Strike sugerido en MXN/USD.  Si no se pasa, se calcula automáticamente.",
    )
    parser.add_argument(
        "--capa",
        type=str,
        default="Base",
        help='Etiqueta de la capa de cobertura (ej. "Base", "Táctica 1") (default: Base)',
    )

    # Contexto
    parser.add_argument(
        "--justificacion",
        type=str,
        default="",
        help="Texto libre con la justificación de la operación (razón del trigger, etc.)",
    )

    # Salida
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        dest="output_dir",
        help="Directorio de salida (default: outputs/ en el root del proyecto)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Ruta exacta del PDF de salida (sobreescribe --output-dir)",
    )

    args = parser.parse_args()

    _banner()

    from agents.orders.coverage_order import (
        construir_datos_orden, datos_demo, generar_pdf_orden,
    )

    # -----------------------------------------------------------------------
    # Obtener DatosOrden
    # -----------------------------------------------------------------------
    if args.demo:
        print("  Modo DEMO — usando datos ficticios.\n")
        datos = datos_demo(
            tipo=args.tipo,
            monto_usd=args.monto,
            plazo_dias=args.plazo,
            capa=args.capa,
            justificacion=args.justificacion or (
                "Trigger activado: USD/MXN superó el nivel de alerta de $18.80.  "
                "Se activa la cobertura conforme al plan de gestión de riesgo cambiario."
            ),
        )
        # Aplicar strike manual si se pasó
        if args.strike is not None:
            datos.strike = args.strike

    else:
        if args.cliente_id is None:
            print("[ERROR] Debes indicar --cliente-id o usar --demo.")
            print("Ejecuta con --help para ver ejemplos.")
            sys.exit(1)

        print(f"  Cargando datos del cliente ID {args.cliente_id} desde BD...\n")
        try:
            from core.database import init_db, DB_PATH
            init_db(DB_PATH)
        except Exception as exc:
            print(f"[ERROR] No se pudo inicializar la BD: {exc}")
            sys.exit(1)

        try:
            datos = construir_datos_orden(
                prospect_id=args.cliente_id,
                tipo=args.tipo,
                monto_usd=args.monto,
                plazo_dias=args.plazo,
                capa=args.capa,
                justificacion=args.justificacion,
                strike=args.strike,
            )
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            sys.exit(1)
        except Exception as exc:
            logger.exception("Error al construir datos de la orden")
            print(f"[ERROR] No se pudieron cargar los datos del cliente: {exc}")
            sys.exit(1)

    # -----------------------------------------------------------------------
    # Generar PDF
    # -----------------------------------------------------------------------
    print("  Generando PDF...")
    try:
        ruta_pdf = generar_pdf_orden(
            datos,
            output_path=args.output,
            output_dir=args.output_dir,
        )
    except Exception as exc:
        logger.exception("Error al generar el PDF")
        print(f"\n[ERROR] No se pudo generar el PDF: {exc}")
        sys.exit(1)

    _imprimir_resumen(datos, ruta_pdf)


if __name__ == "__main__":
    main()
