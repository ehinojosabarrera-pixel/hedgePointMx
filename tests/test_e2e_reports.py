"""
Tests de integración end-to-end — flujo completo de reportes HedgePoint MX.

Simula el ciclo completo: BD temporal → inserción de datos → generar_datos_reporte
→ generar_pdf_reporte → enviar_reporte (dry_run).

No dependen de API keys ni de HEDGEPOINT_ENCRYPTION_KEY.
HedgePointLLM se mockea para que el PDF use el texto de fallback.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.database import (
    init_db,
    insert_fx_rate,
    insert_hedge,
    insert_prospect,
)


# ---------------------------------------------------------------------------
# Helpers de setup
# ---------------------------------------------------------------------------

_SEED = 7


def _insertar_fx_rates(db_path: Path, n: int = 30) -> float:
    """Inserta n registros USDMXN con variación realista 19.5–20.5.

    Retorna el bid del registro más reciente (spot_actual).
    """
    rng = random.Random(_SEED)
    hoy = date.today()
    bid = 20.00
    primer_bid = None
    for i in range(n - 1, -1, -1):
        fecha = (hoy - timedelta(days=i)).isoformat()
        bid += rng.uniform(-0.06, 0.06)
        bid = max(19.50, min(20.50, bid))
        if primer_bid is None:
            primer_bid = bid
        insert_fx_rate(
            fecha=fecha,
            hora="12:00:00",
            par="USDMXN",
            bid=round(bid, 4),
            ask=round(bid + 0.05, 4),
            source="test",
            db_path=db_path,
        )
    return round(bid, 4)   # último bid insertado = spot más reciente


def _insertar_prospect(db_path: Path, sufijo: str = "") -> int:
    """Inserta un prospect completo con _enc como strings planos."""
    return insert_prospect(
        {
            "nombre_enc":          f"Carlos Test{sufijo}",
            "empresa_enc":         f"Empresa Test{sufijo} S.A.",
            "sector":              "Importador",
            "volumen_usd_mensual": 500_000,
            "margen_utilidad":     0.12,
            "status":              "diagnosticado",
        },
        db_path=db_path,
    )


def _insertar_tres_coberturas(
    prospect_id: int,
    db_path: Path,
    spot_actual: float,
) -> tuple[int, int, int]:
    """Inserta forward ITM, put OTM y collar para el prospect dado."""
    hoy = date.today().isoformat()

    # Forward ITM: strike < spot → ganancia para un importador (MXN barato al fijar)
    id_fwd = insert_hedge(
        {
            "prospect_id":     prospect_id,
            "tipo":            "forward",
            "monto_usd":       200_000.0,
            "strike":          spot_actual - 0.40,     # ITM
            "spot_entrada":    spot_actual - 0.50,
            "tasa_forward":    spot_actual - 0.40,
            "prima_pagada_mxn": 0.0,
            "fecha_inicio":    hoy,
            "fecha_vencimiento": (date.today() + timedelta(days=60)).isoformat(),
        },
        db_path=db_path,
    )

    # Put OTM: strike < spot → derecho a vender por debajo del mercado (OTM)
    id_put = insert_hedge(
        {
            "prospect_id":     prospect_id,
            "tipo":            "put",
            "monto_usd":       150_000.0,
            "strike":          spot_actual - 0.50,     # OTM
            "spot_entrada":    spot_actual - 0.30,
            "prima_pagada_mxn": 9_000.0,
            "fecha_inicio":    hoy,
            "fecha_vencimiento": (date.today() + timedelta(days=30)).isoformat(),
        },
        db_path=db_path,
    )

    # Collar: strike_put < spot < strike_call
    id_collar = insert_hedge(
        {
            "prospect_id":     prospect_id,
            "tipo":            "collar",
            "monto_usd":       100_000.0,
            "strike":          spot_actual - 0.30,     # put leg OTM
            "strike_call":     spot_actual + 0.30,     # call leg OTM
            "spot_entrada":    spot_actual,
            "prima_pagada_mxn": 5_000.0,
            "fecha_inicio":    hoy,
            "fecha_vencimiento": (date.today() + timedelta(days=45)).isoformat(),
        },
        db_path=db_path,
    )

    return id_fwd, id_put, id_collar


# ---------------------------------------------------------------------------
# Mock de HedgePointLLM (evita API key en todos los tests)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    """Sustituye HedgePointLLM por un doble que retorna texto de fallback."""
    fake_llm = MagicMock()
    fake_llm.return_value.generate_report_recommendations.return_value = (
        "Recomendaciones no disponibles temporalmente. "
        "Las metricas cuantitativas de su posicion estan actualizadas en este reporte."
    )
    monkeypatch.setattr(
        "agents.reports.report_generator.HedgePointLLM",
        fake_llm,
        raising=False,
    )


# ---------------------------------------------------------------------------
# test_flujo_completo_un_cliente
# ---------------------------------------------------------------------------

class TestFlujoCompletoUnCliente:
    def test_flujo_completo_un_cliente(self, tmp_path: Path):
        db = tmp_path / "test.db"
        init_db(db)

        spot = _insertar_fx_rates(db)
        pid = _insertar_prospect(db)
        _insertar_tres_coberturas(pid, db, spot)

        from agents.reports.report_generator import (
            generar_datos_reporte,
            generar_pdf_reporte,
        )

        datos = generar_datos_reporte(pid, db_path=db)

        # Verificar claves presentes
        for key in ("cliente", "spot_actual", "resumen_mercado", "pnl",
                    "coberturas", "proximos_vencimientos", "fecha_reporte"):
            assert key in datos, f"Falta la clave '{key}' en datos"

        # spot > 0
        assert datos["spot_actual"] > 0

        # total_cubierto_usd == suma de montos de las 3 coberturas
        total_esperado = 200_000.0 + 150_000.0 + 100_000.0
        assert datos["pnl"]["total_cubierto_usd"] == pytest.approx(total_esperado)

        # Generar PDF
        out_path = str(tmp_path / "reporte.pdf")
        datos["_db_path"] = db
        path_pdf = generar_pdf_reporte(datos, output_path=out_path)

        # Archivo existe y tiene > 5000 bytes
        pdf_file = Path(path_pdf)
        assert pdf_file.exists()
        assert pdf_file.stat().st_size > 5_000

        # Contiene la marca "HedgePoint" en el texto extraído
        import pdfplumber
        with pdfplumber.open(path_pdf) as pdf:
            texto_completo = " ".join(
                page.extract_text() or "" for page in pdf.pages
            )
        assert "HedgePoint" in texto_completo


# ---------------------------------------------------------------------------
# test_flujo_completo_multiples_clientes
# ---------------------------------------------------------------------------

class TestFlujoCompletoMultiplesClientes:
    def test_flujo_completo_multiples_clientes(self, tmp_path: Path):
        db = tmp_path / "test_multi.db"
        init_db(db)

        spot = _insertar_fx_rates(db)

        pid1 = _insertar_prospect(db, sufijo="_1")
        _insertar_tres_coberturas(pid1, db, spot)

        pid2 = _insertar_prospect(db, sufijo="_2")
        hoy = date.today().isoformat()
        insert_hedge(
            {
                "prospect_id":      pid2,
                "tipo":             "forward",
                "monto_usd":        80_000.0,
                "strike":           spot - 0.20,
                "spot_entrada":     spot - 0.10,
                "tasa_forward":     spot - 0.20,
                "prima_pagada_mxn": 0.0,
                "fecha_inicio":     hoy,
                "fecha_vencimiento": (date.today() + timedelta(days=90)).isoformat(),
            },
            db_path=db,
        )

        from agents.reports.report_generator import generar_reportes_todos

        paths = generar_reportes_todos(db_path=db)

        assert len(paths) == 2
        for p in paths:
            f = Path(p)
            assert f.exists()
            assert f.stat().st_size > 0


# ---------------------------------------------------------------------------
# test_envio_dry_run_flujo_completo
# ---------------------------------------------------------------------------

class TestEnvioDryRunFlujoCompleto:
    def test_envio_dry_run_flujo_completo(self, tmp_path: Path):
        db = tmp_path / "test_dry.db"
        init_db(db)

        spot = _insertar_fx_rates(db)
        pid = _insertar_prospect(db)
        _insertar_tres_coberturas(pid, db, spot)

        from agents.reports.report_generator import (
            generar_datos_reporte,
            generar_pdf_reporte,
        )
        from agents.reports.report_sender import enviar_reporte
        from core.database import get_prospect

        datos = generar_datos_reporte(pid, db_path=db)
        datos["_db_path"] = db
        out_path = str(tmp_path / "reporte_dry.pdf")
        path_pdf = generar_pdf_reporte(datos, output_path=out_path)

        prospect = get_prospect(pid, db_path=db) or {}

        with patch("agents.reports.report_sender.requests.post") as mock_post, \
             patch("agents.reports.report_sender.send_whatsapp_alert") as mock_wa:

            resultado = enviar_reporte(
                datos_reporte=datos,
                pdf_path=path_pdf,
                prospect=prospect,
                canales=["email", "whatsapp"],
                dry_run=True,
            )

        assert resultado == {"email": "dry_run", "whatsapp": "dry_run"}
        mock_post.assert_not_called()
        mock_wa.assert_not_called()


# ---------------------------------------------------------------------------
# test_cliente_sin_coberturas
# ---------------------------------------------------------------------------

class TestClienteSinCoberturas:
    def test_cliente_sin_coberturas(self, tmp_path: Path):
        db = tmp_path / "test_sin_cob.db"
        init_db(db)

        _insertar_fx_rates(db)
        pid = _insertar_prospect(db)

        from agents.reports.report_generator import (
            generar_datos_reporte,
            generar_pdf_reporte,
        )

        datos = generar_datos_reporte(pid, db_path=db)

        assert datos["pnl"]["total_cubierto_usd"] == 0
        assert datos["coberturas"] == []

        # El PDF debe generarse sin crashear
        out_path = str(tmp_path / "reporte_sin_cob.pdf")
        datos["_db_path"] = db
        path_pdf = generar_pdf_reporte(datos, output_path=out_path)

        assert Path(path_pdf).exists()
        assert Path(path_pdf).stat().st_size > 0


# ---------------------------------------------------------------------------
# test_coberturas_vencidas_no_aparecen
# ---------------------------------------------------------------------------

class TestCoberturasVencidasNoAparecen:
    def test_coberturas_vencidas_no_aparecen(self, tmp_path: Path):
        db = tmp_path / "test_vencidas.db"
        init_db(db)

        spot = _insertar_fx_rates(db)
        pid = _insertar_prospect(db)
        hoy = date.today().isoformat()

        # Cobertura activa
        insert_hedge(
            {
                "prospect_id":      pid,
                "tipo":             "forward",
                "monto_usd":        120_000.0,
                "strike":           spot - 0.20,
                "spot_entrada":     spot - 0.30,
                "tasa_forward":     spot - 0.20,
                "prima_pagada_mxn": 0.0,
                "fecha_inicio":     hoy,
                "fecha_vencimiento": (date.today() + timedelta(days=45)).isoformat(),
            },
            db_path=db,
        )

        # Cobertura vencida
        id_vencida = insert_hedge(
            {
                "prospect_id":      pid,
                "tipo":             "put",
                "monto_usd":        80_000.0,
                "strike":           spot - 0.10,
                "spot_entrada":     spot,
                "prima_pagada_mxn": 6_000.0,
                "fecha_inicio":     (date.today() - timedelta(days=60)).isoformat(),
                "fecha_vencimiento": (date.today() - timedelta(days=1)).isoformat(),
            },
            db_path=db,
        )

        # Marcar la segunda como vencida
        from core.database import update_hedge_status
        update_hedge_status(id_vencida, "vencida", db_path=db)

        from agents.reports.report_generator import generar_datos_reporte

        datos = generar_datos_reporte(pid, db_path=db)

        # Solo la activa debe aparecer
        assert len(datos["coberturas"]) == 1
        assert datos["coberturas"][0]["tipo"] == "forward"

        # El cálculo de P&L solo considera la activa
        assert datos["pnl"]["num_coberturas"] == 1
        assert datos["pnl"]["total_cubierto_usd"] == pytest.approx(120_000.0)


# ---------------------------------------------------------------------------
# test_consistencia_numeros
# ---------------------------------------------------------------------------

class TestConsistenciaNumeros:
    def test_consistencia_numeros(self, tmp_path: Path):
        """MTM de un forward simple debe coincidir con el cálculo manual."""
        db = tmp_path / "test_cons.db"
        init_db(db)

        spot = _insertar_fx_rates(db)
        pid = _insertar_prospect(db)

        strike = 20.00
        monto_usd = 100_000.0
        tasa_forward = 20.00

        insert_hedge(
            {
                "prospect_id":      pid,
                "tipo":             "forward",
                "monto_usd":        monto_usd,
                "strike":           strike,
                "spot_entrada":     19.50,
                "tasa_forward":     tasa_forward,
                "prima_pagada_mxn": 0.0,
                "fecha_inicio":     date.today().isoformat(),
                "fecha_vencimiento": (date.today() + timedelta(days=30)).isoformat(),
            },
            db_path=db,
        )

        from agents.reports.report_generator import generar_datos_reporte

        datos = generar_datos_reporte(pid, db_path=db)
        spot_real = datos["spot_actual"]

        # MTM esperado: (tasa_forward - spot_actual) * monto_usd
        mtm_esperado = (tasa_forward - spot_real) * monto_usd

        coberturas_pnl = datos["pnl"]["coberturas"]
        assert len(coberturas_pnl) == 1

        mtm_calculado = coberturas_pnl[0].mtm_mxn
        assert mtm_calculado == pytest.approx(mtm_esperado, abs=0.01)
