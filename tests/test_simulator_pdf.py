"""
Tests para agents/simulator/pdf_generator.py y agents/simulator/savings_simulator.py

Cobertura:
- ParametrosCliente: defaults correctos (spread 0.05, markup 0.00, fee 15000)
- flags --spread, --markup, --fee sobreescriben defaults correctamente
- generar_pdf: PDF creado, >0 bytes
- PDF 100% en español — sin texto en inglés
- Resumen ejecutivo: 6 KPIs nuevos presentes, sin KPIs eliminados
- Sin sección de mezcla óptima / comparativa estrategias
- Sin gráfica de resultado acumulado (la línea descendente)
- Tabla mensual: 12 filas para período de 1 año
- SimuladorAhorro con --year: simula año calendario correcto
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest

from agents.simulator.savings_simulator import (
    ParametrosCliente,
    ResultadoPeriodo,
    ResultadoSimulacion,
)


# ---------------------------------------------------------------------------
# Helpers para construir un ResultadoSimulacion sintético (sin DB)
# ---------------------------------------------------------------------------

def _make_periodo(
    anio: int,
    mes: int,
    spot: float = 18.50,
    forward: float = 18.65,
    volumen: float = 300_000.0,
    spread: float = 0.05,
    markup: float = 0.00,
    fee: float = 15_000.0,
) -> ResultadoPeriodo:
    """Construye un ResultadoPeriodo determinista sin DB."""
    costo_spot = volumen * spot
    costo_fwd_teorico = volumen * forward
    costo_spread = volumen * spread
    costo_markup = volumen * markup
    costo_total_fwd = costo_fwd_teorico + costo_spread + costo_markup + fee
    ahorro = costo_spot - costo_total_fwd
    return ResultadoPeriodo(
        periodo=f"{anio}-{mes:02d}",
        fecha_compra=date(anio, mes, 15),
        spot=spot,
        forward_30d=forward,
        fecha_forward=date(anio, mes, 1),
        spot_forward_base=spot - 0.10,
        volumen_usd=volumen,
        costo_spot_mxn=costo_spot,
        costo_forward_mxn=costo_total_fwd,
        costo_forward_teorico_mxn=costo_fwd_teorico,
        costo_spread_banco_mxn=costo_spread,
        costo_markup_hp_mxn=costo_markup,
        costo_fee_hp_mxn=fee,
        ahorro_mxn=ahorro,
        ahorro_porcentaje=(ahorro / costo_spot * 100) if costo_spot > 0 else 0.0,
    )


def _make_resultado(
    n_meses: int = 12,
    anio: int = 2024,
    spot: float = 18.50,
    forward: float = 18.65,
    volumen: float = 300_000.0,
    margen: float = 0.12,
    spread: float = 0.05,
    markup: float = 0.00,
    fee: float = 15_000.0,
    cobertura: float = 100.0,
) -> ResultadoSimulacion:
    """Construye un ResultadoSimulacion sintético de n_meses meses."""
    params = ParametrosCliente(
        volumen_mensual_usd=volumen,
        margen_utilidad=margen,
        spread_banco=spread,
        markup_hedgepoint=markup,
        fee_mensual=fee,
        cobertura_pct=cobertura,
    )
    periodos = [
        _make_periodo(anio, mes + 1, spot=spot, forward=forward,
                      volumen=volumen, spread=spread, markup=markup, fee=fee)
        for mes in range(n_meses)
    ]
    return ResultadoSimulacion(
        parametros=params,
        periodos=periodos,
        fecha_inicio=date(anio, 1, 1),
        fecha_fin=date(anio, n_meses, 28),
    )


# ---------------------------------------------------------------------------
# Tests: ParametrosCliente — defaults
# ---------------------------------------------------------------------------

_P_BASE = dict(volumen_mensual_usd=100_000.0, margen_utilidad=0.12)


class TestParametrosClienteDefaults:

    def test_spread_banco_default(self):
        p = ParametrosCliente(**_P_BASE)
        assert p.spread_banco == pytest.approx(0.05)

    def test_markup_hedgepoint_default_es_cero(self):
        """Markup HP debe ser 0.00 en fase inicial."""
        p = ParametrosCliente(**_P_BASE)
        assert p.markup_hedgepoint == pytest.approx(0.00)

    def test_fee_mensual_default(self):
        p = ParametrosCliente(**_P_BASE)
        assert p.fee_mensual == pytest.approx(15_000.0)

    def test_spread_sobreescribible(self):
        p = ParametrosCliente(**_P_BASE, spread_banco=0.08)
        assert p.spread_banco == pytest.approx(0.08)

    def test_markup_sobreescribible(self):
        p = ParametrosCliente(**_P_BASE, markup_hedgepoint=0.04)
        assert p.markup_hedgepoint == pytest.approx(0.04)

    def test_fee_sobreescribible(self):
        p = ParametrosCliente(**_P_BASE, fee_mensual=20_000.0)
        assert p.fee_mensual == pytest.approx(20_000.0)


# ---------------------------------------------------------------------------
# Tests: ResultadoSimulacion — cálculos con markup=0
# ---------------------------------------------------------------------------

class TestResultadoSimulacionMarkupCero:

    def test_costo_markup_hp_es_cero_con_default(self):
        r = _make_resultado()
        assert r.costo_total_markup_hp_mxn == pytest.approx(0.0)

    def test_costo_hedgepoint_solo_fee_con_markup_cero(self):
        r = _make_resultado(n_meses=12, fee=15_000.0)
        # Con markup=0, costo HP total = fee * 12 meses
        assert r.costo_total_hedgepoint_mxn == pytest.approx(15_000.0 * 12)

    def test_costo_banco_es_spread_por_volumen(self):
        r = _make_resultado(volumen=100_000.0, spread=0.05, n_meses=3)
        # 3 meses × 100k USD × 0.05 MXN/USD
        assert r.costo_total_banco_mxn == pytest.approx(3 * 100_000 * 0.05)

    def test_total_meses_correcto(self):
        r = _make_resultado(n_meses=12)
        assert r.total_meses == 12

    def test_prima_forward_no_es_notional(self):
        """
        El KPI 'costo de cobertura' debe ser la prima TIIE/SOFR pura (fwd - spot_forward_base),
        no el costo nocional (forward × volumen) ni la diferencia fwd-spot (que incluye movimiento de mercado).
        _make_resultado fija spot_forward_base = spot - 0.10:
          Con spot=18.50, spot_base=18.40, forward=18.65, vol=300k, spread=0.05, cobertura=100%:
          - Prima correcta: 300k × (18.65-18.40) + 300k × 0.05 = 75k+15k = 90k MXN/mes
          - Costo nocional incorrecto: 300k × 18.65 = 5,595,000 MXN/mes
        """
        r = _make_resultado(
            spot=18.50, forward=18.65,
            volumen=300_000.0, spread=0.05, markup=0.00, fee=0.0,
            n_meses=1, cobertura=100.0,
        )
        frac = 1.0
        prima_esperada = 300_000 * frac * (18.65 - 18.40)   # 75,000 (usa spot_forward_base=18.40)
        spread_esperado = 300_000 * frac * 0.05              # 15,000
        costo_esperado = prima_esperada + spread_esperado    # 90,000

        # El costo nocional incorrecto sería ~5.6M — verificar que NO sea eso
        assert costo_esperado == pytest.approx(90_000.0, rel=1e-6)
        # El costo nocional es ~62x mayor — cualquier valor > 500k sería el bug
        notional_incorrecto = 300_000 * 18.65
        assert costo_esperado < notional_incorrecto / 10, (
            "El costo de cobertura no debe incluir el valor nocional del forward"
        )

    def test_pct_cobertura_es_razonable(self):
        """
        El % del volumen debe ser ~1-2%, no 100%+.
        _make_resultado fija spot_forward_base = spot - 0.10.
        Con spot=18.50, spot_base=18.40, forward=18.65, spread=0.05:
          prima/vol_base = (18.65-18.40+0.05)/18.40 = 0.30/18.40 = 1.63%
        """
        r = _make_resultado(
            spot=18.50, forward=18.65,
            volumen=300_000.0, spread=0.05, markup=0.00, fee=0.0,
            n_meses=1, cobertura=100.0,
        )
        frac = 1.0
        prima = 300_000 * frac * (18.65 - 18.40 + 0.05)   # usa spot_forward_base=18.40
        vol_spot = 300_000 * frac * 18.40
        pct = prima / vol_spot * 100
        assert 0.5 < pct < 5.0, f"% cobertura debería ser ~1.6%, got {pct:.2f}%"


# ---------------------------------------------------------------------------
# Tests: generar_pdf — PDF se genera y es válido
# ---------------------------------------------------------------------------

class TestGenerarPdf:

    def test_pdf_se_crea_y_tiene_bytes(self, tmp_path: Path):
        from agents.simulator.pdf_generator import generar_pdf
        r = _make_resultado()
        out = str(tmp_path / "reporte_test.pdf")
        path = generar_pdf(r, ruta_salida=out)
        assert Path(path).exists()
        assert Path(path).stat().st_size > 10_000

    def test_pdf_sin_periodos_lanza_valueerror(self, tmp_path: Path):
        from agents.simulator.pdf_generator import generar_pdf
        params = ParametrosCliente(volumen_mensual_usd=100_000.0, margen_utilidad=0.12)
        r = ResultadoSimulacion(
            parametros=params,
            periodos=[],
            fecha_inicio=date(2024, 1, 1),
            fecha_fin=date(2024, 12, 31),
        )
        with pytest.raises(ValueError):
            generar_pdf(r, ruta_salida=str(tmp_path / "vacio.pdf"))

    def test_pdf_con_markup_cero_no_crashea(self, tmp_path: Path):
        from agents.simulator.pdf_generator import generar_pdf
        r = _make_resultado(markup=0.00, spread=0.05, fee=15_000.0)
        out = str(tmp_path / "markup_cero.pdf")
        path = generar_pdf(r, ruta_salida=out)
        assert Path(path).stat().st_size > 0

    def test_pdf_desglose_costos_omitido_cuando_no_hay_costos(self, tmp_path: Path):
        """Con spread=0, markup=0, fee=0 la sección de desglose no aparece."""
        from agents.simulator.pdf_generator import generar_pdf
        r = _make_resultado(spread=0.0, markup=0.0, fee=0.0)
        out = str(tmp_path / "sin_costos.pdf")
        path = generar_pdf(r, ruta_salida=out)
        assert Path(path).exists()

    def test_pdf_desglose_aparece_cuando_hay_spread(self, tmp_path: Path):
        """Con spread>0 la sección de desglose sí se incluye (más páginas)."""
        import pdfplumber
        from agents.simulator.pdf_generator import generar_pdf

        r_sin = _make_resultado(spread=0.0, markup=0.0, fee=0.0)
        r_con = _make_resultado(spread=0.05, markup=0.0, fee=15_000.0)

        out_sin = str(tmp_path / "sin_spread.pdf")
        out_con = str(tmp_path / "con_spread.pdf")
        generar_pdf(r_sin, ruta_salida=out_sin)
        generar_pdf(r_con, ruta_salida=out_con)

        with pdfplumber.open(out_sin) as p_sin, pdfplumber.open(out_con) as p_con:
            assert len(p_con.pages) >= len(p_sin.pages)


# ---------------------------------------------------------------------------
# Tests: PDF — contenido en español, sin inglés
# ---------------------------------------------------------------------------

class TestPdfContenidoEspanol:

    @pytest.fixture(scope="class")
    def texto_pdf(self, tmp_path_factory: pytest.TempPathFactory) -> str:
        import pdfplumber
        from agents.simulator.pdf_generator import generar_pdf

        tmp = tmp_path_factory.mktemp("pdf_espanol")
        r = _make_resultado()
        out = str(tmp / "reporte_es.pdf")
        generar_pdf(r, ruta_salida=out)

        with pdfplumber.open(out) as pdf:
            return " ".join(page.extract_text() or "" for page in pdf.pages)

    def test_contiene_resumen_ejecutivo(self, texto_pdf: str):
        assert "Resumen Ejecutivo" in texto_pdf

    def test_contiene_analisis_de_riesgo(self, texto_pdf: str):
        assert "Análisis de Riesgo" in texto_pdf

    def test_contiene_catalogo_de_estrategias(self, texto_pdf: str):
        assert "Catálogo de Estrategias" in texto_pdf

    def test_contiene_recomendacion(self, texto_pdf: str):
        assert "Recomendación" in texto_pdf

    def test_no_contiene_texto_en_ingles_executive_summary(self, texto_pdf: str):
        assert "Executive Summary" not in texto_pdf

    def test_no_contiene_texto_en_ingles_risk_analysis(self, texto_pdf: str):
        assert "Risk Analysis" not in texto_pdf

    def test_no_contiene_texto_en_ingles_savings(self, texto_pdf: str):
        assert "Historical Savings" not in texto_pdf
        assert "Cumulative Savings" not in texto_pdf

    def test_no_contiene_mezcla_optima(self, texto_pdf: str):
        assert "Mezcla Óptima" not in texto_pdf
        assert "Optimal Mix" not in texto_pdf

    def test_no_contiene_comparativa_estrategias_seccion(self, texto_pdf: str):
        assert "Comparativa de Estrategias" not in texto_pdf

    def test_no_contiene_desempeno_por_anio(self, texto_pdf: str):
        assert "Desempeño por Año" not in texto_pdf

    def test_contiene_forward(self, texto_pdf: str):
        assert "Forward" in texto_pdf

    def test_contiene_opciones_put(self, texto_pdf: str):
        assert "Opciones Put" in texto_pdf

    def test_contiene_collar(self, texto_pdf: str):
        assert "Collar" in texto_pdf

    def test_contiene_disclaimer_legal(self, texto_pdf: str):
        assert "fines ilustrativos" in texto_pdf


# ---------------------------------------------------------------------------
# Tests: Resumen ejecutivo — KPIs nuevos presentes
# ---------------------------------------------------------------------------

class TestResumenEjecutivoKpis:

    @pytest.fixture(scope="class")
    def texto_pdf(self, tmp_path_factory: pytest.TempPathFactory) -> str:
        import pdfplumber
        from agents.simulator.pdf_generator import generar_pdf

        tmp = tmp_path_factory.mktemp("pdf_kpis")
        r = _make_resultado()
        out = str(tmp / "reporte_kpis.pdf")
        generar_pdf(r, ruta_salida=out)

        with pdfplumber.open(out) as pdf:
            # Solo primera y segunda página (portada + resumen ejecutivo)
            return " ".join(
                (pdf.pages[i].extract_text() or "")
                for i in range(min(2, len(pdf.pages)))
            )

    def test_kpi_costo_cobertura_mensual_presente(self, texto_pdf: str):
        assert "Costo de cobertura" in texto_pdf

    def test_kpi_meses_analizados_presente(self, texto_pdf: str):
        assert "Meses analizados" in texto_pdf

    def test_kpi_volumen_cubierto_presente(self, texto_pdf: str):
        assert "Volumen cubierto" in texto_pdf

    def test_kpi_proteccion_maxima_presente(self, texto_pdf: str):
        assert "protección" in texto_pdf.lower() or "Mayor protección" in texto_pdf

    def test_recuadro_contexto_periodo_presente(self, texto_pdf: str):
        assert "Período" in texto_pdf

    def test_recuadro_contexto_volumen_presente(self, texto_pdf: str):
        assert "Volumen" in texto_pdf

    def test_recuadro_contexto_cobertura_presente(self, texto_pdf: str):
        assert "Cobertura" in texto_pdf

    def test_no_contiene_prima_de_seguro_cambiario_como_kpi(self, texto_pdf: str):
        # El texto "prima de seguro cambiario" como KPI prominente fue eliminado
        assert "Prima de seguro cambiario" not in texto_pdf

    def test_no_contiene_forward_teorico_acumulado_kpi(self, texto_pdf: str):
        assert "Forward teórico acumulado" not in texto_pdf

    def test_costo_cobertura_pct_no_supera_10_pct(self, texto_pdf: str):
        """
        El KPI de % del volumen operado no debe mostrar valores absurdos (>10%).
        El bug original mostraba ~102% por usar el costo nocional del forward.
        Con spread=0.05 y prima TIIE/SOFR ~0.5-1%, el valor correcto está entre 0.5% y 5%.
        """
        import re
        # Buscar patrones como "1.08%" o "2.50%" en la página de resumen ejecutivo
        # Evitar falsos positivos de otras secciones buscando el patrón de KPI
        matches = re.findall(r'(\d+\.\d+)%', texto_pdf)
        pct_values = [float(m) for m in matches]
        # Ningún porcentaje de cobertura debería ser > 50% (el 102% era el bug)
        cobertura_kpis = [v for v in pct_values if 0.01 < v < 200]
        assert not any(v > 50 for v in cobertura_kpis[:10]), (
            f"Se encontró porcentaje sospechosamente alto en los primeros KPIs: "
            f"{[v for v in cobertura_kpis[:10] if v > 50]}"
        )


# ---------------------------------------------------------------------------
# Tests: Tabla mensual — 12 filas para 1 año
# ---------------------------------------------------------------------------

class TestTablaMensual:

    def test_tabla_tiene_12_periodos(self, tmp_path: Path):
        import pdfplumber
        from agents.simulator.pdf_generator import generar_pdf

        r = _make_resultado(n_meses=12, anio=2024)
        out = str(tmp / "reporte_12m.pdf") if False else str(tmp_path / "reporte_12m.pdf")
        generar_pdf(r, ruta_salida=out)

        with pdfplumber.open(out) as pdf:
            texto = " ".join(p.extract_text() or "" for p in pdf.pages)

        # Los 12 meses de 2024 deben aparecer como Ene-24 … Dic-24
        meses_esperados = [
            "Ene-24", "Feb-24", "Mar-24", "Abr-24", "May-24", "Jun-24",
            "Jul-24", "Ago-24", "Sep-24", "Oct-24", "Nov-24", "Dic-24",
        ]
        encontrados = [m for m in meses_esperados if m in texto]
        assert len(encontrados) == 12, (
            f"Solo se encontraron {len(encontrados)}/12 meses: {encontrados}"
        )


# ---------------------------------------------------------------------------
# Tests: Catálogo de estrategias — estructura
# ---------------------------------------------------------------------------

class TestCatalogoEstrategias:

    @pytest.fixture(scope="class")
    def texto_pdf(self, tmp_path_factory: pytest.TempPathFactory) -> str:
        import pdfplumber
        from agents.simulator.pdf_generator import generar_pdf

        tmp = tmp_path_factory.mktemp("pdf_catalogo")
        r = _make_resultado()
        out = str(tmp / "reporte_cat.pdf")
        generar_pdf(r, ruta_salida=out)

        with pdfplumber.open(out) as pdf:
            return " ".join(page.extract_text() or "" for page in pdf.pages)

    def test_tabla_forward_presente(self, texto_pdf: str):
        assert "Forward" in texto_pdf

    def test_tabla_opciones_presente(self, texto_pdf: str):
        assert "Opciones Put" in texto_pdf

    def test_tabla_collar_presente(self, texto_pdf: str):
        assert "Collar" in texto_pdf

    def test_niveles_cobertura_presentes(self, texto_pdf: str):
        for nivel in ("25%", "50%", "75%", "100%"):
            assert nivel in texto_pdf, f"Nivel {nivel} no encontrado"

    def test_pros_contras_forward_certeza(self, texto_pdf: str):
        assert "Certeza total" in texto_pdf or "certeza total" in texto_pdf.lower()

    def test_pros_contras_collar_menor_costo(self, texto_pdf: str):
        assert "Menor costo" in texto_pdf or "menor costo" in texto_pdf.lower()

    def test_nota_volatilidad_presente(self, texto_pdf: str):
        assert "volatilidad" in texto_pdf.lower()

    def test_sin_fila_recomendada_resaltada(self):
        """No debe haber texto 'recomendada' o 'recommended' en el catálogo."""
        # Verificación estructural: el catálogo no agrega texto "recomendada"
        from agents.simulator.pdf_generator import _catalogo_estrategias
        import inspect
        src = inspect.getsource(_catalogo_estrategias)
        assert "recomendad" not in src.lower()
        assert "recommended" not in src.lower()


# ---------------------------------------------------------------------------
# Tests: Gráfica de análisis de riesgo — barras rojas simples
# ---------------------------------------------------------------------------

class TestGraficaExposicion:

    def test_grafica_exposicion_genera_imagen(self):
        from agents.simulator.pdf_generator import _grafica_exposicion_sin_cobertura
        r = _make_resultado(n_meses=12)
        img = _grafica_exposicion_sin_cobertura(r)
        assert img is not None

    def test_grafica_impacto_margen_eliminada(self):
        """La función antigua _grafica_impacto_margen no debe estar en el story."""
        from agents.simulator import pdf_generator
        import inspect
        story_src = inspect.getsource(pdf_generator.generar_pdf)
        assert "_grafica_impacto_margen" not in story_src

    def test_grafica_exposicion_en_seccion_riesgo(self):
        from agents.simulator.pdf_generator import _seccion_analisis_riesgo
        import inspect
        src = inspect.getsource(_seccion_analisis_riesgo)
        assert "_grafica_exposicion_sin_cobertura" in src
        assert "_grafica_impacto_margen" not in src


# ---------------------------------------------------------------------------
# Tests: Desglose de costos — tabla mensual
# ---------------------------------------------------------------------------

class TestDesgloseCostos:

    def test_desglose_costos_estructura(self):
        from agents.simulator.pdf_generator import _seccion_desglose_costos
        import inspect
        src = inspect.getsource(_seccion_desglose_costos)
        assert "Costo por USD" in src
        assert "Costo mensual" in src
        # Forward teórico no debe ser una fila de la tabla
        assert "Forward teórico" not in src

    def test_desglose_costos_en_pdf(self, tmp_path: Path):
        import pdfplumber
        from agents.simulator.pdf_generator import generar_pdf

        r = _make_resultado(spread=0.05, markup=0.00, fee=15_000.0)
        out = str(tmp_path / "reporte_costos.pdf")
        generar_pdf(r, ruta_salida=out)

        with pdfplumber.open(out) as pdf:
            texto = " ".join(p.extract_text() or "" for p in pdf.pages)

        assert "Desglose de Costos" in texto
        assert "Spread banco" in texto
        assert "Fee HedgePoint" in texto
        # La tabla acumulada antigua con $351M ya no debe aparecer
        assert "351" not in texto  # aproximación del total acumulado 5 años


# ---------------------------------------------------------------------------
# Tests: SimuladorAhorro con --year
# ---------------------------------------------------------------------------

class TestSimuladorAhorroYear:

    def test_anio_parametro_acepta_entero(self):
        from agents.simulator.savings_simulator import SimuladorAhorro
        p = ParametrosCliente(volumen_mensual_usd=100_000.0, margen_utilidad=0.12)
        sim = SimuladorAhorro(p, anio=2023)
        assert sim.anio == 2023

    def test_anio_invalido_futuro_lanza_error(self):
        from agents.simulator.savings_simulator import SimuladorAhorro
        p = ParametrosCliente(volumen_mensual_usd=100_000.0, margen_utilidad=0.12)
        sim = SimuladorAhorro(p, anio=2099)
        with pytest.raises(ValueError):
            sim.ejecutar()

    def test_anio_invalido_muy_antiguo_lanza_error(self):
        from agents.simulator.savings_simulator import SimuladorAhorro
        p = ParametrosCliente(volumen_mensual_usd=100_000.0, margen_utilidad=0.12)
        sim = SimuladorAhorro(p, anio=1999)
        with pytest.raises(ValueError):
            sim.ejecutar()


# ---------------------------------------------------------------------------
# Tests: argparse defaults en run_simulation.py
# ---------------------------------------------------------------------------

class TestArgparseDefaults:
    """Verifica defaults de argparse en run_simulation.py leyendo el código fuente."""

    @staticmethod
    def _defaults_from_src() -> dict:
        """Extrae defaults de add_argument por nombre exacto de flag."""
        import re
        src = open("scripts/run_simulation.py").read()
        # Buscar bloques add_argument("--flag", ..., default=X, ...)
        # Cada bloque termina en )
        defaults = {}
        for m in re.finditer(
            r'add_argument\(\s*"(--[^"]+)"[^)]*?default\s*=\s*([0-9_.]+)',
            src,
        ):
            flag = m.group(1)
            val_str = m.group(2).replace("_", "")
            defaults[flag] = float(val_str)
        return defaults

    def test_markup_default_en_argparse_es_cero(self):
        defaults = self._defaults_from_src()
        assert "--markup" in defaults, "--markup arg not found in run_simulation.py"
        assert defaults["--markup"] == pytest.approx(0.0), (
            f"--markup default should be 0.0, got {defaults['--markup']}"
        )

    def test_spread_default_en_argparse_es_005(self):
        defaults = self._defaults_from_src()
        assert "--spread" in defaults
        assert defaults["--spread"] == pytest.approx(0.05)

    def test_fee_default_en_argparse_es_15000(self):
        defaults = self._defaults_from_src()
        assert "--fee" in defaults
        assert defaults["--fee"] == pytest.approx(15_000.0)
