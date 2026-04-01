"""
Notificador de alertas por email — HedgePoint MX.

Envía un email HTML con la tabla de triggers activados usando la API HTTP de Resend.
Antes de enviar, genera un análisis de contexto de mercado con Claude (Sonnet).

Credenciales requeridas en .env:
    RESEND_API_KEY     — API key de Resend (re_xxxxxxxxxxxxxxxx)
    ANTHROPIC_API_KEY  — API key de Anthropic para el análisis con Claude

Remitente por defecto: onboarding@resend.dev (cuenta Resend sin dominio propio)

Uso:
    from agents.monitor.notifier import send_alert_email
    send_alert_email(fired_triggers, recipients=["a@ejemplo.com", "b@ejemplo.com"])
"""

import logging
import os
from datetime import datetime

import anthropic
import requests

from agents.monitor.triggers import FiredTrigger

logger = logging.getLogger("hedgepoint.notifier")

_RESEND_URL = "https://api.resend.com/emails"
_FROM_ADDRESS = "onboarding@resend.dev"
_ANALYSIS_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Análisis de contexto con Claude
# ---------------------------------------------------------------------------

def generate_market_analysis(fired: list[FiredTrigger]) -> str:
    """
    Envía a Claude los triggers activados y datos públicos de mercado para obtener
    un análisis breve en español (3-4 párrafos).

    Solo se incluyen datos públicos de mercado (precios, niveles, cambios).
    No se envían datos de clientes.

    Returns:
        Texto del análisis, o cadena vacía si falla la llamada.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("[ANALYSIS] ANTHROPIC_API_KEY no configurada — análisis omitido.")
        return ""

    # Construir resumen de triggers con datos públicos de mercado
    trigger_lines = []
    for ft in fired:
        t = ft.trigger
        trigger_lines.append(
            f"- Trigger: {t.name} | Tipo: {t.trigger_type.value} | "
            f"Símbolo: {t.symbol} | Valor observado: {ft.observed_value:.4f} | "
            f"Umbral: {t.threshold:.4f} | Descripción: {t.description or '—'}"
        )

    triggers_text = "\n".join(trigger_lines)
    symbols = sorted({ft.trigger.symbol for ft in fired})
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prompt = f"""Eres un analista de mercados financieros especializado en México y América Latina.

Se han activado las siguientes alertas de mercado el {timestamp}:

{triggers_text}

Activos involucrados: {', '.join(symbols)}

Con base únicamente en estos datos públicos de mercado (niveles de precio, umbrales activados y contexto macroeconómico general de México y los mercados globales), redacta un análisis breve de 3 a 4 párrafos en español que explique:

1. Qué está ocurriendo en el mercado según las alertas activadas.
2. El contexto macroeconómico relevante que podría explicar estos movimientos.
3. Qué aspectos debería considerar o monitorear un cliente con exposición a estos activos.

El tono debe ser profesional, claro y orientado a la toma de decisiones. No incluyas recomendaciones de inversión específicas ni datos de clientes. Solo análisis de mercado con datos públicos."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_ANALYSIS_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = next(
            (block.text for block in response.content if block.type == "text"), ""
        )
        logger.info("[ANALYSIS] Análisis generado con Claude (%d chars).", len(analysis))
        return analysis

    except anthropic.AuthenticationError:
        logger.error("[ANALYSIS] ANTHROPIC_API_KEY inválida.")
    except anthropic.APIStatusError as exc:
        logger.error("[ANALYSIS] Error de API Anthropic %d: %s", exc.status_code, exc.message)
    except Exception as exc:  # noqa: BLE001
        logger.error("[ANALYSIS] Error inesperado al llamar Claude: %s", exc)

    return ""


# ---------------------------------------------------------------------------
# Construcción del cuerpo HTML
# ---------------------------------------------------------------------------

def _build_html(fired: list[FiredTrigger], timestamp: str, analysis: str) -> str:
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

    # Sección de análisis: convertir saltos de línea en párrafos HTML
    analysis_html = ""
    if analysis:
        paragraphs = [p.strip() for p in analysis.split("\n") if p.strip()]
        analysis_html = (
            '<div style="padding:20px 24px;border-top:1px solid #e0e0e0;">'
            '<h3 style="margin:0 0 12px;color:#1a3c5e;font-size:15px;">&#128202; Análisis de contexto de mercado</h3>'
            + "".join(
                f'<p style="margin:0 0 10px;font-size:13px;line-height:1.6;color:#333;">{p}</p>'
                for p in paragraphs
            )
            + '<p style="margin:8px 0 0;font-size:11px;color:#999;">Análisis generado por IA con datos públicos de mercado. '
            'No constituye asesoría de inversión.</p>'
            "</div>"
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

    {analysis_html}

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
    Genera un análisis con Claude y envía un email HTML con los triggers activados
    usando la API de Resend.

    Parameters
    ----------
    fired:
        Lista de FiredTrigger a reportar. Si está vacía no se envía nada.
    recipients:
        Lista de direcciones de email destino. Si está vacía, el envío se omite.

    Returns
    -------
    bool
        True si la API de Resend respondió 200/201, False en cualquier error.
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

    # Generar análisis de mercado con Claude antes de armar el email
    analysis = generate_market_analysis(fired)

    plain_lines = [f"HedgePoint MX — {len(fired)} alerta(s) — {timestamp}", ""]
    for ft in fired:
        plain_lines.append(f"• {ft.message}")
    if analysis:
        plain_lines += ["", "--- Análisis de contexto ---", analysis]

    payload = {
        "from": _FROM_ADDRESS,
        "to": recipients,
        "subject": _build_subject(fired),
        "html": _build_html(fired, timestamp, analysis),
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
