"""
Onboarding orchestrator for HedgePoint MX.

Coordinates the full prospect diagnostic flow:
  questionnaire → encrypt & persist → exposure calc → LLM insights → DB update

Usage:
    from agents.onboarding.diagnostic import DiagnosticOrchestrator

    result = DiagnosticOrchestrator().run_full_diagnostic()
    # or, passing pre-collected data (testing / WhatsApp):
    result = DiagnosticOrchestrator().run_full_diagnostic(prospect_data=data)
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.rule import Rule

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agents.onboarding.questionnaire import ProspectQuestionnaire, calculate_exposure
from core.database import insert_prospect, update_prospect_diagnostic
from core.llm_client import HedgePointLLM
from core.security.anonymizer import FieldEncryptor

logger = logging.getLogger(__name__)

_ENC_UNAVAILABLE = "[ENCRIPTACIÓN NO DISPONIBLE]"

# Regex to pull the strategy name out of Claude's response.
# Looks for the word forward / opciones / collar / mix (case-insensitive).
_RE_STRATEGY = re.compile(
    r"\b(forward|opciones?|collar|mix|combinaci[oó]n)\b",
    re.IGNORECASE,
)


def _extract_strategy(insights_text: str) -> str:
    """Return the first hedging strategy mentioned in *insights_text*, or 'forward'."""
    m = _RE_STRATEGY.search(insights_text)
    if not m:
        return "forward"
    word = m.group(1).lower()
    if word.startswith("opci"):
        return "opciones"
    if word in ("mix", "combinación", "combinacion"):
        return "mix"
    return word  # forward | collar


class DiagnosticOrchestrator:
    """
    End-to-end prospect onboarding orchestrator.

    Coordinates:
    1. Data collection (questionnaire or injected dict)
    2. Encryption of sensitive fields + database persistence
    3. FX exposure calculation
    4. LLM diagnostic insights (with anonymization middleware)
    5. Database update with diagnostic results

    Parameters
    ----------
    console : rich.console.Console, optional
        Console instance for progress output.  A new one is created if
        not provided.

    Examples
    --------
    ::

        result = DiagnosticOrchestrator().run_full_diagnostic()
    """

    def __init__(self, console: Optional[Console] = None) -> None:
        self._console = console or Console()

        # FieldEncryptor — graceful degradation if the key is missing
        try:
            self._encryptor: Optional[FieldEncryptor] = FieldEncryptor()
        except ValueError as exc:
            logger.error("FieldEncryptor no disponible: %s", exc)
            self._console.print(
                "[yellow]Advertencia:[/yellow] HEDGEPOINT_ENCRYPTION_KEY no configurada. "
                "Los campos sensibles no se encriptarán."
            )
            self._encryptor = None

        # LLM client — raises ValueError if ANTHROPIC_API_KEY is missing
        try:
            self._llm = HedgePointLLM()
        except ValueError as exc:
            logger.error("HedgePointLLM no disponible: %s", exc)
            self._console.print(
                "[yellow]Advertencia:[/yellow] ANTHROPIC_API_KEY no configurada. "
                "Los insights del diagnóstico usarán texto de respaldo."
            )
            self._llm = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run_full_diagnostic(
        self,
        prospect_data: Optional[dict] = None,
    ) -> dict:
        """Execute the full diagnostic pipeline and return a results dict.

        Parameters
        ----------
        prospect_data : dict, optional
            Pre-collected prospect data (same shape as the dict returned by
            :meth:`ProspectQuestionnaire.run`).  When ``None`` the interactive
            questionnaire is launched.

        Returns
        -------
        dict
            prospect_id        — DB row id (None if persistence failed)
            prospect_data      — original plain-text prospect dict
            exposure           — dict from calculate_exposure()
            insights           — qualitative LLM text
            market_context     — LLM market commentary
            status             — "diagnosticado"
        """
        c = self._console

        # ------------------------------------------------------------------
        # Step 1 — Collect prospect data
        # ------------------------------------------------------------------
        if prospect_data is None:
            prospect_data = ProspectQuestionnaire(console=c).run()

        c.print()
        c.print(Rule("[bold green]Procesando tu diagnóstico[/bold green]", style="green"))

        # ------------------------------------------------------------------
        # Step 2 — Encrypt sensitive fields + persist to DB
        # ------------------------------------------------------------------
        prospect_id: Optional[int] = None

        with c.status("[cyan]Guardando datos del prospecto...[/cyan]"):
            try:
                db_row = self._build_db_row(prospect_data)
                prospect_id = insert_prospect(db_row)
                logger.info("Prospecto guardado con id=%s", prospect_id)
            except Exception as exc:
                logger.error("Error al guardar prospecto en BD: %s", exc)
                c.print("[yellow]Advertencia:[/yellow] No se pudo guardar en la base de datos. El diagnóstico continúa.")

        # ------------------------------------------------------------------
        # Step 3 — Calculate FX exposure
        # ------------------------------------------------------------------
        with c.status("[cyan]Calculando exposición cambiaria...[/cyan]"):
            exposure = calculate_exposure(prospect_data)
            logger.debug("Exposición calculada: %s", exposure)

        c.print(
            f"  [green]OK[/green] Exposición anual: "
            f"[bold]${exposure['exposicion_anual_usd']:,.0f} USD[/bold] "
            f"(${exposure['exposicion_anual_mxn']:,.0f} MXN)"
        )
        if exposure["margen_en_riesgo"]:
            c.print("  [red]AVISO: La perdida potencial supera el margen de utilidad.[/red]")

        # ------------------------------------------------------------------
        # Step 4 — Register entities in the LLM anonymizer
        # ------------------------------------------------------------------
        if self._llm is not None:
            self._llm.register_entity("company", prospect_data.get("empresa", ""))
            self._llm.register_entity("person",  prospect_data.get("nombre", ""))

        # ------------------------------------------------------------------
        # Step 5 — Generate LLM insights
        # ------------------------------------------------------------------
        insights = ""
        market_context = ""

        if self._llm is not None:
            with c.status("[cyan]Generando diagnóstico con IA...[/cyan]"):
                insights = self._llm.generate_diagnostic_insights(
                    exposure_data=exposure,
                    prospect_sector=prospect_data.get("sector", ""),
                )
            c.print("  [green]OK[/green] Diagnóstico generado.")

            with c.status("[cyan]Analizando contexto de mercado...[/cyan]"):
                market_context = self._llm.analyze_market_context()
            c.print("  [green]OK[/green] Contexto de mercado obtenido.")
        else:
            from core.llm_client import _FALLBACK_DIAGNOSTIC, _FALLBACK_MARKET
            insights       = _FALLBACK_DIAGNOSTIC
            market_context = _FALLBACK_MARKET

        # ------------------------------------------------------------------
        # Step 6 — Update DB with diagnostic results
        # ------------------------------------------------------------------
        if prospect_id is not None:
            with c.status("[cyan]Actualizando resultados en BD...[/cyan]"):
                try:
                    estrategia = _extract_strategy(insights)
                    update_prospect_diagnostic(
                        prospect_id=prospect_id,
                        exposicion=exposure["exposicion_anual_usd"],
                        var_95=exposure["perdida_potencial_5pct"],   # proxy for now
                        ahorro=exposure["costo_estimado_forward_mensual"] * 12,
                        estrategia=estrategia,
                    )
                    logger.info(
                        "Diagnóstico guardado para prospect_id=%s, estrategia=%s",
                        prospect_id, estrategia,
                    )
                except Exception as exc:
                    logger.error("Error al actualizar diagnóstico en BD: %s", exc)

        # ------------------------------------------------------------------
        # Step 7 — Return results
        # ------------------------------------------------------------------
        c.print()
        c.print(Rule("[bold green]Diagnóstico completado[/bold green]", style="green"))
        c.print()

        return {
            "prospect_id":    prospect_id,
            "prospect_data":  prospect_data,
            "exposure":       exposure,
            "insights":       insights,
            "market_context": market_context,
            "status":         "diagnosticado",
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _encrypt(self, value: str) -> str:
        """Encrypt *value* or return the unavailability sentinel."""
        if not value:
            return ""
        if self._encryptor is None:
            return _ENC_UNAVAILABLE
        try:
            return self._encryptor.encrypt(value)
        except Exception as exc:
            logger.error("Error encriptando campo: %s", exc)
            return _ENC_UNAVAILABLE

    def _build_db_row(self, data: dict) -> dict:
        """Build the prospects table row from plain prospect_data."""
        return {
            # Sensitive — encrypted
            "nombre_enc":   self._encrypt(data.get("nombre", "")),
            "empresa_enc":  self._encrypt(data.get("empresa", "")),
            "email_enc":    self._encrypt(data.get("email", "")),
            "telefono_enc": self._encrypt(data.get("telefono", "")),
            # Analytical — plaintext
            "sector":               data.get("sector", ""),
            "volumen_usd_mensual":  data.get("volumen_usd_mensual", 0),
            "frecuencia_compra":    data.get("frecuencia_compra", ""),
            "plazo_pago_dias":      data.get("plazo_pago_dias", 30),
            "margen_utilidad":      data.get("margen_utilidad", 0),
            "usa_coberturas":       data.get("usa_coberturas", 0),
            "moneda_principal":     data.get("moneda_principal", "USD"),
        }
