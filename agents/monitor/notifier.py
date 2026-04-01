"""
Notificador de alertas por email — HedgePoint MX.

Envía un email HTML con la tabla de triggers activados usando Gmail SMTP.

Credenciales requeridas en .env:
    GMAIL_USER         — dirección Gmail del remitente (ej: alerts@gmail.com)
    GMAIL_APP_PASSWORD — App Password de Google (16 caracteres, sin espacios)

Para obtener un App Password:
    Google Account → Seguridad → Verificación en 2 pasos → Contraseñas de aplicación

Uso:
    from agents.monitor.notifier import send_alert_email
    send_alert_email(fired_triggers, recipient="destino@ejemplo.com")
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from agents.monitor.triggers import FiredTrigger

logger = logging.getLogger("hedgepoint.notifier")

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


# ---------------------------------------------------------------------------
# Construcción del cuerpo HTML
# ---------------------------------------------------------------------------

def _build_html(fired: list[FiredTrigger], timestamp: str) -> str:
    rows_html = ""
    for ft in fired:
        t = ft.trigger
        rows_html += f"""
        <tr>
            <td style="padding:8px 12px; border:1px solid #ddd; font-weight:bold;">{t.name}</td>
            <td style="padding:8px 12px; border:1px solid #ddd;">{t.trigger_type.value}</td>
            <td style="padding:8px 12px; border:1px solid #ddd;">{t.symbol}</td>
            <td style="padding:8px 12px; border:1px solid #ddd; text-align:right;">{ft.observed_value:.4f}</td>
            <td style="padding:8px 12px; border:1px solid #ddd; text-align:right;">{t.threshold:.4f}</td>
            <td style="padding:8px 12px; border:1px solid #ddd; color:#c0392b;">{ft.message}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Alerta HedgePoint MX</title>
</head>
<body style="font-family: Arial, sans-serif; background:#f5f5f5; padding:20px;">
  <div style="max-width:800px; margin:0 auto; background:#ffffff;
              border-radius:6px; overflow:hidden;
              box-shadow:0 2px 6px rgba(0,0,0,.12);">

    <!-- Header -->
    <div style="background:#1a3c5e; padding:20px 24px;">
      <h2 style="margin:0; color:#ffffff; font-size:18px;">
        ⚠️ HedgePoint MX — Alerta de mercado
      </h2>
      <p style="margin:4px 0 0; color:#a8c4e0; font-size:13px;">{timestamp}</p>
    </div>

    <!-- Resumen -->
    <div style="padding:16px 24px; background:#fff8e1; border-bottom:1px solid #ffe082;">
      <p style="margin:0; font-size:14px; color:#7d6608;">
        Se activaron <strong>{len(fired)} trigger(s)</strong> en el último ciclo de monitoreo.
      </p>
    </div>

    <!-- Tabla de triggers -->
    <div style="padding:20px 24px;">
      <table style="width:100%; border-collapse:collapse; font-size:13px;">
        <thead>
          <tr style="background:#1a3c5e; color:#ffffff;">
            <th style="padding:10px 12px; text-align:left; border:1px solid #1a3c5e;">Nombre</th>
            <th style="padding:10px 12px; text-align:left; border:1px solid #1a3c5e;">Tipo</th>
            <th style="padding:10px 12px; text-align:left; border:1px solid #1a3c5e;">Símbolo</th>
            <th style="padding:10px 12px; text-align:right; border:1px solid #1a3c5e;">Valor obs.</th>
            <th style="padding:10px 12px; text-align:right; border:1px solid #1a3c5e;">Umbral</th>
            <th style="padding:10px 12px; text-align:left; border:1px solid #1a3c5e;">Mensaje</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>

    <!-- Footer -->
    <div style="padding:12px 24px; background:#f0f0f0;
                border-top:1px solid #ddd; font-size:11px; color:#888;">
      Este mensaje fue generado automáticamente por HedgePoint MX Monitor.
      No responder a este correo.
    </div>
  </div>
</body>
</html>"""


def _build_subject(fired: list[FiredTrigger]) -> str:
    count = len(fired)
    symbols = ", ".join(sorted({ft.trigger.symbol for ft in fired}))
    return f"[HedgePoint MX] {count} alerta(s) activada(s) — {symbols}"


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------

def send_alert_email(
    fired: list[FiredTrigger],
    recipient: str | None = None,
) -> bool:
    """
    Envía un email HTML con los triggers activados usando Gmail SMTP.

    Parameters
    ----------
    fired:
        Lista de FiredTrigger a reportar. Si está vacía, no se envía nada.
    recipient:
        Dirección de destino. Si es None, se usa GMAIL_USER (auto-envío).

    Returns
    -------
    bool
        True si el email se envió correctamente, False en caso de error.

    Raises
    ------
    No lanza excepciones — todos los errores se registran en el logger.
    """
    if not fired:
        logger.debug("[EMAIL] Lista de triggers vacía — sin envío.")
        return False

    gmail_user = os.getenv("GMAIL_USER", "").strip()
    app_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    if not gmail_user or not app_password:
        logger.error(
            "[EMAIL] Faltan credenciales: define GMAIL_USER y GMAIL_APP_PASSWORD en .env"
        )
        return False

    to_addr = recipient or gmail_user
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = _build_subject(fired)
    msg["From"] = gmail_user
    msg["To"] = to_addr

    # Parte texto plano como fallback
    plain_lines = [f"HedgePoint MX — {len(fired)} alerta(s) — {timestamp}", ""]
    for ft in fired:
        plain_lines.append(f"• {ft.message}")
    msg.attach(MIMEText("\n".join(plain_lines), "plain", "utf-8"))

    # Parte HTML principal
    msg.attach(MIMEText(_build_html(fired, timestamp), "html", "utf-8"))

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_user, app_password)
            server.sendmail(gmail_user, to_addr, msg.as_string())

        logger.info(
            "[EMAIL] Alerta enviada a %s — %d trigger(s): %s",
            to_addr,
            len(fired),
            ", ".join(ft.trigger.name for ft in fired),
        )
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "[EMAIL] Fallo de autenticación Gmail. "
            "Verifica GMAIL_USER y GMAIL_APP_PASSWORD en .env"
        )
    except smtplib.SMTPException as exc:
        logger.error("[EMAIL] Error SMTP al enviar alerta: %s", exc)
    except OSError as exc:
        logger.error("[EMAIL] Error de red al conectar con Gmail SMTP: %s", exc)

    return False
