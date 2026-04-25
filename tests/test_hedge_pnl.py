"""
Tests para core/models/hedge_pnl.py

Cobertura:
- calcular_pnl_hedge: forward ITM/OTM, put ITM/OTM, collar, días restantes
- calcular_pnl_cliente: múltiples coberturas de un cliente
- resumen_pnl_cliente: agregados, exposición residual, proximos_vencimientos
- calcular_pnl_todos_clientes: múltiples clientes, ordenamiento por MTM
"""

import pytest
from datetime import date, timedelta
from pathlib import Path

from core.database import init_db, insert_prospect, insert_hedge
from core.models.hedge_pnl import (
    HedgePnL,
    calcular_pnl_hedge,
    calcular_pnl_cliente,
    resumen_pnl_cliente,
    calcular_pnl_todos_clientes,
)


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


def _venc(dias: int) -> str:
    return (date.today() + timedelta(days=dias)).isoformat()


def _hedge_row(db: Path, prospect_id: int, **kwargs) -> dict:
    """Inserta un hedge y devuelve el dict tal como lo devuelve get_hedge."""
    from core.database import get_hedge
    defaults = dict(
        prospect_id=prospect_id,
        tipo="forward",
        monto_usd=100_000.0,
        strike=17.50,
        spot_entrada=17.30,
        prima_pagada_mxn=0.0,
        tasa_forward=17.50,
        fecha_inicio=date.today().isoformat(),
        fecha_vencimiento=_venc(90),
    )
    defaults.update(kwargs)
    hid = insert_hedge(defaults, db_path=db)
    return get_hedge(hid, db_path=db)


# ---------------------------------------------------------------------------
# calcular_pnl_hedge — forward
# ---------------------------------------------------------------------------

class TestForward:
    """Forward: mtm = (tasa_forward - spot_actual) * monto_usd"""

    def test_forward_itm(self, db, prospect_id):
        """Spot cae por debajo del forward → cobertura gana."""
        hedge = _hedge_row(db, prospect_id, tasa_forward=17.50)
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.00)

        assert isinstance(pnl, HedgePnL)
        # mtm = (17.50 - 17.00) * 100_000 = 50_000
        assert pnl.mtm_mxn == pytest.approx(50_000.0)
        # pnl_vs_spot = (strike 17.50 - spot 17.00) * 100_000
        assert pnl.pnl_vs_spot_mxn == pytest.approx(50_000.0)

    def test_forward_otm(self, db, prospect_id):
        """Spot sube por encima del forward → cobertura pierde (costo de oportunidad)."""
        hedge = _hedge_row(db, prospect_id, tasa_forward=17.50)
        pnl = calcular_pnl_hedge(hedge, spot_actual=18.00)

        # mtm = (17.50 - 18.00) * 100_000 = -50_000
        assert pnl.mtm_mxn == pytest.approx(-50_000.0)
        assert pnl.pnl_vs_spot_mxn == pytest.approx(-50_000.0)

    def test_forward_at_the_money(self, db, prospect_id):
        hedge = _hedge_row(db, prospect_id, tasa_forward=17.50)
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        assert pnl.mtm_mxn == pytest.approx(0.0)

    def test_forward_sin_tasa_forward_usa_strike(self, db, prospect_id):
        """Si tasa_forward es NULL, se cae a usar strike como precio pactado."""
        hedge = _hedge_row(db, prospect_id, strike=17.80, tasa_forward=None)
        # Al insertar tasa_forward=None queda NULL en BD; forzar manualmente
        hedge = dict(hedge)
        hedge["tasa_forward"] = None
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.00)
        # tasa_fwd = strike = 17.80 → mtm = (17.80 - 17.00) * 100_000 = 80_000
        assert pnl.mtm_mxn == pytest.approx(80_000.0)


# ---------------------------------------------------------------------------
# calcular_pnl_hedge — put
# ---------------------------------------------------------------------------

class TestPut:
    """Put: mtm = max(strike - spot, 0) * monto - prima"""

    def test_put_itm(self, db, prospect_id):
        """Spot < strike → put in-the-money."""
        hedge = _hedge_row(
            db, prospect_id, tipo="put", strike=17.50, prima_pagada_mxn=50_000.0
        )
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.00)

        # valor intrínseco = (17.50 - 17.00) * 100_000 = 50_000
        # mtm = 50_000 - 50_000 prima = 0
        assert pnl.mtm_mxn == pytest.approx(0.0)
        assert pnl.pnl_vs_spot_mxn == pytest.approx(50_000.0)

    def test_put_otm(self, db, prospect_id):
        """Spot > strike → put vale 0, sólo se pierde la prima."""
        hedge = _hedge_row(
            db, prospect_id, tipo="put", strike=17.00, prima_pagada_mxn=30_000.0
        )
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)

        # valor intrínseco = 0
        assert pnl.mtm_mxn == pytest.approx(-30_000.0)
        assert pnl.pnl_vs_spot_mxn == pytest.approx(-50_000.0)  # (17.00 - 17.50) * 100k

    def test_put_sin_prima(self, db, prospect_id):
        hedge = _hedge_row(db, prospect_id, tipo="put", strike=18.00, prima_pagada_mxn=0.0)
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        assert pnl.mtm_mxn == pytest.approx(50_000.0)


# ---------------------------------------------------------------------------
# calcular_pnl_hedge — call
# ---------------------------------------------------------------------------

class TestCall:
    def test_call_itm(self, db, prospect_id):
        """Spot > strike → call in-the-money."""
        hedge = _hedge_row(
            db, prospect_id, tipo="call", strike=17.00, prima_pagada_mxn=20_000.0
        )
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        # valor = (17.50 - 17.00) * 100_000 - 20_000 = 30_000
        assert pnl.mtm_mxn == pytest.approx(30_000.0)

    def test_call_otm(self, db, prospect_id):
        """Spot < strike → call vale 0."""
        hedge = _hedge_row(
            db, prospect_id, tipo="call", strike=18.00, prima_pagada_mxn=20_000.0
        )
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        assert pnl.mtm_mxn == pytest.approx(-20_000.0)


# ---------------------------------------------------------------------------
# calcular_pnl_hedge — collar
# ---------------------------------------------------------------------------

class TestCollar:
    def _collar_row(self, db, prospect_id, spot_actual, **kwargs):
        defaults = dict(
            tipo="collar",
            strike=17.00,       # put floor
            strike_call=18.00,  # call cap
            prima_pagada_mxn=10_000.0,
            monto_usd=100_000.0,
        )
        defaults.update(kwargs)
        hedge = _hedge_row(db, prospect_id, **defaults)
        return calcular_pnl_hedge(hedge, spot_actual=spot_actual)

    def test_collar_spot_dentro_de_rango(self, db, prospect_id):
        """Spot entre floor y cap → ambas opciones valen 0."""
        pnl = self._collar_row(db, prospect_id, spot_actual=17.50)
        # put = 0, call_vendido = 0 → mtm = 0 - 0 - 10_000 = -10_000
        assert pnl.mtm_mxn == pytest.approx(-10_000.0)

    def test_collar_spot_bajo_put_itm(self, db, prospect_id):
        """Spot < floor → put gana, call vendido vale 0."""
        pnl = self._collar_row(db, prospect_id, spot_actual=16.50)
        # put = (17.00 - 16.50) * 100_000 = 50_000
        # call = 0
        # mtm = 50_000 - 0 - 10_000 = 40_000
        assert pnl.mtm_mxn == pytest.approx(40_000.0)

    def test_collar_spot_alto_call_vendido_cuesta(self, db, prospect_id):
        """Spot > cap → put vale 0, call vendido cuesta."""
        pnl = self._collar_row(db, prospect_id, spot_actual=18.50)
        # put = 0
        # call_vendido = (18.50 - 18.00) * 100_000 = 50_000
        # mtm = 0 - 50_000 - 10_000 = -60_000
        assert pnl.mtm_mxn == pytest.approx(-60_000.0)

    def test_collar_tipo_incorrecto_sin_strike_call(self, db, prospect_id):
        """Collar sin strike_call en el dict debe lanzar ValueError."""
        hedge = _hedge_row(db, prospect_id, tipo="collar", strike_call=18.00)
        hedge = dict(hedge)
        hedge["strike_call"] = None
        with pytest.raises(ValueError, match="strike_call"):
            calcular_pnl_hedge(hedge, spot_actual=17.50)


# ---------------------------------------------------------------------------
# días restantes
# ---------------------------------------------------------------------------

class TestDiasRestantes:
    def test_dias_restantes_exacto(self, db, prospect_id):
        hedge = _hedge_row(db, prospect_id, fecha_vencimiento=_venc(45))
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        assert pnl.dias_restantes == 45

    def test_dias_restantes_ya_vencido(self, db, prospect_id):
        """Cobertura vencida → dias_restantes = 0 (no negativo)."""
        ayer = (date.today() - timedelta(days=1)).isoformat()
        hedge = _hedge_row(db, prospect_id, fecha_vencimiento=ayer)
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        assert pnl.dias_restantes == 0

    def test_dias_restantes_hoy(self, db, prospect_id):
        hedge = _hedge_row(db, prospect_id, fecha_vencimiento=date.today().isoformat())
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        assert pnl.dias_restantes == 0


# ---------------------------------------------------------------------------
# exposición residual
# ---------------------------------------------------------------------------

class TestExposicionResidual:
    def test_con_exposicion_total(self, db, prospect_id):
        hedge = _hedge_row(db, prospect_id, monto_usd=60_000.0)
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50, exposicion_total_usd=100_000.0)
        assert pnl.exposicion_residual_usd == pytest.approx(40_000.0)

    def test_sin_exposicion_total(self, db, prospect_id):
        hedge = _hedge_row(db, prospect_id)
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50)
        assert pnl.exposicion_residual_usd is None

    def test_exposicion_residual_no_negativa(self, db, prospect_id):
        """Monto cubierto mayor a la exposición total → residual = 0."""
        hedge = _hedge_row(db, prospect_id, monto_usd=150_000.0)
        pnl = calcular_pnl_hedge(hedge, spot_actual=17.50, exposicion_total_usd=100_000.0)
        assert pnl.exposicion_residual_usd == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# calcular_pnl_cliente
# ---------------------------------------------------------------------------

class TestCalcularPnlCliente:
    def test_multiples_coberturas(self, db, prospect_id):
        _hedge_row(db, prospect_id, tipo="forward", monto_usd=50_000.0, tasa_forward=17.50)
        _hedge_row(db, prospect_id, tipo="put", monto_usd=50_000.0, strike=17.00, prima_pagada_mxn=5_000.0)

        resultados = calcular_pnl_cliente(prospect_id, spot_actual=17.20, db_path=db)
        assert len(resultados) == 2
        assert all(isinstance(r, HedgePnL) for r in resultados)

    def test_cliente_sin_coberturas(self, db, prospect_id):
        resultados = calcular_pnl_cliente(prospect_id, spot_actual=17.50, db_path=db)
        assert resultados == []

    def test_solo_activas(self, db, prospect_id):
        from core.database import update_hedge_status
        hid = insert_hedge(
            dict(
                prospect_id=prospect_id, tipo="forward", monto_usd=100_000.0,
                strike=17.50, spot_entrada=17.30, tasa_forward=17.50,
                fecha_inicio=date.today().isoformat(), fecha_vencimiento=_venc(60),
            ),
            db_path=db,
        )
        update_hedge_status(hid, "liquidada", db_path=db)
        _hedge_row(db, prospect_id)  # esta sí es activa

        resultados = calcular_pnl_cliente(prospect_id, spot_actual=17.50, db_path=db)
        assert len(resultados) == 1


# ---------------------------------------------------------------------------
# resumen_pnl_cliente
# ---------------------------------------------------------------------------

class TestResumenPnlCliente:
    def test_agregados_basicos(self, db, prospect_id):
        # forward ITM: mtm = (17.50 - 17.00) * 50k = 25_000
        _hedge_row(db, prospect_id, tipo="forward", monto_usd=50_000.0, tasa_forward=17.50)
        # put OTM: mtm = 0 - 5_000 prima = -5_000
        _hedge_row(db, prospect_id, tipo="put", monto_usd=50_000.0, strike=16.50, prima_pagada_mxn=5_000.0)

        res = resumen_pnl_cliente(prospect_id, spot_actual=17.00, db_path=db)

        assert res["num_coberturas"] == 2
        assert res["total_cubierto_usd"] == pytest.approx(100_000.0)
        assert res["total_mtm_mxn"] == pytest.approx(20_000.0)   # 25k - 5k
        assert len(res["coberturas"]) == 2

    def test_exposicion_residual_en_resumen(self, db, prospect_id):
        _hedge_row(db, prospect_id, monto_usd=60_000.0)
        res = resumen_pnl_cliente(
            prospect_id, spot_actual=17.50,
            exposicion_total_usd=200_000.0, db_path=db,
        )
        assert res["exposicion_residual_usd"] == pytest.approx(140_000.0)

    def test_proximos_vencimientos_30_dias(self, db, prospect_id):
        _hedge_row(db, prospect_id, fecha_vencimiento=_venc(15))   # próximo
        _hedge_row(db, prospect_id, fecha_vencimiento=_venc(60))   # no próximo

        res = resumen_pnl_cliente(prospect_id, spot_actual=17.50, db_path=db)
        assert len(res["proximos_vencimientos"]) == 1
        assert res["proximos_vencimientos"][0].dias_restantes == 15

    def test_sin_exposicion_total_residual_none(self, db, prospect_id):
        _hedge_row(db, prospect_id)
        res = resumen_pnl_cliente(prospect_id, spot_actual=17.50, db_path=db)
        assert res["exposicion_residual_usd"] is None

    def test_cliente_sin_coberturas(self, db, prospect_id):
        res = resumen_pnl_cliente(prospect_id, spot_actual=17.50, db_path=db)
        assert res["num_coberturas"] == 0
        assert res["total_cubierto_usd"] == pytest.approx(0.0)
        assert res["total_mtm_mxn"] == pytest.approx(0.0)
        assert res["proximos_vencimientos"] == []


# ---------------------------------------------------------------------------
# calcular_pnl_todos_clientes
# ---------------------------------------------------------------------------

class TestCalcularPnlTodosClientes:
    def _crear_cliente(self, db: Path, sector: str = "comercio") -> int:
        return insert_prospect(
            {"nombre_enc": "e", "empresa_enc": "e2", "sector": sector, "volumen_usd_mensual": 50_000.0},
            db_path=db,
        )

    def test_dos_clientes(self, db):
        p1 = self._crear_cliente(db, "manufactura")
        p2 = self._crear_cliente(db, "comercio")
        # p1: forward ITM → mtm positivo
        _hedge_row(db, p1, tasa_forward=18.00, monto_usd=100_000.0)
        # p2: forward OTM → mtm negativo
        _hedge_row(db, p2, tasa_forward=17.00, monto_usd=100_000.0)

        resumenes = calcular_pnl_todos_clientes(spot_actual=17.50, db_path=db)
        assert len(resumenes) == 2
        # Ordenado por MTM descendente: p1 primero
        assert resumenes[0]["prospect_id"] == p1
        assert resumenes[1]["prospect_id"] == p2

    def test_sin_coberturas_activas(self, db):
        resumenes = calcular_pnl_todos_clientes(spot_actual=17.50, db_path=db)
        assert resumenes == []

    def test_cada_cliente_aparece_una_vez(self, db):
        p1 = self._crear_cliente(db)
        _hedge_row(db, p1, fecha_vencimiento=_venc(30))
        _hedge_row(db, p1, fecha_vencimiento=_venc(60))

        resumenes = calcular_pnl_todos_clientes(spot_actual=17.50, db_path=db)
        assert len(resumenes) == 1
        assert resumenes[0]["num_coberturas"] == 2
