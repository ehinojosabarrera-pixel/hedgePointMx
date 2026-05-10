"""
Base de datos SQLite para HedgePoint MX.

Tablas:
- fx_rates: tipos de cambio (bid/ask) por par de divisas
- commodities: precios de materias primas
- prospects: prospectos del agente de onboarding (campos sensibles pre-encriptados)
- hedges: coberturas cambiarias activas (forward, put, call, collar)

Funciones de inserción y consulta con sqlite3 estándar.
"""

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "hedgepoint.db"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Crea las tablas si no existen."""
    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fx_rates (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha   TEXT    NOT NULL,
                hora    TEXT    NOT NULL,
                par     TEXT    NOT NULL,
                bid     REAL    NOT NULL,
                ask     REAL    NOT NULL,
                source  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_fx_par_fecha
                ON fx_rates (par, fecha DESC);

            CREATE TABLE IF NOT EXISTS commodities (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha   TEXT    NOT NULL,
                hora    TEXT    NOT NULL,
                symbol  TEXT    NOT NULL,
                price   REAL    NOT NULL,
                source  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_comm_symbol_fecha
                ON commodities (symbol, fecha DESC);

            CREATE TABLE IF NOT EXISTS prospects (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
                -- Sensitive fields arrive pre-encrypted as base64 strings
                nombre_enc              TEXT    NOT NULL,
                empresa_enc             TEXT    NOT NULL,
                email_enc               TEXT,
                telefono_enc            TEXT,
                -- Analytical fields stored in plaintext for queries and calculations
                sector                  TEXT    NOT NULL,
                volumen_usd_mensual     REAL    NOT NULL,
                frecuencia_compra       TEXT,
                plazo_pago_dias         INTEGER,
                margen_utilidad         REAL,
                usa_coberturas          INTEGER DEFAULT 0,
                moneda_principal        TEXT    DEFAULT 'USD',
                -- Diagnostic results
                exposicion_anual_usd    REAL,
                var_95                  REAL,
                ahorro_potencial_mxn    REAL,
                estrategia_recomendada  TEXT,
                -- Lifecycle state
                status                  TEXT    DEFAULT 'nuevo',
                notas                   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_prospects_sector
                ON prospects (sector);

            CREATE INDEX IF NOT EXISTS idx_prospects_status
                ON prospects (status);

            CREATE TABLE IF NOT EXISTS interest_rates (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha   TEXT    NOT NULL,
                hora    TEXT    NOT NULL,
                symbol  TEXT    NOT NULL,
                rate    REAL    NOT NULL,
                source  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ir_symbol_fecha
                ON interest_rates (symbol, fecha DESC);

            CREATE TABLE IF NOT EXISTS hedges (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id             INTEGER NOT NULL REFERENCES prospects(id),
                tipo                    TEXT    NOT NULL CHECK(tipo IN ('forward', 'put', 'call', 'collar')),
                monto_usd               REAL    NOT NULL,
                strike                  REAL    NOT NULL,
                strike_call             REAL,
                spot_entrada            REAL    NOT NULL,
                prima_pagada_mxn        REAL    NOT NULL DEFAULT 0,
                tasa_forward            REAL,
                fecha_inicio            TEXT    NOT NULL,
                fecha_vencimiento       TEXT    NOT NULL,
                fecha_liquidacion       TEXT,
                estado                  TEXT    NOT NULL DEFAULT 'activa'
                                            CHECK(estado IN ('activa', 'vencida', 'liquidada', 'cancelada')),
                spot_liquidacion        REAL,
                pnl_mxn                 REAL,
                notas                   TEXT,
                banco_ejecutor          TEXT,
                spread_banco_centavos   REAL,
                porcentaje_cobertura    REAL,
                costo_total_mxn         REAL,
                created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_hedges_prospect_id
                ON hedges (prospect_id);

            CREATE INDEX IF NOT EXISTS idx_hedges_estado
                ON hedges (estado);

            CREATE INDEX IF NOT EXISTS idx_hedges_fecha_vencimiento
                ON hedges (fecha_vencimiento);

            CREATE TABLE IF NOT EXISTS hedge_strategies (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id             INTEGER NOT NULL REFERENCES prospects(id),
                exposicion_mensual_usd  REAL    NOT NULL,
                presupuesto_mensual_mxn REAL    NOT NULL,
                cobertura_minima_pct    REAL    NOT NULL DEFAULT 40,
                cobertura_maxima_pct    REAL    NOT NULL DEFAULT 85,
                max_movimientos_mes     INTEGER NOT NULL DEFAULT 3,
                horizonte_meses         INTEGER NOT NULL DEFAULT 3,
                tipos_permitidos        TEXT    NOT NULL DEFAULT 'forward,put,collar',
                ratio_forward_min       REAL    DEFAULT 50,
                ratio_forward_max       REAL    DEFAULT 70,
                ratio_opciones_min      REAL    DEFAULT 20,
                ratio_opciones_max      REAL    DEFAULT 35,
                ratio_collar_min        REAL    DEFAULT 0,
                ratio_collar_max        REAL    DEFAULT 20,
                activa                  INTEGER NOT NULL DEFAULT 1,
                created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at              TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_hedge_strategies_prospect_id
                ON hedge_strategies (prospect_id);

            CREATE TABLE IF NOT EXISTS hedge_strategy_levels (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id     INTEGER NOT NULL REFERENCES hedge_strategies(id),
                nombre          TEXT    NOT NULL,
                orden           INTEGER NOT NULL,
                condicion_tipo  TEXT    NOT NULL CHECK(condicion_tipo IN ('inicio_mes', 'precio_debajo', 'precio_arriba', 'volatilidad', 'combinada')),
                condicion_valor REAL,
                condicion_extra TEXT,
                accion_tipo     TEXT    NOT NULL CHECK(accion_tipo IN ('forward', 'put', 'collar')),
                accion_pct      REAL    NOT NULL,
                estado          TEXT    NOT NULL DEFAULT 'esperando' CHECK(estado IN ('esperando', 'ejecutado', 'cancelado')),
                fecha_ejecucion TEXT,
                hedge_id        INTEGER REFERENCES hedges(id),
                created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_strategy_levels_strategy_id
                ON hedge_strategy_levels (strategy_id);

            CREATE TABLE IF NOT EXISTS hedge_pending (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id             INTEGER NOT NULL REFERENCES prospects(id),
                tipo                    TEXT,
                monto_usd               REAL,
                strike                  REAL,
                strike_call             REAL,
                spot_entrada            REAL,
                prima_pagada_mxn        REAL,
                fecha_inicio            TEXT,
                fecha_vencimiento       TEXT,
                banco_ejecutor          TEXT,
                spread_banco_centavos   REAL,
                estado                  TEXT NOT NULL DEFAULT 'pendiente'
                                            CHECK(estado IN ('pendiente', 'aprobada', 'rechazada')),
                notas                   TEXT,
                documento_nombre        TEXT,
                created_at              TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_hedge_pending_prospect_id
                ON hedge_pending (prospect_id);

            CREATE INDEX IF NOT EXISTS idx_hedge_pending_estado
                ON hedge_pending (estado);
        """)


# ---------------------------------------------------------------------------
# FX Rates
# ---------------------------------------------------------------------------

def insert_fx_rate(
    fecha: str,
    hora: str,
    par: str,
    bid: float,
    ask: float,
    source: str,
    db_path: Path = DB_PATH,
) -> int:
    """Inserta un registro en fx_rates. Retorna el rowid insertado."""
    sql = """
        INSERT INTO fx_rates (fecha, hora, par, bid, ask, source)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, (fecha, hora, par, bid, ask, source))
        return cur.lastrowid


def insert_fx_rates_bulk(rows: list[dict], db_path: Path = DB_PATH) -> int:
    """Inserta múltiples registros en fx_rates. Retorna la cantidad insertada."""
    sql = """
        INSERT INTO fx_rates (fecha, hora, par, bid, ask, source)
        VALUES (:fecha, :hora, :par, :bid, :ask, :source)
    """
    with get_connection(db_path) as conn:
        cur = conn.executemany(sql, rows)
        return cur.rowcount


def get_latest_fx_rates(
    par: str,
    n: int = 10,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retorna los últimos N registros de fx_rates para un par dado."""
    sql = """
        SELECT fecha, hora, par, bid, ask, source
        FROM fx_rates
        WHERE par = ?
        ORDER BY fecha DESC, hora DESC
        LIMIT ?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (par, n)).fetchall()
    return [dict(r) for r in rows]


def get_latest_fx_rates_all(
    n: int = 10,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retorna los últimos N registros de fx_rates para todos los pares."""
    sql = """
        SELECT fecha, hora, par, bid, ask, source
        FROM fx_rates
        ORDER BY fecha DESC, hora DESC
        LIMIT ?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (n,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Commodities
# ---------------------------------------------------------------------------

def insert_commodity(
    fecha: str,
    hora: str,
    symbol: str,
    price: float,
    source: str,
    db_path: Path = DB_PATH,
) -> int:
    """Inserta un registro en commodities. Retorna el rowid insertado."""
    sql = """
        INSERT INTO commodities (fecha, hora, symbol, price, source)
        VALUES (?, ?, ?, ?, ?)
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, (fecha, hora, symbol, price, source))
        return cur.lastrowid


def insert_commodities_bulk(rows: list[dict], db_path: Path = DB_PATH) -> int:
    """Inserta múltiples registros en commodities. Retorna la cantidad insertada."""
    sql = """
        INSERT INTO commodities (fecha, hora, symbol, price, source)
        VALUES (:fecha, :hora, :symbol, :price, :source)
    """
    with get_connection(db_path) as conn:
        cur = conn.executemany(sql, rows)
        return cur.rowcount


def get_latest_commodities(
    symbol: str,
    n: int = 10,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retorna los últimos N registros de commodities para un símbolo dado."""
    sql = """
        SELECT fecha, hora, symbol, price, source
        FROM commodities
        WHERE symbol = ?
        ORDER BY fecha DESC, hora DESC
        LIMIT ?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (symbol, n)).fetchall()
    return [dict(r) for r in rows]


def get_latest_commodities_all(
    n: int = 10,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retorna los últimos N registros de commodities para todos los símbolos."""
    sql = """
        SELECT fecha, hora, symbol, price, source
        FROM commodities
        ORDER BY fecha DESC, hora DESC
        LIMIT ?
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (n,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Interest Rates (TIIE, SOFR)
# ---------------------------------------------------------------------------

def insert_interest_rate(
    fecha: str,
    hora: str,
    symbol: str,
    rate: float,
    source: str,
    db_path: Path = DB_PATH,
) -> int:
    """Inserta una tasa de interés en interest_rates. Retorna el rowid insertado."""
    sql = """
        INSERT INTO interest_rates (fecha, hora, symbol, rate, source)
        VALUES (?, ?, ?, ?, ?)
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, (fecha, hora, symbol, rate, source))
        return cur.lastrowid


def get_latest_interest_rate(
    symbol: str,
    db_path: Path = DB_PATH,
) -> Optional[float]:
    """Retorna la tasa más reciente para un símbolo (ej. 'TIIE28D', 'SOFR'), o None."""
    sql = """
        SELECT rate FROM interest_rates
        WHERE symbol = ?
        ORDER BY fecha DESC, hora DESC
        LIMIT 1
    """
    with get_connection(db_path) as conn:
        row = conn.execute(sql, (symbol,)).fetchone()
    return float(row["rate"]) if row else None


# ---------------------------------------------------------------------------
# Prospects
# ---------------------------------------------------------------------------

# Columns accepted by insert_prospect / update_prospect.
# Sensitive fields are expected to arrive already encrypted (base64 strings).
_PROSPECT_COLUMNS = frozenset({
    "nombre_enc", "empresa_enc", "email_enc", "telefono_enc",
    "sector", "volumen_usd_mensual", "frecuencia_compra",
    "plazo_pago_dias", "margen_utilidad", "usa_coberturas", "moneda_principal",
    "exposicion_anual_usd", "var_95", "ahorro_potencial_mxn",
    "estrategia_recomendada", "status", "notas",
})


def insert_prospect(data: dict, db_path: Path = DB_PATH) -> int:
    """Inserta un nuevo prospecto y retorna el rowid.

    Los campos sensibles (nombre_enc, empresa_enc, email_enc, telefono_enc)
    deben llegar ya encriptados por FieldEncryptor — esta función no encripta.

    Args:
        data: Diccionario con los campos del prospecto.  Las claves no
              reconocidas se ignoran silenciosamente.
        db_path: Ruta a la base de datos.

    Returns:
        rowid del registro insertado.
    """
    allowed = {k: v for k, v in data.items() if k in _PROSPECT_COLUMNS}
    cols = ", ".join(allowed.keys())
    placeholders = ", ".join(f":{k}" for k in allowed.keys())
    sql = f"INSERT INTO prospects ({cols}) VALUES ({placeholders})"
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, allowed)
        return cur.lastrowid


def get_prospect(prospect_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Retorna un prospecto por ID, o None si no existe.

    Args:
        prospect_id: ID del prospecto.
        db_path: Ruta a la base de datos.

    Returns:
        Diccionario con todos los campos, o None.
    """
    sql = "SELECT * FROM prospects WHERE id = ?"
    with get_connection(db_path) as conn:
        row = conn.execute(sql, (prospect_id,)).fetchone()
    return dict(row) if row else None


def get_all_prospects(
    status: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Lista todos los prospectos, opcionalmente filtrados por status.

    Args:
        status: Si se indica, filtra por este valor de status
                (p.ej. ``'nuevo'``, ``'diagnosticado'``).
        db_path: Ruta a la base de datos.

    Returns:
        Lista de diccionarios, ordenados por created_at descendente.
    """
    if status is not None:
        sql = "SELECT * FROM prospects WHERE status = ? ORDER BY created_at DESC"
        params: tuple = (status,)
    else:
        sql = "SELECT * FROM prospects ORDER BY created_at DESC"
        params = ()
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_prospect(
    prospect_id: int,
    data: dict,
    db_path: Path = DB_PATH,
) -> bool:
    """Actualiza campos de un prospecto y renueva updated_at.

    Args:
        prospect_id: ID del prospecto a actualizar.
        data: Diccionario con los campos a modificar.  Las claves no
              reconocidas se ignoran.  ``id``, ``created_at`` y
              ``updated_at`` no pueden modificarse desde aquí.
        db_path: Ruta a la base de datos.

    Returns:
        True si se actualizó al menos una fila, False si el ID no existe.
    """
    allowed = {k: v for k, v in data.items() if k in _PROSPECT_COLUMNS}
    if not allowed:
        return False
    set_clause = ", ".join(f"{k} = :{k}" for k in allowed.keys())
    sql = f"""
        UPDATE prospects
        SET {set_clause}, updated_at = datetime('now')
        WHERE id = :_id
    """
    allowed["_id"] = prospect_id
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, allowed)
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Hedges
# ---------------------------------------------------------------------------

_HEDGE_REQUIRED = frozenset({
    "prospect_id", "tipo", "monto_usd", "strike", "spot_entrada",
    "fecha_inicio", "fecha_vencimiento",
})

_HEDGE_COLUMNS = frozenset({
    "prospect_id", "tipo", "monto_usd", "strike", "strike_call",
    "spot_entrada", "prima_pagada_mxn", "tasa_forward",
    "fecha_inicio", "fecha_vencimiento", "fecha_liquidacion",
    "estado", "spot_liquidacion", "pnl_mxn", "notas",
    "banco_ejecutor", "spread_banco_centavos", "porcentaje_cobertura", "costo_total_mxn",
})

_TIPOS_VALIDOS = {"forward", "put", "call", "collar"}


def insert_hedge(data: dict, db_path: Path = DB_PATH) -> int:
    """Inserta una cobertura activa y retorna el rowid.

    Args:
        data: Diccionario con los campos de la cobertura.  Las claves no
              reconocidas se ignoran silenciosamente.
        db_path: Ruta a la base de datos.

    Returns:
        rowid del registro insertado.

    Raises:
        ValueError: Si faltan campos requeridos, el tipo es inválido, o el
                    tipo 'collar' no incluye strike_call.
    """
    missing = _HEDGE_REQUIRED - data.keys()
    if missing:
        raise ValueError(f"Campos requeridos faltantes: {missing}")

    tipo = data["tipo"]
    if tipo not in _TIPOS_VALIDOS:
        raise ValueError(f"tipo '{tipo}' no válido. Opciones: {_TIPOS_VALIDOS}")

    if tipo == "collar" and not data.get("strike_call"):
        raise ValueError("El tipo 'collar' requiere el campo 'strike_call'.")

    allowed = {k: v for k, v in data.items() if k in _HEDGE_COLUMNS}
    cols = ", ".join(allowed.keys())
    placeholders = ", ".join(f":{k}" for k in allowed.keys())
    sql = f"INSERT INTO hedges ({cols}) VALUES ({placeholders})"
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, allowed)
        return cur.lastrowid


def get_hedge(hedge_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Retorna una cobertura por ID, o None si no existe.

    Args:
        hedge_id: ID de la cobertura.
        db_path: Ruta a la base de datos.

    Returns:
        Diccionario con todos los campos, o None.
    """
    sql = "SELECT * FROM hedges WHERE id = ?"
    with get_connection(db_path) as conn:
        row = conn.execute(sql, (hedge_id,)).fetchone()
    return dict(row) if row else None


def get_client_hedges(
    prospect_id: int,
    estado: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retorna las coberturas de un cliente, opcionalmente filtradas por estado.

    Args:
        prospect_id: ID del prospecto/cliente.
        estado: Si se indica, filtra por este estado
                (``'activa'``, ``'vencida'``, ``'liquidada'``, ``'cancelada'``).
        db_path: Ruta a la base de datos.

    Returns:
        Lista de diccionarios ordenados por fecha_vencimiento ascendente.
    """
    if estado is not None:
        sql = """
            SELECT * FROM hedges
            WHERE prospect_id = ? AND estado = ?
            ORDER BY fecha_vencimiento ASC
        """
        params: tuple = (prospect_id, estado)
    else:
        sql = """
            SELECT * FROM hedges
            WHERE prospect_id = ?
            ORDER BY fecha_vencimiento ASC
        """
        params = (prospect_id,)
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_active_hedges(db_path: Path = DB_PATH) -> list[dict]:
    """Retorna todas las coberturas con estado 'activa'.

    Args:
        db_path: Ruta a la base de datos.

    Returns:
        Lista de diccionarios ordenados por fecha_vencimiento ascendente.
    """
    sql = """
        SELECT * FROM hedges
        WHERE estado = 'activa'
        ORDER BY fecha_vencimiento ASC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def update_hedge_status(
    hedge_id: int,
    estado: str,
    spot_liquidacion: Optional[float] = None,
    pnl_mxn: Optional[float] = None,
    db_path: Path = DB_PATH,
) -> bool:
    """Cambia el estado de una cobertura y opcionalmente registra liquidación.

    Args:
        hedge_id: ID de la cobertura.
        estado: Nuevo estado (``'activa'``, ``'vencida'``, ``'liquidada'``, ``'cancelada'``).
        spot_liquidacion: Tipo de cambio al momento de liquidar (opcional).
        pnl_mxn: Resultado neto de la cobertura en MXN (opcional).
        db_path: Ruta a la base de datos.

    Returns:
        True si se actualizó al menos una fila, False si el ID no existe.
    """
    sql = """
        UPDATE hedges
        SET estado           = ?,
            spot_liquidacion = COALESCE(?, spot_liquidacion),
            pnl_mxn          = COALESCE(?, pnl_mxn),
            updated_at       = datetime('now')
        WHERE id = ?
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, (estado, spot_liquidacion, pnl_mxn, hedge_id))
        return cur.rowcount > 0


def get_expiring_hedges(dias: int = 7, db_path: Path = DB_PATH) -> list[dict]:
    """Retorna coberturas activas que vencen en los próximos N días.

    Args:
        dias: Número de días hacia adelante a considerar.
        db_path: Ruta a la base de datos.

    Returns:
        Lista de diccionarios ordenados por fecha_vencimiento ascendente.
    """
    sql = """
        SELECT * FROM hedges
        WHERE estado = 'activa'
          AND fecha_vencimiento BETWEEN date('now') AND date('now', :offset)
        ORDER BY fecha_vencimiento ASC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, {"offset": f"+{dias} days"}).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Hedge Strategies
# ---------------------------------------------------------------------------

_STRATEGY_COLUMNS = frozenset({
    "prospect_id", "exposicion_mensual_usd", "presupuesto_mensual_mxn",
    "cobertura_minima_pct", "cobertura_maxima_pct", "max_movimientos_mes",
    "horizonte_meses", "tipos_permitidos",
    "ratio_forward_min", "ratio_forward_max",
    "ratio_opciones_min", "ratio_opciones_max",
    "ratio_collar_min", "ratio_collar_max",
    "activa",
})


def insert_hedge_strategy(data: dict, db_path: Path = DB_PATH) -> int:
    """Inserta una estrategia de cobertura y retorna el rowid."""
    allowed = {k: v for k, v in data.items() if k in _STRATEGY_COLUMNS}
    cols = ", ".join(allowed.keys())
    placeholders = ", ".join(f":{k}" for k in allowed.keys())
    sql = f"INSERT INTO hedge_strategies ({cols}) VALUES ({placeholders})"
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, allowed)
        return cur.lastrowid


def get_hedge_strategy(strategy_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Retorna una estrategia por ID, o None si no existe."""
    sql = "SELECT * FROM hedge_strategies WHERE id = ?"
    with get_connection(db_path) as conn:
        row = conn.execute(sql, (strategy_id,)).fetchone()
    return dict(row) if row else None


def get_client_strategy(prospect_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Retorna la estrategia activa de un cliente, o None si no existe."""
    sql = """
        SELECT * FROM hedge_strategies
        WHERE prospect_id = ? AND activa = 1
        ORDER BY created_at DESC
        LIMIT 1
    """
    with get_connection(db_path) as conn:
        row = conn.execute(sql, (prospect_id,)).fetchone()
    return dict(row) if row else None


def update_hedge_strategy(
    strategy_id: int,
    data: dict,
    db_path: Path = DB_PATH,
) -> bool:
    """Actualiza campos de una estrategia y renueva updated_at."""
    allowed = {k: v for k, v in data.items() if k in _STRATEGY_COLUMNS}
    if not allowed:
        return False
    set_clause = ", ".join(f"{k} = :{k}" for k in allowed.keys())
    sql = f"""
        UPDATE hedge_strategies
        SET {set_clause}, updated_at = datetime('now')
        WHERE id = :_id
    """
    allowed["_id"] = strategy_id
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, allowed)
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Hedge Strategy Levels
# ---------------------------------------------------------------------------

_LEVEL_COLUMNS = frozenset({
    "strategy_id", "nombre", "orden", "condicion_tipo", "condicion_valor",
    "condicion_extra", "accion_tipo", "accion_pct", "estado",
    "fecha_ejecucion", "hedge_id",
})


def insert_strategy_level(data: dict, db_path: Path = DB_PATH) -> int:
    """Inserta un nivel de estrategia y retorna el rowid."""
    allowed = {k: v for k, v in data.items() if k in _LEVEL_COLUMNS}
    cols = ", ".join(allowed.keys())
    placeholders = ", ".join(f":{k}" for k in allowed.keys())
    sql = f"INSERT INTO hedge_strategy_levels ({cols}) VALUES ({placeholders})"
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, allowed)
        return cur.lastrowid


def get_strategy_levels(strategy_id: int, db_path: Path = DB_PATH) -> list[dict]:
    """Retorna los niveles de una estrategia, ordenados por orden ascendente."""
    sql = """
        SELECT * FROM hedge_strategy_levels
        WHERE strategy_id = ?
        ORDER BY orden ASC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (strategy_id,)).fetchall()
    return [dict(r) for r in rows]


def update_level_status(
    level_id: int,
    estado: str,
    hedge_id: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> bool:
    """Actualiza el estado de un nivel y opcionalmente registra el hedge ejecutado."""
    sql = """
        UPDATE hedge_strategy_levels
        SET estado          = ?,
            hedge_id        = COALESCE(?, hedge_id),
            fecha_ejecucion = CASE WHEN ? = 'ejecutado' THEN datetime('now') ELSE fecha_ejecucion END
        WHERE id = ?
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, (estado, hedge_id, estado, level_id))
        return cur.rowcount > 0


def update_prospect_diagnostic(
    prospect_id: int,
    exposicion: float,
    var_95: float,
    ahorro: float,
    estrategia: str,
    db_path: Path = DB_PATH,
) -> bool:
    """Escribe los resultados del diagnóstico y marca el prospecto como 'diagnosticado'.

    Args:
        prospect_id: ID del prospecto.
        exposicion: Exposición cambiaria anual estimada en USD.
        var_95: Value-at-Risk al 95% en MXN.
        ahorro: Ahorro potencial anual en MXN con cobertura.
        estrategia: Estrategia recomendada (p.ej. ``'forward'``, ``'collar'``).
        db_path: Ruta a la base de datos.

    Returns:
        True si se actualizó al menos una fila, False si el ID no existe.
    """
    sql = """
        UPDATE prospects
        SET exposicion_anual_usd   = ?,
            var_95                 = ?,
            ahorro_potencial_mxn   = ?,
            estrategia_recomendada = ?,
            status                 = 'diagnosticado',
            updated_at             = datetime('now')
        WHERE id = ?
    """
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, (exposicion, var_95, ahorro, estrategia, prospect_id))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Hedge pending
# ---------------------------------------------------------------------------

_HEDGE_PENDING_COLUMNS = frozenset({
    "prospect_id", "tipo", "monto_usd", "strike", "strike_call",
    "spot_entrada", "prima_pagada_mxn", "fecha_inicio", "fecha_vencimiento",
    "banco_ejecutor", "spread_banco_centavos", "estado", "notas", "documento_nombre",
})


def insert_hedge_pending(data: dict, db_path: Path = DB_PATH) -> int:
    """Inserta una cobertura pendiente de aprobación. Retorna el id insertado."""
    cols = [k for k in data if k in _HEDGE_PENDING_COLUMNS]
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    values = [data[c] for c in cols]
    sql = f"INSERT INTO hedge_pending ({col_names}) VALUES ({placeholders})"
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, values)
        return cur.lastrowid


def get_pending_hedges(estado: str = "pendiente", db_path: Path = DB_PATH) -> list[dict]:
    """Retorna todas las coberturas pendientes con el estado dado."""
    sql = "SELECT * FROM hedge_pending WHERE estado = ? ORDER BY created_at DESC"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (estado,)).fetchall()
    return [dict(r) for r in rows]


def get_client_pending_hedges(
    prospect_id: int,
    estado: str = "pendiente",
    db_path: Path = DB_PATH,
) -> list[dict]:
    """Retorna las coberturas pendientes de un cliente con el estado dado."""
    sql = "SELECT * FROM hedge_pending WHERE prospect_id = ? AND estado = ? ORDER BY created_at DESC"
    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (prospect_id, estado)).fetchall()
    return [dict(r) for r in rows]


def get_pending_hedge(pending_id: int, db_path: Path = DB_PATH) -> Optional[dict]:
    """Retorna una cobertura pendiente por su id, o None si no existe."""
    sql = "SELECT * FROM hedge_pending WHERE id = ?"
    with get_connection(db_path) as conn:
        row = conn.execute(sql, (pending_id,)).fetchone()
    return dict(row) if row else None


def update_pending_status(pending_id: int, estado: str, db_path: Path = DB_PATH) -> bool:
    """Actualiza el estado de una cobertura pendiente. Retorna True si se actualizó."""
    sql = "UPDATE hedge_pending SET estado = ? WHERE id = ?"
    with get_connection(db_path) as conn:
        cur = conn.execute(sql, (estado, pending_id))
        return cur.rowcount > 0
