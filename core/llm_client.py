"""
Claude API client with integrated anonymization middleware for HedgePoint MX.

All text sent to the Claude API passes through the Anonymizer so that no
personally-identifiable information ever leaves the system.

Usage:
    from core.llm_client import HedgePointLLM

    llm = HedgePointLLM()
    llm.register_entity("company", "Importadora del Norte S.A.")

    insights = llm.generate_diagnostic_insights(
        exposure_data=metrics,          # dict from calculate_exposure()
        prospect_sector="Importador",
    )
    context = llm.analyze_market_context()
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import anthropic

from core.security.anonymizer import Anonymizer

logger = logging.getLogger(__name__)

# Fallback text returned when the API is unavailable, so the rest of the
# onboarding flow can continue without a hard failure.
_FALLBACK_DIAGNOSTIC = (
    "Análisis no disponible temporalmente. "
    "Tu diagnóstico de exposición cambiaria ha sido calculado correctamente; "
    "el reporte incluye todas las métricas cuantitativas. "
    "Nuestro equipo te enviará el comentario cualitativo en breve."
)

_FALLBACK_MARKET = (
    "Análisis de mercado no disponible temporalmente. "
    "Consulta banxico.org.mx para el tipo de cambio FIX más reciente."
)

_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_TIMEOUT_SECONDS = 30


class HedgePointLLM:
    """
    Claude API client with an integrated Anonymizer middleware.

    Every prompt sent to the API is scrubbed through :class:`Anonymizer`
    before transmission.  Named entities (companies, people) registered via
    :meth:`register_entity` receive opaque labels in the API call;
    the reverse mapping is kept only in memory for internal audit logs.

    Parameters
    ----------
    api_key : str, optional
        Anthropic API key.  Falls back to the ``ANTHROPIC_API_KEY``
        environment variable.
    model : str, optional
        Claude model ID.  Defaults to ``claude-sonnet-4-20250514``.

    Raises
    ------
    ValueError
        If no API key is found in the parameter or the environment.

    Examples
    --------
    ::

        llm = HedgePointLLM()
        llm.register_entity("company", "Importadora del Norte S.A.")
        text = llm.generate_diagnostic_insights(metrics, "Importador")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        resolved_key = (api_key or os.environ.get("ANTHROPIC_API_KEY", "")).strip()
        if not resolved_key:
            raise ValueError(
                "No se encontró la clave de API de Anthropic. "
                "Agrégala al archivo .env:\n\n"
                "    ANTHROPIC_API_KEY=sk-ant-...\n\n"
                "O pásala explícitamente: HedgePointLLM(api_key='sk-ant-...')"
            )
        self._client = anthropic.Anthropic(api_key=resolved_key)
        self._model = model
        self.anonymizer = Anonymizer()

    # ------------------------------------------------------------------
    # Entity registration
    # ------------------------------------------------------------------

    def register_entity(self, entity_type: str, name: str) -> str:
        """Register a named entity and return its anonymized label.

        Thin wrapper around :meth:`Anonymizer.add_entity`.

        Parameters
        ----------
        entity_type : {"company", "person"}
            Category of the entity.
        name : str
            Real name to anonymize (e.g. ``"Importadora del Norte S.A."``).

        Returns
        -------
        str
            Label assigned (e.g. ``"Cliente A"``).
        """
        return self.anonymizer.add_entity(entity_type, name)

    # ------------------------------------------------------------------
    # Diagnostic insights
    # ------------------------------------------------------------------

    def generate_diagnostic_insights(
        self,
        exposure_data: dict,
        prospect_sector: str,
    ) -> str:
        """Generate a qualitative FX-risk diagnostic using Claude.

        The ``exposure_data`` dict (output of ``calculate_exposure()``) is
        formatted into a structured prompt.  The complete prompt is passed
        through :meth:`Anonymizer.anonymize` as a safety net before being
        sent to the API.

        Parameters
        ----------
        exposure_data : dict
            Metrics produced by ``calculate_exposure()``.  Expected keys:
            ``exposicion_anual_usd``, ``exposicion_anual_mxn``,
            ``perdida_potencial_10pct``, ``margen_en_riesgo``,
            ``costo_estimado_forward_mensual``, ``tipo_cambio_usado``.
        prospect_sector : str
            Human-readable sector label (e.g. ``"Importador"``).

        Returns
        -------
        str
            Claude's qualitative analysis (≤ 500 words), or a fallback
            message if the API is unavailable.
        """
        tc       = exposure_data.get("tipo_cambio_usado", 0)
        exp_usd  = exposure_data.get("exposicion_anual_usd", 0)
        exp_mxn  = exposure_data.get("exposicion_anual_mxn", 0)
        p10      = exposure_data.get("perdida_potencial_10pct", 0)
        p15      = exposure_data.get("perdida_potencial_15pct", 0)
        en_riesgo = exposure_data.get("margen_en_riesgo", False)
        costo_fwd = exposure_data.get("costo_estimado_forward_mensual", 0)

        urgencia_hint = "ALTO" if en_riesgo else "MEDIO"

        prompt = f"""Eres un consultor de gestión de riesgos financieros especializado en \
PyMEs mexicanas. Analiza la siguiente situación de exposición cambiaria y genera un \
diagnóstico ejecutivo.

DATOS DE EXPOSICIÓN:
- Sector de la empresa: {prospect_sector}
- Tipo de cambio USD/MXN actual: ${tc:.4f}
- Exposición anual en USD: ${exp_usd:,.0f}
- Exposición anual en MXN: ${exp_mxn:,.0f}
- Pérdida potencial si el peso se deprecia 10%: ${p10:,.0f} MXN
- Pérdida potencial si el peso se deprecia 15%: ${p15:,.0f} MXN
- ¿La pérdida potencial supera el margen de utilidad?: {"Sí" if en_riesgo else "No"}
- Costo estimado de cobertura forward mensual: ${costo_fwd:,.0f} MXN

INSTRUCCIONES:
Genera una respuesta estructurada con exactamente estas 4 secciones:

1. DIAGNÓSTICO DE SITUACIÓN (2-3 párrafos):
   Explica en lenguaje claro para un dueño de PyME qué significa esta exposición, \
cuál es el riesgo real para su negocio y cómo el mercado cambiario actual afecta \
a su sector específico.

2. NIVEL DE URGENCIA:
   Indica únicamente: ALTO, MEDIO o BAJO. Justifica en una oración.
   (Referencia interna: el análisis sugiere nivel {urgencia_hint})

3. ESTRATEGIA RECOMENDADA:
   Recomienda una estrategia entre: forward, opciones, collar, o una combinación. \
Explica brevemente por qué esa estrategia es adecuada para su perfil y sector. \
Menciona ventajas y limitaciones en 2-3 oraciones.

4. SIGUIENTE PASO CONCRETO:
   Una acción específica y accionable que el empresario puede tomar esta semana para \
empezar a protegerse. Sé directo.

TONO: Profesional pero accesible. Evita jerga financiera innecesaria. \
Máximo 500 palabras en total."""

        clean_prompt = self.anonymizer.anonymize(prompt)

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=700,
                timeout=_TIMEOUT_SECONDS,
                messages=[{"role": "user", "content": clean_prompt}],
            )
            return message.content[0].text
        except Exception as exc:
            logger.error("Error al llamar a Claude API (diagnostic): %s", exc)
            return _FALLBACK_DIAGNOSTIC

    # ------------------------------------------------------------------
    # Market context
    # ------------------------------------------------------------------

    def analyze_market_context(self, currency_pair: str = "USD/MXN") -> str:
        """Generate a brief market context analysis for a currency pair.

        Uses only public, non-client information.  Safe to include in
        prospect-facing reports without anonymization.

        Parameters
        ----------
        currency_pair : str, optional
            Currency pair to analyze.  Defaults to ``"USD/MXN"``.

        Returns
        -------
        str
            Brief market commentary (≤ 200 words), or a fallback message
            if the API is unavailable.
        """
        prompt = f"""Eres un analista de mercados de divisas especializado en México. \
Proporciona un análisis breve del contexto actual del mercado para el par {currency_pair}.

INSTRUCCIONES:
- Describe los factores macroeconómicos relevantes que afectan a {currency_pair} hoy
- Menciona el sesgo direccional general (alcista para el dólar, bajista, o lateral)
- Señala 1-2 riesgos clave que los importadores/exportadores mexicanos deben vigilar
- Usa únicamente información de conocimiento público general; no inventes datos precisos
- Tono: conciso y ejecutivo
- Máximo 200 palabras
- No incluyas precios o niveles específicos que requieran datos en tiempo real"""

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=300,
                timeout=_TIMEOUT_SECONDS,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as exc:
            logger.error("Error al llamar a Claude API (market context): %s", exc)
            return _FALLBACK_MARKET
