"""
Envío de reportes semanales de coberturas por email y WhatsApp — HedgePoint MX.

Credenciales requeridas en .env:
    RESEND_API_KEY          — API key de Resend
    TWILIO_ACCOUNT_SID      — SID de cuenta Twilio
    TWILIO_AUTH_TOKEN       — Auth token de Twilio
    TWILIO_WHATSAPP_FROM    — Número origen WhatsApp
    HEDGEPOINT_ENCRYPTION_KEY — Para desencriptar email/teléfono/nombre del prospect

Funciones públicas:
    enviar_reporte_email      — adjunta el PDF y envía por email vía Resend
    enviar_reporte_whatsapp   — envía resumen corto por WhatsApp vía Twilio
    enviar_reporte            — orquesta ambos canales con soporte dry_run
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import date
from pathlib import Path

import requests

from agents.monitor.whatsapp_notifier import send_whatsapp_alert
from core.security.anonymizer import FieldEncryptor

logger = logging.getLogger(__name__)

_RESEND_URL   = "https://api.resend.com/emails"
_FROM_ADDRESS = "onboarding@resend.dev"


# ---------------------------------------------------------------------------
# Helpers de desencriptación
# ---------------------------------------------------------------------------

def _decrypt_field(encrypted: str, fallback: str) -> str:
    """Intenta desencriptar un campo con FieldEncryptor; retorna fallback si falla."""
    try:
        return FieldEncryptor().decrypt(encrypted)
    except Exception as exc:
        logger.warning("No se pudo desencriptar campo: %s", exc)
        return fallback


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def enviar_reporte_email(
    pdf_path: str,
    prospect: dict,
    db_path=None,
) -> bool:
    """Envía el PDF de reporte semanal como adjunto por email vía Resend.

    Intenta desencriptar ``email_enc`` y ``nombre_enc`` del prospect con
    FieldEncryptor.  Si la clave de encriptación no está configurada o la
    desencriptación falla, loggea un warning y retorna False.

    Parameters
    ----------
    pdf_path : str
        Ruta al PDF generado.
    prospect : dict
        Fila del prospect desde la BD (con campos ``email_enc``, ``nombre_enc``).
    db_path : Path, optional
        No se usa directamente; incluido para consistencia de firma.

    Returns
    -------
    bool
        True si Resend respondió 200/201, False en cualquier error.
    """
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.error("[EMAIL] Falta RESEND_API_KEY en .env")
        return False

    email_enc = prospect.get("email_enc", "")
    if not email_enc:
        logger.warning("[EMAIL] El prospect no tiene email_enc — sin envío.")
        return False

    try:
        destinatario = FieldEncryptor().decrypt(email_enc)
    except Exception as exc:
        logger.warning("[EMAIL] No se pudo desencriptar email_enc: %s", exc)
        return False

    nombre = _decrypt_field(prospect.get("nombre_enc", ""), "Estimado cliente")

    # Leer PDF en base64
    try:
        pdf_bytes = Path(pdf_path).read_bytes()
    except OSError as exc:
        logger.error("[EMAIL] No se pudo leer el PDF %s: %s", pdf_path, exc)
        return False
    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

    hoy     = date.today()
    fecha_s = hoy.strftime("%d/%m/%Y")
    subject = f"[HedgePoint MX] Reporte Semanal de Coberturas — {fecha_s}"

    html_body = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:20px;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:6px;
              overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,.12);">
    <div style="background:#1a365d;padding:20px 24px;">
      <h2 style="margin:0;color:#fff;font-size:18px;">HedgePoint MX</h2>
      <p style="margin:4px 0 0;color:#a8c4e0;font-size:13px;">
        Reporte Semanal de Coberturas — {fecha_s}
      </p>
    </div>
    <div style="padding:24px;">
      <p style="font-size:14px;color:#333;">Estimado/a <strong>{nombre}</strong>,</p>
      <p style="font-size:14px;color:#333;line-height:1.6;">
        Adjunto encontrar&aacute; su reporte semanal de coberturas con el estado
        actual de sus posiciones y recomendaciones.
      </p>
      <p style="font-size:14px;color:#333;line-height:1.6;">
        Si tiene alguna pregunta o desea revisar su estrategia de cobertura,
        no dude en contactarnos.
      </p>
    </div>
    <div style="padding:16px 24px;background:#f0f0f0;border-top:1px solid #ddd;
                font-size:12px;color:#666;">
      <strong>HedgePoint MX</strong><br>
      Email: contacto@hedgepointmx.com &nbsp;|&nbsp;
      WhatsApp: +52 (993) 170-1758 &nbsp;|&nbsp;
      Web: www.hedgepointmx.com
    </div>
  </div>
</body>
</html>"""

    payload = {
        "from": _FROM_ADDRESS,
        "to":   [destinatario],
        "subject": subject,
        "html":    html_body,
        "attachments": [
            {
                "filename": "reporte_semanal.pdf",
                "content":  pdf_b64,
            }
        ],
    }

    try:
        response = requests.post(
            _RESEND_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        if response.status_code in (200, 201):
            logger.info("[EMAIL] Reporte enviado a %s — id=%s",
                        destinatario, response.json().get("id", "—"))
            return True
        logger.error("[EMAIL] Resend API error %d: %s",
                     response.status_code, response.text)
    except requests.Timeout:
        logger.error("[EMAIL] Timeout al conectar con Resend API")
    except requests.RequestException as exc:
        logger.error("[EMAIL] Error de red: %s", exc)

    return False


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------

def enviar_reporte_whatsapp(
    datos_reporte: dict,
    prospect: dict,
    db_path=None,
) -> bool:
    """Envía un resumen corto del reporte por WhatsApp vía Twilio.

    Parameters
    ----------
    datos_reporte : dict
        Dict devuelto por ``generar_datos_reporte()``.
    prospect : dict
        Fila del prospect desde la BD (con campo ``telefono_enc``).
    db_path : Path, optional
        No se usa directamente; incluido para consistencia de firma.

    Returns
    -------
    bool
        True si el mensaje fue enviado, False en cualquier error.
    """
    telefono_enc = prospect.get("telefono_enc", "")
    if not telefono_enc:
        logger.warning("[WHATSAPP] El prospect no tiene telefono_enc — sin envío.")
        return False

    try:
        telefono = FieldEncryptor().decrypt(telefono_enc)
    except Exception as exc:
        logger.warning("[WHATSAPP] No se pudo desencriptar telefono_enc: %s", exc)
        return False

    pnl   = datos_reporte.get("pnl", {})
    rm    = datos_reporte.get("resumen_mercado", {})
    prox  = datos_reporte.get("proximos_vencimientos", [])

    spot  = rm.get("spot", 0.0)
    mtm   = pnl.get("total_mtm_mxn", 0.0)
    n_cob = pnl.get("num_coberturas", 0)
    n_venc = len(prox)

    mensaje = (
        f"HedgePoint MX - Reporte Semanal\n\n"
        f"Spot: ${spot:.4f}\n"
        f"Ahorro total: ${mtm:,.0f} MXN\n"
        f"Coberturas activas: {n_cob}\n"
        f"Proximos vencimientos: {n_venc} en 30 dias\n\n"
        f"Revisa tu email para el reporte completo."
    )

    return send_whatsapp_alert(telefono, mensaje)


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def enviar_reporte(
    datos_reporte: dict,
    pdf_path: str,
    prospect: dict,
    canales: list[str] | None = None,
    dry_run: bool = False,
    db_path=None,
) -> dict:
    """Orquesta el envío del reporte por los canales indicados.

    Parameters
    ----------
    datos_reporte : dict
        Dict devuelto por ``generar_datos_reporte()``.
    pdf_path : str
        Ruta al PDF generado.
    prospect : dict
        Fila del prospect desde la BD.
    canales : list[str], optional
        Canales de envío: ``["email"]``, ``["whatsapp"]`` o ambos.
        Default: ``["email"]``.
    dry_run : bool
        Si True, imprime a qué enviaría pero no llama a la API.
    db_path : Path, optional
        Ruta a la BD (se propaga a las funciones de envío).

    Returns
    -------
    dict
        Claves: ``"email"``, ``"whatsapp"``.
        Valor: ``True``/``False`` si se intentó enviar, ``None`` si el canal
        no estaba en la lista, ``"dry_run"`` en modo simulación.
    """
    if canales is None:
        canales = ["email"]

    resultado: dict = {"email": None, "whatsapp": None}

    if dry_run:
        email_enc  = prospect.get("email_enc", "")
        tel_enc    = prospect.get("telefono_enc", "")
        dest_email = _decrypt_field(email_enc, "<email no disponible>") if email_enc else "<sin email>"
        dest_tel   = _decrypt_field(tel_enc,  "<tel no disponible>")   if tel_enc   else "<sin teléfono>"

        print("[DRY-RUN] Envío simulado — no se realizan llamadas reales.")
        print(f"  PDF:       {pdf_path}")
        print(f"  Canales:   {', '.join(canales)}")
        if "email" in canales:
            print(f"  Email ->   {dest_email}")
            resultado["email"] = "dry_run"
        if "whatsapp" in canales:
            print(f"  WA    ->   {dest_tel}")
            resultado["whatsapp"] = "dry_run"
        return resultado

    if "email" in canales:
        resultado["email"] = enviar_reporte_email(pdf_path, prospect, db_path=db_path)

    if "whatsapp" in canales:
        resultado["whatsapp"] = enviar_reporte_whatsapp(
            datos_reporte, prospect, db_path=db_path
        )

    return resultado
