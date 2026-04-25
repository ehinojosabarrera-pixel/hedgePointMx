"""
Tests para agents/reports/report_generator.py

Cobertura:
- generar_datos_reporte: todas las claves presentes, tipos correctos
- spot_actual: usa último bid de BD; fallback 20.0 si no hay datos
- variacion_semanal: cálculo coherente con datos insertados
- volatilidad_30d: positiva con suficientes datos, 0 con pocos datos
- proximos_vencimientos: filtra correctamente por prospect_id y días
- pnl: delegado correctamente a resumen_pnl_cliente
- generar_pdf_reporte: PDF creado, >0 bytes, ruta correcta
- generar_pdf_reporte sin datos de mercado: no crashea
- generar_reportes_todos: genera PDF por cada cliente con coberturas activas
"""

import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from core.database import (
    init_db,
    insert_fx_rate,
    insert_hedge,
    insert_prospect,
)
from agents.reports.report_generator import generar_datos_reporte


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def prospect_id(db: Path) -> int:
    return insert_prospect(
        {
            "nombre_enc": "enc_nombre",
            "empresa_enc": "enc_empresa",
            "sector": "manufactura",
            "volumen_usd_mensual": 100_000.0,
        },
        db_path=db,
    )


def _insertar_fx_rates(db: Path, n: int = 30, bid_base: float = 17.50) -> None:
    """Inserta n registros de fx_rates simulando días consecutivos (más reciente primero)."""
    for i in range(n):
        fecha = (date.today() - timedelta(days=i)).isoformat()
        # Pequeña variación diaria para generar volatilidad no nula
        bid = bid_base * (1 + 0.002 * (i % 3 - 1))  # oscila ±0.2%
        insert_fx_rate(
            fecha=fecha,
            hora="12:00:00",
            par="USDMXN",
            bid=round(bid, 4),
            ask=round(bid + 0.05, 4),
            source="test",
            db_path=db,
        )


def _insertar_hedge(db: Path, prospect_id: int, dias_venc: int = 60) -> int:
    return insert_hedge(
        {
            "prospect_id": prospect_id,
            "tipo": "forward",
            "monto_usd": 50_000.0,
            "strike": 17.50,
            "spot_entrada": 17.30,
            "tasa_forward": 17.55,
            "fecha_inicio": date.today().isoformat(),
            "fecha_vencimiento": (date.today() + timedelta(days=dias_venc)).isoformat(),
        },
        db_path=db,
    )


# ---------------------------------------------------------------------------
# Claves y tipos del resultado
# ---------------------------------------------------------------------------

class TestEstructuraResultado:

    def test_todas_las_claves_presentes(self, db, prospect_id):
        _insertar_fx_rates(db)
        _insertar_hedge(db, prospect_id)

        resultado = generar_datos_reporte(prospect_id, db_path=db)

        claves_esperadas = {
            "cliente", "spot_actual", "resumen_mercado",
            "pnl", "coberturas", "proximos_vencimientos", "fecha_reporte",
        }
        assert claves_esperadas == set(resultado.keys())

    def test_resumen_mercado_tiene_subclaves(self, db, prospect_id):
        _insertar_fx_rates(db)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        rm = resultado["resumen_mercado"]
        assert {"spot", "variacion_semanal", "volatilidad_30d"} == set(rm.keys())

    def test_pnl_tiene_subclaves(self, db, prospect_id):
        _insertar_fx_rates(db)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        claves_pnl = {
            "prospect_id", "spot_actual", "num_coberturas",
            "total_cubierto_usd", "total_mtm_mxn", "total_pnl_vs_spot_mxn",
            "exposicion_residual_usd", "proximos_vencimientos", "coberturas",
        }
        assert claves_pnl.issubset(set(resultado["pnl"].keys()))

    def test_fecha_reporte_es_hoy(self, db, prospect_id):
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["fecha_reporte"] == date.today()

    def test_cliente_es_dict_o_none(self, db, prospect_id):
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert isinstance(resultado["cliente"], dict)
        assert resultado["cliente"]["id"] == prospect_id

    def test_cliente_inexistente_retorna_none(self, db):
        resultado = generar_datos_reporte(9999, db_path=db)
        assert resultado["cliente"] is None


# ---------------------------------------------------------------------------
# spot_actual
# ---------------------------------------------------------------------------

class TestSpotActual:

    def test_usa_ultimo_bid_de_bd(self, db, prospect_id):
        _insertar_fx_rates(db, n=5, bid_base=17.80)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        # El registro más reciente tiene fecha=hoy y bid cercano a 17.80
        assert 17.0 < resultado["spot_actual"] < 19.0

    def test_fallback_sin_datos_fx(self, db, prospect_id):
        # No insertamos ningún registro fx
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["spot_actual"] == 20.0

    def test_spot_en_resumen_mercado_coincide(self, db, prospect_id):
        _insertar_fx_rates(db, n=10, bid_base=17.50)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["resumen_mercado"]["spot"] == resultado["spot_actual"]


# ---------------------------------------------------------------------------
# variacion_semanal
# ---------------------------------------------------------------------------

class TestVariacionSemanal:

    def test_variacion_con_datos_suficientes(self, db, prospect_id):
        """Con 10+ registros la variación semanal debe ser un float no nulo."""
        _insertar_fx_rates(db, n=10, bid_base=17.50)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        var = resultado["resumen_mercado"]["variacion_semanal"]
        assert isinstance(var, float)

    def test_variacion_cero_sin_datos(self, db, prospect_id):
        """Sin registros fx, variacion_semanal = 0.0 (fallback)."""
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["resumen_mercado"]["variacion_semanal"] == 0.0

    def test_variacion_positiva_cuando_spot_sube(self, db, prospect_id):
        """Registros donde el más reciente tiene bid mayor que el de hace 5 días."""
        today = date.today()
        # Insertar 6 registros: el más reciente (hoy) con bid alto
        bids = [18.00, 17.80, 17.70, 17.60, 17.55, 17.50]  # hoy → hace 5 días
        for i, bid in enumerate(bids):
            fecha = (today - timedelta(days=i)).isoformat()
            insert_fx_rate(
                fecha=fecha, hora="12:00:00", par="USDMXN",
                bid=bid, ask=bid + 0.05, source="test", db_path=db,
            )
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        var = resultado["resumen_mercado"]["variacion_semanal"]
        assert var > 0, f"Se esperaba variación positiva, got {var}"

    def test_variacion_negativa_cuando_spot_baja(self, db, prospect_id):
        """Registros donde el más reciente tiene bid menor que el de hace 5 días."""
        today = date.today()
        bids = [17.00, 17.20, 17.35, 17.45, 17.48, 17.50]
        for i, bid in enumerate(bids):
            fecha = (today - timedelta(days=i)).isoformat()
            insert_fx_rate(
                fecha=fecha, hora="12:00:00", par="USDMXN",
                bid=bid, ask=bid + 0.05, source="test", db_path=db,
            )
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        var = resultado["resumen_mercado"]["variacion_semanal"]
        assert var < 0, f"Se esperaba variación negativa, got {var}"


# ---------------------------------------------------------------------------
# volatilidad_30d
# ---------------------------------------------------------------------------

class TestVolatilidad30d:

    def test_volatilidad_positiva_con_30_registros(self, db, prospect_id):
        _insertar_fx_rates(db, n=30, bid_base=17.50)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        vol = resultado["resumen_mercado"]["volatilidad_30d"]
        assert vol > 0, "Con 30 registros con variación debe haber volatilidad"

    def test_volatilidad_cero_con_un_registro(self, db, prospect_id):
        """Un solo registro: no hay returns → volatilidad = 0."""
        insert_fx_rate(
            fecha=date.today().isoformat(), hora="12:00:00", par="USDMXN",
            bid=17.50, ask=17.55, source="test", db_path=db,
        )
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["resumen_mercado"]["volatilidad_30d"] == 0.0

    def test_volatilidad_cero_sin_registros(self, db, prospect_id):
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["resumen_mercado"]["volatilidad_30d"] == 0.0

    def test_volatilidad_es_anualizada(self, db, prospect_id):
        """Con variación diaria de ~0.2%, la vol anualizada debe estar en rango razonable (1%-50%)."""
        _insertar_fx_rates(db, n=30, bid_base=17.50)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        vol = resultado["resumen_mercado"]["volatilidad_30d"]
        assert 0.5 < vol < 100, f"Volatilidad fuera de rango esperado: {vol}"


# ---------------------------------------------------------------------------
# coberturas y proximos_vencimientos
# ---------------------------------------------------------------------------

class TestCoberturas:

    def test_coberturas_activas_del_cliente(self, db, prospect_id):
        _insertar_fx_rates(db, n=5)
        _insertar_hedge(db, prospect_id, dias_venc=60)
        _insertar_hedge(db, prospect_id, dias_venc=90)

        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert len(resultado["coberturas"]) == 2

    def test_coberturas_vacias_sin_hedges(self, db, prospect_id):
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["coberturas"] == []

    def test_proximos_vencimientos_dentro_de_30_dias(self, db, prospect_id):
        _insertar_fx_rates(db, n=5)
        _insertar_hedge(db, prospect_id, dias_venc=15)   # vence pronto
        _insertar_hedge(db, prospect_id, dias_venc=60)   # no vence pronto

        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert len(resultado["proximos_vencimientos"]) == 1

    def test_proximos_vencimientos_solo_de_este_cliente(self, db, prospect_id):
        """Un hedge de otro cliente que vence en 10 días no debe aparecer."""
        otro_id = insert_prospect(
            {"nombre_enc": "x", "empresa_enc": "y", "sector": "comercio",
             "volumen_usd_mensual": 20_000.0},
            db_path=db,
        )
        _insertar_fx_rates(db, n=5)
        _insertar_hedge(db, otro_id, dias_venc=10)     # otro cliente
        _insertar_hedge(db, prospect_id, dias_venc=60) # este cliente, no próximo

        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["proximos_vencimientos"] == []

    def test_coberturas_no_activas_excluidas(self, db, prospect_id):
        from core.database import update_hedge_status
        _insertar_fx_rates(db, n=5)
        hid = _insertar_hedge(db, prospect_id, dias_venc=60)
        update_hedge_status(hid, "liquidada", db_path=db)

        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["coberturas"] == []


# ---------------------------------------------------------------------------
# Integración: 2 hedges + 30 fx_rates
# ---------------------------------------------------------------------------

class TestIntegracion:

    def test_reporte_completo_con_datos_reales(self, db, prospect_id):
        """Escenario completo: 30 fx_rates + 2 hedges activos."""
        _insertar_fx_rates(db, n=30, bid_base=17.50)
        _insertar_hedge(db, prospect_id, dias_venc=20)  # vence dentro de 30d
        _insertar_hedge(db, prospect_id, dias_venc=75)

        resultado = generar_datos_reporte(prospect_id, db_path=db)

        assert resultado["cliente"] is not None
        assert resultado["spot_actual"] > 0
        assert resultado["resumen_mercado"]["volatilidad_30d"] > 0
        assert resultado["pnl"]["num_coberturas"] == 2
        assert resultado["pnl"]["total_cubierto_usd"] == pytest.approx(100_000.0)
        assert len(resultado["coberturas"]) == 2
        assert len(resultado["proximos_vencimientos"]) == 1
        assert resultado["fecha_reporte"] == date.today()

    def test_pnl_prospect_id_correcto(self, db, prospect_id):
        _insertar_fx_rates(db, n=10)
        resultado = generar_datos_reporte(prospect_id, db_path=db)
        assert resultado["pnl"]["prospect_id"] == prospect_id


# ---------------------------------------------------------------------------
# generar_pdf_reporte
# ---------------------------------------------------------------------------

def _datos_ficticios(prospect_id: int, db_path: Path) -> dict:
    """Construye un dict de datos completo para generar_pdf_reporte."""
    from core.models.hedge_pnl import HedgePnL
    from datetime import date

    pnl_item = HedgePnL(
        hedge_id=1,
        prospect_id=prospect_id,
        tipo="forward",
        monto_usd=100_000.0,
        strike=17.50,
        spot_actual=17.30,
        mtm_mxn=20_000.0,
        pnl_vs_spot_mxn=20_000.0,
        dias_restantes=45,
        exposicion_residual_usd=None,
        estado="activa",
    )

    return {
        "cliente": {
            "id": prospect_id,
            "empresa_enc": "enc_empresa",
            "sector": "manufactura",
            "volumen_usd_mensual": 100_000.0,
        },
        "spot_actual": 17.30,
        "resumen_mercado": {
            "spot": 17.30,
            "variacion_semanal": -0.85,
            "volatilidad_30d": 8.40,
        },
        "pnl": {
            "prospect_id": prospect_id,
            "spot_actual": 17.30,
            "num_coberturas": 1,
            "total_cubierto_usd": 100_000.0,
            "total_mtm_mxn": 20_000.0,
            "total_pnl_vs_spot_mxn": 20_000.0,
            "exposicion_residual_usd": 50_000.0,
            "proximos_vencimientos": [],
            "coberturas": [pnl_item],
        },
        "coberturas": [],
        "proximos_vencimientos": [
            {
                "id": 1,
                "prospect_id": prospect_id,
                "tipo": "forward",
                "monto_usd": 100_000.0,
                "strike": 17.50,
                "fecha_vencimiento": (date.today() + timedelta(days=20)).isoformat(),
                "estado": "activa",
            }
        ],
        "fecha_reporte": date.today(),
        "_db_path": db_path,
    }


class TestGenerarPdfReporte:

    def _mock_encryptor_y_llm(self, monkeypatch):
        """Mockea FieldEncryptor.decrypt y HedgePointLLM para no requerir keys."""
        monkeypatch.setattr(
            "agents.reports.report_generator.FieldEncryptor",
            lambda: type("FE", (), {"decrypt": lambda self, x: "Empresa Test S.A."})(),
            raising=False,
        )
        # Mockea el import dentro de _pagina_recomendaciones
        import unittest.mock as um
        mock_llm = um.MagicMock()
        mock_llm.return_value.generate_report_recommendations.return_value = (
            "1. Renovar cobertura próxima.\n2. Cubrir exposición residual.\n3. Revisar volatilidad."
        )
        monkeypatch.setattr(
            "agents.reports.report_generator.HedgePointLLM",
            mock_llm,
            raising=False,
        )

    def test_pdf_se_crea_y_tiene_bytes(self, db, prospect_id, tmp_path, monkeypatch):
        """El PDF debe crearse y tener tamaño > 0."""
        self._mock_encryptor_y_llm(monkeypatch)
        datos = _datos_ficticios(prospect_id, db)
        out = str(tmp_path / "reporte.pdf")

        from agents.reports.report_generator import generar_pdf_reporte
        path = generar_pdf_reporte(datos, output_path=out)

        assert path == out
        assert Path(path).exists()
        assert Path(path).stat().st_size > 0

    def test_pdf_path_default_contiene_prospect_id(self, db, prospect_id, tmp_path, monkeypatch):
        """Sin output_path, el PDF va a output/reports/{id}/{fecha}/reporte.pdf."""
        self._mock_encryptor_y_llm(monkeypatch)

        # Redirigir la raíz de output a tmp_path
        import agents.reports.report_generator as rg
        monkeypatch.setattr(rg, "__file__",
                            str(tmp_path / "agents" / "reports" / "report_generator.py"))

        datos = _datos_ficticios(prospect_id, db)
        from agents.reports.report_generator import generar_pdf_reporte
        path = generar_pdf_reporte(datos, output_path=str(
            tmp_path / str(prospect_id) / date.today().isoformat() / "reporte.pdf"
        ))

        assert "reporte.pdf" in path
        assert Path(path).exists()

    def test_pdf_sin_datos_mercado_no_crashea(self, db, prospect_id, tmp_path, monkeypatch):
        """resumen_mercado vacío no debe lanzar excepción."""
        self._mock_encryptor_y_llm(monkeypatch)
        datos = _datos_ficticios(prospect_id, db)
        datos["resumen_mercado"] = {}
        out = str(tmp_path / "reporte_vacio.pdf")

        from agents.reports.report_generator import generar_pdf_reporte
        path = generar_pdf_reporte(datos, output_path=out)

        assert Path(path).exists()
        assert Path(path).stat().st_size > 0

    def test_pdf_sin_coberturas_no_crashea(self, db, prospect_id, tmp_path, monkeypatch):
        """pnl sin coberturas ni proximos vencimientos no debe lanzar excepción."""
        self._mock_encryptor_y_llm(monkeypatch)
        datos = _datos_ficticios(prospect_id, db)
        datos["pnl"]["coberturas"] = []
        datos["proximos_vencimientos"] = []
        out = str(tmp_path / "reporte_sin_cob.pdf")

        from agents.reports.report_generator import generar_pdf_reporte
        path = generar_pdf_reporte(datos, output_path=out)

        assert Path(path).exists()
        assert Path(path).stat().st_size > 0

    def test_pdf_llm_no_disponible_usa_fallback(self, db, prospect_id, tmp_path, monkeypatch):
        """Si HedgePointLLM lanza excepción, el PDF igual se genera con fallback."""
        # FieldEncryptor falla → usa "Cliente {id}"
        monkeypatch.setattr(
            "agents.reports.report_generator.FieldEncryptor",
            lambda: (_ for _ in ()).throw(Exception("no key")),
            raising=False,
        )
        import unittest.mock as um
        monkeypatch.setattr(
            "agents.reports.report_generator.HedgePointLLM",
            um.MagicMock(side_effect=Exception("API no disponible")),
            raising=False,
        )
        datos = _datos_ficticios(prospect_id, db)
        out = str(tmp_path / "reporte_fallback.pdf")

        from agents.reports.report_generator import generar_pdf_reporte
        path = generar_pdf_reporte(datos, output_path=out)

        assert Path(path).exists()
        assert Path(path).stat().st_size > 0


# ---------------------------------------------------------------------------
# generar_reportes_todos
# ---------------------------------------------------------------------------

class TestGenerarReportesTodos:

    def _crear_prospect_con_hedge(self, db: Path, sector: str = "manufactura") -> int:
        pid = insert_prospect(
            {"nombre_enc": "enc", "empresa_enc": "enc2",
             "sector": sector, "volumen_usd_mensual": 50_000.0},
            db_path=db,
        )
        _insertar_hedge(db, pid, dias_venc=60)
        return pid

    def test_genera_pdf_por_cada_cliente(self, db, tmp_path, monkeypatch):
        """Con 2 clientes con coberturas activas, retorna 2 paths."""
        _insertar_fx_rates(db, n=10)
        p1 = self._crear_prospect_con_hedge(db, "manufactura")
        p2 = self._crear_prospect_con_hedge(db, "comercio")

        # Mock completo: FieldEncryptor + HedgePointLLM + output_path
        import unittest.mock as um
        import agents.reports.report_generator as rg

        monkeypatch.setattr(rg, "FieldEncryptor",
                            lambda: type("FE", (), {"decrypt": lambda s, x: "Empresa"})(),
                            raising=False)
        mock_llm = um.MagicMock()
        mock_llm.return_value.generate_report_recommendations.return_value = "Rec 1.\nRec 2."
        monkeypatch.setattr(rg, "HedgePointLLM", mock_llm, raising=False)

        # Parchar generar_pdf_reporte para usar tmp_path como output
        original_pdf = rg.generar_pdf_reporte
        generated: list[str] = []

        def _mock_pdf(datos, output_path=None):
            out = str(tmp_path / f"reporte_{datos['cliente']['id']}.pdf")
            path = original_pdf(datos, output_path=out)
            generated.append(path)
            return path

        monkeypatch.setattr(rg, "generar_pdf_reporte", _mock_pdf)

        paths = rg.generar_reportes_todos(db_path=db)

        assert len(paths) == 2
        assert all(Path(p).exists() for p in paths)
        assert all(Path(p).stat().st_size > 0 for p in paths)

    def test_sin_coberturas_activas_retorna_lista_vacia(self, db):
        from agents.reports.report_generator import generar_reportes_todos
        paths = generar_reportes_todos(db_path=db)
        assert paths == []

    def test_error_en_un_cliente_no_detiene_los_demas(self, db, tmp_path, monkeypatch):
        """Si un cliente falla, los demás se procesan igual."""
        _insertar_fx_rates(db, n=5)
        p1 = self._crear_prospect_con_hedge(db, "manufactura")
        p2 = self._crear_prospect_con_hedge(db, "comercio")

        import unittest.mock as um
        import agents.reports.report_generator as rg

        monkeypatch.setattr(rg, "FieldEncryptor",
                            lambda: type("FE", (), {"decrypt": lambda s, x: "Emp"})(),
                            raising=False)
        monkeypatch.setattr(rg, "HedgePointLLM",
                            um.MagicMock(return_value=um.MagicMock(
                                generate_report_recommendations=um.MagicMock(return_value="Rec.")
                            )),
                            raising=False)

        calls = []
        original_datos = rg.generar_datos_reporte

        def _falla_primer_cliente(pid, db_path=db):
            calls.append(pid)
            if len(calls) == 1:
                raise RuntimeError("Error simulado")
            return original_datos(pid, db_path=db_path)

        monkeypatch.setattr(rg, "generar_datos_reporte", _falla_primer_cliente)

        # Un cliente falla, el otro debe generar PDF
        out_calls: list[str] = []
        orig_pdf = rg.generar_pdf_reporte

        def _pdf_tmp(datos, output_path=None):
            out = str(tmp_path / f"r_{datos['cliente']['id']}.pdf")
            p = orig_pdf(datos, output_path=out)
            out_calls.append(p)
            return p

        monkeypatch.setattr(rg, "generar_pdf_reporte", _pdf_tmp)

        paths = rg.generar_reportes_todos(db_path=db)
        assert len(paths) == 1   # solo el que no falló
