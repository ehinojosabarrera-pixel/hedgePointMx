"""
Test de integracion: market data -> SQLite.

Ejecuta:
  1. fetch_usdmxn_banxico()  (equivalente a get_fx_rate)  -> insert_fx_rate
  2. get_all_commodities()   (WTI)                        -> insert_commodity

Luego consulta la BD con:
  - get_latest_fx_rates('USDMXN', 3)
  - get_latest_commodities('WTI', 3)

Y verifica + imprime los resultados.

Uso:
    python tests/test_market_data_db.py
"""

import gc
import os
import sys
import tempfile
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from core.database import (
    init_db,
    insert_fx_rate,
    get_latest_fx_rates,
    get_latest_commodities,
)
from core.data.market_data import fetch_usdmxn_banxico
from core.market_data import get_all_commodities


def separator(title: str = "") -> None:
    line = "-" * 50
    print(f"\n{line}")
    if title:
        print(f"  {title}")
        print(line)


def print_rows(rows: list[dict]) -> None:
    if not rows:
        print("  (sin registros)")
        return
    headers = list(rows[0].keys())
    col_w = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in headers}
    header_line = "  " + "  ".join(h.ljust(col_w[h]) for h in headers)
    print(header_line)
    print("  " + "  ".join("-" * col_w[h] for h in headers))
    for r in rows:
        print("  " + "  ".join(str(r[h]).ljust(col_w[h]) for h in headers))


def run(db_path: Path) -> bool:
    all_ok = True

    # ------------------------------------------------------------------
    # 1. FX: fetch_usdmxn_banxico -> insert_fx_rate
    # ------------------------------------------------------------------
    separator("PASO 1 — Descarga USD/MXN desde Banxico")

    try:
        df = fetch_usdmxn_banxico(days=7)
        print(f"  {len(df)} observaciones descargadas de Banxico.")
    except Exception as e:
        print(f"  [ERROR] fetch_usdmxn_banxico: {e}")
        all_ok = False
        df = None

    if df is not None and len(df) > 0:
        latest = df.iloc[-1]
        fecha_str = latest["fecha"].strftime("%Y-%m-%d")
        tasa = float(latest["tipo_cambio"])
        hora_str = datetime.now().strftime("%H:%M:%S")

        try:
            rowid = insert_fx_rate(
                fecha=fecha_str,
                hora=hora_str,
                par="USDMXN",
                bid=tasa,
                ask=round(tasa + 0.02, 4),
                source="Banxico",
                db_path=db_path,
            )
            print(f"  Insertado en fx_rates (rowid={rowid}): {fecha_str} bid={tasa}")
        except Exception as e:
            print(f"  [ERROR] insert_fx_rate: {e}")
            all_ok = False

    # ------------------------------------------------------------------
    # 2. Commodities: get_all_commodities (WTI) -> insert_commodity
    # ------------------------------------------------------------------
    separator("PASO 2 — Descarga WTI desde Alpha Vantage")

    import core.market_data as mkt
    _orig = mkt.insert_commodity

    def _insert_to_test_db(fecha, hora, symbol, price, source, db_path=db_path):
        from core.database import insert_commodity as _real
        return _real(fecha=fecha, hora=hora, symbol=symbol,
                     price=price, source=source, db_path=db_path)

    mkt.insert_commodity = _insert_to_test_db
    try:
        results = get_all_commodities()
    except Exception as e:
        print(f"  [ERROR] get_all_commodities: {e}")
        all_ok = False
        results = []
    finally:
        mkt.insert_commodity = _orig

    for r in results:
        if "error" in r:
            print(f"  [ERROR] {r['symbol']}: {r['error']}")
            all_ok = False
        else:
            print(f"  Insertado en commodities: {r['symbol']} = {r['price']} ({r['fecha']})")

    # ------------------------------------------------------------------
    # 3. Consulta y verificacion
    # ------------------------------------------------------------------
    separator("PASO 3 — Consulta get_latest_fx_rates('USDMXN', 3)")

    try:
        fx_rows = get_latest_fx_rates("USDMXN", 3, db_path=db_path)
        print_rows(fx_rows)
        if not fx_rows:
            print("  [FALLO] No se encontraron registros en fx_rates.")
            all_ok = False
    except Exception as e:
        print(f"  [ERROR] get_latest_fx_rates: {e}")
        all_ok = False
        fx_rows = []

    separator("PASO 4 — Consulta get_latest_commodities('WTI', 3)")

    try:
        comm_rows = get_latest_commodities("WTI", 3, db_path=db_path)
        print_rows(comm_rows)
        if not comm_rows:
            print("  [FALLO] No se encontraron registros de WTI en commodities.")
            all_ok = False
    except Exception as e:
        print(f"  [ERROR] get_latest_commodities: {e}")
        all_ok = False
        comm_rows = []

    # ------------------------------------------------------------------
    # 4. Resumen
    # ------------------------------------------------------------------
    separator("RESULTADO")

    checks = {
        "fx_rates contiene registros USDMXN": bool(fx_rows),
        "commodities contiene registros WTI":  bool(comm_rows),
    }
    if fx_rows:
        checks["fx_rates.par == 'USDMXN'"]    = fx_rows[0]["par"] == "USDMXN"
        checks["fx_rates.source == 'Banxico'"] = fx_rows[0]["source"] == "Banxico"
    if comm_rows:
        checks["commodities.symbol == 'WTI'"]          = comm_rows[0]["symbol"] == "WTI"
        checks["commodities.price > 0"]                = comm_rows[0]["price"] > 0
        checks["commodities.source == 'AlphaVantage'"] = comm_rows[0]["source"] == "AlphaVantage"

    for desc, passed in checks.items():
        tag = "PASS" if passed else "FAIL"
        print(f"  [{tag}] {desc}")
        if not passed:
            all_ok = False

    print()
    return all_ok


def main() -> None:
    fd, tmp = tempfile.mkstemp(suffix=".db", prefix="hedgepoint_test_")
    os.close(fd)
    db_path = Path(tmp)

    try:
        init_db(db_path)
        print(f"BD temporal: {db_path}")
        ok = run(db_path)
    finally:
        gc.collect()
        try:
            db_path.unlink(missing_ok=True)
        except PermissionError:
            pass

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
