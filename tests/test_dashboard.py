"""
Tests para agents/reports/dashboard.py

Usa TestClient de Starlette con una BD temporal inyectada mediante
override de las llamadas a core.database (monkeypatch sobre las
funciones importadas en el módulo dashboard).

No dependen de DASHBOARD_PASSWORD, RESEND_API_KEY ni ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from core.database import (
    init_db,
    insert_fx_rate,
    insert_hedge,
    insert_prospect,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PWD = "testpwd123"
_TOKEN = hashlib.sha256(_PWD.encode()).hexdigest()


@pytest.fixture(autouse=True)
def set_test_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", _PWD)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """BD temporal con un prospect, fx_rates y 2 coberturas activas."""
    db_path = tmp_path / "dash_test.db"
    init_db(db_path)

    pid = insert_prospect(
        {
            "nombre_enc":          "Ana Test",
            "empresa_enc":         "Empresa Test S.A.",
            "sector":              "Exportador",
            "volumen_usd_mensual": 200_000,
            "margen_utilidad":     0.10,
            "status":              "diagnosticado",
        },
        db_path=db_path,
    )

    hoy = date.today()
    bid = 20.00
    for i in range(29, -1, -1):
        fecha = (hoy - timedelta(days=i)).isoformat()
        insert_fx_rate(
            fecha=fecha, hora="12:00:00", par="USDMXN",
            bid=round(bid, 4), ask=round(bid + 0.05, 4),
            source="test", db_path=db_path,
        )

    hoy_iso = hoy.isoformat()
    insert_hedge(
        {
            "prospect_id":      pid,
            "tipo":             "forward",
            "monto_usd":        100_000.0,
            "strike":           20.10,
            "spot_entrada":     20.00,
            "tasa_forward":     20.10,
            "prima_pagada_mxn": 0.0,
            "fecha_inicio":     hoy_iso,
            "fecha_vencimiento": (hoy + timedelta(days=45)).isoformat(),
        },
        db_path=db_path,
    )
    insert_hedge(
        {
            "prospect_id":      pid,
            "tipo":             "put",
            "monto_usd":        80_000.0,
            "strike":           19.80,
            "spot_entrada":     20.00,
            "prima_pagada_mxn": 8_000.0,
            "fecha_inicio":     hoy_iso,
            "fecha_vencimiento": (hoy + timedelta(days=20)).isoformat(),
        },
        db_path=db_path,
    )

    return db_path


@pytest.fixture
def prospect_id(db: Path) -> int:
    """Retorna el ID del prospect creado en la BD de test (siempre 1)."""
    return 1


@pytest.fixture
def client(db: Path, monkeypatch) -> TestClient:
    """TestClient con DB inyectada vía monkeypatch sobre dashboard globals."""
    import agents.reports.dashboard as dash

    # Reemplazar DB_PATH en todas las funciones que usan el default
    monkeypatch.setattr(dash, "DB_PATH", db)

    # Mockear HedgePointLLM para evitar llamadas reales durante POST /reporte
    fake_llm = MagicMock()
    fake_llm.return_value.generate_report_recommendations.return_value = (
        "Recomendaciones de prueba."
    )
    monkeypatch.setattr(
        "agents.reports.report_generator.HedgePointLLM",
        fake_llm,
        raising=False,
    )

    # Las rutas usan DB_PATH como default — necesitamos que las funciones
    # importadas en dash usen db. Las parchamos directamente en el módulo.
    from core.database import (
        get_all_prospects,
        get_client_hedges,
        get_expiring_hedges,
        get_latest_fx_rates,
        get_prospect,
    )
    from core.models.hedge_pnl import resumen_pnl_cliente

    monkeypatch.setattr(dash, "get_all_prospects",
                        lambda **kw: get_all_prospects(db_path=db))
    monkeypatch.setattr(dash, "get_prospect",
                        lambda pid, **kw: get_prospect(pid, db_path=db))
    monkeypatch.setattr(dash, "get_client_hedges",
                        lambda pid, estado=None, **kw: get_client_hedges(pid, estado=estado, db_path=db))
    monkeypatch.setattr(dash, "get_latest_fx_rates",
                        lambda par, n=1, **kw: get_latest_fx_rates(par, n=n, db_path=db))
    monkeypatch.setattr(dash, "get_expiring_hedges",
                        lambda dias=30, **kw: get_expiring_hedges(dias=dias, db_path=db))
    monkeypatch.setattr(dash, "resumen_pnl_cliente",
                        lambda pid, spot, **kw: resumen_pnl_cliente(pid, spot, db_path=db))

    return TestClient(dash.app, follow_redirects=False)


def _authed_cookies() -> dict:
    return {"session_token": _TOKEN}


# ---------------------------------------------------------------------------
# Tests de autenticación
# ---------------------------------------------------------------------------

class TestAuth:
    def test_raiz_sin_cookie_redirige_a_login(self, client: TestClient):
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_cliente_sin_cookie_redirige_a_login(self, client: TestClient):
        resp = client.get("/cliente/1")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_mercado_sin_cookie_redirige_a_login(self, client: TestClient):
        resp = client.get("/mercado")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_login_password_correcto_setea_cookie(self, client: TestClient):
        resp = client.post("/login", data={"password": _PWD})
        assert resp.status_code == 302
        assert "session_token" in resp.cookies

    def test_login_password_incorrecto_redirige_con_error(self, client: TestClient):
        resp = client.post("/login", data={"password": "malo"})
        assert resp.status_code == 302
        assert "login" in resp.headers["location"]
        assert "session_token" not in resp.cookies

    def test_logout_borra_cookie(self, client: TestClient):
        resp = client.get("/logout", cookies=_authed_cookies())
        assert resp.status_code == 302
        # La cookie debe quedar vacía o eliminada
        assert resp.cookies.get("session_token", "") == ""


# ---------------------------------------------------------------------------
# Tests de rutas con sesión válida
# ---------------------------------------------------------------------------

class TestRutasAutenticadas:
    def test_raiz_muestra_lista_de_clientes(self, client: TestClient):
        # / es el dashboard de KPIs; la lista de clientes está en /clientes
        resp_root = client.get("/", cookies=_authed_cookies())
        assert resp_root.status_code == 200

        resp_clientes = client.get("/clientes", cookies=_authed_cookies())
        assert resp_clientes.status_code == 200
        # Sin HEDGEPOINT_ENCRYPTION_KEY el campo empresa muestra el fallback "Cliente {id}"
        assert "Cliente 1" in resp_clientes.text or "Empresa Test" in resp_clientes.text
        assert "Exportador" in resp_clientes.text

    def test_raiz_muestra_spot_actual(self, client: TestClient):
        resp = client.get("/", cookies=_authed_cookies())
        assert resp.status_code == 200
        assert "Spot USD/MXN" in resp.text or "spot" in resp.text.lower()

    def test_detalle_cliente_muestra_empresa(self, client: TestClient, prospect_id: int):
        resp = client.get(f"/cliente/{prospect_id}", cookies=_authed_cookies())
        assert resp.status_code == 200
        # Sin HEDGEPOINT_ENCRYPTION_KEY muestra fallback "Cliente {id}"
        assert "Cliente 1" in resp.text or "Empresa Test" in resp.text

    def test_detalle_cliente_muestra_coberturas(self, client: TestClient, prospect_id: int):
        resp = client.get(f"/cliente/{prospect_id}", cookies=_authed_cookies())
        assert resp.status_code == 200
        assert "FORWARD" in resp.text
        assert "PUT" in resp.text

    def test_detalle_cliente_inexistente_retorna_404(self, client: TestClient):
        resp = client.get("/cliente/9999", cookies=_authed_cookies())
        assert resp.status_code == 404

    def test_mercado_muestra_spot(self, client: TestClient):
        resp = client.get("/mercado", cookies=_authed_cookies())
        assert resp.status_code == 200
        assert "20." in resp.text   # spot está en el rango 19.5–20.5

    def test_mercado_muestra_tabla_cotizaciones(self, client: TestClient):
        resp = client.get("/mercado", cookies=_authed_cookies())
        assert resp.status_code == 200
        assert "USDMXN" in resp.text or "test" in resp.text


# ---------------------------------------------------------------------------
# Test de POST generar reporte
# ---------------------------------------------------------------------------

class TestGenerarReporte:
    def test_post_reporte_redirige_a_detalle(
        self,
        client: TestClient,
        prospect_id: int,
        db: Path,
        monkeypatch,
        tmp_path: Path,
    ):
        """POST /cliente/{id}/reporte debe redirigir a /cliente/{id}."""
        import agents.reports.dashboard as dash

        # Mockear generar_datos_reporte y generar_pdf_reporte para no
        # depender de LLM ni de paths de salida reales.
        fake_path = str(tmp_path / "reporte_test.pdf")
        Path(fake_path).write_bytes(b"%PDF-1.4 fake")

        def _fake_generar_reporte(inner_func_path):
            """Parcha la llamada dentro de la ruta POST."""
            pass

        with patch(
            "agents.reports.report_generator.generar_datos_reporte",
            return_value={
                "cliente": {"id": prospect_id, "empresa_enc": "Empresa Test S.A."},
                "spot_actual": 20.00,
                "resumen_mercado": {"spot": 20.00, "variacion_semanal": 0.0, "volatilidad_30d": 0.0},
                "pnl": {"total_mtm_mxn": 0.0, "total_cubierto_usd": 0.0,
                        "num_coberturas": 0, "coberturas": [], "proximos_vencimientos": []},
                "coberturas": [],
                "proximos_vencimientos": [],
                "fecha_reporte": date.today(),
                "_db_path": db,
            },
        ), patch(
            "agents.reports.report_generator.generar_pdf_reporte",
            return_value=fake_path,
        ):
            resp = client.post(
                f"/cliente/{prospect_id}/reporte",
                cookies=_authed_cookies(),
            )

        assert resp.status_code == 302
        assert f"/cliente/{prospect_id}" in resp.headers["location"]

    def test_post_reporte_sin_sesion_redirige_a_login(
        self, client: TestClient, prospect_id: int
    ):
        resp = client.post(f"/cliente/{prospect_id}/reporte")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]
