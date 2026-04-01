"""
Base de datos SQLite para HedgePoint MX.

Tablas:
- fx_rates: tipos de cambio (bid/ask) por par de divisas
- commodities: precios de materias primas

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
