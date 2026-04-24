"""
Motor de simulación de escenarios hipotéticos para HedgePoint MX.

Recibe un ScenarioInput estructurado y calcula el impacto financiero en la
posición de un importador mexicano bajo tres estrategias de cobertura:
forward, opciones y collar.

El módulo NO realiza parseo de lenguaje natural — esa responsabilidad recae
en HedgePointLLM.parse_scenario() en core/llm_client.py. Esto mantiene el
engine completamente testeable sin dependencia de la Claude API.

Uso típico:
    from agents.simulator.scenario_engine import ScenarioEngine, ScenarioInput

    engine = ScenarioEngine()

    # Escenario spot fijo
    resultado = engine.run(ScenarioInput(tipo="spot_fijo", valor=22.0))

    # Escenario histórico
    resultado = engine.run_historico("covid_2020", volumen=300_000)

    # Múltiples escenarios
    resultados = engine.comparar_escenarios([
        ScenarioInput(tipo="cambio_porcentual", valor=10.0),
        ScenarioInput(tipo="cambio_porcentual", valor=20.0),
    ])
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.models.pricing import (
    calcular_forward,
    calcular_opcion_gk,
    simular_monte_carlo,
    TIIE_ANUAL,
    SOFR_ANUAL,
)
from core.data.market_data import fetch_usdmxn_banxico

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de configuración
# ---------------------------------------------------------------------------

_SPREAD_BANCO_DEFAULT: float = 0.05      # MXN/USD spread bancario en forward
_MARKUP_HP_DEFAULT: float = 0.04         # MXN/USD markup HedgePoint
_FEE_MENSUAL_DEFAULT: float = 15_000.0   # MXN fee mensual de consultoría
_SPOT_FALLBACK: float = 20.50            # Spot de respaldo si Banxico no responde
_COLLAR_FLOOR_PCT: float = 0.97          # Put vendido OTM al -3% del spot

# Umbral de movimiento (%) para recomendar forward vs collar
_UMBRAL_DEPRECIACION_FORWARD: float = 5.0

# ---------------------------------------------------------------------------
# Escenarios históricos pre-armados
# ---------------------------------------------------------------------------

ESCENARIOS_HISTORICOS: dict[str, dict] = {
    "crisis_2008": {
        "nombre": "Crisis Financiera 2008",
        "spot_antes": 10.50,
        "spot_despues": 14.50,
        "cambio_pct": 38.1,
        "descripcion": (
            "Quiebra de Lehman Brothers. Peso se depreció 38% en meses."
        ),
    },
    "trump_2016": {
        "nombre": "Elección Trump Nov 2016",
        "spot_antes": 18.50,
        "spot_despues": 21.90,
        "cambio_pct": 18.4,
        "descripcion": (
            "Victoria de Trump. Peso cayó a mínimos históricos."
        ),
    },
    "covid_2020": {
        "nombre": "COVID-19 Mar 2020",
        "spot_antes": 18.80,
        "spot_despues": 25.30,
        "cambio_pct": 34.6,
        "descripcion": (
            "Pandemia global. Peso se depreció 35% en semanas."
        ),
    },
    "super_peso_2023": {
        "nombre": "Super Peso 2023",
        "spot_antes": 19.80,
        "spot_despues": 16.90,
        "cambio_pct": -14.6,
        "descripcion": (
            "Apreciación histórica del peso por nearshoring y tasas altas."
        ),
    },
    "aranceles_trump_2025": {
        "nombre": "Aranceles Trump 2025",
        "spot_antes": 17.20,
        "spot_despues": 21.00,
        "cambio_pct": 22.1,
        "descripcion": (
            "Amenaza de aranceles del 25%. Peso se deprecia fuerte."
        ),
    },
}


# ---------------------------------------------------------------------------
# Dataclasses de entrada y salida
# ---------------------------------------------------------------------------

@dataclass
class ScenarioInput:
    """
    Parámetros estructurados de un escenario hipotético.

    Producido por HedgePointLLM.parse_scenario() o construido directamente
    en tests y scripts.

    Campos
    ------
    tipo : {"spot_fijo", "cambio_porcentual", "historico"}
        Cómo interpretar `valor`:
        - "spot_fijo": `valor` es el spot objetivo en MXN/USD (p.ej. 22.0).
        - "cambio_porcentual": `valor` es el cambio en % respecto al spot
          actual (p.ej. 10.0 → +10%).  Valores negativos = apreciación.
        - "historico": usa `nombre_evento` para buscar en ESCENARIOS_HISTORICOS;
          `valor` se ignora.
    valor : float
        Interpretado según `tipo`. Para "historico" puede ser 0.0.
    plazo_meses : int
        Horizonte de la posición en meses. Default: 3.
    volumen_mensual_usd : float
        Volumen mensual de compra de divisas en USD. Default: 500 000.
    margen_utilidad : float
        Margen de utilidad como decimal (p.ej. 0.08 = 8%). Default: 0.08.
    spot_actual : float | None
        Tipo de cambio spot actual. Si es None, se consulta Banxico.
    nombre_evento : str | None
        Clave en ESCENARIOS_HISTORICOS (solo para tipo="historico").
    volatilidad : float
        Volatilidad implícita anualizada para pricing GK. Default: 0.12.
    """

    tipo: Literal["spot_fijo", "cambio_porcentual", "historico"]
    valor: float
    plazo_meses: int = 3
    volumen_mensual_usd: float = 500_000.0
    margen_utilidad: float = 0.08
    spot_actual: float | None = None
    nombre_evento: str | None = None
    volatilidad: float = 0.12


@dataclass
class ScenarioResult:
    """
    Resultado completo del análisis de un escenario hipotético.

    Campos de impacto
    -----------------
    Cada dict de impacto (`impacto_sin_cobertura`, `impacto_forward`,
    `impacto_opciones`, `impacto_collar`) contiene las métricas financieras
    específicas de esa estrategia.  Ver ScenarioEngine.run() para el detalle
    de cada clave.

    mejor_estrategia : {"forward", "opciones", "collar"}
        Estrategia con el menor costo neto bajo el escenario dado.
    resumen : str
        Una línea en español con el diagnóstico ejecutivo.
    evento_historico : dict | None
        Metadatos del evento histórico si tipo=="historico", None en otro caso.
    """

    input: ScenarioInput
    spot_actual: float
    spot_hipotetico: float
    movimiento_pct: float
    direccion: str                    # "depreciacion" o "apreciacion"
    impacto_sin_cobertura: dict
    impacto_forward: dict
    impacto_opciones: dict
    impacto_collar: dict
    mejor_estrategia: str
    resumen: str
    evento_historico: dict | None = None


# ---------------------------------------------------------------------------
# Motor principal
# ---------------------------------------------------------------------------

class ScenarioEngine:
    """
    Motor de cálculo de escenarios hipotéticos USD/MXN.

    Orquesta los modelos de pricing (forward, GK, Monte Carlo) para cuantificar
    el impacto de un escenario sobre la posición de un importador mexicano.

    No tiene estado persistente — cada llamada a run() es independiente.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _obtener_spot_actual(self) -> float:
        """
        Consulta el tipo de cambio FIX más reciente desde Banxico.

        Si la API no está disponible (credenciales faltantes, timeout, etc.)
        retorna el valor de respaldo _SPOT_FALLBACK con un warning.

        Returns
        -------
        float
            Último tipo de cambio FIX disponible.
        """
        try:
            df = fetch_usdmxn_banxico(days=5)
            spot = float(df["tipo_cambio"].iloc[-1])
            self._logger.info("Spot obtenido de Banxico: %.4f", spot)
            return spot
        except Exception as exc:
            self._logger.warning(
                "No se pudo obtener spot de Banxico (%s). "
                "Usando fallback: %.2f",
                exc,
                _SPOT_FALLBACK,
            )
            return _SPOT_FALLBACK

    def _resolver_spot_hipotetico(
        self,
        inp: ScenarioInput,
        spot_actual: float,
    ) -> tuple[float, dict | None]:
        """
        Calcula el spot hipotético y los metadatos del evento histórico (si aplica).

        Returns
        -------
        (spot_hipotetico, evento_historico)
        """
        if inp.tipo == "spot_fijo":
            return inp.valor, None

        if inp.tipo == "cambio_porcentual":
            spot_h = spot_actual * (1.0 + inp.valor / 100.0)
            return spot_h, None

        # tipo == "historico"
        if not inp.nombre_evento:
            raise ValueError(
                "ScenarioInput.nombre_evento es requerido para tipo='historico'."
            )
        if inp.nombre_evento not in ESCENARIOS_HISTORICOS:
            claves = ", ".join(ESCENARIOS_HISTORICOS)
            raise ValueError(
                f"Evento histórico desconocido: '{inp.nombre_evento}'. "
                f"Opciones disponibles: {claves}."
            )
        evento = ESCENARIOS_HISTORICOS[inp.nombre_evento]
        spot_h = spot_actual * (1.0 + evento["cambio_pct"] / 100.0)
        return spot_h, evento

    def _calcular_sin_cobertura(
        self,
        spot_actual: float,
        spot_h: float,
        volumen_mensual: float,
        plazo_meses: int,
        margen: float,
    ) -> dict:
        """Impacto de no tener cobertura cambiaria."""
        volumen_total_usd = volumen_mensual * plazo_meses
        exposicion_actual_mxn = volumen_total_usd * spot_actual
        exposicion_hipotetica_mxn = volumen_total_usd * spot_h
        diferencia_mxn = volumen_total_usd * (spot_h - spot_actual)
        # Cuántos puntos porcentuales del margen consume el movimiento
        impacto_margen_pct = (
            diferencia_mxn / exposicion_actual_mxn * 100.0 / margen
            if margen > 0 and exposicion_actual_mxn > 0
            else 0.0
        )
        return {
            "exposicion_total_mxn": exposicion_hipotetica_mxn,
            "diferencia_vs_actual_mxn": diferencia_mxn,
            "impacto_margen_pct": impacto_margen_pct,
            "volumen_total_usd": volumen_total_usd,
        }

    def _calcular_forward(
        self,
        spot_actual: float,
        spot_h: float,
        volumen_mensual: float,
        plazo_meses: int,
        tiie: float,
        sofr: float,
    ) -> dict:
        """
        Impacto con cobertura forward.

        Usa el forward teórico (paridad de tasas) y aplica spread bancario,
        markup HedgePoint y fee mensual de consultoría.
        """
        plazo_dias = plazo_meses * 30
        volumen_total_usd = volumen_mensual * plazo_meses

        fwd = calcular_forward(spot_actual, plazo_dias, tiie, sofr)
        tasa_forward = fwd.forward

        # Costo total = forward + spread banco + markup HP + fees
        costo_tc_efectivo = tasa_forward + _SPREAD_BANCO_DEFAULT + _MARKUP_HP_DEFAULT
        costo_cobertura_mxn = (
            volumen_total_usd * costo_tc_efectivo
            + _FEE_MENSUAL_DEFAULT * plazo_meses
        )

        # Costo sin cobertura al spot hipotético
        costo_sin_cobertura_mxn = volumen_total_usd * spot_h

        ahorro_vs_sin_cobertura_mxn = costo_sin_cobertura_mxn - costo_cobertura_mxn
        diferencia_base = volumen_total_usd * (spot_h - spot_actual)
        proteccion_pct = (
            ahorro_vs_sin_cobertura_mxn / diferencia_base * 100.0
            if diferencia_base != 0
            else 0.0
        )

        return {
            "tasa_forward": tasa_forward,
            "plazo_dias": plazo_dias,
            "costo_tc_efectivo": costo_tc_efectivo,
            "costo_cobertura_mxn": costo_cobertura_mxn,
            "ahorro_vs_sin_cobertura_mxn": ahorro_vs_sin_cobertura_mxn,
            "proteccion_pct": proteccion_pct,
            "spread_banco": _SPREAD_BANCO_DEFAULT,
            "markup_hp": _MARKUP_HP_DEFAULT,
            "fee_total_mxn": _FEE_MENSUAL_DEFAULT * plazo_meses,
        }

    def _calcular_opciones(
        self,
        spot_actual: float,
        spot_h: float,
        volumen_mensual: float,
        plazo_meses: int,
        volatilidad: float,
        tiie: float,
        sofr: float,
    ) -> dict:
        """
        Impacto con cobertura mediante put ATM (strike = spot_actual).

        El importador compra puts para protegerse de depreciación.
        Solo pierde la prima si el peso se aprecia (no ejerce la opción).
        """
        plazo_dias = plazo_meses * 30
        volumen_total_usd = volumen_mensual * plazo_meses

        opcion = calcular_opcion_gk(
            spot=spot_actual,
            strike=spot_actual,   # ATM
            dias=plazo_dias,
            vol=volatilidad,
            tiie=tiie,
            sofr=sofr,
        )
        prima_put_mxn_usd = opcion.put
        prima_total_mxn = prima_put_mxn_usd * volumen_total_usd

        # Con depreciación: la put se ejerce, el importador paga spot_actual
        costo_con_opcion_mxn = volumen_total_usd * spot_actual + prima_total_mxn
        costo_sin_cobertura_mxn = volumen_total_usd * spot_h

        ahorro_vs_sin_cobertura_mxn = costo_sin_cobertura_mxn - costo_con_opcion_mxn
        # Pérdida máxima: solo la prima (si el peso se aprecia mucho)
        perdida_maxima_mxn = prima_total_mxn

        return {
            "prima_put_mxn_usd": prima_put_mxn_usd,
            "prima_total_mxn": prima_total_mxn,
            "perdida_maxima_mxn": perdida_maxima_mxn,
            "costo_con_opcion_mxn": costo_con_opcion_mxn,
            "ahorro_vs_sin_cobertura_mxn": ahorro_vs_sin_cobertura_mxn,
            "delta_put": opcion.delta_put,
            "vega": opcion.vega,
            "strike_put": spot_actual,
        }

    def _calcular_collar(
        self,
        spot_actual: float,
        spot_h: float,
        volumen_mensual: float,
        plazo_meses: int,
        volatilidad: float,
        tiie: float,
        sofr: float,
    ) -> dict:
        """
        Impacto con estrategia collar.

        Estructura:
        - Compra call ATM (strike = spot_actual): protección ante depreciación.
        - Vende put OTM -3% (strike = spot_actual * 0.97): financia la prima.
        La prima neta es la diferencia call - put vendido.
        """
        plazo_dias = plazo_meses * 30
        volumen_total_usd = volumen_mensual * plazo_meses
        strike_floor = spot_actual * _COLLAR_FLOOR_PCT

        # Call comprado ATM
        opcion_call = calcular_opcion_gk(
            spot=spot_actual,
            strike=spot_actual,
            dias=plazo_dias,
            vol=volatilidad,
            tiie=tiie,
            sofr=sofr,
        )
        # Put vendido OTM
        opcion_put = calcular_opcion_gk(
            spot=spot_actual,
            strike=strike_floor,
            dias=plazo_dias,
            vol=volatilidad,
            tiie=tiie,
            sofr=sofr,
        )

        prima_call_comprado = opcion_call.call
        prima_put_vendido = opcion_put.put
        prima_neta_mxn_usd = prima_call_comprado - prima_put_vendido
        costo_neto_mxn = prima_neta_mxn_usd * volumen_total_usd

        # Con depreciación: se ejerce el call, el importador compra a spot_actual
        costo_con_collar_mxn = volumen_total_usd * spot_actual + costo_neto_mxn
        costo_sin_cobertura_mxn = volumen_total_usd * spot_h

        ahorro_vs_sin_cobertura_mxn = costo_sin_cobertura_mxn - costo_con_collar_mxn

        return {
            "prima_call_comprado": prima_call_comprado,
            "prima_put_vendido": prima_put_vendido,
            "prima_neta_mxn_usd": prima_neta_mxn_usd,
            "costo_neto_mxn": costo_neto_mxn,
            "costo_con_collar_mxn": costo_con_collar_mxn,
            "ahorro_vs_sin_cobertura_mxn": ahorro_vs_sin_cobertura_mxn,
            "proteccion_desde": spot_actual,   # cap: no paga más de este spot
            "limite_beneficio": strike_floor,  # floor: mínimo si el peso se aprecia
        }

    @staticmethod
    def _determinar_mejor_estrategia(
        movimiento_pct: float,
        direccion: str,
        impacto_forward: dict,
        impacto_opciones: dict,
        impacto_collar: dict,
    ) -> str:
        """
        Determina la mejor estrategia según el escenario.

        Regla de negocio:
        - Apreciación del peso → opciones (solo pierde la prima; beneficio si no ejerce).
        - Depreciación ≤ 5%   → collar (cobertura parcial con prima reducida).
        - Depreciación > 5%   → forward (máxima certeza de precio, costo predecible).
        """
        if direccion == "apreciacion":
            return "opciones"
        if movimiento_pct <= _UMBRAL_DEPRECIACION_FORWARD:
            return "collar"
        return "forward"

    @staticmethod
    def _generar_resumen(
        spot_actual: float,
        spot_h: float,
        movimiento_pct: float,
        direccion: str,
        mejor_estrategia: str,
        impacto_sin_cobertura: dict,
        impacto_forward: dict,
        impacto_opciones: dict,
        impacto_collar: dict,
    ) -> str:
        """Genera una línea de resumen ejecutivo en español."""
        signo = "+" if movimiento_pct >= 0 else ""
        diferencia = impacto_sin_cobertura["diferencia_vs_actual_mxn"]

        if direccion == "depreciacion":
            perdida_str = f"${diferencia:,.0f} MXN adicionales"
            if mejor_estrategia == "forward":
                ahorro = impacto_forward["ahorro_vs_sin_cobertura_mxn"]
                costo = impacto_forward["costo_cobertura_mxn"]
                return (
                    f"Si el dólar sube a ${spot_h:.2f} ({signo}{movimiento_pct:.1f}%), "
                    f"pagarías {perdida_str} sin cobertura. "
                    f"Con forward, el costo se limita a ${costo:,.0f} MXN "
                    f"(ahorro de ${ahorro:,.0f} MXN)."
                )
            elif mejor_estrategia == "collar":
                costo = impacto_collar["costo_con_collar_mxn"]
                ahorro = impacto_collar["ahorro_vs_sin_cobertura_mxn"]
                return (
                    f"Si el dólar sube a ${spot_h:.2f} ({signo}{movimiento_pct:.1f}%), "
                    f"pagarías {perdida_str} sin cobertura. "
                    f"Un collar limita el costo a ${costo:,.0f} MXN "
                    f"(ahorro estimado de ${ahorro:,.0f} MXN)."
                )
            else:
                prima = impacto_opciones["prima_total_mxn"]
                ahorro = impacto_opciones["ahorro_vs_sin_cobertura_mxn"]
                return (
                    f"Si el dólar sube a ${spot_h:.2f} ({signo}{movimiento_pct:.1f}%), "
                    f"pagarías {perdida_str} sin cobertura. "
                    f"Con opciones (prima ${prima:,.0f} MXN), "
                    f"ahorrarías ${ahorro:,.0f} MXN."
                )
        else:
            beneficio = abs(diferencia)
            prima = impacto_opciones["prima_total_mxn"]
            return (
                f"Si el peso se aprecia a ${spot_h:.2f} ({signo}{movimiento_pct:.1f}%), "
                f"pagarías ${beneficio:,.0f} MXN menos en divisas. "
                f"Opciones (prima ${prima:,.0f} MXN) te permiten beneficiarte "
                f"de la apreciación si no ejerces."
            )

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def run(self, scenario_input: ScenarioInput) -> ScenarioResult:
        """
        Ejecuta el cálculo completo para un escenario estructurado.

        Pasos:
        1. Resuelve spot actual (Banxico o campo spot_actual del input).
        2. Resuelve spot hipotético según tipo_escenario.
        3. Calcula impacto sin cobertura, forward, opciones y collar.
        4. Determina mejor estrategia y genera resumen.

        Parameters
        ----------
        scenario_input : ScenarioInput
            Escenario estructurado. Puede venir de HedgePointLLM.parse_scenario()
            o construirse directamente.

        Returns
        -------
        ScenarioResult
            Resultado completo con impactos por estrategia.

        Raises
        ------
        ValueError
            Si el tipo de escenario es inválido o falta nombre_evento para "historico".
        """
        inp = scenario_input
        tiie = TIIE_ANUAL
        sofr = SOFR_ANUAL

        # 1. Spot actual
        spot_actual = (
            inp.spot_actual
            if inp.spot_actual is not None
            else self._obtener_spot_actual()
        )

        # 2. Spot hipotético y metadatos históricos
        spot_h, evento_historico = self._resolver_spot_hipotetico(inp, spot_actual)

        # 3. Movimiento
        movimiento_pct = (spot_h - spot_actual) / spot_actual * 100.0
        direccion = "apreciacion" if spot_h < spot_actual else "depreciacion"

        self._logger.info(
            "Escenario: tipo=%s | spot_actual=%.4f | spot_hipotetico=%.4f | "
            "movimiento=%.2f%% (%s)",
            inp.tipo, spot_actual, spot_h, movimiento_pct, direccion,
        )

        # 4. Impactos
        sin_cob = self._calcular_sin_cobertura(
            spot_actual, spot_h,
            inp.volumen_mensual_usd, inp.plazo_meses, inp.margen_utilidad,
        )
        fwd = self._calcular_forward(
            spot_actual, spot_h,
            inp.volumen_mensual_usd, inp.plazo_meses, tiie, sofr,
        )
        opc = self._calcular_opciones(
            spot_actual, spot_h,
            inp.volumen_mensual_usd, inp.plazo_meses, inp.volatilidad, tiie, sofr,
        )
        collar = self._calcular_collar(
            spot_actual, spot_h,
            inp.volumen_mensual_usd, inp.plazo_meses, inp.volatilidad, tiie, sofr,
        )

        # 5. Mejor estrategia y resumen
        mejor = self._determinar_mejor_estrategia(
            abs(movimiento_pct), direccion, fwd, opc, collar,
        )
        resumen = self._generar_resumen(
            spot_actual, spot_h, movimiento_pct, direccion,
            mejor, sin_cob, fwd, opc, collar,
        )

        return ScenarioResult(
            input=inp,
            spot_actual=spot_actual,
            spot_hipotetico=spot_h,
            movimiento_pct=movimiento_pct,
            direccion=direccion,
            impacto_sin_cobertura=sin_cob,
            impacto_forward=fwd,
            impacto_opciones=opc,
            impacto_collar=collar,
            mejor_estrategia=mejor,
            resumen=resumen,
            evento_historico=evento_historico,
        )

    def run_historico(
        self,
        nombre_evento: str,
        volumen: float = 500_000.0,
        margen: float = 0.08,
        plazo_meses: int = 3,
        volatilidad: float = 0.12,
    ) -> ScenarioResult:
        """
        Atajo para correr un escenario histórico pre-armado.

        Parameters
        ----------
        nombre_evento : str
            Clave en ESCENARIOS_HISTORICOS (p.ej. "covid_2020").
        volumen : float
            Volumen mensual en USD. Default: 500 000.
        margen : float
            Margen de utilidad como decimal. Default: 0.08.
        plazo_meses : int
            Horizonte en meses. Default: 3.
        volatilidad : float
            Volatilidad implícita anualizada. Default: 0.12.

        Returns
        -------
        ScenarioResult
        """
        inp = ScenarioInput(
            tipo="historico",
            valor=0.0,
            nombre_evento=nombre_evento,
            volumen_mensual_usd=volumen,
            margen_utilidad=margen,
            plazo_meses=plazo_meses,
            volatilidad=volatilidad,
        )
        return self.run(inp)

    def comparar_escenarios(
        self,
        inputs: list[ScenarioInput],
    ) -> list[ScenarioResult]:
        """
        Ejecuta múltiples escenarios y retorna la lista de resultados en el
        mismo orden que `inputs`.

        Útil para generar tablas comparativas en la CLI y el PDF.

        Parameters
        ----------
        inputs : list[ScenarioInput]
            Lista de escenarios a evaluar.

        Returns
        -------
        list[ScenarioResult]
        """
        resultados: list[ScenarioResult] = []
        for i, inp in enumerate(inputs):
            try:
                resultados.append(self.run(inp))
            except Exception as exc:
                self._logger.error(
                    "Error en escenario %d (%s): %s", i, inp.tipo, exc
                )
                raise
        return resultados

    def listar_historicos(self) -> dict[str, dict]:
        """
        Retorna el catálogo completo de escenarios históricos pre-armados.

        Returns
        -------
        dict
            Copia de ESCENARIOS_HISTORICOS con clave → metadatos.
        """
        return dict(ESCENARIOS_HISTORICOS)
