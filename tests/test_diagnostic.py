"""
Unit / integration tests for DiagnosticOrchestrator.

All tests inject prospect_data directly — the interactive CLI is never launched.
A temporary SQLite database is used for every test that touches persistence.

Run:
    pytest tests/test_diagnostic.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_PROSPECT = {
    "nombre":              "Juan Pérez",
    "empresa":             "Importadora del Norte SA",
    "email":               "juan@importnorte.mx",
    "telefono":            "8112345678",
    "sector":              "Importador",
    "volumen_usd_mensual": 300_000.0,
    "frecuencia_compra":   "Mensual",
    "plazo_pago_dias":     30,
    "margen_utilidad":     0.12,
    "usa_coberturas":      0,
    "moneda_principal":    "USD",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_env_keys(monkeypatch):
    """Provide valid-looking env keys for every test by default."""
    monkeypatch.setenv("HEDGEPOINT_ENCRYPTION_KEY", "test-key-diagnostic-suite")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-key")


@pytest.fixture()
def tmp_db(tmp_path):
    """Initialise a fresh SQLite DB in a pytest tmp_path and return its Path."""
    from core.database import init_db
    db = tmp_path / "test_hedgepoint.db"
    init_db(db)
    return db


@pytest.fixture()
def quiet_console():
    from rich.console import Console
    return Console(quiet=True)


@pytest.fixture()
def orchestrator(tmp_db, quiet_console, monkeypatch):
    """
    Return a DiagnosticOrchestrator whose DB calls are redirected to tmp_db.
    The Anthropic API key is a fake so all LLM calls fall back gracefully.
    """
    import agents.onboarding.diagnostic as diag_mod
    from core.database import (
        init_db,
        insert_prospect as real_insert,
        update_prospect_diagnostic as real_update,
    )

    monkeypatch.setattr(
        diag_mod,
        "insert_prospect",
        lambda data: real_insert(data, tmp_db),
    )
    monkeypatch.setattr(
        diag_mod,
        "update_prospect_diagnostic",
        lambda prospect_id, exposicion, var_95, ahorro, estrategia: real_update(
            prospect_id, exposicion, var_95, ahorro, estrategia, tmp_db
        ),
    )

    from agents.onboarding.diagnostic import DiagnosticOrchestrator
    return DiagnosticOrchestrator(console=quiet_console)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _run(orchestrator, data=None):
    """Convenience wrapper — copies SAMPLE_PROSPECT so tests can't mutate it."""
    prospect = dict(data or SAMPLE_PROSPECT)
    return orchestrator.run_full_diagnostic(prospect_data=prospect)


# ===========================================================================
# Test 1 — return dict shape
# ===========================================================================

class TestReturnShape:

    def test_full_diagnostic_returns_complete_dict(self, orchestrator):
        result = _run(orchestrator)

        assert "prospect_id"    in result
        assert "prospect_data"  in result
        assert "exposure"       in result
        assert "insights"       in result
        assert "market_context" in result
        assert "status"         in result

    def test_status_is_diagnosticado(self, orchestrator):
        result = _run(orchestrator)
        assert result["status"] == "diagnosticado"

    def test_prospect_id_is_int_or_none(self, orchestrator):
        result = _run(orchestrator)
        assert result["prospect_id"] is None or isinstance(result["prospect_id"], int)

    def test_prospect_data_is_original_dict(self, orchestrator):
        original = dict(SAMPLE_PROSPECT)
        result = orchestrator.run_full_diagnostic(prospect_data=original)
        assert result["prospect_data"] is original

    def test_insights_is_non_empty_string(self, orchestrator):
        result = _run(orchestrator)
        assert isinstance(result["insights"], str)
        assert len(result["insights"]) > 0

    def test_market_context_is_non_empty_string(self, orchestrator):
        result = _run(orchestrator)
        assert isinstance(result["market_context"], str)
        assert len(result["market_context"]) > 0

    def test_exposure_is_dict(self, orchestrator):
        result = _run(orchestrator)
        assert isinstance(result["exposure"], dict)


# ===========================================================================
# Test 2 — exposure calculation
# ===========================================================================

class TestExposureCalculation:

    def test_exposicion_anual_usd_is_twelve_months(self, orchestrator):
        result = _run(orchestrator)
        assert result["exposure"]["exposicion_anual_usd"] == 300_000 * 12

    def test_perdida_potencial_10pct_is_positive(self, orchestrator):
        result = _run(orchestrator)
        assert result["exposure"]["perdida_potencial_10pct"] > 0

    def test_perdida_potencial_ordering(self, orchestrator):
        """5% < 10% < 15% losses."""
        exp = _run(orchestrator)["exposure"]
        assert exp["perdida_potencial_5pct"] < exp["perdida_potencial_10pct"]
        assert exp["perdida_potencial_10pct"] < exp["perdida_potencial_15pct"]

    def test_exposicion_mxn_uses_fx_rate(self, orchestrator):
        exp = _run(orchestrator)["exposure"]
        tc = exp["tipo_cambio_usado"]
        expected_mxn = exp["exposicion_anual_usd"] * tc
        assert abs(exp["exposicion_anual_mxn"] - expected_mxn) < 1.0

    def test_margen_en_riesgo_is_bool(self, orchestrator):
        exp = _run(orchestrator)["exposure"]
        assert isinstance(exp["margen_en_riesgo"], bool)

    def test_margen_en_riesgo_false_for_high_margin(self, orchestrator):
        """12% margin should not be at risk from a 10% FX move."""
        exp = _run(orchestrator)["exposure"]
        # 10% < 12%  →  False
        assert exp["margen_en_riesgo"] is False

    def test_margen_en_riesgo_true_for_thin_margin(self, orchestrator):
        thin_margin = dict(SAMPLE_PROSPECT, margen_utilidad=0.05)
        exp = orchestrator.run_full_diagnostic(prospect_data=thin_margin)["exposure"]
        # 10% > 5%  →  True
        assert exp["margen_en_riesgo"] is True

    def test_costo_forward_mensual_is_positive(self, orchestrator):
        exp = _run(orchestrator)["exposure"]
        assert exp["costo_estimado_forward_mensual"] > 0


# ===========================================================================
# Test 3 — DB persistence
# ===========================================================================

class TestDatabasePersistence:

    def test_prospect_saved_to_db(self, orchestrator, tmp_db):
        from core.database import get_prospect
        result = _run(orchestrator)
        pid = result["prospect_id"]
        assert pid is not None
        row = get_prospect(pid, tmp_db)
        assert row is not None

    def test_nombre_enc_is_not_plaintext(self, orchestrator, tmp_db):
        from core.database import get_prospect
        result = _run(orchestrator)
        row = get_prospect(result["prospect_id"], tmp_db)
        assert row["nombre_enc"] != SAMPLE_PROSPECT["nombre"]

    def test_empresa_enc_is_not_plaintext(self, orchestrator, tmp_db):
        from core.database import get_prospect
        result = _run(orchestrator)
        row = get_prospect(result["prospect_id"], tmp_db)
        assert row["empresa_enc"] != SAMPLE_PROSPECT["empresa"]

    def test_encrypted_fields_are_decryptable(self, orchestrator, tmp_db):
        from core.database import get_prospect
        from core.security.anonymizer import FieldEncryptor
        enc = FieldEncryptor()
        result = _run(orchestrator)
        row = get_prospect(result["prospect_id"], tmp_db)
        assert enc.decrypt(row["nombre_enc"])   == SAMPLE_PROSPECT["nombre"]
        assert enc.decrypt(row["empresa_enc"])  == SAMPLE_PROSPECT["empresa"]
        assert enc.decrypt(row["email_enc"])    == SAMPLE_PROSPECT["email"]
        assert enc.decrypt(row["telefono_enc"]) == SAMPLE_PROSPECT["telefono"]

    def test_analytical_fields_stored_in_plaintext(self, orchestrator, tmp_db):
        from core.database import get_prospect
        result = _run(orchestrator)
        row = get_prospect(result["prospect_id"], tmp_db)
        assert row["sector"]              == SAMPLE_PROSPECT["sector"]
        assert row["volumen_usd_mensual"] == SAMPLE_PROSPECT["volumen_usd_mensual"]
        assert row["margen_utilidad"]     == SAMPLE_PROSPECT["margen_utilidad"]

    def test_status_updated_to_diagnosticado(self, orchestrator, tmp_db):
        from core.database import get_prospect
        result = _run(orchestrator)
        row = get_prospect(result["prospect_id"], tmp_db)
        assert row["status"] == "diagnosticado"

    def test_exposicion_anual_persisted(self, orchestrator, tmp_db):
        from core.database import get_prospect
        result = _run(orchestrator)
        row = get_prospect(result["prospect_id"], tmp_db)
        assert row["exposicion_anual_usd"] == 300_000 * 12

    def test_estrategia_recomendada_persisted(self, orchestrator, tmp_db):
        from core.database import get_prospect
        result = _run(orchestrator)
        row = get_prospect(result["prospect_id"], tmp_db)
        assert row["estrategia_recomendada"] in ("forward", "collar", "opciones", "mix")


# ===========================================================================
# Test 4 — runs without encryption key
# ===========================================================================

class TestWithoutEncryptionKey:

    def test_completes_without_error(self, tmp_db, quiet_console, monkeypatch):
        monkeypatch.delenv("HEDGEPOINT_ENCRYPTION_KEY", raising=False)
        import agents.onboarding.diagnostic as diag_mod
        from core.database import (
            insert_prospect as real_insert,
            update_prospect_diagnostic as real_update,
        )
        monkeypatch.setattr(diag_mod, "insert_prospect",
                            lambda d: real_insert(d, tmp_db))
        monkeypatch.setattr(
            diag_mod, "update_prospect_diagnostic",
            lambda prospect_id, exposicion, var_95, ahorro, estrategia: real_update(
                prospect_id, exposicion, var_95, ahorro, estrategia, tmp_db
            ),
        )
        from agents.onboarding.diagnostic import DiagnosticOrchestrator
        orch = DiagnosticOrchestrator(console=quiet_console)
        result = orch.run_full_diagnostic(prospect_data=dict(SAMPLE_PROSPECT))
        assert result["status"] == "diagnosticado"

    def test_prospect_id_may_be_none_or_int(self, tmp_db, quiet_console, monkeypatch):
        """Without encryption the DB insert may still succeed with sentinel values."""
        monkeypatch.delenv("HEDGEPOINT_ENCRYPTION_KEY", raising=False)
        import agents.onboarding.diagnostic as diag_mod
        from core.database import (
            insert_prospect as real_insert,
            update_prospect_diagnostic as real_update,
        )
        monkeypatch.setattr(diag_mod, "insert_prospect",
                            lambda d: real_insert(d, tmp_db))
        monkeypatch.setattr(
            diag_mod, "update_prospect_diagnostic",
            lambda prospect_id, exposicion, var_95, ahorro, estrategia: real_update(
                prospect_id, exposicion, var_95, ahorro, estrategia, tmp_db
            ),
        )
        from agents.onboarding.diagnostic import DiagnosticOrchestrator
        orch = DiagnosticOrchestrator(console=quiet_console)
        result = orch.run_full_diagnostic(prospect_data=dict(SAMPLE_PROSPECT))
        assert result["prospect_id"] is None or isinstance(result["prospect_id"], int)

    def test_enc_fields_contain_fallback_sentinel(self, tmp_db, quiet_console, monkeypatch):
        from agents.onboarding.diagnostic import _ENC_UNAVAILABLE
        monkeypatch.delenv("HEDGEPOINT_ENCRYPTION_KEY", raising=False)
        import agents.onboarding.diagnostic as diag_mod
        from core.database import (
            insert_prospect as real_insert,
            update_prospect_diagnostic as real_update,
        )
        monkeypatch.setattr(diag_mod, "insert_prospect",
                            lambda d: real_insert(d, tmp_db))
        monkeypatch.setattr(
            diag_mod, "update_prospect_diagnostic",
            lambda prospect_id, exposicion, var_95, ahorro, estrategia: real_update(
                prospect_id, exposicion, var_95, ahorro, estrategia, tmp_db
            ),
        )
        from agents.onboarding.diagnostic import DiagnosticOrchestrator
        orch = DiagnosticOrchestrator(console=quiet_console)
        row = orch._build_db_row(SAMPLE_PROSPECT)
        assert row["nombre_enc"]   == _ENC_UNAVAILABLE
        assert row["empresa_enc"]  == _ENC_UNAVAILABLE


# ===========================================================================
# Test 5 — runs without Anthropic API key
# ===========================================================================

class TestWithoutApiKey:

    @pytest.fixture()
    def orch_no_llm(self, tmp_db, quiet_console, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Evitar que load_dotenv() en HedgePointLLM.__init__ restaure la key desde .env
        import core.llm_client as llm_mod
        monkeypatch.setattr(llm_mod, "load_dotenv", lambda **kw: None)
        import agents.onboarding.diagnostic as diag_mod
        from core.database import (
            insert_prospect as real_insert,
            update_prospect_diagnostic as real_update,
        )
        monkeypatch.setattr(diag_mod, "insert_prospect",
                            lambda d: real_insert(d, tmp_db))
        monkeypatch.setattr(
            diag_mod, "update_prospect_diagnostic",
            lambda prospect_id, exposicion, var_95, ahorro, estrategia: real_update(
                prospect_id, exposicion, var_95, ahorro, estrategia, tmp_db
            ),
        )
        from agents.onboarding.diagnostic import DiagnosticOrchestrator
        return DiagnosticOrchestrator(console=quiet_console)

    def test_completes_without_error(self, orch_no_llm):
        result = orch_no_llm.run_full_diagnostic(prospect_data=dict(SAMPLE_PROSPECT))
        assert result["status"] == "diagnosticado"

    def test_insights_contains_fallback_text(self, orch_no_llm):
        result = orch_no_llm.run_full_diagnostic(prospect_data=dict(SAMPLE_PROSPECT))
        assert "no disponible" in result["insights"].lower()

    def test_market_context_contains_fallback_text(self, orch_no_llm):
        result = orch_no_llm.run_full_diagnostic(prospect_data=dict(SAMPLE_PROSPECT))
        assert "no disponible" in result["market_context"].lower()

    def test_exposure_still_calculated(self, orch_no_llm):
        """Exposure calculation must not depend on the LLM client."""
        result = orch_no_llm.run_full_diagnostic(prospect_data=dict(SAMPLE_PROSPECT))
        assert result["exposure"]["exposicion_anual_usd"] == 300_000 * 12
