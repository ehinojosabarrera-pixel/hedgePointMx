"""
Notificador de alertas por WhatsApp — HedgePoint MX.

Envía mensajes de texto vía Twilio WhatsApp Business API.
Límite de 1600 caracteres por mensaje; si excede, trunca y agrega aviso de email.
Reintenta hasta 2 veces ante errores transitorios.

Credenciales requeridas en .env:
    TWILIO_ACCOUNT_SID      — SID de la cuenta Twilio (ACxxxxxxxxxxxxxxxx)
    TWILIO_AUTH_TOKEN       — Auth token de la cuenta Twilio
    TWILIO_WHATSAPP_FROM    — Número origen en formato whatsapp:+14155238886
                              (sandbox Twilio) o el número aprobado de producción

Uso:
    from agents.monitor.whatsapp_notifier import send_whatsapp_alert
    send_whatsapp_alert("+5215512345678", "Alerta: USD/MXN superó 20.00")
"""

import logging
import os
import time

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

logger = logging.getLogger("hedgepoint.whatsapp")

_MAX_CHARS = 1600
_TRUNCATION_SUFFIX = "…\n\nVer detalle por email."
_MAX_RETRIES = 2
_RETRY_DELAY_SECS = 3


def _get_client() -> Client:
    """Construye el cliente Twilio con credenciales de .env."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    if not sid or not token:
        raise EnvironmentError(
            "Faltan credenciales Twilio: TWILIO_ACCOUNT_SID y/o TWILIO_AUTH_TOKEN no configuradas en .env"
        )
    return Client(sid, token)


def _truncate(message: str) -> str:
    """Trunca el mensaje al límite de _MAX_CHARS incluyendo el sufijo de aviso."""
    if len(message) <= _MAX_CHARS:
        return message
    cutoff = _MAX_CHARS - len(_TRUNCATION_SUFFIX)
    return message[:cutoff] + _TRUNCATION_SUFFIX


def send_whatsapp_alert(telefono: str, mensaje: str) -> bool:
    """
    Envía un mensaje de WhatsApp al número indicado vía Twilio.

    Parameters
    ----------
    telefono:
        Número destino en formato E.164, ej. "+5215512345678".
    mensaje:
        Texto del mensaje. Se trunca a 1600 caracteres si excede el límite.

    Returns
    -------
    bool
        True si el mensaje fue aceptado por Twilio, False en cualquier error.
    """
    if not telefono or not mensaje:
        logger.warning("[WHATSAPP] telefono o mensaje vacío — sin envío.")
        return False

    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "").strip()
    if not from_number:
        logger.error("[WHATSAPP] Falta TWILIO_WHATSAPP_FROM en .env")
        return False

    # Normalizar números al formato whatsapp:+XXXXXXXXXXX
    to_number = telefono if telefono.startswith("whatsapp:") else f"whatsapp:{telefono}"
    if not from_number.startswith("whatsapp:"):
        from_number = f"whatsapp:{from_number}"

    body = _truncate(mensaje)
    truncated = len(body) < len(mensaje)

    try:
        client = _get_client()
    except EnvironmentError as exc:
        logger.error("[WHATSAPP] %s", exc)
        return False

    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 2):  # intentos: 1, 2, 3
        try:
            msg = client.messages.create(
                from_=from_number,
                to=to_number,
                body=body,
            )
            logger.info(
                "[WHATSAPP] Mensaje enviado a %s — SID=%s%s",
                telefono,
                msg.sid,
                " [truncado]" if truncated else "",
            )
            return True

        except TwilioRestException as exc:
            last_exc = exc
            # Errores 4xx son permanentes — no reintentar
            if 400 <= exc.status < 500:
                logger.error(
                    "[WHATSAPP] Error permanente Twilio %d (código %s): %s — destino: %s",
                    exc.status,
                    exc.code,
                    exc.msg,
                    telefono,
                )
                return False

            # Errores 5xx: reintentable
            if attempt <= _MAX_RETRIES:
                logger.warning(
                    "[WHATSAPP] Error Twilio %d en intento %d/%d — reintentando en %ds...",
                    exc.status,
                    attempt,
                    _MAX_RETRIES + 1,
                    _RETRY_DELAY_SECS,
                )
                time.sleep(_RETRY_DELAY_SECS)
            else:
                logger.error(
                    "[WHATSAPP] Error Twilio %d tras %d intentos: %s — destino: %s",
                    exc.status,
                    _MAX_RETRIES + 1,
                    exc.msg,
                    telefono,
                )

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt <= _MAX_RETRIES:
                logger.warning(
                    "[WHATSAPP] Error inesperado en intento %d/%d: %s — reintentando en %ds...",
                    attempt,
                    _MAX_RETRIES + 1,
                    exc,
                    _RETRY_DELAY_SECS,
                )
                time.sleep(_RETRY_DELAY_SECS)
            else:
                logger.error(
                    "[WHATSAPP] Error inesperado tras %d intentos: %s — destino: %s",
                    _MAX_RETRIES + 1,
                    exc,
                    telefono,
                )

    if last_exc:
        logger.error("[WHATSAPP] Envío fallido definitivamente a %s.", telefono)
    return False
