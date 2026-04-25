"""
Tests de seguridad: verifica que ninguna llamada a la API de Anthropic
envíe datos identificables de clientes (PII).

Estrategia:
- Se parchea anthropic.Anthropic completo con unittest.mock.patch.
- Los payloads capturados se analizan con detectar_pii() antes de que
  lleguen a la red. Nunca se hacen llamadas reales a la API.
- test_direct_anthropic_import_forbidden hace análisis estático de agents/.
- test_anonymizer_covers_all_llm_methods hace análisis estático de HedgePointLLM.
"""

from __future__ import annotations

import ast
import inspect
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Detección de PII
# ---------------------------------------------------------------------------

_RE_EMAIL = re.compile(r"\b\w+@\w+\.\w+\b")
_RE_PHONE_MX = re.compile(r"(?:\+52[\s\-]?)?\d{2}[\s\-]?\d{4}[\s\-]?\d{4}")
_RE_RFC = re.compile(r"\b[A-ZÑ&]{3,4}-?\d{6}-?[A-Z0-9]{3}\b", re.IGNORECASE)

# Monto exacto ≥ 10,000 que NO esté dentro de [MONTO: ...]
# Estrategia en dos pasos: (1) borrar todos los brackets anonimizados,
# (2) buscar cifras exactas en el texto resultante.
_RE_ANON_BRACKET = re.compile(r"\[MONTO:[^\]]+\]")
# Miles separados por coma (10,000+) o entero de 5+ dígitos
_RE_EXACT_AMOUNT = re.compile(
    r"(?:^|[\s\$])(\d{1,3}(?:,\d{3})+|\d{5,})"
)


def detectar_pii(texto: str, nombres_conocidos: list[str] | None = None) -> list[str]:
    """Analiza *texto* y retorna lista de problemas PII encontrados.

    Detecta:
    - Emails
    - Teléfonos mexicanos
    - RFC mexicano
    - Montos exactos ≥ 10,000 fuera de brackets [MONTO: ...]
    - Nombres de empresas/personas pasados en *nombres_conocidos*

    Parameters
    ----------
    texto : str
        Texto a analizar (prompt enviado a la API).
    nombres_conocidos : list[str], optional
        Nombres de empresas o personas que NO deben aparecer en claro.

    Returns
    -------
    list[str]
        Lista de strings describiendo cada problema encontrado.
        Lista vacía si el texto está limpio.
    """
    problemas: list[str] = []

    if _RE_EMAIL.search(texto):
        for m in _RE_EMAIL.finditer(texto):
            problemas.append(f"EMAIL encontrado: {m.group()!r}")

    if _RE_PHONE_MX.search(texto):
        for m in _RE_PHONE_MX.finditer(texto):
            problemas.append(f"TELÉFONO encontrado: {m.group()!r}")

    if _RE_RFC.search(texto):
        for m in _RE_RFC.finditer(texto):
            problemas.append(f"RFC encontrado: {m.group()!r}")

    # Montos exactos: quitar primero los ya anonimizados, luego buscar cifras
    texto_sin_brackets = _RE_ANON_BRACKET.sub("", texto)
    for m in _RE_EXACT_AMOUNT.finditer(texto_sin_brackets):
        digits_str = m.group(1).replace(",", "")
        if int(digits_str) >= 10_000:
            problemas.append(f"MONTO EXACTO encontrado: {m.group().strip()!r}")

    for nombre in (nombres_conocidos or []):
        if nombre and nombre in texto:
            problemas.append(f"NOMBRE CONOCIDO encontrado: {nombre!r}")

    return problemas


# ---------------------------------------------------------------------------
# Context manager: interceptar llamadas a la API
# ---------------------------------------------------------------------------

@contextmanager
def interceptar_llamada_api(
    nombres_conocidos: list[str] | None = None,
) -> Generator[MagicMock, None, None]:
    """Context manager que parchea anthropic.Anthropic y valida PII post-bloque.

    Captura todos los ``messages`` enviados en cada llamada a
    ``messages.create()``.  Al salir del bloque, analiza cada mensaje con
    :func:`detectar_pii`.  Si encuentra PII llama ``pytest.fail()``.

    Parameters
    ----------
    nombres_conocidos : list[str], optional
        Nombres que no deben aparecer en claro en los payloads.

    Yields
    ------
    MagicMock
        El mock del cliente de Anthropic (por si el test necesita
        configurar respuestas personalizadas).

    Example
    -------
    ::

        with interceptar_llamada_api(["Empresa Ejemplo S.A."]) as mock_client:
            llm.generate_diagnostic_insights(data, "Importador")
    """
    captured_texts: list[str] = []

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="[respuesta simulada]")]

    mock_messages = MagicMock()

    def _capture_create(**kwargs):
        # Captura system prompt si existe
        if "system" in kwargs:
            captured_texts.append(kwargs["system"])
        # Captura cada mensaje del array messages
        for msg in kwargs.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                captured_texts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and "text" in block:
                        captured_texts.append(block["text"])
        return mock_response

    mock_messages.create.side_effect = _capture_create

    mock_client_instance = MagicMock()
    mock_client_instance.messages = mock_messages

    mock_anthropic_cls = MagicMock(return_value=mock_client_instance)

    with patch("anthropic.Anthropic", mock_anthropic_cls):
        yield mock_client_instance

    # — Post-bloque: análisis de PII —
    fallos: list[str] = []
    for i, texto in enumerate(captured_texts):
        problemas = detectar_pii(texto, nombres_conocidos)
        for problema in problemas:
            fallos.append(f"[payload #{i}] {problema}")

    if fallos:
        detalle = "\n  ".join(fallos)
        pytest.fail(
            f"La API de Anthropic recibió PII en {len(fallos)} instancia(s):\n  {detalle}"
        )


# ---------------------------------------------------------------------------
# Fixtures comunes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_api_key(monkeypatch):
    """Garantiza que ANTHROPIC_API_KEY esté presente (valor ficticio)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-no-real")


def _exposure_data_con_montos() -> dict:
    """Datos de exposición con montos exactos ≥ 10,000 para probar detección."""
    return {
        "tipo_cambio_usado": 17.45,
        "exposicion_anual_usd": 1_200_000,
        "exposicion_anual_mxn": 20_940_000,
        "perdida_potencial_10pct": 2_094_000,
        "perdida_potencial_15pct": 3_141_000,
        "margen_en_riesgo": True,
        "costo_estimado_forward_mensual": 87_500,
    }


def _scenario_result_con_montos() -> dict:
    """ScenarioResult mínimo con montos exactos para probar detección."""
    return {
        "spot_actual": 17.45,
        "spot_hipotetico": 19.20,
        "movimiento_pct": 10.03,
        "direccion": "depreciacion",
        "mejor_estrategia": "forward",
        "resumen": "El forward protege completamente la posición.",
        "impacto_sin_cobertura": {
            "exposicion_total_mxn": 19_200_000,
            "diferencia_vs_actual_mxn": 1_750_000,
            "impacto_margen_pct": 35.0,
        },
        "impacto_forward": {
            "tasa_forward": 17.95,
            "costo_cobertura_mxn": 500_000,
            "ahorro_vs_sin_cobertura_mxn": 1_250_000,
        },
        "impacto_opciones": {
            "prima_put_mxn_usd": 0.32,
            "prima_total_mxn": 320_000,
            "ahorro_vs_sin_cobertura_mxn": 1_430_000,
        },
        "impacto_collar": {
            "prima_neta_mxn_usd": 0.08,
            "costo_neto_mxn": 80_000,
            "ahorro_vs_sin_cobertura_mxn": 1_670_000,
            "proteccion_desde": 17.45,
            "limite_beneficio": 18.50,
        },
    }


# ---------------------------------------------------------------------------
# Tests de seguridad — llamadas a la API
# ---------------------------------------------------------------------------

class TestApiNoPii:

    def test_diagnostic_no_leaks(self):
        """generate_diagnostic_insights no debe filtrar nombre de empresa ni persona."""
        from core.llm_client import HedgePointLLM

        empresa = "Constructora Ejemplo S.A. de C.V."
        persona = "María García López"

        nombres = [empresa, persona]
        with interceptar_llamada_api(nombres_conocidos=nombres):
            llm = HedgePointLLM()
            llm.register_entity("company", empresa)
            llm.register_entity("person", persona)
            llm.generate_diagnostic_insights(
                exposure_data=_exposure_data_con_montos(),
                prospect_sector="Construcción",
            )

    def test_diagnostic_no_leaks_email_y_telefono(self):
        """El prompt de diagnóstico no debe contener email ni teléfono aunque se incluyan
        en los datos de sector (el anonymizer los limpia)."""
        from core.llm_client import HedgePointLLM

        # Inyectamos PII dentro del sector para forzar la detección si no se anonimiza
        sector_con_pii = "contacto@empresa.com — +52 55 1234 5678"
        with interceptar_llamada_api():
            llm = HedgePointLLM()
            # El anonymizer actúa sobre el prompt completo; sector va al prompt sin registro
            # previo — si el anonymizer no lo limpia, detectar_pii lo atrapará.
            llm.generate_diagnostic_insights(
                exposure_data=_exposure_data_con_montos(),
                prospect_sector=sector_con_pii,
            )

    def test_scenario_no_leaks(self):
        """analyze_scenario no debe filtrar nombres ni montos exactos."""
        from core.llm_client import HedgePointLLM

        empresa = "Exportadora del Bajío S.A."
        persona = "Roberto Hernández Trujillo"

        nombres = [empresa, persona]
        with interceptar_llamada_api(nombres_conocidos=nombres):
            llm = HedgePointLLM()
            llm.register_entity("company", empresa)
            llm.register_entity("person", persona)
            llm.analyze_scenario(_scenario_result_con_montos())

    def test_scenario_no_leaks_rfc(self):
        """Si un RFC apareciera en el resumen, el anonymizer debe limpiarlo."""
        from core.llm_client import HedgePointLLM

        resultado = _scenario_result_con_montos()
        resultado["resumen"] = (
            "Empresa RFC XAXX010101000 requiere forward. "
            + resultado["resumen"]
        )
        with interceptar_llamada_api():
            llm = HedgePointLLM()
            llm.analyze_scenario(resultado)

    def test_mock_intercepta_llamadas(self):
        """Verifica que el mock funciona: messages.create fue llamado."""
        from core.llm_client import HedgePointLLM

        with interceptar_llamada_api() as mock_client:
            llm = HedgePointLLM()
            llm.generate_diagnostic_insights(
                exposure_data=_exposure_data_con_montos(),
                prospect_sector="Manufactura",
            )
            assert mock_client.messages.create.called


# ---------------------------------------------------------------------------
# Test de análisis estático — imports directos de anthropic en agents/
# ---------------------------------------------------------------------------

class TestDirectImportForbidden:

    def test_direct_anthropic_import_forbidden(self):
        """Solo core/llm_client.py puede importar anthropic directamente.

        Todos los archivos en agents/ deben usar HedgePointLLM como middleware.
        Un import directo de anthropic en agents/ significa que el middleware
        de anonimización podría estar siendo omitido.

        Excepciones documentadas (archivos que usan solo datos públicos de mercado,
        nunca datos de clientes, y tienen su propio manejo de errores de API):
        - agents/monitor/notifier.py: genera análisis de mercado FX con datos
          públicos (tipos de cambio, noticias macro). No recibe ni procesa PII.
          Pendiente de refactorizar a HedgePointLLM en Sprint 6.
        """
        # EXCEPCIÓN: notifier.py importa anthropic directamente pero solo envía
        # datos públicos de mercado (precios FX, umbrales, símbolos). No maneja
        # datos de clientes. Verificado: triggers.yaml no contiene PII.
        # Revisado: Sprint 5, Tarea 3.
        EXCEPCIONES: set[str] = {
            "agents/monitor/notifier.py",
            "agents\\monitor\\notifier.py",  # Windows path variant
        }

        repo_root = Path(__file__).parent.parent
        agents_dir = repo_root / "agents"

        violaciones: list[str] = []

        for py_file in agents_dir.rglob("*.py"):
            rel_path = str(py_file.relative_to(repo_root))
            if rel_path in EXCEPCIONES:
                continue

            source = py_file.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "anthropic" or alias.name.startswith("anthropic."):
                            violaciones.append(
                                f"{rel_path}:{node.lineno} "
                                f"→ 'import {alias.name}'"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and (
                        node.module == "anthropic"
                        or node.module.startswith("anthropic.")
                    ):
                        nombres_imp = ", ".join(a.name for a in node.names)
                        violaciones.append(
                            f"{rel_path}:{node.lineno} "
                            f"→ 'from {node.module} import {nombres_imp}'"
                        )

        if violaciones:
            detalle = "\n  ".join(violaciones)
            pytest.fail(
                "Los siguientes archivos en agents/ importan anthropic directamente "
                "(deben usar HedgePointLLM en su lugar):\n\n  " + detalle
            )


# ---------------------------------------------------------------------------
# Test de análisis estático — todo método que llame a messages.create
# también debe llamar a anonymizer.anonymize
# ---------------------------------------------------------------------------

class TestAnonymizerCoverage:

    def test_anonymizer_covers_all_llm_methods(self):
        """Verifica estáticamente que cada método de HedgePointLLM que llame
        a self._client.messages.create() también llame a self.anonymizer.anonymize().

        Usa inspect.getsource() + ast.parse() para analizar cada método por separado.
        """
        from core.llm_client import HedgePointLLM

        # Métodos que llaman a messages.create pero usan SOLO información pública
        # y están documentados como seguros (no contienen datos de clientes).
        EXENTOS = {"analyze_market_context", "parse_scenario"}

        source = inspect.getsource(HedgePointLLM)
        tree = ast.parse(source)

        class_def = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        )

        violaciones: list[str] = []

        for node in ast.walk(class_def):
            if not isinstance(node, ast.FunctionDef):
                continue
            method_name = node.name
            if method_name.startswith("_") or method_name in EXENTOS:
                continue

            method_source = ast.get_source_segment(source, node) or ""

            llama_create = "messages.create" in method_source
            llama_anonymize = "anonymizer.anonymize" in method_source

            if llama_create and not llama_anonymize:
                violaciones.append(
                    f"HedgePointLLM.{method_name}() llama a messages.create() "
                    "pero NO llama a self.anonymizer.anonymize()"
                )

        if violaciones:
            detalle = "\n  ".join(violaciones)
            pytest.fail(
                "Métodos de HedgePointLLM sin cobertura de anonymizer:\n\n  " + detalle
            )


# ---------------------------------------------------------------------------
# Tests unitarios de detectar_pii (sin BD, sin mock de Anthropic)
# ---------------------------------------------------------------------------

class TestDetectarPii:
    """Verifica que detectar_pii() identifica correctamente cada tipo de PII."""

    def test_detecta_email(self):
        assert any("EMAIL" in p for p in detectar_pii("Contacto: juan@empresa.com"))

    def test_detecta_telefono(self):
        assert any("TELÉFONO" in p for p in detectar_pii("+52 55 1234 5678"))

    def test_detecta_telefono_sin_prefijo(self):
        assert any("TELÉFONO" in p for p in detectar_pii("55 1234 5678"))

    def test_detecta_rfc(self):
        assert any("RFC" in p for p in detectar_pii("RFC: XAXX010101000"))

    def test_detecta_monto_exacto_grande(self):
        assert any("MONTO EXACTO" in p for p in detectar_pii("Monto: $1,500,000 MXN"))

    def test_detecta_monto_exacto_entero(self):
        assert any("MONTO EXACTO" in p for p in detectar_pii("pagó 50000 pesos"))

    def test_no_detecta_monto_anonimizado(self):
        texto = "Monto: [MONTO: ~$1.5M-$1.6M] MXN"
        assert not any("MONTO EXACTO" in p for p in detectar_pii(texto))

    def test_no_detecta_monto_pequeno(self):
        """Montos < 10,000 no son PII relevante."""
        assert not any("MONTO EXACTO" in p for p in detectar_pii("cobró $500"))

    def test_detecta_nombre_conocido(self):
        texto = "El cliente es Constructora Ejemplo S.A. de C.V."
        resultado = detectar_pii(texto, nombres_conocidos=["Constructora Ejemplo S.A. de C.V."])
        assert any("NOMBRE CONOCIDO" in p for p in resultado)

    def test_no_detecta_sin_pii(self):
        texto = (
            "El sector manufactura tiene una exposición de [MONTO: ~$1.5M-$1.6M] USD. "
            "Cliente A opera con forward a 90 días."
        )
        assert detectar_pii(texto) == []

    def test_multiples_pii_en_un_texto(self):
        texto = "juan@corp.mx, +52 55 9999 0000, RFC JUAN800101ABC, $500,000"
        problemas = detectar_pii(texto)
        tipos = {p.split()[0] for p in problemas}
        assert "EMAIL" in tipos
        assert "TELÉFONO" in tipos
        assert "RFC" in tipos
        assert "MONTO" in tipos
