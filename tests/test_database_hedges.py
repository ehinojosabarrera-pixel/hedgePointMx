"""
Tests para las funciones CRUD de hedges en core/database.py.

Cobertura:
- insert_hedge: caso válido, campo faltante, tipo inválido, collar sin strike_call
- get_hedge: por ID existente y no existente
- get_client_hedges: sin filtro y filtrando por estado
- update_hedge_status: cambio de estado + registro de P&L
- get_expiring_hedges: rangos de días distintos
"""

import pytest
from pathlib import Path
from datetime import date, timedelta

from core.database import (
    init_db,
    insert_prospect,
    insert_hedge,
    get_hedge,
    get_client_hedges,
    get_active_hedges,
    update_hedge_status,
    get_expiring_hedges,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path: Path) -> Path:
    """Base de datos temporal inicializada."""
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def prospect_id(db: Path) -> int:
    """Inserta un prospecto mínimo y retorna su ID."""
    return insert_prospect(
        {
            "nombre_enc": "enc_nombre",
            "empresa_enc": "enc_empresa",
            "sector": "manufactura",
            "volumen_usd_mensual": 50000.0,
        },
        db_path=db,
    )


def _base_hedge(prospect_id: int, **overrides) -> dict:
    """Devuelve un dict válido para un hedge forward."""
    today = date.today().isoformat()
    venc = (date.today() + timedelta(days=90)).isoformat()
    data = {
        "prospect_id": prospect_id,
        "tipo": "forward",
        "monto_usd": 100_000.0,
        "strike": 17.50,
        "spot_entrada": 17.30,
        "prima_pagada_mxn": 0.0,
        "tasa_forward": 17.55,
        "fecha_inicio": today,
        "fecha_vencimiento": venc,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# insert_hedge
# ---------------------------------------------------------------------------

def test_insert_hedge_valido(db, prospect_id):
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)
    assert isinstance(hid, int)
    assert hid > 0


def test_insert_hedge_campo_faltante(db, prospect_id):
    data = _base_hedge(prospect_id)
    del data["monto_usd"]
    with pytest.raises(ValueError, match="monto_usd"):
        insert_hedge(data, db_path=db)


def test_insert_hedge_tipo_invalido(db, prospect_id):
    data = _base_hedge(prospect_id, tipo="swap")
    with pytest.raises(ValueError, match="tipo"):
        insert_hedge(data, db_path=db)


def test_insert_hedge_collar_sin_strike_call(db, prospect_id):
    data = _base_hedge(prospect_id, tipo="collar")
    with pytest.raises(ValueError, match="strike_call"):
        insert_hedge(data, db_path=db)


def test_insert_hedge_collar_valido(db, prospect_id):
    data = _base_hedge(prospect_id, tipo="collar", strike_call=18.00)
    hid = insert_hedge(data, db_path=db)
    assert hid > 0
    hedge = get_hedge(hid, db_path=db)
    assert hedge["strike_call"] == 18.00


def test_insert_hedge_ignora_claves_desconocidas(db, prospect_id):
    data = _base_hedge(prospect_id, campo_raro="x")
    hid = insert_hedge(data, db_path=db)
    assert hid > 0


# ---------------------------------------------------------------------------
# get_hedge
# ---------------------------------------------------------------------------

def test_get_hedge_existente(db, prospect_id):
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)
    hedge = get_hedge(hid, db_path=db)
    assert hedge is not None
    assert hedge["id"] == hid
    assert hedge["tipo"] == "forward"
    assert hedge["estado"] == "activa"


def test_get_hedge_no_existe(db):
    assert get_hedge(9999, db_path=db) is None


# ---------------------------------------------------------------------------
# get_client_hedges
# ---------------------------------------------------------------------------

def test_get_client_hedges_sin_filtro(db, prospect_id):
    insert_hedge(_base_hedge(prospect_id), db_path=db)
    insert_hedge(_base_hedge(prospect_id, tipo="put"), db_path=db)
    hedges = get_client_hedges(prospect_id, db_path=db)
    assert len(hedges) == 2


def test_get_client_hedges_filtrado_por_estado(db, prospect_id):
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)
    insert_hedge(_base_hedge(prospect_id, tipo="put"), db_path=db)
    # Liquida el primero
    update_hedge_status(hid, "liquidada", db_path=db)

    activas = get_client_hedges(prospect_id, estado="activa", db_path=db)
    liquidadas = get_client_hedges(prospect_id, estado="liquidada", db_path=db)
    assert len(activas) == 1
    assert len(liquidadas) == 1


def test_get_client_hedges_otro_cliente_no_aparece(db, prospect_id):
    otro_id = insert_prospect(
        {
            "nombre_enc": "enc2",
            "empresa_enc": "enc_emp2",
            "sector": "comercio",
            "volumen_usd_mensual": 20000.0,
        },
        db_path=db,
    )
    insert_hedge(_base_hedge(otro_id), db_path=db)
    assert get_client_hedges(prospect_id, db_path=db) == []


# ---------------------------------------------------------------------------
# get_active_hedges
# ---------------------------------------------------------------------------

def test_get_active_hedges(db, prospect_id):
    hid1 = insert_hedge(_base_hedge(prospect_id), db_path=db)
    insert_hedge(_base_hedge(prospect_id, tipo="put"), db_path=db)
    update_hedge_status(hid1, "vencida", db_path=db)

    activas = get_active_hedges(db_path=db)
    assert len(activas) == 1
    assert activas[0]["tipo"] == "put"


# ---------------------------------------------------------------------------
# update_hedge_status
# ---------------------------------------------------------------------------

def test_update_hedge_status_liquidada(db, prospect_id):
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)
    result = update_hedge_status(hid, "liquidada", spot_liquidacion=17.80, pnl_mxn=30_000.0, db_path=db)
    assert result is True

    hedge = get_hedge(hid, db_path=db)
    assert hedge["estado"] == "liquidada"
    assert hedge["spot_liquidacion"] == pytest.approx(17.80)
    assert hedge["pnl_mxn"] == pytest.approx(30_000.0)


def test_update_hedge_status_id_inexistente(db):
    result = update_hedge_status(9999, "cancelada", db_path=db)
    assert result is False


def test_update_hedge_status_sin_pnl(db, prospect_id):
    hid = insert_hedge(_base_hedge(prospect_id), db_path=db)
    update_hedge_status(hid, "vencida", db_path=db)
    hedge = get_hedge(hid, db_path=db)
    assert hedge["estado"] == "vencida"
    assert hedge["pnl_mxn"] is None


# ---------------------------------------------------------------------------
# get_expiring_hedges
# ---------------------------------------------------------------------------

def test_get_expiring_hedges_dentro_del_rango(db, prospect_id):
    venc_5d = (date.today() + timedelta(days=5)).isoformat()
    insert_hedge(_base_hedge(prospect_id, fecha_vencimiento=venc_5d), db_path=db)

    expiring = get_expiring_hedges(dias=7, db_path=db)
    assert len(expiring) == 1


def test_get_expiring_hedges_fuera_del_rango(db, prospect_id):
    venc_30d = (date.today() + timedelta(days=30)).isoformat()
    insert_hedge(_base_hedge(prospect_id, fecha_vencimiento=venc_30d), db_path=db)

    expiring = get_expiring_hedges(dias=7, db_path=db)
    assert len(expiring) == 0


def test_get_expiring_hedges_excluye_no_activas(db, prospect_id):
    venc_3d = (date.today() + timedelta(days=3)).isoformat()
    hid = insert_hedge(_base_hedge(prospect_id, fecha_vencimiento=venc_3d), db_path=db)
    update_hedge_status(hid, "liquidada", db_path=db)

    expiring = get_expiring_hedges(dias=7, db_path=db)
    assert len(expiring) == 0


def test_get_expiring_hedges_rango_amplio(db, prospect_id):
    for dias_offset in [3, 15, 25]:
        venc = (date.today() + timedelta(days=dias_offset)).isoformat()
        insert_hedge(_base_hedge(prospect_id, fecha_vencimiento=venc), db_path=db)

    assert len(get_expiring_hedges(dias=7, db_path=db)) == 1
    assert len(get_expiring_hedges(dias=20, db_path=db)) == 2
    assert len(get_expiring_hedges(dias=30, db_path=db)) == 3
