"""
Notificador de alertas por email — HedgePoint MX.

Envía un email HTML con la tabla de triggers activados usando la API HTTP de Resend.

Credenciales requeridas en .env:
    RESEND_API_KEY — API key de Resend (re_xxxxxxxxxxxxxxxx)

Remitente por defecto: onboarding@resend.dev (cuenta Resend sin dominio propio)

Uso:
    from agents.monitor.notifier import send_alert_email
    send_alert_email(fired_triggers, recipients=["a@ejemplo.com", "b@ejemplo.com"])
"""

import logging
import os
from datetime import datetime

import requests

from agents.monitor.triggers import FiredTrigger

logger = logging.getLogger("hedgepoint.notifier")

_RESEND_URL = "https://api.resend.com/emails"
_FROM_ADDRESS = "onboarding@resend.dev"


# ---------------------------------------------------------------------------
# Construcción del cuerpo HTML
# ---------------------------------------------------------------------------

def _build_html(fired: list[FiredTrigger], timestamp: str) -> str:
    rows_html = ""
    for ft in fired:
        t = ft.trigger
        rows_html += (
            "<tr>"
            f'<td style="padding:8px 12px;border:1px solid #ddd;font-weight:bold;">{t.name}</td>'
            f'<td style="padding:8px 12px;border:1px solid #ddd;">{t.trigger_type.value}</td>'
            f'<td style="padding:8px 12px;border:1px solid #ddd;">{t.symbol}</td>'
            f'<td style="padding:8px 12px;border:1px solid #ddd;text-align:right;">{ft.observed_value:.4f}</td>'
            f'<td style="padding:8px 12px;border:1px solid #ddd;text-align:right;">{t.threshold:.4f}</td>'
            f'<td style="padding:8px 12px;border:1px solid #ddd;color:#c0392b;">{ft.message}</td>'
            "</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>Alerta HedgePoint MX</title></head>
<body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:20px;">
  <div style="max-width:820px;margin:0 auto;background:#fff;border-radius:6px;
              overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,.12);">

    <div style="background:#1a3c5e;padding:20px 24px;">
      <h2 style="margin:0;color:#fff;font-size:18px;">&#9888;&#65039; HedgePoint MX &mdash; Alerta de mercado</h2>
      <p style="margin:4px 0 0;color:#a8c4e0;font-size:13px;">{timestamp}</p>
    </div>

    <div style="padding:16px 24px;background:#fff8e1;border-bottom:1px solid #ffe082;">
      <p style="margin:0;font-size:14px;color:#7d6608;">
        Se activaron <strong>{len(fired)} trigger(s)</strong> en el último ciclo de monitoreo.
      </p>
    </div>

    <div style="padding:20px 24px;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="background:#1a3c5e;color:#fff;">
            <th style="padding:10px 12px;text-align:left;border:1px solid #1a3c5e;">Nombre</th>
            <th style="padding:10px 12px;text-align:left;border:1px solid #1a3c5e;">Tipo</th>
            <th style="padding:10px 12px;text-align:left;border:1px solid #1a3c5e;">S&iacute;mbolo</th>
            <th style="padding:10px 12px;text-align:right;border:1px solid #1a3c5e;">Valor obs.</th>
            <th style="padding:10px 12px;text-align:right;border:1px solid #1a3c5e;">Umbral</th>
            <th style="padding:10px 12px;text-align:left;border:1px solid #1a3c5e;">Mensaje</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>

    <div style="padding:12px 24px;background:#f0f0f0;border-top:1px solid #ddd;
                font-size:11px;color:#888;">
      Mensaje generado autom&aacute;ticamente por HedgePoint MX Monitor. No responder.
    </div>
  </div>
</body>
</html>"""


def _build_subject(fired: list[FiredTrigger]) -> str:
    symbols = ", ".join(sorted({ft.trigger.symbol for ft in fired}))
    return f"[HedgePoint MX] {len(fired)} alerta(s) activada(s) — {symbols}"


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------

def send_alert_email(
    fired: list[FiredTrigger],
    recipients: list[str],
) -> bool:
    """
    Envía un email HTML con los triggers activados usando la API de Resend.

    Parameters
    ----------
    fired:
        Lista de FiredTrigger a reportar. Si está vacía no se envía nada.
    recipients:
        Lista de direcciones de email destino. Si está vacía, el envío se omite.

    Returns
    -------
    bool
        True si la API respondió 200/201, False en cualquier error.
    """
    if not fired:
        logger.debug("[EMAIL] Lista de triggers vacía — sin envío.")
        return False

    if not recipients:
        logger.warning("[EMAIL] Sin destinatarios configurados — sin envío.")
        return False

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.error("[EMAIL] Falta RESEND_API_KEY en .env")
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    plain_lines = [f"HedgePoint MX — {len(fired)} alerta(s) — {timestamp}", ""]
    for ft in fired:
        plain_lines.append(f"• {ft.message}")

    payload = {
        "from": _FROM_ADDRESS,
        "to": recipients,
        "subject": _build_subject(fired),
        "html": _build_html(fired, timestamp),
        "text": "\n".join(plain_lines),
    }

    try:
        response = requests.post(
            _RESEND_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )

        if response.status_code in (200, 201):
            email_id = response.json().get("id", "—")
            logger.info(
                "[EMAIL] Alerta enviada a %d destinatario(s): %s — id=%s — %d trigger(s): %s",
                len(recipients),
                ", ".join(recipients),
                email_id,
                len(fired),
                ", ".join(ft.trigger.name for ft in fired),
            )
            return True

        logger.error(
            "[EMAIL] Resend API error %d: %s",
            response.status_code,
            response.text,
        )

    except requests.Timeout:
        logger.error("[EMAIL] Timeout al conectar con Resend API")
    except requests.RequestException as exc:
        logger.error("[EMAIL] Error de red: %s", exc)

    return False
