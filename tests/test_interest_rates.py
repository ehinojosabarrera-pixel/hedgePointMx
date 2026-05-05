"""
Tests para fetch de tasas de interés (TIIE y SOFR) y get_tasas_actuales().

Incluye:
- Tests unitarios con mocks (no requieren APIs ni BD)
- Tests de integración opcionales que sí llaman a las APIs reales (marcados con skip si no hay key)
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# fetch_tiie_banxico — unitarios con mock HTTP
# ---------------------------------------------------------------------------

class TestFetchTiieBanxico:

    def _mock_response(self, valor_pct: float):
        """Construye un mock de requests.Response con el payload de Banxico."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bmx": {
                "series": [
                    {
                        "idSerie": "SF43783",
                        "titulo": "TIIE a 28 días",
                        "datos": [
                            {"fecha": "28/04/2026", "dato": str(valor_pct)},
                        ],
                    }
                ]
            }
        }
        return mock_resp

    def test_retorna_decimal(self):
        from core.data.market_data import fetch_tiie_banxico
        with patch("core.data.market_data.requests.get", return_value=self._mock_response(7.02)), \
             patch.dict("os.environ", {"BANXICO_API_KEY": "fake-key"}):
            resultado = fetch_tiie_banxico()
        assert isinstance(resultado, float)

    def test_rango_valido(self):
        from core.data.market_data import fetch_tiie_banxico
        with patch("core.data.market_data.requests.get", return_value=self._mock_response(7.02)), \
             patch.dict("os.environ", {"BANXICO_API_KEY": "fake-key"}):
            resultado = fetch_tiie_banxico()
        assert 0.01 <= resultado <= 0.20, f"TIIE fuera de rango: {resultado}"

    def test_convierte_porcentaje_a_decimal(self):
        from core.data.market_data import fetch_tiie_banxico
        with patch("core.data.market_data.requests.get", return_value=self._mock_response(7.02)), \
             patch.dict("os.environ", {"BANXICO_API_KEY": "fake-key"}):
            resultado = fetch_tiie_banxico()
        assert abs(resultado - 0.0702) < 1e-6, f"Esperado ~0.0702, obtenido {resultado}"

    def test_sin_api_key_lanza_environment_error(self):
        from core.data.market_data import fetch_tiie_banxico
        with patch.dict("os.environ", {}, clear=True):
            import os
            os.environ.pop("BANXICO_API_KEY", None)
            with pytest.raises(EnvironmentError, match="BANXICO_API_KEY"):
                fetch_tiie_banxico()

    def test_datos_vacios_lanza_value_error(self):
        from core.data.market_data import fetch_tiie_banxico
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "bmx": {"series": [{"idSerie": "SF43783", "datos": []}]}
        }
        with patch("core.data.market_data.requests.get", return_value=mock_resp), \
             patch.dict("os.environ", {"BANXICO_API_KEY": "fake-key"}):
            with pytest.raises(ValueError):
                fetch_tiie_banxico()

    def test_timeout_lanza_connection_error(self):
        import requests as req
        from core.data.market_data import fetch_tiie_banxico
        with patch("core.data.market_data.requests.get", side_effect=req.exceptions.Timeout()), \
             patch.dict("os.environ", {"BANXICO_API_KEY": "fake-key"}):
            with pytest.raises(ConnectionError, match="timeout"):
                fetch_tiie_banxico()


# ---------------------------------------------------------------------------
# fetch_sofr_fred — unitarios con mock HTTP
# ---------------------------------------------------------------------------

class TestFetchSofrFred:

    def _mock_response(self, valor_pct: float):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "observations": [
                {"date": "2026-04-28", "value": str(valor_pct)},
            ]
        }
        return mock_resp

    def test_retorna_decimal(self):
        from core.data.market_data import fetch_sofr_fred
        with patch("core.data.market_data.requests.get", return_value=self._mock_response(3.66)), \
             patch.dict("os.environ", {"FRED_API_KEY": "fake-fred-key"}):
            resultado = fetch_sofr_fred()
        assert isinstance(resultado, float)

    def test_rango_valido(self):
        from core.data.market_data import fetch_sofr_fred
        with patch("core.data.market_data.requests.get", return_value=self._mock_response(3.66)), \
             patch.dict("os.environ", {"FRED_API_KEY": "fake-fred-key"}):
            resultado = fetch_sofr_fred()
        assert 0.00 <= resultado <= 0.10, f"SOFR fuera de rango: {resultado}"

    def test_convierte_porcentaje_a_decimal(self):
        from core.data.market_data import fetch_sofr_fred
        with patch("core.data.market_data.requests.get", return_value=self._mock_response(3.66)), \
             patch.dict("os.environ", {"FRED_API_KEY": "fake-fred-key"}):
            resultado = fetch_sofr_fred()
        assert abs(resultado - 0.0366) < 1e-6, f"Esperado ~0.0366, obtenido {resultado}"

    def test_sin_api_key_lanza_environment_error(self):
        from core.data.market_data import fetch_sofr_fred
        import os
        os.environ.pop("FRED_API_KEY", None)
        with patch.dict("os.environ", {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}):
            with pytest.raises(EnvironmentError, match="FRED_API_KEY"):
                fetch_sofr_fred()

    def test_observaciones_vacias_lanza_value_error(self):
        from core.data.market_data import fetch_sofr_fred
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"observations": []}
        with patch("core.data.market_data.requests.get", return_value=mock_resp), \
             patch.dict("os.environ", {"FRED_API_KEY": "fake-fred-key"}):
            with pytest.raises(ValueError):
                fetch_sofr_fred()

    def test_timeout_lanza_connection_error(self):
        import requests as req
        from core.data.market_data import fetch_sofr_fred
        with patch("core.data.market_data.requests.get", side_effect=req.exceptions.Timeout()), \
             patch.dict("os.environ", {"FRED_API_KEY": "fake-fred-key"}):
            with pytest.raises(ConnectionError, match="timeout"):
                fetch_sofr_fred()


# ---------------------------------------------------------------------------
# get_tasas_actuales — unitarios con BD temporal
# ---------------------------------------------------------------------------

class TestGetTasasActuales:

    def _make_db(self, tiie=None, sofr=None):
        """Crea una BD temporal con los datos especificados."""
        from core.database import init_db, insert_interest_rate
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db_path = Path(tmp.name)
        init_db(db_path)
        if tiie is not None:
            insert_interest_rate("2026-04-28", "10:00:00", "TIIE28D", tiie, "Banxico", db_path)
        if sofr is not None:
            insert_interest_rate("2026-04-28", "10:00:00", "SOFR", sofr, "FRED", db_path)
        return db_path

    def test_fallback_bd_vacia(self):
        from core.models.pricing import get_tasas_actuales, _TIIE_FALLBACK, _SOFR_FALLBACK
        db_path = self._make_db()
        tasas = get_tasas_actuales(db_path=db_path)
        assert tasas["tiie"] == _TIIE_FALLBACK
        assert tasas["sofr"] == _SOFR_FALLBACK

    def test_lee_tiie_de_bd(self):
        from core.models.pricing import get_tasas_actuales
        db_path = self._make_db(tiie=0.0702, sofr=0.0366)
        tasas = get_tasas_actuales(db_path=db_path)
        assert abs(tasas["tiie"] - 0.0702) < 1e-9
        assert abs(tasas["sofr"] - 0.0366) < 1e-9

    def test_tiie_bd_sofr_fallback(self):
        from core.models.pricing import get_tasas_actuales, _SOFR_FALLBACK
        db_path = self._make_db(tiie=0.0702)
        tasas = get_tasas_actuales(db_path=db_path)
        assert abs(tasas["tiie"] - 0.0702) < 1e-9
        assert tasas["sofr"] == _SOFR_FALLBACK

    def test_error_bd_devuelve_fallbacks(self):
        from core.models.pricing import get_tasas_actuales, _TIIE_FALLBACK, _SOFR_FALLBACK
        tasas = get_tasas_actuales(db_path=Path("/ruta/inexistente/db.sqlite"))
        assert tasas["tiie"] == _TIIE_FALLBACK
        assert tasas["sofr"] == _SOFR_FALLBACK

    def test_retorna_dict_con_llaves_correctas(self):
        from core.models.pricing import get_tasas_actuales
        db_path = self._make_db()
        tasas = get_tasas_actuales(db_path=db_path)
        assert "tiie" in tasas
        assert "sofr" in tasas


# ---------------------------------------------------------------------------
# calcular_forward con tiie=None — usa BD o fallback
# ---------------------------------------------------------------------------

class TestCalcForwardConNone:

    def test_forward_con_none_no_crashea(self):
        from core.models.pricing import calcular_forward
        resultado = calcular_forward(spot=17.5, dias=30)
        assert resultado.forward > 0

    def test_forward_con_none_usa_tasas_razonables(self):
        """El forward con tasas actuales debe ser cercano al spot (diferencial ~3%)."""
        from core.models.pricing import calcular_forward
        resultado = calcular_forward(spot=17.5, dias=30)
        # Con TIIE~7% y SOFR~3.7%, forward a 30d ≈ spot * (1 + 0.033/12) ≈ spot + 0.05
        assert abs(resultado.forward - resultado.spot) < 1.0

    def test_forward_explicito_no_llama_bd(self):
        """Si se pasan tiie y sofr explícitos, no debe invocar get_tasas_actuales."""
        from core.models.pricing import calcular_forward
        with patch("core.models.pricing.get_tasas_actuales") as mock_get:
            resultado = calcular_forward(spot=17.5, dias=30, tiie=0.07, sofr=0.035)
        mock_get.assert_not_called()
        assert resultado.tiie == 0.07
        assert resultado.sofr == 0.035


# ---------------------------------------------------------------------------
# Tests de integración con APIs reales (saltan sin keys)
# ---------------------------------------------------------------------------

def test_integration_tiie_banxico_rango():
    import os
    if not os.getenv("BANXICO_API_KEY"):
        pytest.skip("BANXICO_API_KEY no configurada")
    from core.data.market_data import fetch_tiie_banxico
    tiie = fetch_tiie_banxico()
    assert 0.01 <= tiie <= 0.20, f"TIIE real fuera de rango razonable: {tiie}"


def test_integration_sofr_fred_rango():
    import os
    if not os.getenv("FRED_API_KEY"):
        pytest.skip("FRED_API_KEY no configurada")
    from core.data.market_data import fetch_sofr_fred
    sofr = fetch_sofr_fred()
    assert 0.00 <= sofr <= 0.10, f"SOFR real fuera de rango razonable: {sofr}"
