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
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Literal

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
            costo_spot = volumen * spot_compra
            costo_fwd_teorico = volumen * precio_forward
            costo_spread = volumen * p.spread_banco
            costo_markup = volumen * p.markup_hedgepoint
            costo_fee = p.fee_mensual
            costo_forward = costo_fwd_teorico + costo_spread + costo_markup + costo_fee

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
