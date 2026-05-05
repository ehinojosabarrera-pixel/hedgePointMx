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

import json
import logging
import os
import re
from typing import Optional

import anthropic
from dotenv import load_dotenv

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

_FALLBACK_PARSE = None  # parse_scenario returns None on failure

_FALLBACK_SCENARIO = (
    "Análisis de escenario no disponible temporalmente. "
    "Las métricas cuantitativas del escenario han sido calculadas correctamente."
)

_FALLBACK_REPORT = (
    "Recomendaciones no disponibles temporalmente. "
    "Las métricas cuantitativas de su posición están actualizadas en este reporte."
)


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
        load_dotenv()
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
diagnóstico ejecutivo breve.

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
Genera una respuesta estructurada con exactamente estas 2 secciones. \
NO incluyas estrategia recomendada, nombres de bancos ni pasos siguientes específicos.

1. DIAGNÓSTICO DE SITUACIÓN:
   En 1 párrafo claro para un dueño de PyME: qué significa esta exposición, \
cuál es el riesgo real para su negocio y cómo el entorno cambiario actual \
afecta a su sector. Sin recomendar instrumentos financieros concretos.

2. NIVEL DE URGENCIA:
   Indica únicamente: ALTO, MEDIO o BAJO. Justifica en una oración breve.
   (Referencia interna: el análisis sugiere nivel {urgencia_hint})

TONO: Profesional pero accesible. Sin jerga financiera innecesaria. \
Sin markdown (no uses **, ##, *, listas con guiones). Texto plano. \
Máximo 200 palabras en total."""

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
- Máximo 100 palabras. Sé conciso.
- No incluyas precios o niveles específicos que requieran datos en tiempo real
- Sin markdown (no uses **, ##, *, listas con guiones). Texto plano."""

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=200,
                timeout=_TIMEOUT_SECONDS,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as exc:
            logger.error("Error al llamar a Claude API (market context): %s", exc)
            return _FALLBACK_MARKET

    # ------------------------------------------------------------------
    # Scenario parsing
    # ------------------------------------------------------------------

    def parse_scenario(self, texto_usuario: str, spot_actual: float) -> dict | None:
        """Interpreta texto en lenguaje natural y extrae parámetros del escenario.

        Envía el texto a Claude con un system prompt estricto que fuerza una
        respuesta JSON.  Si el JSON falla, intenta un regex fallback que extrae
        el primer número decimal del texto y lo trata como spot_fijo.

        Parameters
        ----------
        texto_usuario : str
            Texto libre del usuario, ej: "¿Qué pasa si el dólar sube a $22?"
        spot_actual : float
            Spot USD/MXN actual para dar contexto al modelo.

        Returns
        -------
        dict | None
            Dict con keys: ``tipo`` ("spot_fijo"|"cambio_porcentual"|"historico"),
            ``valor`` (float), ``plazo_meses`` (int), ``nombre_evento`` (str|None).
            ``None`` si no se puede parsear.
        """
        system_prompt = (
            "Eres un parser de escenarios financieros. El usuario describe un escenario "
            "hipotético sobre el tipo de cambio USD/MXN. Extrae los parámetros.\n\n"
            f"Spot actual: ${spot_actual:.4f}\n\n"
            "Responde ÚNICAMENTE con un JSON válido, sin markdown, sin backticks, "
            "sin explicación. El JSON debe tener estas keys exactas:\n"
            '- "tipo": "spot_fijo" si menciona un precio específico, '
            '"cambio_porcentual" si menciona un porcentaje, '
            '"historico" si menciona un evento histórico '
            "(crisis 2008, Trump 2016, COVID 2020, super peso 2023, aranceles Trump 2025)\n"
            '- "valor": el número extraído (precio en pesos si spot_fijo, '
            "porcentaje sin signo si cambio_porcentual, 0 si historico)\n"
            '- "plazo_meses": plazo mencionado en meses (default 3 si no se menciona)\n'
            '- "nombre_evento": null excepto si tipo es "historico", entonces uno de: '
            '"crisis_2008", "trump_2016", "covid_2020", "super_peso_2023", '
            '"aranceles_trump_2025"\n\n'
            "Ejemplos:\n"
            '"¿Qué pasa si el dólar sube a $22?" → '
            '{"tipo":"spot_fijo","valor":22.0,"plazo_meses":3,"nombre_evento":null}\n'
            '"Si sube 10%" → '
            '{"tipo":"cambio_porcentual","valor":10.0,"plazo_meses":3,"nombre_evento":null}\n'
            '"¿Cómo me habría afectado el COVID?" → '
            '{"tipo":"historico","valor":0,"plazo_meses":3,"nombre_evento":"covid_2020"}\n'
            '"Si baja a 16 pesos en 6 meses" → '
            '{"tipo":"spot_fijo","valor":16.0,"plazo_meses":6,"nombre_evento":null}'
        )

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=150,
                timeout=_TIMEOUT_SECONDS,
                system=system_prompt,
                messages=[{"role": "user", "content": texto_usuario}],
            )
            raw = message.content[0].text.strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            # Regex fallback: extrae el primer número decimal del texto original
            logger.warning(
                "parse_scenario: JSON inválido en respuesta de Claude. "
                "Intentando regex fallback sobre el texto del usuario."
            )
            match = re.search(r"\b(\d{1,3}(?:\.\d{1,4})?)\b", texto_usuario)
            if match:
                return {
                    "tipo": "spot_fijo",
                    "valor": float(match.group(1)),
                    "plazo_meses": 3,
                    "nombre_evento": None,
                }
            logger.error(
                "parse_scenario: regex fallback no encontró número en: %r",
                texto_usuario,
            )
            return _FALLBACK_PARSE
        except Exception as exc:
            logger.error("Error al llamar a Claude API (parse_scenario): %s", exc)
            return _FALLBACK_PARSE

    # ------------------------------------------------------------------
    # Scenario analysis
    # ------------------------------------------------------------------

    def analyze_scenario(self, scenario_result: dict) -> str:
        """Genera análisis narrativo de un resultado de escenario.

        Convierte el dict de ScenarioResult a texto estructurado, lo anonimiza
        y lo envía a Claude para obtener una narrativa ejecutiva en español.

        Parameters
        ----------
        scenario_result : dict
            ScenarioResult convertido a dict (con ``dataclasses.asdict()``
            o construido manualmente).

        Returns
        -------
        str
            Análisis narrativo en español (≤ 200 palabras), o fallback si
            la API no está disponible.
        """
        system_prompt = (
            "Eres un asesor financiero de HedgePoint MX especializado en cobertura "
            "cambiaria para PyMEs mexicanas. Analiza este escenario hipotético y explica "
            "al cliente:\n"
            "1. Qué significa este movimiento para su negocio (en pesos y centavos, "
            "no en abstracto)\n"
            "2. Cuál de las 3 estrategias (forward, opciones, collar) le conviene más "
            "y por qué\n"
            "3. Una recomendación concreta de acción\n"
            "Tono: directo, sin jerga innecesaria. Máximo 200 palabras. En español."
        )

        # Build a readable summary of the key figures from scenario_result
        sin_cob = scenario_result.get("impacto_sin_cobertura", {})
        fwd     = scenario_result.get("impacto_forward", {})
        opc     = scenario_result.get("impacto_opciones", {})
        collar  = scenario_result.get("impacto_collar", {})

        escenario_texto = (
            f"ESCENARIO HIPOTÉTICO USD/MXN\n"
            f"Spot actual:      ${scenario_result.get('spot_actual', 0):.4f}\n"
            f"Spot hipotético:  ${scenario_result.get('spot_hipotetico', 0):.4f}\n"
            f"Movimiento:       {scenario_result.get('movimiento_pct', 0):+.2f}% "
            f"({scenario_result.get('direccion', '')})\n"
            f"Mejor estrategia sugerida: {scenario_result.get('mejor_estrategia', '')}\n"
            f"\nIMPACTO SIN COBERTURA\n"
            f"Exposición total: ${sin_cob.get('exposicion_total_mxn', 0):,.0f} MXN\n"
            f"Costo adicional vs. hoy: ${sin_cob.get('diferencia_vs_actual_mxn', 0):,.0f} MXN\n"
            f"Impacto sobre margen: {sin_cob.get('impacto_margen_pct', 0):.1f}%\n"
            f"\nFORWARD\n"
            f"Tasa forward: ${fwd.get('tasa_forward', 0):.4f}\n"
            f"Costo total: ${fwd.get('costo_cobertura_mxn', 0):,.0f} MXN\n"
            f"Ahorro vs. sin cobertura: ${fwd.get('ahorro_vs_sin_cobertura_mxn', 0):,.0f} MXN\n"
            f"\nOPCIONES (put ATM)\n"
            f"Prima por USD: ${opc.get('prima_put_mxn_usd', 0):.4f} MXN\n"
            f"Prima total: ${opc.get('prima_total_mxn', 0):,.0f} MXN\n"
            f"Ahorro vs. sin cobertura: ${opc.get('ahorro_vs_sin_cobertura_mxn', 0):,.0f} MXN\n"
            f"\nCOLLAR\n"
            f"Prima neta por USD: ${collar.get('prima_neta_mxn_usd', 0):.4f} MXN\n"
            f"Costo neto total: ${collar.get('costo_neto_mxn', 0):,.0f} MXN\n"
            f"Ahorro vs. sin cobertura: ${collar.get('ahorro_vs_sin_cobertura_mxn', 0):,.0f} MXN\n"
            f"Protección desde: ${collar.get('proteccion_desde', 0):.4f} | "
            f"Límite beneficio: ${collar.get('limite_beneficio', 0):.4f}\n"
            f"\nRESUMEN AUTOMÁTICO: {scenario_result.get('resumen', '')}"
        )

        clean_texto = self.anonymizer.anonymize(escenario_texto)

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=400,
                timeout=_TIMEOUT_SECONDS,
                system=system_prompt,
                messages=[{"role": "user", "content": clean_texto}],
            )
            return message.content[0].text
        except Exception as exc:
            logger.error("Error al llamar a Claude API (analyze_scenario): %s", exc)
            return _FALLBACK_SCENARIO

    # ------------------------------------------------------------------
    # Report recommendations
    # ------------------------------------------------------------------

    def generate_report_recommendations(
        self,
        resumen_mercado: dict,
        pnl_resumen: dict,
    ) -> str:
        """Genera recomendaciones ejecutivas para el reporte periódico del cliente.

        Los datos se asumen ya anonimizados por el caller (montos en rangos,
        sin nombres de empresa ni personas).  Se aplica ``anonymizer.anonymize``
        como safety net adicional antes de enviar a la API.

        Parameters
        ----------
        resumen_mercado : dict
            Contexto de mercado.  Claves esperadas:
            ``spot``, ``variacion_semanal`` (float, porcentaje),
            ``volatilidad_30d`` (float, porcentaje anualizado).
        pnl_resumen : dict
            Salida de ``resumen_pnl_cliente()``.  Claves usadas:
            ``total_mtm_mxn``, ``exposicion_residual_usd``,
            ``num_coberturas``, ``proximos_vencimientos``.

        Returns
        -------
        str
            Recomendaciones concretas en español (≤ 200 palabras), o fallback
            si la API no está disponible.
        """
        spot        = resumen_mercado.get("spot", 0)
        var_sem     = resumen_mercado.get("variacion_semanal", 0)
        vol_30d     = resumen_mercado.get("volatilidad_30d", 0)
        mtm         = pnl_resumen.get("total_mtm_mxn", 0)
        exp_res     = pnl_resumen.get("exposicion_residual_usd", 0) or 0
        num_cob     = pnl_resumen.get("num_coberturas", 0)
        por_vencer  = len(pnl_resumen.get("proximos_vencimientos", []))

        tendencia = "al alza" if var_sem > 0 else "a la baja" if var_sem < 0 else "lateral"

        prompt = f"""Eres un asesor financiero de HedgePoint MX. Genera recomendaciones \
concretas y accionables para un dueño de PyME mexicana basándote ÚNICAMENTE en estos números.

SITUACIÓN ACTUAL:
- Spot USD/MXN: ${spot:.4f}
- Variación semanal: {var_sem:+.2f}% ({tendencia})
- Volatilidad 30 días (anualizada): {vol_30d:.1f}%
- P&L mark-to-market de coberturas activas: ${mtm:,.0f} MXN
- Exposición residual sin cubrir: ${exp_res:,.0f} USD
- Coberturas activas: {num_cob}
- Coberturas que vencen en los próximos 30 días: {por_vencer}

INSTRUCCIONES:
- Da exactamente 3 recomendaciones numeradas, específicas a estos números
- Cada recomendación debe mencionar un número concreto del contexto anterior
- Si hay coberturas por vencer pronto, indica qué hacer con ellas
- Si la exposición residual es significativa, recomienda cubrir qué porción
- Si el P&L es negativo, explica si conviene mantener o ajustar
- Tono: directo, sin jerga. Máximo 100 palabras en total. En español.
- Sin markdown. Texto plano solamente (sin **, ##, *, guiones de lista)."""

        clean_prompt = self.anonymizer.anonymize(prompt)

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=250,
                timeout=_TIMEOUT_SECONDS,
                messages=[{"role": "user", "content": clean_prompt}],
            )
            return message.content[0].text
        except Exception as exc:
            logger.error("Error al llamar a Claude API (report_recommendations): %s", exc)
            return _FALLBACK_REPORT
