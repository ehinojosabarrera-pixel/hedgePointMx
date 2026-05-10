"""
Tests para hedge_strategies, hedge_strategy_levels y nuevos campos de hedges.

Cobertura:
- insert_hedge_strategy / get_hedge_strategy / get_client_strategy / update_hedge_strategy
- insert_strategy_level / get_strategy_levels / update_level_status
- Campos banco_ejecutor, spread_banco_centavos, porcentaje_cobertura, costo_total_mxn en hedges
"""

import pytest
from pathlib import Path
from datetime import date, timedelta

from core.database import (
    init_db,
    insert_prospect,
    insert_hedge,
    get_hedge,
    insert_hedge_strategy,
    get_hedge_strategy,
    get_client_strategy,
    update_hedge_strategy,
    insert_strategy_level,
    get_strategy_levels,
    update_level_status,
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
            "volumen_usd_mensual": 50_000.0,
        },
        db_path=db,
    )


def _base_strategy(prospect_id: int, **overrides) -> dict:
    data = {
        "prospect_id": prospect_id,
        "exposicion_mensual_usd": 100_000.0,
        "presupuesto_mensual_mxn": 50_000.0,
    }
    data.update(overrides)
    return data


def _base_hedge(prospect_id: int, **overrides) -> dict:
    today = date.today().isoformat()
    venc = (date.today() + timedelta(days=90)).isoformat()
    data = {
        "prospect_id": prospect_id,
        "tipo": "forward",
        "monto_usd": 100_000.0,
        "strike": 17.50,
        "spot_entrada": 17.30,
        "prima_pagada_mxn": 0.0,
        "fecha_inicio": today,
        "fecha_vencimiento": venc,
    }
    data.update(overrides)
    return data


def _base_level(strategy_id: int, orden: int = 1, **overrides) -> dict:
    data = {
        "strategy_id": strategy_id,
        "nombre": f"Nivel {orden}",
        "orden": orden,
        "condicion_tipo": "inicio_mes",
        "accion_tipo": "forward",
        "accion_pct": 50.0,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# hedge_strategies — insert / get
# ---------------------------------------------------------------------------

def test_insert_strategy(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    assert isinstance(sid, int)
    assert sid > 0

    strategy = get_hedge_strategy(sid, db_path=db)
    assert strategy is not None
    assert strategy["id"] == sid
    assert strategy["prospect_id"] == prospect_id
    assert strategy["exposicion_mensual_usd"] == pytest.approx(100_000.0)
    assert strategy["presupuesto_mensual_mxn"] == pytest.approx(50_000.0)
    assert strategy["activa"] == 1
    # Defaults de la tabla
    assert strategy["cobertura_minima_pct"] == pytest.approx(40.0)
    assert strategy["cobertura_maxima_pct"] == pytest.approx(85.0)
    assert strategy["max_movimientos_mes"] == 3
    assert strategy["horizonte_meses"] == 3
    assert strategy["tipos_permitidos"] == "forward,put,collar"


def test_get_hedge_strategy_no_existe(db):
    assert get_hedge_strategy(9999, db_path=db) is None


def test_get_client_strategy(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    strategy = get_client_strategy(prospect_id, db_path=db)
    assert strategy is not None
    assert strategy["id"] == sid


def test_get_client_strategy_sin_estrategia(db, prospect_id):
    assert get_client_strategy(prospect_id, db_path=db) is None


def test_get_client_strategy_solo_activa(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    update_hedge_strategy(sid, {"activa": 0}, db_path=db)

    assert get_client_strategy(prospect_id, db_path=db) is None


# ---------------------------------------------------------------------------
# hedge_strategies — update
# ---------------------------------------------------------------------------

def test_update_strategy(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)

    result = update_hedge_strategy(
        sid,
        {"exposicion_mensual_usd": 200_000.0, "horizonte_meses": 6},
        db_path=db,
    )
    assert result is True

    strategy = get_hedge_strategy(sid, db_path=db)
    assert strategy["exposicion_mensual_usd"] == pytest.approx(200_000.0)
    assert strategy["horizonte_meses"] == 6
    # Campo no tocado se conserva
    assert strategy["presupuesto_mensual_mxn"] == pytest.approx(50_000.0)


def test_update_strategy_id_inexistente(db):
    result = update_hedge_strategy(9999, {"horizonte_meses": 6}, db_path=db)
    assert result is False


def test_update_strategy_datos_vacios(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    result = update_hedge_strategy(sid, {}, db_path=db)
    assert result is False


# ---------------------------------------------------------------------------
# hedge_strategies — solo una activa por cliente
# ---------------------------------------------------------------------------

def test_only_one_active(db, prospect_id):
    sid1 = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)

    # Desactivar la primera antes de crear la segunda (comportamiento esperado de la capa de negocio)
    update_hedge_strategy(sid1, {"activa": 0}, db_path=db)
    sid2 = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)

    active = get_client_strategy(prospect_id, db_path=db)
    assert active is not None
    assert active["id"] == sid2

    old = get_hedge_strategy(sid1, db_path=db)
    assert old["activa"] == 0


# ---------------------------------------------------------------------------
# hedge_strategy_levels — insert / get
# ---------------------------------------------------------------------------

def test_insert_levels(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)

    lid1 = insert_strategy_level(_base_level(sid, orden=1), db_path=db)
    lid2 = insert_strategy_level(_base_level(sid, orden=2, accion_tipo="put", accion_pct=25.0), db_path=db)

    assert isinstance(lid1, int) and lid1 > 0
    assert isinstance(lid2, int) and lid2 > 0
    assert lid1 != lid2

    levels = get_strategy_levels(sid, db_path=db)
    assert len(levels) == 2
    assert levels[0]["orden"] == 1
    assert levels[1]["orden"] == 2


def test_get_levels_ordered(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)

    # Insertar fuera de orden
    insert_strategy_level(_base_level(sid, orden=3), db_path=db)
    insert_strategy_level(_base_level(sid, orden=1), db_path=db)
    insert_strategy_level(_base_level(sid, orden=2), db_path=db)

    levels = get_strategy_levels(sid, db_path=db)
    ordenes = [lv["orden"] for lv in levels]
    assert ordenes == sorted(ordenes)


def test_get_levels_strategy_vacia(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    assert get_strategy_levels(sid, db_path=db) == []


def test_level_defaults(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    lid = insert_strategy_level(_base_level(sid), db_path=db)

    levels = get_strategy_levels(sid, db_path=db)
    level = levels[0]
    assert level["id"] == lid
    assert level["estado"] == "esperando"
    assert level["hedge_id"] is None
    assert level["fecha_ejecucion"] is None


# ---------------------------------------------------------------------------
# hedge_strategy_levels — update_level_status
# ---------------------------------------------------------------------------

def test_update_level_status(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    lid = insert_strategy_level(_base_level(sid), db_path=db)

    result = update_level_status(lid, "ejecutado", db_path=db)
    assert result is True

    level = get_strategy_levels(sid, db_path=db)[0]
    assert level["estado"] == "ejecutado"
    assert level["fecha_ejecucion"] is not None


def test_update_level_status_cancelado(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    lid = insert_strategy_level(_base_level(sid), db_path=db)

    result = update_level_status(lid, "cancelado", db_path=db)
    assert result is True

    level = get_strategy_levels(sid, db_path=db)[0]
    assert level["estado"] == "cancelado"
    assert level["fecha_ejecucion"] is None


def test_update_level_status_id_inexistente(db):
    result = update_level_status(9999, "ejecutado", db_path=db)
    assert result is False


# ---------------------------------------------------------------------------
# hedge_strategy_levels — vincular hedge al ejecutar
# ---------------------------------------------------------------------------

def test_link_level_to_hedge(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    lid = insert_strategy_level(_base_level(sid), db_path=db)
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)

    result = update_level_status(lid, "ejecutado", hedge_id=hid, db_path=db)
    assert result is True

    level = get_strategy_levels(sid, db_path=db)[0]
    assert level["estado"] == "ejecutado"
    assert level["hedge_id"] == hid
    assert level["fecha_ejecucion"] is not None


def test_link_level_hedge_id_no_sobreescribe_si_no_se_pasa(db, prospect_id):
    sid = insert_hedge_strategy(_base_strategy(prospect_id), db_path=db)
    lid = insert_strategy_level(_base_level(sid), db_path=db)
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)

    update_level_status(lid, "ejecutado", hedge_id=hid, db_path=db)
    # Segunda llamada sin hedge_id: no debe borrar el existente
    update_level_status(lid, "ejecutado", db_path=db)

    level = get_strategy_levels(sid, db_path=db)[0]
    assert level["hedge_id"] == hid


# ---------------------------------------------------------------------------
# Nuevos campos de hedges
# ---------------------------------------------------------------------------

def test_insert_hedge_with_banco(db, prospect_id):
    data = _base_hedge(
        prospect_id,
        banco_ejecutor="BBVA",
        spread_banco_centavos=0.05,
        porcentaje_cobertura=60.0,
        costo_total_mxn=12_500.0,
    )
    hid = insert_hedge(data, db_path=db)
    hedge = get_hedge(hid, db_path=db)

    assert hedge["banco_ejecutor"] == "BBVA"
    assert hedge["spread_banco_centavos"] == pytest.approx(0.05)
    assert hedge["porcentaje_cobertura"] == pytest.approx(60.0)
    assert hedge["costo_total_mxn"] == pytest.approx(12_500.0)


def test_hedge_fields_optional(db, prospect_id):
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)
    hedge = get_hedge(hid, db_path=db)

    assert hedge["banco_ejecutor"] is None
    assert hedge["spread_banco_centavos"] is None
    assert hedge["porcentaje_cobertura"] is None
    assert hedge["costo_total_mxn"] is None


def test_hedge_banco_ejecutor_valores(db, prospect_id):
    for banco in ("Banco Base", "Monex", "BBVA", "Santander"):
        hid = insert_hedge(_base_hedge(prospect_id, banco_ejecutor=banco), db_path=db)
        hedge = get_hedge(hid, db_path=db)
        assert hedge["banco_ejecutor"] == banco
