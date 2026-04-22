"""
Simulador de ahorro por cobertura forward USD/MXN para HedgePoint MX.

Calcula cuánto habría ahorrado un importador mexicano si hubiera usado
forwards a 30 días en lugar de comprar divisas al tipo de cambio spot,
durante los últimos 2 años.

Uso:
    from agents.simulator.savings_simulator import SimuladorAhorro, ParametrosCliente

    params = ParametrosCliente(volumen_mensual_usd=300_000, margen_utilidad=0.12)
    sim = SimuladorAhorro(params)
    resultado = sim.ejecutar()
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import replace as _dataclass_replace
import logging
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.database import get_connection, DB_PATH
from core.models.pricing import calcular_forward, TIIE_ANUAL, SOFR_ANUAL

logger = logging.getLogger(__name__)

FrecuenciaCompra = Literal["semanal", "quincenal", "mensual"]


@dataclass
class ParametrosCliente:
    """Parámetros de entrada del cliente para la simulación."""
    volumen_mensual_usd: float
    """Volumen mensual total de compras en USD."""

    margen_utilidad: float
    """Margen de utilidad como decimal (p.ej. 0.12 para 12%)."""

    frecuencia: FrecuenciaCompra = "mensual"
    """Frecuencia de compra de divisas."""

    plazo_forward_dias: int = 30
    """Plazo del forward en días (default: 30)."""

    tiie: float = TIIE_ANUAL
    """Tasa TIIE anualizada usada en el pricing (decimal)."""

    sofr: float = SOFR_ANUAL
    """Tasa SOFR anualizada usada en el pricing (decimal)."""

    spread_banco: float = 0.05
    """Spread del banco en MXN/USD cobrado al ejecutar el forward (default: 5 centavos)."""

    markup_hedgepoint: float = 0.04
    """Markup de HedgePoint en MXN/USD (default: 4 centavos)."""

    fee_mensual: float = 15_000.0
    """Fee fijo mensual de consultoría HedgePoint en MXN (default: $15,000)."""

    cobertura_pct: float = 100.0
    """Porcentaje del volumen mensual que se cubre con forward (default: 100%).
    El volumen restante (1 - cobertura_pct/100) se compra al spot del mes."""

    def volumen_por_compra(self) -> float:
        """Calcula el volumen en USD por cada compra según la frecuencia."""
        if self.frecuencia == "semanal":
            return self.volumen_mensual_usd / 4.33
        elif self.frecuencia == "quincenal":
            return self.volumen_mensual_usd / 2.0
        else:  # mensual
            return self.volumen_mensual_usd


@dataclass
class ResultadoPeriodo:
    """Resultado de la simulación para un período mensual."""
    periodo: str
    """Mes en formato YYYY-MM."""

    fecha_compra: date
    """Fecha efectiva de la compra de divisas."""

    spot: float
    """Tipo de cambio spot USD/MXN en la fecha de compra."""

    forward_30d: float
    """Precio forward teórico a 30 días calculado un mes antes."""

    fecha_forward: date
    """Fecha en que se habría pactado el forward (spot_fecha - 30 días)."""

    spot_forward_base: float
    """Tipo de cambio spot al momento de pactar el forward."""

    volumen_usd: float
    """Volumen en USD comprado en este período."""

    costo_spot_mxn: float
    """Costo total en MXN pagando al tipo de cambio spot."""

    costo_forward_mxn: float
    """Costo total en MXN si se hubiera usado el forward (incluye spread, markup y fee)."""

    # --- Desglose del costo forward ---
    costo_forward_teorico_mxn: float
    """Componente del forward teórico: volumen_usd * tc_forward."""

    costo_spread_banco_mxn: float
    """Componente del spread bancario: volumen_usd * spread_banco."""

    costo_markup_hp_mxn: float
    """Componente del markup HedgePoint: volumen_usd * markup_hedgepoint."""

    costo_fee_hp_mxn: float
    """Fee fijo mensual de consultoría HedgePoint."""

    ahorro_mxn: float
    """Ahorro en MXN (positivo = forward fue mejor; negativo = spot fue mejor)."""

    ahorro_porcentaje: float
    """Ahorro como porcentaje del costo spot."""


@dataclass
class ResumenAnual:
    """Resumen agregado de la simulación para un año calendario."""
    anio: int
    """Año calendario."""

    meses: int
    """Cantidad de meses simulados en el año."""

    ahorro_total_mxn: float
    """Ahorro neto total del año en MXN (positivo = se ahorró, negativo = costó más)."""

    costo_total_spot_mxn: float
    """Costo total pagando al spot en el año."""

    ahorro_porcentaje: float
    """Ahorro como porcentaje del costo spot del año."""

    tc_promedio_spot: float
    """Tipo de cambio spot promedio del año."""

    tc_promedio_forward: float
    """Tipo de cambio forward promedio del año."""

    @property
    def tendencia_fx(self) -> str:
        """
        Indica la tendencia del peso durante el año.
        Si el forward pactado fue mayor al spot de compra, el peso se depreció (favorable).
        Si el forward fue menor, el peso se apreció (desfavorable para el importador).
        """
        diff = self.tc_promedio_forward - self.tc_promedio_spot
        if diff > 0.10:
            return "Depreciación"
        elif diff < -0.10:
            return "Apreciación"
        else:
            return "Estable"

    @property
    def tendencia_fx_en(self) -> str:
        """English version of FX trend label."""
        diff = self.tc_promedio_forward - self.tc_promedio_spot
        if diff > 0.10:
            return "Depreciation"
        elif diff < -0.10:
            return "Appreciation"
        else:
            return "Stable"


@dataclass
class ResultadoSimulacion:
    """Resultado completo de la simulación de ahorro."""
    parametros: ParametrosCliente
    periodos: list[ResultadoPeriodo]

    fecha_inicio: date
    fecha_fin: date

    # Estadísticas generales
    ahorro_total_mxn: float = field(init=False)
    ahorro_promedio_mensual_mxn: float = field(init=False)
    ahorro_total_porcentaje: float = field(init=False)
    costo_total_spot_mxn: float = field(init=False)
    costo_total_forward_mxn: float = field(init=False)

    # Desglose acumulado de costos del forward
    costo_total_forward_teorico_mxn: float = field(init=False)
    costo_total_spread_banco_mxn: float = field(init=False)
    costo_total_markup_hp_mxn: float = field(init=False)
    costo_total_fee_hp_mxn: float = field(init=False)
    costo_total_hedgepoint_mxn: float = field(init=False)   # markup + fee
    costo_total_banco_mxn: float = field(init=False)        # solo spread

    # Extremos
    mejor_mes: ResultadoPeriodo | None = field(init=False)
    peor_mes: ResultadoPeriodo | None = field(init=False)

    # Ratio de éxito
    meses_con_ahorro: int = field(init=False)
    porcentaje_meses_con_ahorro: float = field(init=False)
    total_meses: int = field(init=False)

    # --- Métricas de protección contra peor escenario ---

    danio_total_evitado_mxn: float = field(init=False)
    """Suma de ahorros en todos los meses donde el forward fue mejor que el spot.
    Representa el daño total que la cobertura habría evitado."""

    perdida_maxima_un_mes_mxn: float = field(init=False)
    """Mayor exposición negativa en un solo mes sin cobertura (vs con cobertura).
    Equivale al ahorro del mejor mes — lo que el cliente habría perdido de más
    si ese mes no hubiera tenido forward."""

    peor_trimestre_spot_mxn: float = field(init=False)
    """Costo acumulado del peor bloque de 3 meses consecutivos SIN cobertura,
    medido como cuánto más caro habría sido el spot vs el forward en esos 3 meses."""

    peor_trimestre_periodos: list[str] = field(init=False)
    """Lista de los periodos (YYYY-MM) que forman el peor trimestre consecutivo."""

    peor_racha_meses: int = field(init=False)
    """Duración en meses de la racha consecutiva más larga donde el spot
    fue más caro que el forward (depreciación sostenida favorable para cubrir)."""

    peor_racha_periodos: list[str] = field(init=False)
    """Periodos que conforman la peor racha consecutiva."""

    peor_racha_danio_mxn: float = field(init=False)
    """Daño total acumulado durante la peor racha consecutiva."""

    def __post_init__(self) -> None:
        self._calcular_estadisticas()

    def _calcular_estadisticas(self) -> None:
        """Calcula las estadísticas agregadas a partir de los períodos."""
        if not self.periodos:
            self.ahorro_total_mxn = 0.0
            self.ahorro_promedio_mensual_mxn = 0.0
            self.ahorro_total_porcentaje = 0.0
            self.costo_total_spot_mxn = 0.0
            self.costo_total_forward_mxn = 0.0
            self.costo_total_forward_teorico_mxn = 0.0
            self.costo_total_spread_banco_mxn = 0.0
            self.costo_total_markup_hp_mxn = 0.0
            self.costo_total_fee_hp_mxn = 0.0
            self.costo_total_hedgepoint_mxn = 0.0
            self.costo_total_banco_mxn = 0.0
            self.mejor_mes = None
            self.peor_mes = None
            self.meses_con_ahorro = 0
            self.porcentaje_meses_con_ahorro = 0.0
            self.total_meses = 0
            self.danio_total_evitado_mxn = 0.0
            self.perdida_maxima_un_mes_mxn = 0.0
            self.peor_trimestre_spot_mxn = 0.0
            self.peor_trimestre_periodos = []
            self.peor_racha_meses = 0
            self.peor_racha_periodos = []
            self.peor_racha_danio_mxn = 0.0
            return

        self.costo_total_spot_mxn = sum(p.costo_spot_mxn for p in self.periodos)
        self.costo_total_forward_mxn = sum(p.costo_forward_mxn for p in self.periodos)
        self.costo_total_forward_teorico_mxn = sum(p.costo_forward_teorico_mxn for p in self.periodos)
        self.costo_total_spread_banco_mxn = sum(p.costo_spread_banco_mxn for p in self.periodos)
        self.costo_total_markup_hp_mxn = sum(p.costo_markup_hp_mxn for p in self.periodos)
        self.costo_total_fee_hp_mxn = sum(p.costo_fee_hp_mxn for p in self.periodos)
        self.costo_total_hedgepoint_mxn = self.costo_total_markup_hp_mxn + self.costo_total_fee_hp_mxn
        self.costo_total_banco_mxn = self.costo_total_spread_banco_mxn

        self.ahorro_total_mxn = self.costo_total_spot_mxn - self.costo_total_forward_mxn
        self.total_meses = len(self.periodos)
        self.ahorro_promedio_mensual_mxn = self.ahorro_total_mxn / self.total_meses

        if self.costo_total_spot_mxn > 0:
            self.ahorro_total_porcentaje = (
                self.ahorro_total_mxn / self.costo_total_spot_mxn * 100
            )
        else:
            self.ahorro_total_porcentaje = 0.0

        self.mejor_mes = max(self.periodos, key=lambda p: p.ahorro_mxn)
        self.peor_mes = min(self.periodos, key=lambda p: p.ahorro_mxn)

        self.meses_con_ahorro = sum(1 for p in self.periodos if p.ahorro_mxn > 0)
        self.porcentaje_meses_con_ahorro = (
            self.meses_con_ahorro / self.total_meses * 100
        )

        self._calcular_metricas_proteccion()

    def _calcular_metricas_proteccion(self) -> None:
        """
        Calcula las métricas de protección contra el peor escenario.
        Se llama desde _calcular_estadisticas después de validar que hay períodos.
        """
        ahorros = [p.ahorro_mxn for p in self.periodos]
        n = len(ahorros)

        # 1. Daño total evitado: suma de ahorros positivos (meses donde el forward protegió)
        self.danio_total_evitado_mxn = sum(a for a in ahorros if a > 0)

        # 2. Pérdida máxima evitada en un solo mes
        #    = mayor ahorro positivo = lo que más habría costado un mes sin cobertura
        positivos = [a for a in ahorros if a > 0]
        self.perdida_maxima_un_mes_mxn = max(positivos) if positivos else 0.0

        # 3. Peor trimestre consecutivo sin cobertura (3 meses donde el spot fue más caro)
        #    Buscamos la ventana de 3 meses con mayor suma de ahorros positivos
        #    (es decir, donde el spot habría sido más costoso que el forward)
        mejor_ventana_3_suma = float("-inf")
        mejor_ventana_3_idx = 0
        for i in range(n - 2):
            suma = sum(max(ahorros[i + j], 0) for j in range(3))
            if suma > mejor_ventana_3_suma:
                mejor_ventana_3_suma = suma
                mejor_ventana_3_idx = i
        self.peor_trimestre_spot_mxn = max(mejor_ventana_3_suma, 0.0)
        self.peor_trimestre_periodos = [
            self.periodos[mejor_ventana_3_idx + j].periodo for j in range(min(3, n))
        ] if n >= 3 else [p.periodo for p in self.periodos]

        # 4. Peor racha consecutiva: secuencia más larga donde ahorro > 0
        #    (el spot fue más caro que el forward de forma continua)
        mejor_racha_inicio = 0
        mejor_racha_len = 0
        mejor_racha_suma = 0.0
        racha_inicio = 0
        racha_len = 0
        racha_suma = 0.0
        for i, a in enumerate(ahorros):
            if a > 0:
                if racha_len == 0:
                    racha_inicio = i
                racha_len += 1
                racha_suma += a
                if racha_len > mejor_racha_len:
                    mejor_racha_len = racha_len
                    mejor_racha_inicio = racha_inicio
                    mejor_racha_suma = racha_suma
            else:
                racha_len = 0
                racha_suma = 0.0

        self.peor_racha_meses = mejor_racha_len
        self.peor_racha_periodos = [
            self.periodos[mejor_racha_inicio + j].periodo
            for j in range(mejor_racha_len)
        ] if mejor_racha_len > 0 else []
        self.peor_racha_danio_mxn = mejor_racha_suma

    def to_dataframe(self) -> pd.DataFrame:
        """Convierte los períodos a un DataFrame de pandas para análisis o graficación."""
        data = []
        ahorro_acumulado = 0.0
        for p in self.periodos:
            ahorro_acumulado += p.ahorro_mxn
            data.append({
                "periodo": p.periodo,
                "fecha_compra": p.fecha_compra,
                "spot": p.spot,
                "forward_30d": p.forward_30d,
                "volumen_usd": p.volumen_usd,
                "costo_spot_mxn": p.costo_spot_mxn,
                "costo_forward_mxn": p.costo_forward_mxn,
                "costo_forward_teorico_mxn": p.costo_forward_teorico_mxn,
                "costo_spread_banco_mxn": p.costo_spread_banco_mxn,
                "costo_markup_hp_mxn": p.costo_markup_hp_mxn,
                "costo_fee_hp_mxn": p.costo_fee_hp_mxn,
                "ahorro_mxn": p.ahorro_mxn,
                "ahorro_porcentaje": p.ahorro_porcentaje,
                "ahorro_acumulado_mxn": ahorro_acumulado,
            })
        return pd.DataFrame(data)

    def resumen(self) -> str:
        """Genera un resumen de texto del resultado de la simulación."""
        p = self.parametros
        lineas = [
            "=" * 65,
            "  SIMULADOR DE AHORRO POR COBERTURA FORWARD — HedgePoint MX",
            "=" * 65,
            f"  Período simulado:        {self.fecha_inicio} → {self.fecha_fin}",
            f"  Volumen mensual USD:      ${p.volumen_mensual_usd:,.0f}",
            f"  Margen de utilidad:       {p.margen_utilidad*100:.1f}%",
            f"  Frecuencia de compra:     {p.frecuencia}",
            f"  Spread banco:             ${p.spread_banco:.2f} MXN/USD",
            f"  Markup HedgePoint:        ${p.markup_hedgepoint:.2f} MXN/USD",
            f"  Fee mensual HedgePoint:   ${p.fee_mensual:,.0f} MXN",
            "-" * 65,
            f"  Costo total SIN cobertura: ${self.costo_total_spot_mxn:>16,.2f} MXN",
            f"  Costo total CON forward:   ${self.costo_total_forward_mxn:>16,.2f} MXN",
            f"    ├─ Forward teórico:      ${self.costo_total_forward_teorico_mxn:>16,.2f} MXN",
            f"    ├─ Spread banco:         ${self.costo_total_banco_mxn:>16,.2f} MXN",
            f"    ├─ Markup HedgePoint:    ${self.costo_total_markup_hp_mxn:>16,.2f} MXN",
            f"    └─ Fee HedgePoint:       ${self.costo_total_fee_hp_mxn:>16,.2f} MXN",
            f"  Ahorro total:              ${self.ahorro_total_mxn:>16,.2f} MXN",
            f"  Ahorro total (%):          {self.ahorro_total_porcentaje:>15.2f} %",
            "-" * 65,
            f"  Ahorro promedio mensual:   ${self.ahorro_promedio_mensual_mxn:>16,.2f} MXN",
            f"  Costo total banco:         ${self.costo_total_banco_mxn:>16,.2f} MXN",
            f"  Costo total HedgePoint:    ${self.costo_total_hedgepoint_mxn:>16,.2f} MXN",
            f"  Meses con ahorro:          {self.meses_con_ahorro}/{self.total_meses} "
            f"({self.porcentaje_meses_con_ahorro:.0f}%)",
        ]
        if self.mejor_mes:
            lineas.append(
                f"  Mejor mes:                 {self.mejor_mes.periodo} "
                f"(${self.mejor_mes.ahorro_mxn:,.2f} MXN)"
            )
        if self.peor_mes:
            lineas.append(
                f"  Peor mes:                  {self.peor_mes.periodo} "
                f"(${self.peor_mes.ahorro_mxn:,.2f} MXN)"
            )
        lineas.append("=" * 65)
        return "\n".join(lineas)

    def ahorro_por_anio(self) -> list[ResumenAnual]:
        """
        Agrupa los períodos por año calendario y calcula el resumen anual.

        Returns:
            Lista de ResumenAnual ordenada cronológicamente.
        """
        from collections import defaultdict

        grupos: dict[int, list[ResultadoPeriodo]] = defaultdict(list)
        for p in self.periodos:
            anio = int(p.periodo.split("-")[0])
            grupos[anio].append(p)

        resumenes = []
        for anio in sorted(grupos):
            ps = grupos[anio]
            ahorro = sum(p.ahorro_mxn for p in ps)
            costo_spot = sum(p.costo_spot_mxn for p in ps)
            pct = (ahorro / costo_spot * 100) if costo_spot > 0 else 0.0
            tc_spot_prom = sum(p.spot for p in ps) / len(ps)
            tc_fwd_prom = sum(p.forward_30d for p in ps) / len(ps)
            resumenes.append(ResumenAnual(
                anio=anio,
                meses=len(ps),
                ahorro_total_mxn=ahorro,
                costo_total_spot_mxn=costo_spot,
                ahorro_porcentaje=pct,
                tc_promedio_spot=tc_spot_prom,
                tc_promedio_forward=tc_fwd_prom,
            ))
        return resumenes


def _cargar_fx_historico(
    fecha_inicio: date,
    fecha_fin: date,
    db_path: Path = DB_PATH,
) -> pd.DataFrame:
    """
    Carga los tipos de cambio USD/MXN desde SQLite para el rango dado.

    Returns:
        DataFrame con columnas ['fecha', 'tc'] ordenado por fecha ascendente.

    Raises:
        ValueError: Si no hay suficientes datos en la DB para el rango solicitado.
    """
    sql = """
        SELECT fecha, AVG(bid) AS tc
        FROM fx_rates
        WHERE par = 'USD/MXN'
          AND fecha BETWEEN ? AND ?
        GROUP BY fecha
        ORDER BY fecha ASC
    """
    fecha_ini_str = fecha_inicio.strftime("%Y-%m-%d")
    fecha_fin_str = fecha_fin.strftime("%Y-%m-%d")

    with get_connection(db_path) as conn:
        rows = conn.execute(sql, (fecha_ini_str, fecha_fin_str)).fetchall()

    if not rows:
        raise ValueError(
            f"No hay datos de USD/MXN en la DB para el rango "
            f"{fecha_ini_str} → {fecha_fin_str}. "
            "Ejecuta primero: python scripts/fetch_historical.py"
        )

    df = pd.DataFrame([dict(r) for r in rows])
    df["fecha"] = pd.to_datetime(df["fecha"])
    df = df.sort_values("fecha").reset_index(drop=True)

    logger.info(
        "Datos cargados: %d registros (%s → %s)",
        len(df),
        df["fecha"].iloc[0].date(),
        df["fecha"].iloc[-1].date(),
    )
    return df


def _tc_mas_cercano(df_fx: pd.DataFrame, target_date: date,
                    max_dias_busqueda: int = 5) -> tuple[date, float]:
    """
    Retorna el tipo de cambio del día hábil más cercano anterior o igual a target_date.

    Busca hacia atrás hasta max_dias_busqueda días para cubrir fines de semana y festivos.

    Args:
        df_fx: DataFrame con columnas ['fecha', 'tc'], ordenado por fecha ascendente.
        target_date: Fecha objetivo.
        max_dias_busqueda: Ventana de búsqueda en días hacia atrás (default: 5).

    Returns:
        Tupla (fecha_efectiva, tipo_de_cambio).

    Raises:
        ValueError: Si no hay ningún registro en los últimos max_dias_busqueda días.
    """
    limite = pd.Timestamp(target_date - timedelta(days=max_dias_busqueda))
    target_ts = pd.Timestamp(target_date)
    disponibles = df_fx[(df_fx["fecha"] >= limite) & (df_fx["fecha"] <= target_ts)]

    if disponibles.empty:
        # Fallback: cualquier registro anterior (para el inicio del histórico)
        disponibles = df_fx[df_fx["fecha"] <= target_ts]
        if disponibles.empty:
            raise ValueError(
                f"No hay registros de TC anteriores a {target_date} en los datos cargados."
            )

    ultimo = disponibles.iloc[-1]
    return ultimo["fecha"].date(), float(ultimo["tc"])


class SimuladorAhorro:
    """
    Simula el ahorro potencial por cobertura forward para un cliente importador.

    El backtesting funciona así:
    - Para cada mes M en el período:
        1. El cliente necesita comprar USD a principios de M.
        2. SIN cobertura: compra al spot del primer día hábil de M.
        3. CON forward: habría pactado un forward 30 días antes (último día hábil de M-1),
           usando el spot de ese momento para calcular el precio forward a 30 días.
        4. La diferencia entre ambos costos es el ahorro (o costo adicional).

    Args:
        parametros: Configuración del cliente.
        years: Número de años hacia atrás para la simulación (default: 2).
        db_path: Ruta a la base de datos SQLite.
    """

    def __init__(
        self,
        parametros: ParametrosCliente,
        years: int = 2,
        db_path: Path = DB_PATH,
    ) -> None:
        self.parametros = parametros
        self.years = years
        self.db_path = db_path

    def ejecutar(self) -> ResultadoSimulacion:
        """
        Ejecuta la simulación de backtesting.

        Returns:
            ResultadoSimulacion con resultados por período y estadísticas agregadas.

        Raises:
            ValueError: Si no hay datos suficientes en la DB.
        """
        hoy = date.today()
        # Necesitamos datos 30 días extra al inicio para calcular el primer forward
        fecha_inicio_datos = date(hoy.year - self.years, hoy.month, 1)
        fecha_datos_extendida = date(
            fecha_inicio_datos.year - (1 if fecha_inicio_datos.month == 1 else 0),
            ((fecha_inicio_datos.month - 2) % 12) + 1,
            1,
        )
        fecha_fin = hoy

        df_fx = _cargar_fx_historico(fecha_datos_extendida, fecha_fin, self.db_path)

        # Generar lista de primeros días de cada mes en el período de 2 años
        meses = pd.date_range(
            start=fecha_inicio_datos,
            end=fecha_fin,
            freq="MS",  # Month Start
        )

        periodos: list[ResultadoPeriodo] = []
        volumen = self.parametros.volumen_mensual_usd

        for mes_ts in meses:
            mes_dt = mes_ts.date()

            # TC spot: primer día hábil del mes (busca hasta 5 días hacia adelante
            # para cubrir el caso en que el día 1 caiga en festivo o fin de semana)
            try:
                primer_dia_habil = mes_dt
                fecha_compra = None
                spot_compra = None
                for offset in range(6):
                    candidato = mes_dt + pd.Timedelta(days=offset)
                    candidato_ts = pd.Timestamp(candidato)
                    fila = df_fx[df_fx["fecha"] == candidato_ts]
                    if not fila.empty:
                        fecha_compra = fila.iloc[0]["fecha"].date()
                        spot_compra = float(fila.iloc[0]["tc"])
                        break

                if fecha_compra is None:
                    logger.warning(
                        "Sin datos para el mes %s (ningún día hábil en los primeros 5 días), omitiendo.",
                        mes_dt.strftime("%Y-%m"),
                    )
                    continue

            except (ValueError, IndexError) as e:
                logger.warning("Error al obtener TC spot para %s: %s", mes_dt, e)
                continue

            # TC para el forward: día hábil anterior al día de compra menos N días (según plazo)
            fecha_pacto = fecha_compra - pd.Timedelta(days=self.parametros.plazo_forward_dias)

            try:
                fecha_forward_efectiva, spot_forward_base = _tc_mas_cercano(
                    df_fx, fecha_pacto.date() if hasattr(fecha_pacto, "date") else fecha_pacto
                )
            except ValueError as e:
                logger.warning(
                    "No se puede calcular forward para %s (sin TC 30d antes): %s",
                    mes_dt.strftime("%Y-%m"), e
                )
                continue

            # Calcular precio forward teórico a 30 días
            try:
                fwd = calcular_forward(
                    spot=spot_forward_base,
                    dias=self.parametros.plazo_forward_dias,
                    tiie=self.parametros.tiie,
                    sofr=self.parametros.sofr,
                )
                precio_forward = fwd.forward
            except ValueError as e:
                logger.error("Error calculando forward para %s: %s", mes_dt, e)
                continue

            # Costos en MXN — desglose por componente
            p = self.parametros
            _frac = p.cobertura_pct / 100.0          # fracción cubierta con forward
            _vol_fwd = volumen * _frac               # USD cubiertos con forward
            _vol_spot = volumen * (1.0 - _frac)      # USD comprados al spot del mes

            # Costo referencia 100% spot (benchmark sin cobertura)
            costo_spot = volumen * spot_compra

            # Costo real: fracción cubierta a forward + fracción spot
            costo_fwd_teorico = _vol_fwd * precio_forward
            costo_spread = _vol_fwd * p.spread_banco
            costo_markup = _vol_fwd * p.markup_hedgepoint
            costo_fee = p.fee_mensual
            costo_forward = (
                costo_fwd_teorico + costo_spread + costo_markup + costo_fee
                + _vol_spot * spot_compra  # porción no cubierta a spot
            )

            ahorro = costo_spot - costo_forward
            ahorro_pct = (ahorro / costo_spot * 100) if costo_spot > 0 else 0.0

            periodos.append(ResultadoPeriodo(
                periodo=mes_dt.strftime("%Y-%m"),
                fecha_compra=fecha_compra,
                spot=spot_compra,
                forward_30d=precio_forward,
                fecha_forward=fecha_forward_efectiva,
                spot_forward_base=spot_forward_base,
                volumen_usd=volumen,
                costo_spot_mxn=costo_spot,
                costo_forward_mxn=costo_forward,
                costo_forward_teorico_mxn=costo_fwd_teorico,
                costo_spread_banco_mxn=costo_spread,
                costo_markup_hp_mxn=costo_markup,
                costo_fee_hp_mxn=costo_fee,
                ahorro_mxn=ahorro,
                ahorro_porcentaje=ahorro_pct,
            ))

        if not periodos:
            raise ValueError(
                "La simulación no produjo resultados. "
                "Verifica que haya datos históricos suficientes en la BD."
            )

        logger.info("Simulación completada: %d períodos procesados.", len(periodos))

        return ResultadoSimulacion(
            parametros=self.parametros,
            periodos=periodos,
            fecha_inicio=periodos[0].fecha_compra,
            fecha_fin=periodos[-1].fecha_compra,
        )


# ---------------------------------------------------------------------------
# Métricas por nivel de cobertura
# ---------------------------------------------------------------------------

@dataclass
class MetricasNivelCobertura:
    """Métricas agregadas de la simulación para un nivel de cobertura dado."""
    cobertura_pct: float
    """Nivel de cobertura en % (25, 50, 75, 100)."""

    costo_anual_proteccion_mxn: float
    """Diferencia entre costo con forward parcial y costo 100% spot (positivo = ahorro, negativo = costo)."""

    pct_margen: float
    """Costo/ahorro anual de protección como % del margen de utilidad total."""

    perdida_maxima_evitada_mxn: float
    """Suma de ahorros positivos (meses donde el forward protegió), escalada al nivel de cobertura."""


def calcular_metricas_por_nivel(
    resultado_base: "ResultadoSimulacion",
    niveles: list[float] | None = None,
) -> list[MetricasNivelCobertura]:
    """
    Calcula métricas de cobertura para distintos niveles sin re-correr la base de datos.

    Parte de un ResultadoSimulacion calculado con cobertura_pct=100 (o cualquier nivel)
    y deriva los resultados para cada nivel pedido escalando los componentes proporcionales.

    Args:
        resultado_base: Resultado de la simulación con cobertura_pct=100.
        niveles: Lista de porcentajes a calcular (default: [25, 50, 75, 100]).

    Returns:
        Lista de MetricasNivelCobertura ordenada por nivel ascendente.
    """
    if niveles is None:
        niveles = [25.0, 50.0, 75.0, 100.0]

    p = resultado_base.parametros
    periodos = resultado_base.periodos
    margen_total = resultado_base.costo_total_spot_mxn * p.margen_utilidad

    resultados = []
    for nivel in niveles:
        frac = nivel / 100.0
        costo_anual = 0.0
        danio_evitado = 0.0

        for per in periodos:
            vol = per.volumen_usd
            # Costo referencia: 100% spot
            costo_spot = per.costo_spot_mxn

            # Costos del nivel parcial (re-escalar la parte del forward)
            costo_fwd_teorico = vol * frac * per.forward_30d
            costo_spread = vol * frac * p.spread_banco
            costo_markup = vol * frac * p.markup_hedgepoint
            costo_fee = p.fee_mensual  # fee es fijo por mes, no varía con el volumen cubierto
            costo_spot_nocubierto = vol * (1.0 - frac) * per.spot

            costo_total_mes = (
                costo_fwd_teorico + costo_spread + costo_markup + costo_fee
                + costo_spot_nocubierto
            )
            ahorro_mes = costo_spot - costo_total_mes
            costo_anual += ahorro_mes  # acumulado: positivo = ahorro, negativo = costo

            if ahorro_mes > 0:
                danio_evitado += ahorro_mes

        pct_margen = (costo_anual / margen_total * 100) if margen_total > 0 else 0.0

        resultados.append(MetricasNivelCobertura(
            cobertura_pct=nivel,
            costo_anual_proteccion_mxn=costo_anual,
            pct_margen=pct_margen,
            perdida_maxima_evitada_mxn=danio_evitado,
        ))

    return resultados


# ---------------------------------------------------------------------------
# Simulación multi-plazo
# ---------------------------------------------------------------------------

@dataclass
class ResultadoMultiPlazo:
    """
    Agrupa los resultados de simulación para los 3 plazos estándar de forward
    (30, 60 y 90 días), facilitando la comparación entre estrategias.
    """
    plazo_30d: ResultadoSimulacion
    plazo_60d: ResultadoSimulacion
    plazo_90d: ResultadoSimulacion

    @property
    def mejor_plazo(self) -> ResultadoSimulacion:
        """Retorna el resultado del plazo con mayor ahorro total."""
        return max(
            [self.plazo_30d, self.plazo_60d, self.plazo_90d],
            key=lambda r: r.ahorro_total_mxn,
        )

    @property
    def peor_plazo(self) -> ResultadoSimulacion:
        """Retorna el resultado del plazo con menor ahorro total (más costoso)."""
        return min(
            [self.plazo_30d, self.plazo_60d, self.plazo_90d],
            key=lambda r: r.ahorro_total_mxn,
        )

    def tabla_comparativa(self) -> pd.DataFrame:
        """
        Genera un DataFrame comparativo con las métricas clave de los 3 plazos.

        Columnas: plazo_dias, ahorro_total_mxn, ahorro_porcentaje,
                  ahorro_promedio_mensual_mxn, meses_con_ahorro,
                  porcentaje_meses_con_ahorro, costo_total_forward_mxn,
                  costo_total_hedgepoint_mxn, costo_total_banco_mxn.
        """
        filas = []
        for resultado in [self.plazo_30d, self.plazo_60d, self.plazo_90d]:
            filas.append({
                "plazo_dias": resultado.parametros.plazo_forward_dias,
                "ahorro_total_mxn": resultado.ahorro_total_mxn,
                "ahorro_porcentaje": resultado.ahorro_total_porcentaje,
                "ahorro_promedio_mensual_mxn": resultado.ahorro_promedio_mensual_mxn,
                "meses_con_ahorro": resultado.meses_con_ahorro,
                "porcentaje_meses_con_ahorro": resultado.porcentaje_meses_con_ahorro,
                "costo_total_forward_mxn": resultado.costo_total_forward_mxn,
                "costo_total_hedgepoint_mxn": resultado.costo_total_hedgepoint_mxn,
                "costo_total_banco_mxn": resultado.costo_total_banco_mxn,
            })
        return pd.DataFrame(filas)

    def resumen(self) -> str:
        """Genera un resumen comparativo de los 3 plazos en texto."""
        df = self.tabla_comparativa()
        mejor = self.mejor_plazo
        lineas = [
            "=" * 70,
            "  COMPARATIVA MULTI-PLAZO — HedgePoint MX",
            "=" * 70,
            f"  {'Métrica':<38} {'30 días':>9} {'60 días':>9} {'90 días':>9}",
            "-" * 70,
        ]
        metricas = [
            ("Ahorro total MXN", "ahorro_total_mxn", "${:>,.0f}"),
            ("Ahorro total (%)", "ahorro_porcentaje", "{:>8.2f}%"),
            ("Ahorro promedio mensual MXN", "ahorro_promedio_mensual_mxn", "${:>,.0f}"),
            ("Meses con ahorro (%)", "porcentaje_meses_con_ahorro", "{:>8.1f}%"),
            ("Costo total forward MXN", "costo_total_forward_mxn", "${:>,.0f}"),
            ("Costo total HedgePoint MXN", "costo_total_hedgepoint_mxn", "${:>,.0f}"),
            ("Costo total banco MXN", "costo_total_banco_mxn", "${:>,.0f}"),
        ]
        for etiqueta, col, fmt in metricas:
            vals = [fmt.format(v) for v in df[col]]
            lineas.append(f"  {etiqueta:<38} {vals[0]:>9} {vals[1]:>9} {vals[2]:>9}")
        lineas += [
            "-" * 70,
            f"  Plazo óptimo: {mejor.parametros.plazo_forward_dias} días "
            f"(ahorro ${mejor.ahorro_total_mxn:,.0f} MXN)",
            "=" * 70,
        ]
        return "\n".join(lineas)


def simular_multi_plazo(
    parametros: ParametrosCliente,
    plazos: list[int] | None = None,
    years: int = 2,
    db_path: Path = DB_PATH,
) -> ResultadoMultiPlazo:
    """
    Ejecuta la simulación de backtesting para 3 plazos de forward en paralelo.

    Para cada plazo N (30, 60, 90 días):
    - El TC forward se fija consultando el spot N días antes de la fecha de compra.
    - El precio forward teórico se calcula con calcular_forward(spot, dias=N).
    - El TC spot de referencia (costo sin cobertura) es el mismo para los 3 plazos.

    Los 3 plazos se ejecutan en paralelo con ThreadPoolExecutor para reducir el
    tiempo total (las operaciones son CPU-bound leves + I/O a SQLite).

    Args:
        parametros: Parámetros base del cliente. El campo plazo_forward_dias
                    se sobreescribe internamente para cada plazo.
        plazos: Lista de plazos en días. Default: [30, 60, 90].
        years: Años de histórico a simular (default: 2).
        db_path: Ruta a la base de datos SQLite.

    Returns:
        ResultadoMultiPlazo con los 3 resultados accesibles como atributos
        plazo_30d, plazo_60d, plazo_90d.

    Raises:
        ValueError: Si la lista de plazos no contiene exactamente 30, 60 y 90,
                    o si alguna simulación individual falla.
    """
    if plazos is None:
        plazos = [30, 60, 90]

    if sorted(plazos) != [30, 60, 90]:
        raise ValueError(
            f"Los plazos deben ser [30, 60, 90], recibido: {plazos}. "
            "Para plazos personalizados usa SimuladorAhorro directamente."
        )

    def _correr_plazo(plazo_dias: int) -> ResultadoSimulacion:
        """Crea una copia de los parámetros con el plazo indicado y ejecuta."""
        params_plazo = _dataclass_replace(parametros, plazo_forward_dias=plazo_dias)
        sim = SimuladorAhorro(params_plazo, years=years, db_path=db_path)
        logger.info("Iniciando simulación plazo %dd...", plazo_dias)
        resultado = sim.ejecutar()
        logger.info("Simulación plazo %dd completada.", plazo_dias)
        return resultado

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {plazo: executor.submit(_correr_plazo, plazo) for plazo in plazos}
        resultados = {}
        for plazo, futuro in futuros.items():
            try:
                resultados[plazo] = futuro.result()
            except Exception as e:
                raise ValueError(
                    f"Error en la simulación del plazo {plazo} días: {e}"
                ) from e

    return ResultadoMultiPlazo(
        plazo_30d=resultados[30],
        plazo_60d=resultados[60],
        plazo_90d=resultados[90],
    )


# ---------------------------------------------------------------------------
# Estrategia de cobertura con opciones put (Garman-Kohlhagen)
# ---------------------------------------------------------------------------

@dataclass
class ResultadoPeriodoOpciones:
    """Resultado mensual de la estrategia de cobertura con put USD/MXN."""

    periodo: str
    """Mes en formato YYYY-MM."""

    fecha_compra: date
    """Fecha efectiva de compra de divisas (vencimiento de la opción)."""

    spot_compra: float
    """Tipo de cambio spot USD/MXN en la fecha de compra (= vencimiento put)."""

    strike: float
    """Precio de ejercicio del put = spot at-the-money al momento de contratar."""

    spot_contratacion: float
    """Spot USD/MXN en la fecha de contratación de la opción (N días antes)."""

    fecha_contratacion: date
    """Fecha en que se contrató la opción (spot_fecha - plazo_dias)."""

    vol_historica: float
    """Volatilidad histórica 30d anualizada usada como proxy de vol implícita."""

    prima_teorica_mxn: float
    """Prima teórica Black-Scholes (Garman-Kohlhagen) por USD, en MXN/USD."""

    markup_banco_pct: float
    """Markup del banco sobre la prima teórica (default: 15%)."""

    prima_banco_mxn: float
    """Prima real cobrada por el banco: prima_teorica × (1 + markup_banco_pct)."""

    prima_total_pagada_mxn: float
    """Prima total pagada por el volumen completo: prima_banco × volumen_usd."""

    ejercida: bool
    """True si spot_compra > strike (put tiene valor positivo al vencimiento)."""

    valor_ejercicio_mxn: float
    """
    Valor intrínseco del put al vencimiento × volumen, antes de restar prima.
    = max(0, spot_compra - strike) × volumen_usd.
    Cero si no se ejerció.
    """

    volumen_usd: float
    """Volumen en USD comprado en este período."""

    tc_efectivo: float
    """
    Tipo de cambio efectivo pagado por el importador:
    - Si ejercida: strike + prima_banco (compró al strike, pero pagó la prima)
    - Si no ejercida: spot_compra + prima_banco (compró a spot, pagó la prima perdida)
    """

    costo_spot_mxn: float
    """Costo de referencia: volumen × spot_compra (sin ninguna cobertura)."""

    costo_opcion_mxn: float
    """
    Costo total con la estrategia de opciones:
    - Si ejercida: volumen × strike + prima_total + markup_hp + fee
    - Si no ejercida: volumen × spot_compra + prima_total + markup_hp + fee
    """

    costo_markup_hp_mxn: float
    """Markup HedgePoint sobre el volumen: volumen × markup_hedgepoint."""

    costo_fee_hp_mxn: float
    """Fee fijo mensual de consultoría HedgePoint."""

    ahorro_vs_spot_mxn: float
    """
    Diferencia entre costo_spot y costo_opcion.
    Positivo = la opción fue ventajosa frente a comprar 100% a spot.
    Negativo = la prima no se recuperó (spot bajó o quedó plano).
    """

    ahorro_vs_forward_mxn: float
    """
    Diferencia entre costo_forward (de la simulación de forwards) y costo_opcion.
    Permite comparar ambas estrategias directamente mes a mes.
    None si no se tiene el dato del forward del mismo período.
    """

    ahorro_porcentaje: float
    """ahorro_vs_spot_mxn / costo_spot_mxn × 100."""


@dataclass
class ResultadoSimulacionOpciones:
    """Resultado completo de la simulación de cobertura con opciones put."""

    parametros: ParametrosCliente
    periodos: list[ResultadoPeriodoOpciones]

    fecha_inicio: date
    fecha_fin: date

    markup_banco_pct: float
    """Markup del banco sobre la prima teórica (0.15 = 15%)."""

    # Estadísticas agregadas — calculadas en __post_init__
    costo_total_spot_mxn: float = field(init=False)
    costo_total_opciones_mxn: float = field(init=False)
    prima_total_pagada_mxn: float = field(init=False)
    valor_total_ejercicios_mxn: float = field(init=False)
    costo_total_markup_hp_mxn: float = field(init=False)
    costo_total_fee_hp_mxn: float = field(init=False)

    ahorro_total_vs_spot_mxn: float = field(init=False)
    ahorro_total_porcentaje: float = field(init=False)
    ahorro_promedio_mensual_mxn: float = field(init=False)

    meses_ejercidos: int = field(init=False)
    """Meses en que el put fue ejercido (spot > strike al vencimiento)."""

    porcentaje_meses_ejercidos: float = field(init=False)
    total_meses: int = field(init=False)

    mejor_mes: ResultadoPeriodoOpciones | None = field(init=False)
    peor_mes: ResultadoPeriodoOpciones | None = field(init=False)

    vol_promedio: float = field(init=False)
    """Volatilidad histórica promedio usada en el período."""

    prima_promedio_mxn_por_usd: float = field(init=False)
    """Prima promedio por USD en el período (banco, con markup)."""

    def __post_init__(self) -> None:
        self._calcular_estadisticas()

    def _calcular_estadisticas(self) -> None:
        if not self.periodos:
            self.costo_total_spot_mxn = 0.0
            self.costo_total_opciones_mxn = 0.0
            self.prima_total_pagada_mxn = 0.0
            self.valor_total_ejercicios_mxn = 0.0
            self.costo_total_markup_hp_mxn = 0.0
            self.costo_total_fee_hp_mxn = 0.0
            self.ahorro_total_vs_spot_mxn = 0.0
            self.ahorro_total_porcentaje = 0.0
            self.ahorro_promedio_mensual_mxn = 0.0
            self.meses_ejercidos = 0
            self.porcentaje_meses_ejercidos = 0.0
            self.total_meses = 0
            self.mejor_mes = None
            self.peor_mes = None
            self.vol_promedio = 0.0
            self.prima_promedio_mxn_por_usd = 0.0
            return

        self.costo_total_spot_mxn = sum(p.costo_spot_mxn for p in self.periodos)
        self.costo_total_opciones_mxn = sum(p.costo_opcion_mxn for p in self.periodos)
        self.prima_total_pagada_mxn = sum(p.prima_total_pagada_mxn for p in self.periodos)
        self.valor_total_ejercicios_mxn = sum(p.valor_ejercicio_mxn for p in self.periodos)
        self.costo_total_markup_hp_mxn = sum(p.costo_markup_hp_mxn for p in self.periodos)
        self.costo_total_fee_hp_mxn = sum(p.costo_fee_hp_mxn for p in self.periodos)

        self.ahorro_total_vs_spot_mxn = sum(p.ahorro_vs_spot_mxn for p in self.periodos)
        self.total_meses = len(self.periodos)
        self.ahorro_promedio_mensual_mxn = self.ahorro_total_vs_spot_mxn / self.total_meses

        self.ahorro_total_porcentaje = (
            self.ahorro_total_vs_spot_mxn / self.costo_total_spot_mxn * 100
            if self.costo_total_spot_mxn > 0 else 0.0
        )

        self.meses_ejercidos = sum(1 for p in self.periodos if p.ejercida)
        self.porcentaje_meses_ejercidos = self.meses_ejercidos / self.total_meses * 100

        self.mejor_mes = max(self.periodos, key=lambda p: p.ahorro_vs_spot_mxn)
        self.peor_mes = min(self.periodos, key=lambda p: p.ahorro_vs_spot_mxn)

        self.vol_promedio = sum(p.vol_historica for p in self.periodos) / self.total_meses
        self.prima_promedio_mxn_por_usd = (
            self.prima_total_pagada_mxn
            / sum(p.volumen_usd for p in self.periodos)
            if self.periodos else 0.0
        )

    def to_dataframe(self) -> pd.DataFrame:
        """Convierte los períodos a DataFrame para análisis o graficación."""
        ahorro_acum = 0.0
        data = []
        for p in self.periodos:
            ahorro_acum += p.ahorro_vs_spot_mxn
            data.append({
                "periodo": p.periodo,
                "fecha_compra": p.fecha_compra,
                "spot_compra": p.spot_compra,
                "strike": p.strike,
                "spot_contratacion": p.spot_contratacion,
                "vol_historica": p.vol_historica,
                "prima_teorica_mxn": p.prima_teorica_mxn,
                "prima_banco_mxn": p.prima_banco_mxn,
                "prima_total_pagada_mxn": p.prima_total_pagada_mxn,
                "ejercida": p.ejercida,
                "valor_ejercicio_mxn": p.valor_ejercicio_mxn,
                "volumen_usd": p.volumen_usd,
                "tc_efectivo": p.tc_efectivo,
                "costo_spot_mxn": p.costo_spot_mxn,
                "costo_opcion_mxn": p.costo_opcion_mxn,
                "costo_markup_hp_mxn": p.costo_markup_hp_mxn,
                "costo_fee_hp_mxn": p.costo_fee_hp_mxn,
                "ahorro_vs_spot_mxn": p.ahorro_vs_spot_mxn,
                "ahorro_vs_forward_mxn": p.ahorro_vs_forward_mxn,
                "ahorro_porcentaje": p.ahorro_porcentaje,
                "ahorro_acumulado_mxn": ahorro_acum,
            })
        return pd.DataFrame(data)

    def resumen(self) -> str:
        """Genera un resumen de texto del resultado de la simulación de opciones."""
        p = self.parametros
        lineas = [
            "=" * 65,
            "  SIMULACIÓN DE OPCIONES PUT — HedgePoint MX",
            "=" * 65,
            f"  Período simulado:        {self.fecha_inicio} → {self.fecha_fin}",
            f"  Volumen mensual USD:      ${p.volumen_mensual_usd:,.0f}",
            f"  Margen de utilidad:       {p.margen_utilidad*100:.1f}%",
            f"  Markup banco (prima):     {self.markup_banco_pct*100:.0f}%",
            f"  Markup HedgePoint:        ${p.markup_hedgepoint:.2f} MXN/USD",
            f"  Fee mensual HedgePoint:   ${p.fee_mensual:,.0f} MXN",
            f"  Volatilidad promedio:     {self.vol_promedio*100:.1f}% anual",
            f"  Prima promedio/USD:       ${self.prima_promedio_mxn_por_usd:.4f} MXN",
            "-" * 65,
            f"  Costo total SIN cobertura: ${self.costo_total_spot_mxn:>16,.2f} MXN",
            f"  Costo total CON opciones:  ${self.costo_total_opciones_mxn:>16,.2f} MXN",
            f"    ├─ Primas pagadas:       ${self.prima_total_pagada_mxn:>16,.2f} MXN",
            f"    ├─ Valor de ejercicios:  ${self.valor_total_ejercicios_mxn:>16,.2f} MXN",
            f"    ├─ Markup HedgePoint:    ${self.costo_total_markup_hp_mxn:>16,.2f} MXN",
            f"    └─ Fee HedgePoint:       ${self.costo_total_fee_hp_mxn:>16,.2f} MXN",
            f"  Ahorro total vs spot:      ${self.ahorro_total_vs_spot_mxn:>16,.2f} MXN",
            f"  Ahorro total (%):          {self.ahorro_total_porcentaje:>15.2f} %",
            "-" * 65,
            f"  Ahorro promedio mensual:   ${self.ahorro_promedio_mensual_mxn:>16,.2f} MXN",
            f"  Meses put ejercido:        {self.meses_ejercidos}/{self.total_meses} "
            f"({self.porcentaje_meses_ejercidos:.0f}%)",
        ]
        if self.mejor_mes:
            lineas.append(
                f"  Mejor mes:                 {self.mejor_mes.periodo} "
                f"(${self.mejor_mes.ahorro_vs_spot_mxn:,.2f} MXN)"
            )
        if self.peor_mes:
            lineas.append(
                f"  Peor mes:                  {self.peor_mes.periodo} "
                f"(${self.peor_mes.ahorro_vs_spot_mxn:,.2f} MXN)"
            )
        lineas.append("=" * 65)
        return "\n".join(lineas)


def _calcular_vol_historica(
    df_fx: pd.DataFrame,
    fecha_ref: date,
    ventana_dias: int = 30,
) -> float:
    """
    Calcula la volatilidad histórica anualizada de los retornos log del USD/MXN
    usando los últimos `ventana_dias` días hábiles anteriores a fecha_ref.

    Args:
        df_fx: DataFrame con columnas ['fecha', 'tc'], ordenado ascendente.
        fecha_ref: Fecha de referencia; se usan los datos anteriores a ella.
        ventana_dias: Número de días hábiles para la ventana (default: 30).

    Returns:
        Volatilidad anualizada en decimal (p.ej. 0.12 = 12%).
        Retorna un valor de fallback (0.10) si no hay suficientes datos.
    """
    _FALLBACK_VOL = 0.10  # 10% anual como proxy conservador

    ref_ts = pd.Timestamp(fecha_ref)
    datos = df_fx[df_fx["fecha"] < ref_ts].tail(ventana_dias + 1)

    if len(datos) < 5:
        logger.warning(
            "Datos insuficientes para calcular vol histórica en %s; usando fallback %.0f%%",
            fecha_ref, _FALLBACK_VOL * 100,
        )
        return _FALLBACK_VOL

    retornos = np.log(datos["tc"].values[1:] / datos["tc"].values[:-1])
    vol_diaria = float(np.std(retornos, ddof=1))
    return vol_diaria * math.sqrt(252)  # anualizar con 252 días hábiles


def simulate_options_strategy(
    parametros: ParametrosCliente,
    years: int = 2,
    markup_banco_pct: float = 0.15,
    ventana_vol_dias: int = 30,
    db_path: Path = DB_PATH,
    resultado_forwards: ResultadoSimulacion | None = None,
) -> ResultadoSimulacionOpciones:
    """
    Simula una estrategia de cobertura mensual con opciones put USD/MXN ATM
    (at-the-money) para el mismo período y volumen que la simulación de forwards.

    Mecánica mes a mes
    ------------------
    1. En la fecha de contratación (= fecha_compra - plazo_forward_dias días):
       - Se calcula el strike = spot ATM en esa fecha.
       - Se estima la volatilidad histórica 30d del TC como proxy de vol implícita.
       - Se valúa el put con Garman-Kohlhagen (calcular_opcion_gk).
       - Prima real = prima_teorica × (1 + markup_banco_pct).

    2. En la fecha de compra (= vencimiento del put):
       - Si spot_compra > strike → put ejercido:
           * El importador compra USD al strike (precio garantizado).
           * Ahorro bruto = (spot_compra - strike) × volumen.
           * Costo neto = volumen × strike + prima_total + markup_HP + fee.
       - Si spot_compra ≤ strike → put expira sin valor:
           * El importador compra USD al spot de mercado.
           * Costo neto = volumen × spot_compra + prima_total + markup_HP + fee.

    3. Costos de HedgePoint incluidos (igual que en forwards):
       - markup_hedgepoint (MXN/USD) sobre el volumen total.
       - fee_mensual (MXN fijo).
       NO se incluye spread bancario sobre el subyacente — el banco ya cobra
       su spread implícito en el markup sobre la prima (markup_banco_pct).

    4. Si se proporciona resultado_forwards, se calcula ahorro_vs_forward_mxn
       mes a mes para comparación directa entre ambas estrategias.

    Args:
        parametros: Parámetros del cliente (volumen, margen, plazo, TIIE, SOFR,
                    markup_hedgepoint, fee_mensual, cobertura_pct).
        years: Años de histórico a simular (default: 2).
        markup_banco_pct: Markup del banco sobre la prima teórica GK (default: 15%).
        ventana_vol_dias: Días hábiles para calcular vol histórica (default: 30).
        db_path: Ruta a la base de datos SQLite.
        resultado_forwards: Resultado de forwards del mismo período para comparación
                            mes a mes. Si None, ahorro_vs_forward_mxn será 0.

    Returns:
        ResultadoSimulacionOpciones con resultados por período y estadísticas.

    Raises:
        ValueError: Si no hay datos suficientes en la DB.
    """
    from core.models.pricing import calcular_opcion_gk

    hoy = date.today()
    fecha_inicio_datos = date(hoy.year - years, hoy.month, 1)
    fecha_datos_extendida = date(
        fecha_inicio_datos.year - (1 if fecha_inicio_datos.month == 1 else 0),
        ((fecha_inicio_datos.month - 2) % 12) + 1,
        1,
    )

    df_fx = _cargar_fx_historico(fecha_datos_extendida, hoy, db_path)

    meses = pd.date_range(start=fecha_inicio_datos, end=hoy, freq="MS")

    # Indexar periodos del resultado de forwards para lookup O(1)
    _fwd_por_periodo: dict[str, ResultadoPeriodo] = {}
    if resultado_forwards is not None:
        for fwd_per in resultado_forwards.periodos:
            _fwd_por_periodo[fwd_per.periodo] = fwd_per

    p = parametros
    volumen = p.volumen_mensual_usd
    plazo_dias = p.plazo_forward_dias

    periodos: list[ResultadoPeriodoOpciones] = []

    for mes_ts in meses:
        mes_dt = mes_ts.date()
        periodo_str = mes_dt.strftime("%Y-%m")

        # --- Fecha de compra (vencimiento del put) ---
        fecha_compra = None
        spot_compra = None
        for offset in range(6):
            candidato = mes_dt + pd.Timedelta(days=offset)
            fila = df_fx[df_fx["fecha"] == pd.Timestamp(candidato)]
            if not fila.empty:
                fecha_compra = fila.iloc[0]["fecha"].date()
                spot_compra = float(fila.iloc[0]["tc"])
                break

        if fecha_compra is None:
            logger.warning(
                "Sin datos hábiles para %s; omitiendo período.", periodo_str
            )
            continue

        # --- Fecha de contratación (N días antes del vencimiento) ---
        fecha_contratacion_raw = fecha_compra - pd.Timedelta(days=plazo_dias)
        try:
            fecha_contratacion, spot_contratacion = _tc_mas_cercano(
                df_fx,
                fecha_contratacion_raw.date()
                if hasattr(fecha_contratacion_raw, "date")
                else fecha_contratacion_raw,
            )
        except ValueError as e:
            logger.warning(
                "No hay TC %dd antes de %s para contratar la opción: %s",
                plazo_dias, periodo_str, e,
            )
            continue

        # Strike ATM = spot en la fecha de contratación
        strike = spot_contratacion

        # --- Volatilidad histórica ---
        vol = _calcular_vol_historica(df_fx, fecha_contratacion, ventana_vol_dias)

        # --- Prima Garman-Kohlhagen ---
        try:
            gk = calcular_opcion_gk(
                spot=spot_contratacion,
                strike=strike,
                dias=plazo_dias,
                vol=vol,
                tiie=p.tiie,
                sofr=p.sofr,
            )
        except ValueError as e:
            logger.error(
                "Error calculando opción GK para %s: %s", periodo_str, e
            )
            continue

        prima_teorica = gk.put  # MXN por USD
        prima_banco = prima_teorica * (1.0 + markup_banco_pct)
        prima_total = prima_banco * volumen  # prima total en MXN para el volumen

        # --- Resultado al vencimiento ---
        ejercida = spot_compra > strike
        if ejercida:
            # El importador ejerce: compra USD al strike en vez de al spot
            valor_ejercicio = (spot_compra - strike) * volumen
            costo_subyacente = volumen * strike
        else:
            # La opción expira sin valor; compra al mercado spot
            valor_ejercicio = 0.0
            costo_subyacente = volumen * spot_compra

        costo_markup_hp = volumen * p.markup_hedgepoint
        costo_fee = p.fee_mensual
        costo_opcion = costo_subyacente + prima_total + costo_markup_hp + costo_fee

        costo_spot_ref = volumen * spot_compra
        ahorro_vs_spot = costo_spot_ref - costo_opcion
        ahorro_pct = (ahorro_vs_spot / costo_spot_ref * 100) if costo_spot_ref > 0 else 0.0

        tc_efectivo = (strike + prima_banco) if ejercida else (spot_compra + prima_banco)

        # --- Comparación vs forward del mismo período ---
        fwd_per = _fwd_por_periodo.get(periodo_str)
        ahorro_vs_forward = (
            fwd_per.costo_forward_mxn - costo_opcion
            if fwd_per is not None else 0.0
        )

        periodos.append(ResultadoPeriodoOpciones(
            periodo=periodo_str,
            fecha_compra=fecha_compra,
            spot_compra=spot_compra,
            strike=strike,
            spot_contratacion=spot_contratacion,
            fecha_contratacion=fecha_contratacion,
            vol_historica=vol,
            prima_teorica_mxn=prima_teorica,
            markup_banco_pct=markup_banco_pct,
            prima_banco_mxn=prima_banco,
            prima_total_pagada_mxn=prima_total,
            ejercida=ejercida,
            valor_ejercicio_mxn=valor_ejercicio,
            volumen_usd=volumen,
            tc_efectivo=tc_efectivo,
            costo_spot_mxn=costo_spot_ref,
            costo_opcion_mxn=costo_opcion,
            costo_markup_hp_mxn=costo_markup_hp,
            costo_fee_hp_mxn=costo_fee,
            ahorro_vs_spot_mxn=ahorro_vs_spot,
            ahorro_vs_forward_mxn=ahorro_vs_forward,
            ahorro_porcentaje=ahorro_pct,
        ))

    if not periodos:
        raise ValueError(
            "La simulación de opciones no produjo resultados. "
            "Verifica que haya datos históricos suficientes en la BD."
        )

    logger.info(
        "Simulación de opciones completada: %d períodos procesados.", len(periodos)
    )

    return ResultadoSimulacionOpciones(
        parametros=parametros,
        periodos=periodos,
        fecha_inicio=periodos[0].fecha_compra,
        fecha_fin=periodos[-1].fecha_compra,
        markup_banco_pct=markup_banco_pct,
    )


# ---------------------------------------------------------------------------
# Estrategia collar: put ATM comprado + call OTM vendido
# ---------------------------------------------------------------------------

@dataclass
class ResultadoPeriodoCollar:
    """Resultado mensual de la estrategia de collar USD/MXN."""

    periodo: str
    """Mes en formato YYYY-MM."""

    fecha_compra: date
    """Fecha efectiva de compra de divisas (= vencimiento de ambas opciones)."""

    spot_compra: float
    """Tipo de cambio spot USD/MXN al vencimiento."""

    spot_contratacion: float
    """Spot USD/MXN en la fecha de contratación del collar."""

    fecha_contratacion: date
    """Fecha en que se contrató el collar (fecha_compra - plazo_dias)."""

    vol_historica: float
    """Volatilidad histórica 30d anualizada usada como proxy de vol implícita."""

    # --- Pata call COMPRADA (protección del importador) ---
    strike_call_comprado: float
    """Strike del call comprado = spot ATM en la contratación.
    Protege al importador si el USD sube (peso se deprecia): ejerce si spot_venc > strike."""

    prima_call_teorica_mxn: float
    """Prima teórica Garman-Kohlhagen del call comprado por USD (MXN/USD)."""

    prima_call_banco_mxn: float
    """Prima call real pagada al banco: prima_call_teorica × (1 + markup_banco_pct)."""

    call_comprado_ejercido: bool
    """True si spot_compra > strike_call_comprado al vencimiento (USD subió, put protege)."""

    # --- Pata put VENDIDA (financia el call) ---
    strike_put_vendido: float
    """Strike del put vendido = spot_contratacion × (1 - otm_pct), OTM por debajo del spot.
    Limita el beneficio del importador si el USD baja mucho (peso se aprecia fuertemente)."""

    otm_pct: float
    """Distancia OTM del put vendido como fracción (p.ej. 0.03 = 3% debajo del spot)."""

    prima_put_teorica_mxn: float
    """Prima teórica Garman-Kohlhagen del put vendido por USD (MXN/USD)."""

    prima_put_banco_mxn: float
    """Prima put recibida del banco al vender: prima_put_teorica × (1 - markup_banco_pct).
    El banco descuenta el markup porque él compra el put al importador."""

    put_vendido_ejercido_en_contra: bool
    """True si spot_compra < strike_put_vendido al vencimiento.
    El comprador del put (banco) ejerce: el importador queda obligado a comprar al
    strike_put_vendido, que está POR ENCIMA del spot de mercado (lo perjudica)."""

    # --- Costo neto de prima ---
    prima_neta_mxn: float
    """
    Costo neto de prima por USD = prima_call_banco - prima_put_banco.
    Positivo = el importador paga prima neta (call más caro que put recibido).
    Negativo = zero-cost o crédito (put vendido subsidia o supera el call).
    """

    prima_neta_total_mxn: float
    """prima_neta_mxn × volumen_usd: costo neto total de ambas primas."""

    # --- Escenario al vencimiento ---
    escenario: str
    """
    'call_ejercido': spot_venc > strike_call_comprado — USD subió (peso depreció).
        El importador ejerce el call y compra USD al strike_call_comprado. BUENO.
    'zona_libre': strike_put_vendido <= spot_venc <= strike_call_comprado — sin variación extrema.
        Ni el call ni el put se ejercen; el importador compra a spot_venc. NEUTRAL.
    'put_ejercido': spot_venc < strike_put_vendido — USD bajó mucho (peso se apreció mucho).
        El banco ejerce el put vendido: el importador debe comprar al strike_put_vendido,
        que está por encima del spot de mercado. LIMITA EL BENEFICIO.
    """

    tc_efectivo: float
    """
    Tipo de cambio efectivo al que realmente compra el importador:
    - call_ejercido:  strike_call_comprado (cap superior: importador paga como máximo este TC)
    - zona_libre:     spot_compra (compra a mercado)
    - put_ejercido:   strike_put_vendido  (piso inferior: importador paga mínimo este TC, > spot)
    """

    volumen_usd: float
    """Volumen en USD comprado en este período."""

    costo_spot_mxn: float
    """Costo de referencia 100% spot: volumen × spot_compra."""

    costo_collar_mxn: float
    """
    Costo total con la estrategia collar:
    volumen × tc_efectivo + prima_neta_total + costo_markup_hp + costo_fee_hp.
    (La prima_neta ya incluye el markup del banco.)
    """

    costo_markup_hp_mxn: float
    """Markup HedgePoint: volumen × markup_hedgepoint."""

    costo_fee_hp_mxn: float
    """Fee fijo mensual de consultoría HedgePoint."""

    ahorro_vs_spot_mxn: float
    """costo_spot_mxn - costo_collar_mxn. Positivo = collar fue mejor que spot."""

    ahorro_vs_forward_mxn: float
    """costo_forward_mxn - costo_collar_mxn. Positivo = collar mejor que forward."""

    ahorro_porcentaje: float
    """ahorro_vs_spot_mxn / costo_spot_mxn × 100."""


@dataclass
class ResultadoSimulacionCollar:
    """Resultado completo de la simulación de estrategia collar."""

    parametros: ParametrosCliente
    periodos: list[ResultadoPeriodoCollar]
    fecha_inicio: date
    fecha_fin: date

    markup_banco_pct: float
    """Markup del banco sobre las primas teóricas."""

    otm_pct: float
    """Distancia OTM del put vendido debajo del spot (0.03 = 3% por debajo)."""

    # Estadísticas — calculadas en __post_init__
    costo_total_spot_mxn: float = field(init=False)
    costo_total_collar_mxn: float = field(init=False)
    prima_neta_total_pagada_mxn: float = field(init=False)
    """Suma de primas netas (call comprado - put recibido) de todos los meses. Puede ser negativa."""

    costo_total_markup_hp_mxn: float = field(init=False)
    costo_total_fee_hp_mxn: float = field(init=False)

    ahorro_total_vs_spot_mxn: float = field(init=False)
    ahorro_total_porcentaje: float = field(init=False)
    ahorro_promedio_mensual_mxn: float = field(init=False)

    meses_call_ejercido: int = field(init=False)
    """Meses en que el importador ejerció el call comprado (USD subió — protección activada)."""
    meses_zona_libre: int = field(init=False)
    """Meses sin ejercicio de ninguna opción (TC entre los dos strikes)."""
    meses_put_ejercido: int = field(init=False)
    """Meses en que el banco ejerció el put vendido en contra del importador (USD bajó mucho)."""
    total_meses: int = field(init=False)

    mejor_mes: ResultadoPeriodoCollar | None = field(init=False)
    peor_mes: ResultadoPeriodoCollar | None = field(init=False)

    vol_promedio: float = field(init=False)
    prima_call_promedio_mxn_por_usd: float = field(init=False)
    """Prima promedio del call comprado (pata de protección), por USD."""
    prima_put_promedio_mxn_por_usd: float = field(init=False)
    """Prima promedio del put vendido (pata que financia), por USD."""
    prima_neta_promedio_mxn_por_usd: float = field(init=False)

    es_zero_cost: bool = field(init=False)
    """True si la prima neta total del período fue <= 0 (zero-cost o con crédito)."""

    def __post_init__(self) -> None:
        self._calcular_estadisticas()

    def _calcular_estadisticas(self) -> None:
        if not self.periodos:
            self.costo_total_spot_mxn = 0.0
            self.costo_total_collar_mxn = 0.0
            self.prima_neta_total_pagada_mxn = 0.0
            self.costo_total_markup_hp_mxn = 0.0
            self.costo_total_fee_hp_mxn = 0.0
            self.ahorro_total_vs_spot_mxn = 0.0
            self.ahorro_total_porcentaje = 0.0
            self.ahorro_promedio_mensual_mxn = 0.0
            self.meses_call_ejercido = 0
            self.meses_zona_libre = 0
            self.meses_put_ejercido = 0
            self.total_meses = 0
            self.mejor_mes = None
            self.peor_mes = None
            self.vol_promedio = 0.0
            self.prima_call_promedio_mxn_por_usd = 0.0
            self.prima_put_promedio_mxn_por_usd = 0.0
            self.prima_neta_promedio_mxn_por_usd = 0.0
            self.es_zero_cost = False
            return

        self.costo_total_spot_mxn = sum(p.costo_spot_mxn for p in self.periodos)
        self.costo_total_collar_mxn = sum(p.costo_collar_mxn for p in self.periodos)
        self.prima_neta_total_pagada_mxn = sum(p.prima_neta_total_mxn for p in self.periodos)
        self.costo_total_markup_hp_mxn = sum(p.costo_markup_hp_mxn for p in self.periodos)
        self.costo_total_fee_hp_mxn = sum(p.costo_fee_hp_mxn for p in self.periodos)

        self.ahorro_total_vs_spot_mxn = sum(p.ahorro_vs_spot_mxn for p in self.periodos)
        self.total_meses = len(self.periodos)
        self.ahorro_promedio_mensual_mxn = self.ahorro_total_vs_spot_mxn / self.total_meses

        self.ahorro_total_porcentaje = (
            self.ahorro_total_vs_spot_mxn / self.costo_total_spot_mxn * 100
            if self.costo_total_spot_mxn > 0 else 0.0
        )

        # call_ejercido = importador ejerció el call comprado (USD subió — bueno)
        self.meses_call_ejercido = sum(
            1 for p in self.periodos if p.escenario == "call_ejercido"
        )
        self.meses_zona_libre = sum(
            1 for p in self.periodos if p.escenario == "zona_libre"
        )
        # put_ejercido = banco ejerció el put vendido en contra (USD bajó mucho — limita beneficio)
        self.meses_put_ejercido = sum(
            1 for p in self.periodos if p.escenario == "put_ejercido"
        )

        self.mejor_mes = max(self.periodos, key=lambda p: p.ahorro_vs_spot_mxn)
        self.peor_mes = min(self.periodos, key=lambda p: p.ahorro_vs_spot_mxn)

        self.vol_promedio = sum(p.vol_historica for p in self.periodos) / self.total_meses

        total_vol = sum(p.volumen_usd for p in self.periodos)
        # call comprado = pata de protección (prima pagada)
        self.prima_call_promedio_mxn_por_usd = (
            sum(p.prima_call_banco_mxn for p in self.periodos) / self.total_meses
        )
        # put vendido = pata que financia (prima recibida)
        self.prima_put_promedio_mxn_por_usd = (
            sum(p.prima_put_banco_mxn for p in self.periodos) / self.total_meses
        )
        self.prima_neta_promedio_mxn_por_usd = (
            self.prima_neta_total_pagada_mxn / total_vol if total_vol > 0 else 0.0
        )
        self.es_zero_cost = self.prima_neta_total_pagada_mxn <= 0.0

    def to_dataframe(self) -> pd.DataFrame:
        """Convierte los períodos a DataFrame para análisis o graficación."""
        ahorro_acum = 0.0
        data = []
        for p in self.periodos:
            ahorro_acum += p.ahorro_vs_spot_mxn
            data.append({
                "periodo": p.periodo,
                "fecha_compra": p.fecha_compra,
                "spot_compra": p.spot_compra,
                "spot_contratacion": p.spot_contratacion,
                "vol_historica": p.vol_historica,
                "strike_call_comprado": p.strike_call_comprado,
                "prima_call_banco_mxn": p.prima_call_banco_mxn,
                "call_comprado_ejercido": p.call_comprado_ejercido,
                "strike_put_vendido": p.strike_put_vendido,
                "prima_put_banco_mxn": p.prima_put_banco_mxn,
                "put_vendido_ejercido_en_contra": p.put_vendido_ejercido_en_contra,
                "prima_neta_mxn": p.prima_neta_mxn,
                "prima_neta_total_mxn": p.prima_neta_total_mxn,
                "escenario": p.escenario,
                "tc_efectivo": p.tc_efectivo,
                "volumen_usd": p.volumen_usd,
                "costo_spot_mxn": p.costo_spot_mxn,
                "costo_collar_mxn": p.costo_collar_mxn,
                "costo_markup_hp_mxn": p.costo_markup_hp_mxn,
                "costo_fee_hp_mxn": p.costo_fee_hp_mxn,
                "ahorro_vs_spot_mxn": p.ahorro_vs_spot_mxn,
                "ahorro_vs_forward_mxn": p.ahorro_vs_forward_mxn,
                "ahorro_porcentaje": p.ahorro_porcentaje,
                "ahorro_acumulado_mxn": ahorro_acum,
            })
        return pd.DataFrame(data)

    def resumen(self) -> str:
        """Genera un resumen de texto del resultado de la simulación collar."""
        p = self.parametros
        _pct_call = self.meses_call_ejercido / self.total_meses * 100 if self.total_meses else 0.0
        _pct_libre = self.meses_zona_libre / self.total_meses * 100 if self.total_meses else 0.0
        _pct_put = self.meses_put_ejercido / self.total_meses * 100 if self.total_meses else 0.0
        _zero_cost_str = "Sí (zero-cost o crédito)" if self.es_zero_cost else "No"
        lineas = [
            "=" * 65,
            "  SIMULACIÓN DE COLLAR — HedgePoint MX",
            "=" * 65,
            f"  Período simulado:          {self.fecha_inicio} → {self.fecha_fin}",
            f"  Volumen mensual USD:        ${p.volumen_mensual_usd:,.0f}",
            f"  Margen de utilidad:         {p.margen_utilidad*100:.1f}%",
            f"  Estructura: Call ATM comprado + Put −{self.otm_pct*100:.1f}% OTM vendido",
            f"  Markup banco (primas):      {self.markup_banco_pct*100:.0f}%",
            f"  Collar zero-cost:           {_zero_cost_str}",
            f"  Vol. histórica promedio:    {self.vol_promedio*100:.1f}% anual",
            f"  Prima call (prot.) prom.:   ${self.prima_call_promedio_mxn_por_usd:.4f} MXN/USD",
            f"  Prima put (financ.) prom.:  ${self.prima_put_promedio_mxn_por_usd:.4f} MXN/USD",
            f"  Prima neta prom./USD:       ${self.prima_neta_promedio_mxn_por_usd:.4f} MXN",
            "-" * 65,
            f"  Costo total SIN cobertura:  ${self.costo_total_spot_mxn:>16,.2f} MXN",
            f"  Costo total CON collar:     ${self.costo_total_collar_mxn:>16,.2f} MXN",
            f"    ├─ Prima neta pagada:     ${self.prima_neta_total_pagada_mxn:>16,.2f} MXN",
            f"    ├─ Markup HedgePoint:     ${self.costo_total_markup_hp_mxn:>16,.2f} MXN",
            f"    └─ Fee HedgePoint:        ${self.costo_total_fee_hp_mxn:>16,.2f} MXN",
            f"  Ahorro total vs spot:       ${self.ahorro_total_vs_spot_mxn:>16,.2f} MXN",
            f"  Ahorro total (%):           {self.ahorro_total_porcentaje:>15.2f} %",
            "-" * 65,
            f"  Meses call ejercido (prot.): {self.meses_call_ejercido}/{self.total_meses} ({_pct_call:.0f}%) — USD subió",
            f"  Meses zona libre (spot):     {self.meses_zona_libre}/{self.total_meses} ({_pct_libre:.0f}%)",
            f"  Meses put ejercido (limit.): {self.meses_put_ejercido}/{self.total_meses} ({_pct_put:.0f}%) — USD bajó mucho",
        ]
        if self.mejor_mes:
            lineas.append(
                f"  Mejor mes:                 {self.mejor_mes.periodo} "
                f"(${self.mejor_mes.ahorro_vs_spot_mxn:,.2f} MXN)"
            )
        if self.peor_mes:
            lineas.append(
                f"  Peor mes:                  {self.peor_mes.periodo} "
                f"(${self.peor_mes.ahorro_vs_spot_mxn:,.2f} MXN)"
            )
        lineas.append("=" * 65)
        return "\n".join(lineas)


def simulate_collar_strategy(
    parametros: ParametrosCliente,
    years: int = 2,
    markup_banco_pct: float = 0.15,
    call_otm_pct: float = 0.03,
    ventana_vol_dias: int = 30,
    db_path: Path = DB_PATH,
    resultado_forwards: ResultadoSimulacion | None = None,
) -> ResultadoSimulacionCollar:
    """
    Simula una estrategia de collar mensual USD/MXN para un importador.

    Convención: el subyacente es USD/MXN (cuántos pesos cuesta 1 USD).
    El importador COMPRA USD (paga MXN). Quiere protección si el USD SUBE.

    Estructura del collar
    ---------------------
    - **Call ATM COMPRADO** (strike = spot_contratacion):
        Da derecho a comprar USD al strike si el USD sube por encima de él.
        Protege al importador ante depreciación del peso.
        Prima pagada al banco: call_teorico_GK × (1 + markup_banco_pct).

    - **Put OTM VENDIDO** (strike = spot_contratacion × (1 - call_otm_pct)):
        El importador vende un put OTM por debajo del spot.
        El banco (comprador del put) puede ejercerlo si el USD cae por debajo del
        strike_put_vendido, obligando al importador a comprar USD al strike (> spot
        de mercado), limitando el beneficio de la apreciación.
        Prima recibida del banco: put_teorico_GK × (1 - markup_banco_pct).

    Escenarios al vencimiento (tres regiones mutuamente excluyentes)
    ----------------------------------------------------------------
    strike_put_vendido < strike_call_comprado = spot_contratacion, siempre.

    A) spot_venc > strike_call_comprado  →  "call_ejercido"
       USD subió (peso se depreció). El importador ejerce el call y compra
       al strike_call_comprado. PROTECCIÓN ACTIVADA.
       tc_efectivo = strike_call_comprado.

    B) strike_put_vendido <= spot_venc <= strike_call_comprado  →  "zona_libre"
       TC se movió poco. Ni el call ni el put se ejercen.
       El importador compra a spot_venc (mercado).
       tc_efectivo = spot_venc.

    C) spot_venc < strike_put_vendido  →  "put_ejercido"
       USD bajó mucho (peso se apreció fuertemente). El banco ejerce el put
       vendido: el importador queda obligado a comprar al strike_put_vendido,
       que está POR ENCIMA del spot de mercado. LIMITA EL BENEFICIO.
       tc_efectivo = strike_put_vendido.

    Costos de HedgePoint incluidos:
        - markup_hedgepoint (MXN/USD) sobre el volumen total.
        - fee_mensual (MXN fijo).

    Args:
        parametros: Parámetros del cliente.
        years: Años de histórico a simular (default: 2).
        markup_banco_pct: Markup del banco sobre las primas teóricas (default: 15%).
            Al comprar el call: call_banco = call_teorico × (1 + markup_banco_pct).
            Al vender el put:   put_banco  = put_teorico  × (1 - markup_banco_pct).
        call_otm_pct: Distancia OTM del put vendido POR DEBAJO del spot (default: 3%).
            strike_put_vendido = spot_contratacion × (1 - call_otm_pct).
            El parámetro CLI se llama --call-otm por simetría histórica.
        ventana_vol_dias: Días hábiles para la ventana de vol histórica (default: 30).
        db_path: Ruta a la base de datos SQLite.
        resultado_forwards: Resultado de forwards del mismo período para comparación.

    Returns:
        ResultadoSimulacionCollar con resultados por período y estadísticas.

    Raises:
        ValueError: Si no hay datos suficientes en la DB o call_otm_pct <= 0.
    """
    from core.models.pricing import calcular_opcion_gk

    if call_otm_pct <= 0:
        raise ValueError(
            f"call_otm_pct debe ser positivo, recibido: {call_otm_pct}. "
            "Usa 0.03 para un put vendido 3% OTM por debajo del spot."
        )

    hoy = date.today()
    fecha_inicio_datos = date(hoy.year - years, hoy.month, 1)
    fecha_datos_extendida = date(
        fecha_inicio_datos.year - (1 if fecha_inicio_datos.month == 1 else 0),
        ((fecha_inicio_datos.month - 2) % 12) + 1,
        1,
    )

    df_fx = _cargar_fx_historico(fecha_datos_extendida, hoy, db_path)

    meses = pd.date_range(start=fecha_inicio_datos, end=hoy, freq="MS")

    _fwd_por_periodo: dict[str, ResultadoPeriodo] = {}
    if resultado_forwards is not None:
        for fwd_per in resultado_forwards.periodos:
            _fwd_por_periodo[fwd_per.periodo] = fwd_per

    p = parametros
    volumen = p.volumen_mensual_usd
    plazo_dias = p.plazo_forward_dias

    periodos: list[ResultadoPeriodoCollar] = []

    for mes_ts in meses:
        mes_dt = mes_ts.date()
        periodo_str = mes_dt.strftime("%Y-%m")

        # --- Fecha de compra (vencimiento) ---
        fecha_compra = None
        spot_compra = None
        for offset in range(6):
            candidato = mes_dt + pd.Timedelta(days=offset)
            fila = df_fx[df_fx["fecha"] == pd.Timestamp(candidato)]
            if not fila.empty:
                fecha_compra = fila.iloc[0]["fecha"].date()
                spot_compra = float(fila.iloc[0]["tc"])
                break

        if fecha_compra is None:
            logger.warning(
                "Sin datos hábiles para %s; omitiendo período.", periodo_str
            )
            continue

        # --- Fecha de contratación ---
        fecha_contratacion_raw = fecha_compra - pd.Timedelta(days=plazo_dias)
        try:
            fecha_contratacion, spot_contratacion = _tc_mas_cercano(
                df_fx,
                fecha_contratacion_raw.date()
                if hasattr(fecha_contratacion_raw, "date")
                else fecha_contratacion_raw,
            )
        except ValueError as e:
            logger.warning(
                "No hay TC %dd antes de %s: %s", plazo_dias, periodo_str, e
            )
            continue

        # --- Volatilidad histórica ---
        vol = _calcular_vol_historica(df_fx, fecha_contratacion, ventana_vol_dias)

        # --- Strikes ---
        # El importador COMPRA USD. Necesita protección si USD SUBE (peso deprecia).
        # Convención USD/MXN: S = cuántos pesos cuesta 1 USD.
        #
        # Call ATM comprado:  strike_call_comprado = spot_contratacion (ATM)
        #   → el importador puede comprar USD al spot de hoy si el USD sube.
        # Put OTM vendido:    strike_put_vendido   = spot_contratacion × (1 - otm_pct)
        #   → OTM por debajo del spot; solo se activa si el USD cae mucho.
        strike_call_comprado = spot_contratacion                         # ATM
        strike_put_vendido   = spot_contratacion * (1.0 - call_otm_pct) # OTM −N%

        # --- Pricing de ambas patas (Garman-Kohlhagen) ---
        try:
            gk_call = calcular_opcion_gk(
                spot=spot_contratacion,
                strike=strike_call_comprado,
                dias=plazo_dias,
                vol=vol,
                tiie=p.tiie,
                sofr=p.sofr,
            )
            gk_put = calcular_opcion_gk(
                spot=spot_contratacion,
                strike=strike_put_vendido,
                dias=plazo_dias,
                vol=vol,
                tiie=p.tiie,
                sofr=p.sofr,
            )
        except ValueError as e:
            logger.error("Error calculando opciones GK para %s: %s", periodo_str, e)
            continue

        # Call comprado: importador paga al banco → banco cobra markup
        prima_call_banco = gk_call.call * (1.0 + markup_banco_pct)
        # Put vendido: importador recibe del banco → banco descuenta markup
        prima_put_banco  = gk_put.put  * (1.0 - markup_banco_pct)

        # Prima neta = lo que el importador desembolsa en neto
        # Positivo = paga (call cuesta más de lo que el put genera)
        # Negativo = cobra (zero-cost o crédito)
        prima_neta       = prima_call_banco - prima_put_banco  # MXN/USD
        prima_neta_total = prima_neta * volumen                # MXN total

        # --- Escenario al vencimiento ---
        # strike_put_vendido < strike_call_comprado = spot_contratacion, siempre.
        #
        # A) spot_compra > strike_call_comprado  →  "call_ejercido"
        #    USD subió (peso depreció). El importador ejerce el call comprado
        #    y compra USD al strike_call_comprado. PROTECCIÓN ACTIVADA.
        #
        # B) spot_compra < strike_put_vendido  →  "put_ejercido"
        #    USD bajó mucho (peso se apreció fuertemente). El banco ejerce el
        #    put vendido: el importador queda obligado a comprar al
        #    strike_put_vendido, que está POR ENCIMA del spot de mercado.
        #    LIMITA EL BENEFICIO.
        #
        # C) strike_put_vendido <= spot_compra <= strike_call_comprado  →  "zona_libre"
        #    TC se movió poco. Ninguna opción se ejerce; importador compra a spot.
        if spot_compra > strike_call_comprado:
            escenario    = "call_ejercido"
            tc_efectivo  = strike_call_comprado
        elif spot_compra < strike_put_vendido:
            escenario    = "put_ejercido"
            tc_efectivo  = strike_put_vendido
        else:
            escenario    = "zona_libre"
            tc_efectivo  = spot_compra

        call_comprado_ejercido        = escenario == "call_ejercido"
        put_vendido_ejercido_en_contra = escenario == "put_ejercido"

        # --- Costos ---
        costo_spot_ref   = volumen * spot_compra
        costo_subyacente = volumen * tc_efectivo
        costo_markup_hp  = volumen * p.markup_hedgepoint
        costo_fee        = p.fee_mensual
        costo_collar     = costo_subyacente + prima_neta_total + costo_markup_hp + costo_fee

        ahorro_vs_spot = costo_spot_ref - costo_collar
        ahorro_pct     = (ahorro_vs_spot / costo_spot_ref * 100) if costo_spot_ref > 0 else 0.0

        fwd_per = _fwd_por_periodo.get(periodo_str)
        ahorro_vs_forward = (
            fwd_per.costo_forward_mxn - costo_collar if fwd_per is not None else 0.0
        )

        periodos.append(ResultadoPeriodoCollar(
            periodo=periodo_str,
            fecha_compra=fecha_compra,
            spot_compra=spot_compra,
            spot_contratacion=spot_contratacion,
            fecha_contratacion=fecha_contratacion,
            vol_historica=vol,
            strike_call_comprado=strike_call_comprado,
            prima_call_teorica_mxn=gk_call.call,
            prima_call_banco_mxn=prima_call_banco,
            call_comprado_ejercido=call_comprado_ejercido,
            strike_put_vendido=strike_put_vendido,
            otm_pct=call_otm_pct,
            prima_put_teorica_mxn=gk_put.put,
            prima_put_banco_mxn=prima_put_banco,
            put_vendido_ejercido_en_contra=put_vendido_ejercido_en_contra,
            prima_neta_mxn=prima_neta,
            prima_neta_total_mxn=prima_neta_total,
            escenario=escenario,
            tc_efectivo=tc_efectivo,
            volumen_usd=volumen,
            costo_spot_mxn=costo_spot_ref,
            costo_collar_mxn=costo_collar,
            costo_markup_hp_mxn=costo_markup_hp,
            costo_fee_hp_mxn=costo_fee,
            ahorro_vs_spot_mxn=ahorro_vs_spot,
            ahorro_vs_forward_mxn=ahorro_vs_forward,
            ahorro_porcentaje=ahorro_pct,
        ))

    if not periodos:
        raise ValueError(
            "La simulación de collar no produjo resultados. "
            "Verifica que haya datos históricos suficientes en la BD."
        )

    logger.info(
        "Simulación de collar completada: %d períodos procesados.", len(periodos)
    )

    return ResultadoSimulacionCollar(
        parametros=parametros,
        periodos=periodos,
        fecha_inicio=periodos[0].fecha_compra,
        fecha_fin=periodos[-1].fecha_compra,
        markup_banco_pct=markup_banco_pct,
        otm_pct=call_otm_pct,
    )


# ---------------------------------------------------------------------------
# Comparativa de estrategias y mezcla óptima
# ---------------------------------------------------------------------------

@dataclass
class MetricasEstrategia:
    """Métricas resumidas de una estrategia a un nivel de cobertura dado."""
    instrumento: str
    """'forward', 'opcion' o 'collar'."""
    cobertura_pct: float
    """Porcentaje del volumen mensual cubierto (25 / 50 / 75 / 100)."""
    costo_total_mxn: float
    """Costo total acumulado en MXN del período analizado."""
    costo_vs_spot_mxn: float
    """Diferencia total vs comprar 100% a spot (negativo = más caro que spot)."""
    pct_margen: float
    """costo_vs_spot / margen_total × 100 (cuánto del margen impacta)."""
    meses_con_valor: int
    """Meses en que la estrategia fue ventajosa frente a spot."""
    peor_mes_evitado_mxn: float
    """Mayor ahorro mensual individual — proxy de la protección real aportada."""
    vol_mensual_mxn: float
    """Desviación estándar del resultado mensual (volatilidad del outcome)."""
    ratio_costo_proteccion: float
    """costo_total / peor_mes_evitado (menor = mejor relación costo/protección)."""


@dataclass
class MixOptimo:
    """Mezcla óptima de estrategias de cobertura encontrada por find_optimal_mix."""
    tipo: str
    """'puro' si una sola estrategia domina; 'combinado' si se mezclan."""
    instrumento_principal: str
    """Nombre de la estrategia dominante o descripción del mix."""
    pct_forward: float
    """Porcentaje del volumen cubierto con forwards."""
    pct_opcion: float
    """Porcentaje del volumen cubierto con opciones."""
    pct_collar: float
    """Porcentaje del volumen cubierto con collar."""
    pct_sin_cubrir: float
    """Porcentaje del volumen sin cobertura (spot)."""
    costo_total_mxn: float
    """Costo total acumulado del mix en MXN."""
    costo_vs_spot_mxn: float
    """Diferencia total vs 100% spot."""
    pct_margen: float
    """Impacto sobre el margen de utilidad."""
    meses_protegidos: int
    """Meses en que el mix fue ventajoso vs spot."""
    peor_mes_evitado_mxn: float
    """Mayor ahorro mensual del mix."""
    vol_mensual_mxn: float
    """Volatilidad del resultado mensual del mix."""
    ratio_costo_proteccion: float
    """Ratio costo/protección del mix."""
    razon_seleccion: str
    """Texto explicativo de por qué se eligió este mix."""


@dataclass
class ResultadoComparativa:
    """Resultado completo de la comparativa de estrategias de cobertura."""
    parametros: ParametrosCliente
    estrategias_50pct: list[MetricasEstrategia]
    """Las 3 estrategias evaluadas al 50% de cobertura."""
    todas_metricas: list[MetricasEstrategia]
    """Las 3 estrategias × 4 niveles = hasta 12 entradas."""
    mix_optimo: MixOptimo
    """Mezcla óptima encontrada."""
    resultado_forward: "ResultadoSimulacion"
    resultado_opciones: ResultadoSimulacionOpciones
    resultado_collar: ResultadoSimulacionCollar
    costo_total_spot_mxn: float
    """Costo de referencia: 100% spot durante el período."""
    margen_total_mxn: float
    """Margen de utilidad total del período."""


def _metricas_desde_forward(
    resultado: "ResultadoSimulacion",
    cobertura_pct: float,
) -> MetricasEstrategia:
    """Calcula MetricasEstrategia para forwards a un nivel de cobertura dado."""
    p = resultado.parametros
    frac = cobertura_pct / 100.0
    margen_total = resultado.costo_total_spot_mxn * p.margen_utilidad

    resultados_mes: list[float] = []
    meses_con_valor = 0
    peor_mes_evitado = 0.0
    costo_total = 0.0

    for per in resultado.periodos:
        vol = per.volumen_usd
        costo_fwd_teorico = vol * frac * per.forward_30d
        costo_spread = vol * frac * p.spread_banco
        costo_markup = vol * frac * p.markup_hedgepoint
        costo_fee = p.fee_mensual
        costo_spot_nc = vol * (1.0 - frac) * per.spot
        costo_mes = costo_fwd_teorico + costo_spread + costo_markup + costo_fee + costo_spot_nc
        ahorro_mes = per.costo_spot_mxn - costo_mes
        resultados_mes.append(ahorro_mes)
        costo_total += costo_mes
        if ahorro_mes > 0:
            meses_con_valor += 1
            peor_mes_evitado = max(peor_mes_evitado, ahorro_mes)

    costo_vs_spot = resultado.costo_total_spot_mxn - costo_total
    pct_margen = (costo_vs_spot / margen_total * 100) if margen_total > 0 else 0.0
    vol_mensual = float(np.std(resultados_mes)) if resultados_mes else 0.0
    ratio = (abs(costo_vs_spot) / peor_mes_evitado) if peor_mes_evitado > 0 else float("inf")

    return MetricasEstrategia(
        instrumento="forward",
        cobertura_pct=cobertura_pct,
        costo_total_mxn=costo_total,
        costo_vs_spot_mxn=costo_vs_spot,
        pct_margen=pct_margen,
        meses_con_valor=meses_con_valor,
        peor_mes_evitado_mxn=peor_mes_evitado,
        vol_mensual_mxn=vol_mensual,
        ratio_costo_proteccion=ratio,
    )


def _metricas_desde_opciones(
    resultado: ResultadoSimulacionOpciones,
    cobertura_pct: float,
) -> MetricasEstrategia:
    """Calcula MetricasEstrategia para opciones a un nivel de cobertura dado."""
    p = resultado.parametros
    frac = cobertura_pct / 100.0
    margen_total = resultado.costo_total_spot_mxn * p.margen_utilidad

    resultados_mes: list[float] = []
    meses_con_valor = 0
    peor_mes_evitado = 0.0
    costo_total = 0.0

    for per in resultado.periodos:
        vol = per.volumen_usd
        vol_cubierto = vol * frac
        vol_spot = vol * (1.0 - frac)

        # Costo de la parte cubierta con opción
        if per.ejercida:
            costo_subyacente = vol_cubierto * per.strike
        else:
            costo_subyacente = vol_cubierto * per.spot_compra
        costo_prima = per.prima_banco_mxn * vol_cubierto
        costo_markup = vol_cubierto * p.markup_hedgepoint
        costo_fee = p.fee_mensual
        costo_spot_nc = vol_spot * per.spot_compra

        costo_mes = costo_subyacente + costo_prima + costo_markup + costo_fee + costo_spot_nc
        costo_spot_ref = vol * per.spot_compra
        ahorro_mes = costo_spot_ref - costo_mes

        resultados_mes.append(ahorro_mes)
        costo_total += costo_mes
        if ahorro_mes > 0:
            meses_con_valor += 1
            peor_mes_evitado = max(peor_mes_evitado, ahorro_mes)

    costo_vs_spot = resultado.costo_total_spot_mxn - costo_total
    pct_margen = (costo_vs_spot / margen_total * 100) if margen_total > 0 else 0.0
    vol_mensual = float(np.std(resultados_mes)) if resultados_mes else 0.0
    ratio = (abs(costo_vs_spot) / peor_mes_evitado) if peor_mes_evitado > 0 else float("inf")

    return MetricasEstrategia(
        instrumento="opcion",
        cobertura_pct=cobertura_pct,
        costo_total_mxn=costo_total,
        costo_vs_spot_mxn=costo_vs_spot,
        pct_margen=pct_margen,
        meses_con_valor=meses_con_valor,
        peor_mes_evitado_mxn=peor_mes_evitado,
        vol_mensual_mxn=vol_mensual,
        ratio_costo_proteccion=ratio,
    )


def _metricas_desde_collar(
    resultado: ResultadoSimulacionCollar,
    cobertura_pct: float,
) -> MetricasEstrategia:
    """Calcula MetricasEstrategia para collar a un nivel de cobertura dado."""
    p = resultado.parametros
    frac = cobertura_pct / 100.0
    margen_total = resultado.costo_total_spot_mxn * p.margen_utilidad

    resultados_mes: list[float] = []
    meses_con_valor = 0
    peor_mes_evitado = 0.0
    costo_total = 0.0

    for per in resultado.periodos:
        vol = per.volumen_usd
        vol_cubierto = vol * frac
        vol_spot = vol * (1.0 - frac)

        costo_subyacente = vol_cubierto * per.tc_efectivo
        costo_prima_neta = per.prima_neta_mxn * vol_cubierto
        costo_markup = vol_cubierto * p.markup_hedgepoint
        costo_fee = p.fee_mensual
        costo_spot_nc = vol_spot * per.spot_compra

        costo_mes = costo_subyacente + costo_prima_neta + costo_markup + costo_fee + costo_spot_nc
        costo_spot_ref = vol * per.spot_compra
        ahorro_mes = costo_spot_ref - costo_mes

        resultados_mes.append(ahorro_mes)
        costo_total += costo_mes
        if ahorro_mes > 0:
            meses_con_valor += 1
            peor_mes_evitado = max(peor_mes_evitado, ahorro_mes)

    costo_vs_spot = resultado.costo_total_spot_mxn - costo_total
    pct_margen = (costo_vs_spot / margen_total * 100) if margen_total > 0 else 0.0
    vol_mensual = float(np.std(resultados_mes)) if resultados_mes else 0.0
    ratio = (abs(costo_vs_spot) / peor_mes_evitado) if peor_mes_evitado > 0 else float("inf")

    return MetricasEstrategia(
        instrumento="collar",
        cobertura_pct=cobertura_pct,
        costo_total_mxn=costo_total,
        costo_vs_spot_mxn=costo_vs_spot,
        pct_margen=pct_margen,
        meses_con_valor=meses_con_valor,
        peor_mes_evitado_mxn=peor_mes_evitado,
        vol_mensual_mxn=vol_mensual,
        ratio_costo_proteccion=ratio,
    )


def _evaluar_mix_combinado(
    resultado_fwd: "ResultadoSimulacion",
    resultado_op: ResultadoSimulacionOpciones,
    resultado_col: ResultadoSimulacionCollar,
    pct_fwd: float,
    pct_op: float,
    pct_col: float,
) -> tuple[float, float, float, float, int, float, float]:
    """
    Evalúa una mezcla de forwards + opciones + collar.

    Returns:
        (costo_total, costo_vs_spot, pct_margen, peor_mes_evitado,
         meses_con_valor, vol_mensual, ratio)
    """
    p = resultado_fwd.parametros
    frac_fwd = pct_fwd / 100.0
    frac_op = pct_op / 100.0
    frac_col = pct_col / 100.0
    frac_spot = 1.0 - frac_fwd - frac_op - frac_col

    margen_total = resultado_fwd.costo_total_spot_mxn * p.margen_utilidad

    # Indexar opciones y collar por periodo para lookups O(1)
    op_by_periodo = {per.periodo: per for per in resultado_op.periodos}
    col_by_periodo = {per.periodo: per for per in resultado_col.periodos}

    resultados_mes: list[float] = []
    meses_con_valor = 0
    peor_mes_evitado = 0.0
    costo_total = 0.0
    costo_spot_total = 0.0

    for per_fwd in resultado_fwd.periodos:
        periodo = per_fwd.periodo
        vol = per_fwd.volumen_usd

        # --- Pata forward ---
        costo_fwd = (
            vol * frac_fwd * per_fwd.forward_30d
            + vol * frac_fwd * p.spread_banco
            + vol * frac_fwd * p.markup_hedgepoint
        )

        # --- Pata opción ---
        per_op = op_by_periodo.get(periodo)
        if per_op is not None and frac_op > 0:
            vol_op = vol * frac_op
            if per_op.ejercida:
                costo_op_sub = vol_op * per_op.strike
            else:
                costo_op_sub = vol_op * per_op.spot_compra
            costo_op = costo_op_sub + per_op.prima_banco_mxn * vol_op + vol_op * p.markup_hedgepoint
        else:
            costo_op = 0.0

        # --- Pata collar ---
        per_col = col_by_periodo.get(periodo)
        if per_col is not None and frac_col > 0:
            vol_col = vol * frac_col
            costo_col = (
                vol_col * per_col.tc_efectivo
                + per_col.prima_neta_mxn * vol_col
                + vol_col * p.markup_hedgepoint
            )
        else:
            costo_col = 0.0

        # --- Parte sin cubrir (spot) ---
        costo_spot_nc = vol * frac_spot * per_fwd.spot

        # --- Fee cobrado UNA sola vez por mes ---
        costo_fee = p.fee_mensual

        costo_mes = costo_fwd + costo_op + costo_col + costo_spot_nc + costo_fee
        costo_spot_ref = vol * per_fwd.spot
        ahorro_mes = costo_spot_ref - costo_mes

        resultados_mes.append(ahorro_mes)
        costo_total += costo_mes
        costo_spot_total += costo_spot_ref
        if ahorro_mes > 0:
            meses_con_valor += 1
            peor_mes_evitado = max(peor_mes_evitado, ahorro_mes)

    costo_vs_spot = costo_spot_total - costo_total
    pct_margen = (costo_vs_spot / margen_total * 100) if margen_total > 0 else 0.0
    vol_mensual = float(np.std(resultados_mes)) if resultados_mes else 0.0
    ratio = (abs(costo_vs_spot) / peor_mes_evitado) if peor_mes_evitado > 0 else float("inf")

    return costo_total, costo_vs_spot, pct_margen, peor_mes_evitado, meses_con_valor, vol_mensual, ratio


def find_optimal_mix(
    parametros: ParametrosCliente,
    years: int = 2,
    markup_banco_pct: float = 0.15,
    call_otm_pct: float = 0.03,
    db_path: str = DB_PATH,
) -> "ResultadoComparativa":
    """
    Simula las 3 estrategias puras a 4 niveles de cobertura, calcula métricas
    y encuentra la mezcla óptima minimizando el ratio costo/protección.

    Args:
        parametros: Parámetros del cliente (volumen, margen, fees, markups).
        years: Años históricos a simular (default: 2).
        markup_banco_pct: Markup del banco sobre primas de opciones/collar.
        call_otm_pct: Distancia OTM del put vendido en el collar.
        db_path: Ruta a la base de datos SQLite.

    Returns:
        ResultadoComparativa con todas las métricas y la mezcla óptima.
    """
    from agents.simulator.savings_simulator import (
        SimuladorAhorro,
        simulate_options_strategy,
        simulate_collar_strategy,
    )

    logger.info("Iniciando comparativa de estrategias para find_optimal_mix...")

    # 1. Simular las 3 estrategias base al 100% de cobertura
    params_100 = _dataclass_replace(parametros, cobertura_pct=100.0)

    sim_fwd = SimuladorAhorro(params_100, db_path=db_path, years=years)
    resultado_fwd = sim_fwd.ejecutar()

    resultado_op = simulate_options_strategy(
        params_100, years=years, markup_banco_pct=markup_banco_pct, db_path=db_path
    )

    resultado_col = simulate_collar_strategy(
        params_100,
        years=years,
        markup_banco_pct=markup_banco_pct,
        call_otm_pct=call_otm_pct,
        db_path=db_path,
    )

    # 2. Calcular métricas para 3 estrategias × 4 niveles
    niveles = [25.0, 50.0, 75.0, 100.0]
    todas_metricas: list[MetricasEstrategia] = []
    for nivel in niveles:
        todas_metricas.append(_metricas_desde_forward(resultado_fwd, nivel))
        todas_metricas.append(_metricas_desde_opciones(resultado_op, nivel))
        todas_metricas.append(_metricas_desde_collar(resultado_col, nivel))

    estrategias_50pct = [m for m in todas_metricas if m.cobertura_pct == 50.0]

    # 3. Encontrar la mejor estrategia pura (menor ratio con ratio finito)
    finitas = [m for m in todas_metricas if math.isfinite(m.ratio_costo_proteccion)]
    mejor_pura = min(finitas, key=lambda m: m.ratio_costo_proteccion) if finitas else todas_metricas[0]

    mejor_ratio_puro = mejor_pura.ratio_costo_proteccion
    mejor_pct_fwd_puro = mejor_pura.cobertura_pct if mejor_pura.instrumento == "forward" else 0.0
    mejor_pct_op_puro = mejor_pura.cobertura_pct if mejor_pura.instrumento == "opcion" else 0.0
    mejor_pct_col_puro = mejor_pura.cobertura_pct if mejor_pura.instrumento == "collar" else 0.0

    # 4. Evaluar mezclas combinadas en incrementos de 25%
    mejor_ratio_mix = mejor_ratio_puro
    mejor_combo: tuple[float, float, float] = (mejor_pct_fwd_puro, mejor_pct_op_puro, mejor_pct_col_puro)
    mejor_metricas_mix: tuple | None = None

    incrementos = [0.0, 25.0, 50.0, 75.0, 100.0]
    for pf in incrementos:
        for po in incrementos:
            for pc in incrementos:
                if pf + po + pc > 100.0:
                    continue
                if pf + po + pc == 0.0:
                    continue
                # Evitar los casos puros ya evaluados
                puras = [(100, 0, 0), (0, 100, 0), (0, 0, 100)]
                if (pf, po, pc) in [(p[0], p[1], p[2]) for p in puras]:
                    continue
                try:
                    (ct, cvs, pm, pme, mcv, vm, ratio) = _evaluar_mix_combinado(
                        resultado_fwd, resultado_op, resultado_col, pf, po, pc
                    )
                except Exception:
                    continue
                if math.isfinite(ratio) and ratio < mejor_ratio_mix:
                    mejor_ratio_mix = ratio
                    mejor_combo = (pf, po, pc)
                    mejor_metricas_mix = (ct, cvs, pm, pme, mcv, vm, ratio)

    # 5. Construir MixOptimo
    if mejor_metricas_mix is not None:
        pf, po, pc = mejor_combo
        ct, cvs, pm, pme, mcv, vm, ratio = mejor_metricas_mix
        ps = 100.0 - pf - po - pc
        tipo = "combinado"
        partes = []
        if pf > 0:
            partes.append(f"{pf:.0f}% forward")
        if po > 0:
            partes.append(f"{po:.0f}% opciones")
        if pc > 0:
            partes.append(f"{pc:.0f}% collar")
        if ps > 0:
            partes.append(f"{ps:.0f}% spot")
        instrumento_principal = " + ".join(partes)
        razon = (
            f"La mezcla {instrumento_principal} tiene el mejor ratio "
            f"costo/protección ({ratio:,.0f}), combinando flexibilidad y cobertura."
        )
        mix_optimo = MixOptimo(
            tipo=tipo,
            instrumento_principal=instrumento_principal,
            pct_forward=pf,
            pct_opcion=po,
            pct_collar=pc,
            pct_sin_cubrir=ps,
            costo_total_mxn=ct,
            costo_vs_spot_mxn=cvs,
            pct_margen=pm,
            meses_protegidos=mcv,
            peor_mes_evitado_mxn=pme,
            vol_mensual_mxn=vm,
            ratio_costo_proteccion=ratio,
            razon_seleccion=razon,
        )
    else:
        # La mejor estrategia pura gana
        m = mejor_pura
        ps = 100.0 - m.cobertura_pct
        razon = (
            f"La estrategia {m.instrumento} al {m.cobertura_pct:.0f}% tiene el mejor "
            f"ratio costo/protección ({m.ratio_costo_proteccion:,.0f})."
        )
        mix_optimo = MixOptimo(
            tipo="puro",
            instrumento_principal=m.instrumento,
            pct_forward=m.cobertura_pct if m.instrumento == "forward" else 0.0,
            pct_opcion=m.cobertura_pct if m.instrumento == "opcion" else 0.0,
            pct_collar=m.cobertura_pct if m.instrumento == "collar" else 0.0,
            pct_sin_cubrir=ps,
            costo_total_mxn=m.costo_total_mxn,
            costo_vs_spot_mxn=m.costo_vs_spot_mxn,
            pct_margen=m.pct_margen,
            meses_protegidos=m.meses_con_valor,
            peor_mes_evitado_mxn=m.peor_mes_evitado_mxn,
            vol_mensual_mxn=m.vol_mensual_mxn,
            ratio_costo_proteccion=m.ratio_costo_proteccion,
            razon_seleccion=razon,
        )

    costo_total_spot = resultado_fwd.costo_total_spot_mxn
    margen_total = costo_total_spot * parametros.margen_utilidad

    logger.info(
        "Comparativa completada. Mix óptimo: %s (ratio=%.2f)",
        mix_optimo.instrumento_principal,
        mix_optimo.ratio_costo_proteccion,
    )

    return ResultadoComparativa(
        parametros=parametros,
        estrategias_50pct=estrategias_50pct,
        todas_metricas=todas_metricas,
        mix_optimo=mix_optimo,
        resultado_forward=resultado_fwd,
        resultado_opciones=resultado_op,
        resultado_collar=resultado_col,
        costo_total_spot_mxn=costo_total_spot,
        margen_total_mxn=margen_total,
    )
