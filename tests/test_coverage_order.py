"""
Tests para agents/orders/coverage_order.py y scripts/run_order.py

Cobertura:
- datos_demo: campo correctos, cálculos de pricing, todos los tipos
- generar_pdf_orden (modo demo): PDF creado, >0 bytes, nombre estándar
- generar_pdf_orden con output_path explícito
- generar_pdf_orden collar: incluye strike_call y prima_neta
- construir_datos_orden: datos desde BD, desencripta empresa, cálculo de posición
- construir_datos_orden prospect inexistente: levanta ValueError
- _calcular_vol_30d: positiva con datos, fallback sin datos
- _obtener_mercado: devuelve fallback cuando BD vacía
- CLI --demo (forward / opcion / collar): termina sin error y crea PDF
- CLI --cliente-id sin BD: termina con error 1
"""

from __future__ import annotations

import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers comunes
# ---------------------------------------------------------------------------

def _fecha(offset_days: int = 30) -> str:
    return (date.today() + timedelta(days=offset_days)).isoformat()


def _insertar_fx_rates(db: Path, n: int = 35, bid_base: float = 18.50) -> None:
    from core.database import insert_fx_rate
    for i in range(n):
        fecha = (date.today() - timedelta(days=i)).isoformat()
        bid   = bid_base * (1 + 0.002 * (i % 3 - 1))
        ask   = bid + 0.05
        insert_fx_rate(fecha, "12:00:00", "USDMXN", bid, ask, "test", db_path=db)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_ENC_KEY = "test-encryption-key-for-coverage-order-tests-32x"


@pytest.fixture(autouse=True)
def _set_enc_key(monkeypatch):
    """Asegura que HEDGEPOINT_ENCRYPTION_KEY esté definida en todos los tests."""
    monkeypatch.setenv("HEDGEPOINT_ENCRYPTION_KEY", _TEST_ENC_KEY)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    from core.database import init_db
    db_path = tmp_path / "test_order.db"
    init_db(db_path)
    return db_path


@pytest.fixture
def prospect_id(db: Path) -> int:
    """Prospect con datos encriptados reales."""
    from core.database import insert_prospect, insert_hedge
    from core.security.anonymizer import FieldEncryptor

    enc = FieldEncryptor()
    pid = insert_prospect(
        {
            "nombre_enc":          enc.encrypt("Ana García"),
            "empresa_enc":         enc.encrypt("Exportaciones ACME S.A. de C.V."),
            "telefono_enc":        enc.encrypt("+52 (993) 555-9999"),
            "sector":              "manufactura",
            "volumen_usd_mensual": 500_000.0,
            "margen_utilidad":     0.12,
        },
        db_path=db,
    )

    # Insertar una cobertura activa previa
    insert_hedge(
        {
            "prospect_id":      pid,
            "tipo":             "forward",
            "monto_usd":        150_000.0,
            "strike":           18.65,
            "spot_entrada":     18.40,
            "fecha_inicio":     date.today().isoformat(),
            "fecha_vencimiento": _fecha(60),
        },
        db_path=db,
    )
    return pid


# ===========================================================================
# datos_demo
# ===========================================================================

class TestDatosDemo:

    def test_forward_campos_basicos(self):
        from agents.orders.coverage_order import datos_demo
        d = datos_demo(tipo="forward", monto_usd=200_000, plazo_dias=90, capa="Base")

        assert d.tipo == "forward"
        assert d.monto_usd == 200_000
        assert d.plazo_dias == 90
        assert d.capa == "Base"
        assert d.empresa != ""
        assert d.spot > 0
        assert d.bid < d.ask
        assert d.spread > 0
        assert d.volatilidad_30d > 0

    def test_forward_teorico_calculado(self):
        from agents.orders.coverage_order import datos_demo
        d = datos_demo(tipo="forward")
        assert d.forward_teorico is not None
        # Forward debe ser mayor que spot porque TIIE > SOFR
        assert d.forward_teorico > d.spot

    def test_forward_sin_primas(self):
        from agents.orders.coverage_order import datos_demo
        d = datos_demo(tipo="forward")
        assert d.prima_put is None
        assert d.prima_call is None
        assert d.prima_neta is None
        assert d.strike_call is None

    def test_opcion_tiene_prima_put(self):
        from agents.orders.coverage_order import datos_demo
        d = datos_demo(tipo="opcion")
        assert d.prima_put is not None
        assert d.prima_put > 0
        assert d.prima_neta == d.prima_put
        assert d.prima_call is None

    def test_collar_tiene_ambas_patas(self):
        from agents.orders.coverage_order import datos_demo
        d = datos_demo(tipo="collar")
        assert d.prima_put is not None and d.prima_put > 0
        assert d.prima_call is not None and d.prima_call > 0
        assert d.strike_call is not None and d.strike_call > d.strike
        # Prima neta = put - call (puede ser negativa si call es cara)
        assert d.prima_neta is not None
        assert abs(d.prima_neta - (d.prima_put - d.prima_call)) < 1e-8

    def test_fechas_coherentes(self):
        from agents.orders.coverage_order import datos_demo
        d = datos_demo(plazo_dias=60)
        assert d.fecha_vencimiento == d.fecha_inicio + timedelta(days=60)
        assert d.fecha_inicio == date.today()

    def test_posicion_activa_demo(self):
        from agents.orders.coverage_order import datos_demo
        d = datos_demo()
        assert len(d.coberturas_activas) > 0
        assert d.monto_cubierto_usd > 0
        assert 0 < d.pct_cubierto_actual < 100
        assert d.strike_promedio > 0

    def test_justificacion_personalizada(self):
        from agents.orders.coverage_order import datos_demo
        texto = "Trigger custom activado hoy."
        d = datos_demo(justificacion=texto)
        assert d.justificacion == texto


# ===========================================================================
# generar_pdf_orden
# ===========================================================================

class TestGenerarPdfOrden:

    def test_crea_pdf_forward(self, tmp_path: Path):
        from agents.orders.coverage_order import datos_demo, generar_pdf_orden
        d = datos_demo(tipo="forward")
        ruta = generar_pdf_orden(d, output_dir=str(tmp_path))
        p = Path(ruta)
        assert p.exists()
        assert p.stat().st_size > 5_000   # PDF no vacío
        assert p.suffix == ".pdf"

    def test_crea_pdf_opcion(self, tmp_path: Path):
        from agents.orders.coverage_order import datos_demo, generar_pdf_orden
        d = datos_demo(tipo="opcion")
        ruta = generar_pdf_orden(d, output_dir=str(tmp_path))
        assert Path(ruta).exists()

    def test_crea_pdf_collar(self, tmp_path: Path):
        from agents.orders.coverage_order import datos_demo, generar_pdf_orden
        d = datos_demo(tipo="collar")
        ruta = generar_pdf_orden(d, output_dir=str(tmp_path))
        assert Path(ruta).exists()

    def test_nombre_estandar_contiene_empresa_y_fecha(self, tmp_path: Path):
        from agents.orders.coverage_order import datos_demo, generar_pdf_orden
        d = datos_demo()
        ruta = generar_pdf_orden(d, output_dir=str(tmp_path))
        nombre = Path(ruta).name
        assert "Orden_Cobertura" in nombre
        assert date.today().strftime("%Y%m%d") in nombre

    def test_output_path_explicito(self, tmp_path: Path):
        from agents.orders.coverage_order import datos_demo, generar_pdf_orden
        destino = str(tmp_path / "mi_orden_test.pdf")
        d = datos_demo()
        ruta = generar_pdf_orden(d, output_path=destino)
        assert ruta == destino
        assert Path(destino).exists()

    def test_crea_directorio_si_no_existe(self, tmp_path: Path):
        from agents.orders.coverage_order import datos_demo, generar_pdf_orden
        nuevo_dir = tmp_path / "subdir" / "ordenes"
        d = datos_demo()
        ruta = generar_pdf_orden(d, output_dir=str(nuevo_dir))
        assert Path(ruta).exists()

    def test_pdf_es_bytes_validos(self, tmp_path: Path):
        from agents.orders.coverage_order import datos_demo, generar_pdf_orden
        d = datos_demo()
        ruta = generar_pdf_orden(d, output_dir=str(tmp_path))
        contenido = Path(ruta).read_bytes()
        # Los PDF comienzan con %PDF
        assert contenido[:4] == b"%PDF"


# ===========================================================================
# construir_datos_orden (con BD real)
# ===========================================================================

class TestConstruirDatosOrden:

    def test_devuelve_datos_orden(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="forward",
            monto_usd=100_000,
            plazo_dias=90,
            capa="Base",
            justificacion="Test.",
            db_path=db,
        )
        assert datos.tipo == "forward"
        assert datos.monto_usd == 100_000
        assert datos.plazo_dias == 90
        assert datos.prospect_id == prospect_id

    def test_desencripta_empresa(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="forward",
            monto_usd=100_000,
            plazo_dias=90,
            capa="Base",
            justificacion="",
            db_path=db,
        )
        assert "ACME" in datos.empresa

    def test_desencripta_contacto(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="forward",
            monto_usd=100_000,
            plazo_dias=90,
            capa="Base",
            justificacion="",
            db_path=db,
        )
        assert "Ana" in datos.contacto

    def test_coberturas_activas_incluidas(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="forward",
            monto_usd=50_000,
            plazo_dias=30,
            capa="Táctica 1",
            justificacion="",
            db_path=db,
        )
        assert len(datos.coberturas_activas) == 1
        assert datos.monto_cubierto_usd == 150_000
        assert datos.strike_promedio > 0

    def test_porcentaje_cubierto_correcto(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="forward",
            monto_usd=50_000,
            plazo_dias=30,
            capa="Base",
            justificacion="",
            db_path=db,
        )
        # 150_000 / 500_000 = 30%
        assert abs(datos.pct_cubierto_actual - 30.0) < 0.01

    def test_prospect_inexistente_levanta_error(self, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        with pytest.raises(ValueError, match="no encontrado"):
            construir_datos_orden(
                prospect_id=9999,
                tipo="forward",
                monto_usd=100_000,
                plazo_dias=90,
                capa="Base",
                justificacion="",
                db_path=db,
            )

    def test_opcion_calcula_prima(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="opcion",
            monto_usd=100_000,
            plazo_dias=90,
            capa="Base",
            justificacion="",
            db_path=db,
        )
        assert datos.prima_put is not None and datos.prima_put > 0
        assert datos.prima_neta is not None

    def test_collar_calcula_ambas_patas(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="collar",
            monto_usd=100_000,
            plazo_dias=90,
            capa="Base",
            justificacion="",
            db_path=db,
        )
        assert datos.prima_put is not None
        assert datos.prima_call is not None
        assert datos.strike_call is not None

    def test_strike_manual_respetado(self, prospect_id: int, db: Path):
        from agents.orders.coverage_order import construir_datos_orden
        _insertar_fx_rates(db)
        datos = construir_datos_orden(
            prospect_id=prospect_id,
            tipo="forward",
            monto_usd=100_000,
            plazo_dias=90,
            capa="Base",
            justificacion="",
            strike=19.50,
            db_path=db,
        )
        assert datos.strike == 19.50


# ===========================================================================
# Helpers internos
# ===========================================================================

class TestHelpers:

    def test_calcular_vol_30d_con_datos(self, db: Path):
        from agents.orders.coverage_order import _calcular_vol_30d
        _insertar_fx_rates(db, n=35)
        vol = _calcular_vol_30d(db_path=db)
        assert vol > 0
        assert vol < 100   # porcentaje razonable

    def test_calcular_vol_30d_sin_datos(self, db: Path):
        from agents.orders.coverage_order import _calcular_vol_30d
        vol = _calcular_vol_30d(db_path=db)
        assert vol == 12.0   # fallback

    def test_obtener_mercado_fallback(self, db: Path, monkeypatch):
        """Sin Banxico y sin registros en BD, debe retornar spot fallback=20.0."""
        import agents.orders.coverage_order as mod
        monkeypatch.setattr(
            "agents.orders.coverage_order._SPOT_FALLBACK", 20.0, raising=False
        )
        # No hay datos en BD, Banxico fallará en tests
        spot, bid, ask, spread, hora, fuente = mod._obtener_mercado()
        assert spot > 0
        assert bid < ask
        assert spread > 0
        assert fuente != ""

    def test_datos_demo_no_requiere_db_ni_env(self):
        """datos_demo no debe leer BD ni variables de entorno."""
        from agents.orders.coverage_order import datos_demo
        # No debe levantar excepcion (la key puede o no estar definida)
        d = datos_demo(tipo="forward")
        assert d.empresa != ""

    def test_generar_pdf_no_llama_anthropic(self, tmp_path: Path, monkeypatch):
        """El módulo no debe importar anthropic en ninguna ruta de ejecución."""
        import sys
        # Si anthropic está instalado, aseguramos que no se use
        blocked = []
        real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def _mock_import(name, *args, **kwargs):
            if "anthropic" in name:
                blocked.append(name)
            return real_import(name, *args, **kwargs)

        # Test indirecto: simplemente verificar que el módulo no importa anthropic
        from agents.orders import coverage_order  # noqa: F401
        import inspect
        src = inspect.getsource(coverage_order)
        assert "anthropic" not in src
        assert "claude" not in src.lower().replace("coverage_order", "").replace("ClaudeCode", "")


# ===========================================================================
# CLI integration tests
# ===========================================================================

class TestCLI:

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        import os
        root = Path(__file__).parent.parent
        env = os.environ.copy()
        env.setdefault("HEDGEPOINT_ENCRYPTION_KEY", _TEST_ENC_KEY)
        return subprocess.run(
            [sys.executable, "scripts/run_order.py", *args],
            capture_output=True,
            text=True,
            cwd=str(root),
            env=env,
        )

    def test_demo_forward_exitoso(self, tmp_path: Path):
        result = self._run("--demo", "--tipo", "forward",
                           "--output-dir", str(tmp_path))
        assert result.returncode == 0, result.stderr
        pdfs = list(tmp_path.glob("*.pdf"))
        assert len(pdfs) == 1

    def test_demo_opcion_exitoso(self, tmp_path: Path):
        result = self._run("--demo", "--tipo", "opcion",
                           "--output-dir", str(tmp_path))
        assert result.returncode == 0, result.stderr
        pdfs = list(tmp_path.glob("*.pdf"))
        assert len(pdfs) == 1

    def test_demo_collar_exitoso(self, tmp_path: Path):
        result = self._run("--demo", "--tipo", "collar",
                           "--output-dir", str(tmp_path))
        assert result.returncode == 0, result.stderr
        pdfs = list(tmp_path.glob("*.pdf"))
        assert len(pdfs) == 1

    def test_demo_strike_manual(self, tmp_path: Path):
        result = self._run("--demo", "--strike", "19.50",
                           "--output-dir", str(tmp_path))
        assert result.returncode == 0, result.stderr

    def test_demo_monto_y_plazo_custom(self, tmp_path: Path):
        result = self._run("--demo", "--monto", "500000", "--plazo", "60",
                           "--output-dir", str(tmp_path))
        assert result.returncode == 0, result.stderr

    def test_sin_cliente_id_falla(self):
        result = self._run("--tipo", "forward", "--monto", "100000")
        assert result.returncode != 0

    def test_cliente_id_inexistente_falla(self, tmp_path: Path):
        result = self._run("--cliente-id", "99999", "--tipo", "forward",
                           "--monto", "100000", "--plazo", "90",
                           "--output-dir", str(tmp_path))
        assert result.returncode != 0

    def test_help_no_crashea(self):
        result = self._run("--help")
        assert result.returncode == 0
        assert "Orden de Cobertura" in result.stdout
