"""
Script de prueba para el notificador de WhatsApp — HedgePoint MX.

Envía un mensaje de prueba al número indicado usando las credenciales Twilio
configuradas en .env.

Uso:
    python scripts/test_whatsapp.py --telefono "+5215512345678"
    python scripts/test_whatsapp.py --telefono "+5215512345678" --mensaje "Mensaje personalizado"
    python scripts/test_whatsapp.py --telefono "+5215512345678" --largo   # prueba truncado
"""

import argparse
import logging
import sys
from pathlib import Path

# Permite ejecutar desde la raíz del proyecto o desde scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("hedgepoint.test_whatsapp")

_MENSAJE_DEFAULT = (
    "✅ [HedgePoint MX] Prueba de notificación WhatsApp.\n"
    "El sistema de alertas está configurado correctamente."
)

_MENSAJE_LARGO = (
    "⚠️ [HedgePoint MX] Alerta de mercado — PRUEBA DE TRUNCADO\n\n"
    "Este mensaje es intencionalmente largo para verificar que el sistema "
    "trunca correctamente los mensajes que superan el límite de 1600 caracteres.\n\n"
    + ("USD/MXN superó el umbral de 20.50 — nivel de alerta activado. " * 40)
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prueba el envío de alertas por WhatsApp (HedgePoint MX)"
    )
    parser.add_argument(
        "--telefono",
        required=True,
        metavar="NUM",
        help='Número destino en formato E.164, ej. "+5215512345678"',
    )
    parser.add_argument(
        "--mensaje",
        default=None,
        metavar="TXT",
        help="Texto del mensaje (default: mensaje de prueba estándar)",
    )
    parser.add_argument(
        "--largo",
        action="store_true",
        help="Usa un mensaje largo para probar el truncado automático a 1600 chars",
    )
    args = parser.parse_args()

    if args.largo:
        mensaje = _MENSAJE_LARGO
        logger.info("Usando mensaje largo (%d chars) para probar truncado.", len(mensaje))
    elif args.mensaje:
        mensaje = args.mensaje
    else:
        mensaje = _MENSAJE_DEFAULT

    logger.info("Enviando mensaje de prueba a %s ...", args.telefono)
    logger.info("Longitud del mensaje: %d chars", len(mensaje))

    from agents.monitor.whatsapp_notifier import send_whatsapp_alert

    ok = send_whatsapp_alert(args.telefono, mensaje)

    if ok:
        logger.info("✅ Mensaje enviado exitosamente.")
        sys.exit(0)
    else:
        logger.error("❌ El mensaje no pudo ser enviado. Revisa los logs y las credenciales en .env")
        sys.exit(1)


if __name__ == "__main__":
    main()
