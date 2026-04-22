"""
Generador de PDF profesional bilingüe (español/inglés) para HedgePoint MX.

Produce un reporte de simulación de ahorro por cobertura forward USD/MXN
listo para presentar a prospectos.

Dependencias:
    pip install reportlab matplotlib

Uso:
    from agents.simulator.pdf_generator import generar_pdf
    from agents.simulator.savings_simulator import ResultadoSimulacion

    ruta = generar_pdf(resultado, "output/reporte_cliente.pdf")
"""

from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Backend sin GUI para generación de imágenes
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import KeepTogether

from agents.simulator.savings_simulator import (
    ResultadoSimulacion, ResultadoMultiPlazo, ResumenAnual,
    calcular_metricas_por_nivel, MetricasNivelCobertura,
    ResultadoSimulacionOpciones, ResultadoPeriodoOpciones,
    ResultadoSimulacionCollar, ResultadoPeriodoCollar,
    ResultadoComparativa, MetricasEstrategia, MixOptimo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paleta de colores HedgePoint MX
# ---------------------------------------------------------------------------
AZUL = colors.HexColor("#1a365d")
AZUL_MEDIO = colors.HexColor("#2a4f82")
AZUL_CLARO = colors.HexColor("#e8eef7")
VERDE = colors.HexColor("#2d8659")
VERDE_CLARO = colors.HexColor("#e6f4ed")
GRIS = colors.HexColor("#6b7280")
GRIS_CLARO = colors.HexColor("#f3f4f6")
ROJO = colors.HexColor("#c0392b")
BLANCO = colors.white

PAGE_W, PAGE_H = A4  # 210 x 297 mm


# ---------------------------------------------------------------------------
# Estilos tipográficos
# ---------------------------------------------------------------------------

def _estilos() -> dict:
    """Define los estilos de párrafo del documento."""
    base = getSampleStyleSheet()

    estilos = {
        "titulo_portada": ParagraphStyle(
            "titulo_portada",
            fontName="Helvetica-Bold",
            fontSize=28,
            textColor=BLANCO,
            alignment=TA_CENTER,
            leading=34,
            spaceAfter=6,
        ),
        "subtitulo_portada": ParagraphStyle(
            "subtitulo_portada",
            fontName="Helvetica",
            fontSize=14,
            textColor=colors.HexColor("#b8d4f0"),
            alignment=TA_CENTER,
            leading=18,
        ),
        "etiqueta_portada": ParagraphStyle(
            "etiqueta_portada",
            fontName="Helvetica",
            fontSize=10,
            textColor=colors.HexColor("#90adc9"),
            alignment=TA_CENTER,
        ),
        "encabezado_seccion": ParagraphStyle(
            "encabezado_seccion",
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=AZUL,
            spaceBefore=14,
            spaceAfter=4,
            leading=16,
        ),
        "sub_encabezado": ParagraphStyle(
            "sub_encabezado",
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=AZUL_MEDIO,
            spaceBefore=8,
            spaceAfter=2,
        ),
        "cuerpo": ParagraphStyle(
            "cuerpo",
            fontName="Helvetica",
            fontSize=9,
            textColor=colors.HexColor("#374151"),
            alignment=TA_JUSTIFY,
            leading=13,
            spaceAfter=4,
        ),
        "kpi_numero": ParagraphStyle(
            "kpi_numero",
            fontName="Helvetica-Bold",
            fontSize=22,
            textColor=VERDE,
            alignment=TA_CENTER,
            leading=26,
        ),
        "kpi_etiqueta": ParagraphStyle(
            "kpi_etiqueta",
            fontName="Helvetica",
            fontSize=8,
            textColor=GRIS,
            alignment=TA_CENTER,
            leading=10,
        ),
        "tabla_header": ParagraphStyle(
            "tabla_header",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=BLANCO,
            alignment=TA_CENTER,
        ),
        "tabla_celda": ParagraphStyle(
            "tabla_celda",
            fontName="Helvetica",
            fontSize=8,
            textColor=colors.HexColor("#374151"),
            alignment=TA_CENTER,
        ),
        "tabla_celda_left": ParagraphStyle(
            "tabla_celda_left",
            fontName="Helvetica",
            fontSize=8,
            textColor=colors.HexColor("#374151"),
            alignment=TA_LEFT,
        ),
        "pie": ParagraphStyle(
            "pie",
            fontName="Helvetica",
            fontSize=7,
            textColor=GRIS,
            alignment=TA_CENTER,
        ),
        "recomendacion": ParagraphStyle(
            "recomendacion",
            fontName="Helvetica",
            fontSize=9,
            textColor=colors.HexColor("#1a4731"),
            alignment=TA_JUSTIFY,
            leading=13,
        ),
    }
    return estilos


# ---------------------------------------------------------------------------
# Plantillas de página
# ---------------------------------------------------------------------------

def _crear_plantillas(doc: BaseDocTemplate) -> list[PageTemplate]:
    """Define plantillas de página: portada y páginas interiores."""
    margen = 1.8 * cm

    # Portada: sin encabezado ni pie (fondo azul)
    frame_portada = Frame(0, 0, PAGE_W, PAGE_H, leftPadding=0, rightPadding=0,
                          topPadding=0, bottomPadding=0)
    plantilla_portada = PageTemplate(
        id="portada",
        frames=[frame_portada],
        onPage=_fondo_portada,
    )

    # Páginas interiores
    frame_interior = Frame(
        margen, margen + 1.2 * cm,
        PAGE_W - 2 * margen, PAGE_H - 2 * margen - 2 * cm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    plantilla_interior = PageTemplate(
        id="interior",
        frames=[frame_interior],
        onPage=_encabezado_pie,
    )

    return [plantilla_portada, plantilla_interior]


def _fondo_portada(canvas, doc) -> None:
    """Dibuja el fondo de la portada."""
    canvas.saveState()
    # Fondo azul oscuro
    canvas.setFillColor(AZUL)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=True, stroke=False)
    # Franja verde en la parte inferior
    canvas.setFillColor(VERDE)
    canvas.rect(0, 0, PAGE_W, 3.5 * cm, fill=True, stroke=False)
    # Franja decorativa azul medio
    canvas.setFillColor(AZUL_MEDIO)
    canvas.rect(0, 3.5 * cm, PAGE_W, 0.4 * cm, fill=True, stroke=False)
    canvas.restoreState()


def _encabezado_pie(canvas, doc) -> None:
    """Dibuja encabezado y pie de página en páginas interiores."""
    canvas.saveState()
    # Línea de encabezado
    canvas.setFillColor(AZUL)
    canvas.rect(1.8 * cm, PAGE_H - 1.8 * cm, PAGE_W - 3.6 * cm, 0.9 * cm,
                fill=True, stroke=False)
    # Texto del encabezado
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(BLANCO)
    canvas.drawString(2.2 * cm, PAGE_H - 1.35 * cm, "HedgePoint MX")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - 2.2 * cm, PAGE_H - 1.35 * cm,
                           "Reporte de Simulación de Cobertura Forward")
    # Línea pie de página
    canvas.setStrokeColor(AZUL_CLARO)
    canvas.setLineWidth(0.5)
    canvas.line(1.8 * cm, 1.5 * cm, PAGE_W - 1.8 * cm, 1.5 * cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GRIS)
    canvas.drawString(1.8 * cm, 1.1 * cm,
                      "HedgePoint MX — Gestión de Riesgos Financieros para PyMEs")
    canvas.drawRightString(PAGE_W - 1.8 * cm, 1.1 * cm,
                           f"Página {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Gráficas (matplotlib → buffer PNG → Image ReportLab)
# ---------------------------------------------------------------------------

def _imagen_desde_figura(
    fig: plt.Figure,
    width_cm: float = 16.0,
    max_height_cm: float = 22.0,
) -> Image:
    """
    Convierte una figura matplotlib a un objeto Image de ReportLab.

    Aplica un límite de alto para evitar LayoutError cuando la figura
    es más alta que el frame disponible (~24 cm en A4 con márgenes).
    """
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    width_pt = width_cm * cm
    max_height_pt = max_height_cm * cm

    # Calcular alto proporcional a partir del tamaño intrínseco de la imagen
    img_tmp = Image(buf)
    if img_tmp.imageWidth > 0:
        aspect = img_tmp.imageHeight / img_tmp.imageWidth
        height_pt = width_pt * aspect
    else:
        height_pt = width_pt  # fallback cuadrado

    buf.seek(0)
    if height_pt > max_height_pt:
        img = Image(buf, width=width_pt, height=max_height_pt)
    else:
        img = Image(buf, width=width_pt, height=height_pt)

    img.hAlign = "CENTER"
    return img


def _grafica_tc_historico(df: pd.DataFrame, periodos_compra: list) -> Image:
    """
    Genera la gráfica de tipo de cambio histórico con puntos de compra marcados.

    Args:
        df: DataFrame con columnas ['fecha', 'tc'].
        periodos_compra: Lista de ResultadoPeriodo.
    """
    fig, ax = plt.subplots(figsize=(12, 4.8))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f9fafb")

    # Línea de TC histórico
    ax.plot(df["fecha"], df["tc"], color="#1a365d",
            linewidth=1.5, label="USD/MXN FIX", zorder=2)
    ax.fill_between(df["fecha"], df["tc"].min() * 0.998, df["tc"],
                    alpha=0.08, color="#1a365d")

    # Puntos de compra al spot (rojo)
    fechas_spot = [pd.Timestamp(p.fecha_compra) for p in periodos_compra]
    spots = [p.spot for p in periodos_compra]
    ax.scatter(fechas_spot, spots, color="#c0392b", s=40, zorder=5,
               label="Compra spot (sin cobertura)", marker="o", linewidths=0.5)

    # Precios forward (verde)
    forwards = [p.forward_30d for p in periodos_compra]
    ax.scatter(fechas_spot, forwards, color="#2d8659", s=40, zorder=5,
               label="Precio forward pactado", marker="^", linewidths=0.5)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.tick_params(axis="y", labelsize=8)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.2f}"))
    ax.set_ylabel("MXN por USD", fontsize=10, color="#374151")
    ax.set_title("Tipo de Cambio USD/MXN FIX — Histórico con Puntos de Compra",
                 fontsize=12, fontweight="bold", color="#1a365d", pad=10)
    ax.legend(fontsize=8, loc="upper left", framealpha=0.9)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout(pad=2.0)

    return _imagen_desde_figura(fig, width_cm=16.5)


def _grafica_ahorro_acumulado(df_periodos: pd.DataFrame) -> Image:
    """Genera las gráficas de ahorro mensual y acumulado apiladas verticalmente."""
    n = len(df_periodos)
    periodos = df_periodos["periodo"].tolist()
    # Mostrar etiqueta cada 3 meses para evitar sobreposición
    tick_step = max(1, round(n / 8))
    tick_indices = list(range(0, n, tick_step))
    tick_labels = [periodos[i] for i in tick_indices]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7))
    fig.patch.set_facecolor("white")

    # --- Panel superior: resultado mensual en barras ---
    # Verde = forward ahorró vs spot; azul claro = cobertura costó más (costo de seguro, no pérdida)
    colores_barras = ["#2d8659" if v >= 0 else "#8eafd4"
                      for v in df_periodos["ahorro_mxn"]]
    ax1.set_facecolor("#f9fafb")
    ax1.bar(
        range(n),
        df_periodos["ahorro_mxn"] / 1000,
        color=colores_barras,
        edgecolor="none",
        width=0.75,
    )
    ax1.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    ax1.set_xticks(tick_indices)
    ax1.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax1.tick_params(axis="y", labelsize=8)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))
    ax1.set_ylabel("Resultado vs Spot (miles MXN)", fontsize=10, color="#374151")
    ax1.set_title("Resultado mensual: Forward vs Spot",
                  fontsize=12, fontweight="bold", color="#1a365d")
    # Leyenda compacta
    from matplotlib.patches import Patch
    ax1.legend(
        handles=[
            Patch(facecolor="#2d8659", label="Ahorro vs Spot / Savings vs Spot"),
            Patch(facecolor="#8eafd4", label="Costo de protección / Hedging cost"),
        ],
        fontsize=7, loc="upper right", framealpha=0.7,
    )
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    # --- Panel inferior: resultado acumulado ---
    ax2.set_facecolor("#f9fafb")
    ahorro_acum = df_periodos["ahorro_acumulado_mxn"] / 1000
    positivo = ahorro_acum >= 0
    ax2.fill_between(range(n), 0, ahorro_acum,
                     where=positivo, alpha=0.25, color="#2d8659")
    # Zona bajo cero: azul claro (costo de seguro), no rojo
    ax2.fill_between(range(n), 0, ahorro_acum,
                     where=~positivo, alpha=0.20, color="#8eafd4")
    ax2.plot(range(n), ahorro_acum,
             color="#1a365d", linewidth=2, marker="o", markersize=4)
    ax2.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    ax2.set_xticks(tick_indices)
    ax2.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax2.tick_params(axis="y", labelsize=8)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))
    ax2.set_ylabel("Resultado acumulado (miles MXN)", fontsize=10, color="#374151")
    ax2.set_title("Resultado acumulado: Forward vs Spot",
                  fontsize=12, fontweight="bold", color="#1a365d")
    ax2.grid(True, alpha=0.3, linestyle="--")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    plt.tight_layout(pad=2.0)
    return _imagen_desde_figura(fig, width_cm=16.5, max_height_cm=19.0)


# ---------------------------------------------------------------------------
# Construcción del PDF
# ---------------------------------------------------------------------------

def _portada(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """Genera el contenido de la portada."""
    p = resultado.parametros
    elementos = []

    # Espacio superior
    elementos.append(Spacer(1, 3.5 * cm))

    # Logo / Marca
    elementos.append(Paragraph("HedgePoint MX", estilos["titulo_portada"]))
    elementos.append(Spacer(1, 0.5 * cm))
    elementos.append(Paragraph(
        "Gestión de Riesgos Financieros para PyMEs Mexicanas",
        estilos["subtitulo_portada"],
    ))
    elementos.append(Spacer(1, 1.5 * cm))

    # Título del reporte
    titulo_es = "SIMULADOR DE AHORRO POR COBERTURA FORWARD"
    titulo_en = "Forward Hedging Savings Simulator"
    elementos.append(Paragraph(titulo_es, ParagraphStyle(
        "titulo_rep",
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=BLANCO,
        alignment=TA_CENTER,
        leading=20,
        spaceBefore=10,
        spaceAfter=4,
    )))
    elementos.append(Paragraph(titulo_en, ParagraphStyle(
        "titulo_rep_en",
        fontName="Helvetica",
        fontSize=12,
        textColor=colors.HexColor("#90adc9"),
        alignment=TA_CENTER,
    )))
    elementos.append(Spacer(1, 2.5 * cm))

    # Datos del análisis en tabla centrada
    datos_tabla = [
        ["Período analizado", f"{resultado.fecha_inicio} — {resultado.fecha_fin}"],
        ["Volumen mensual", f"USD ${p.volumen_mensual_usd:,.0f}"],
        ["Margen de utilidad", f"{p.margen_utilidad * 100:.1f}%"],
        ["Frecuencia de compra", p.frecuencia.capitalize()],
        ["Instrumento evaluado", "Forward a 30 días (USD/MXN)"],
    ]
    t = Table(datos_tabla, colWidths=[5.5 * cm, 7 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1e4070")),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#162d50")),
        ("TEXTCOLOR", (0, 0), (-1, -1), BLANCO),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1),
         [colors.HexColor("#1e4070"), colors.HexColor("#1a3a62")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#2a4f82")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    t.hAlign = "CENTER"
    elementos.append(t)
    elementos.append(Spacer(1, 2 * cm))

    # Fecha de generación (nombres de mes en español, sin depender de locale)
    _MESES_ES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
    }
    _hoy = date.today()
    _fecha_es = f"{_hoy.day} de {_MESES_ES[_hoy.month]} de {_hoy.year}"
    elementos.append(Paragraph(
        f"Generado el {_fecha_es}",
        estilos["etiqueta_portada"],
    ))
    elementos.append(Paragraph(
        "Confidencial — Solo para uso interno y presentación a prospectos",
        estilos["etiqueta_portada"],
    ))

    return elementos


def _kpi_box(numero: str, etiqueta: str, es_negativo: bool = False) -> Table:
    """Genera un cuadro KPI individual."""
    # Negativo = costo de protección (neutral), no pérdida → gris oscuro, no rojo
    color_num = GRIS if es_negativo else VERDE
    estilo_num = ParagraphStyle(
        "kpi_n", fontName="Helvetica-Bold", fontSize=18,
        textColor=color_num, alignment=TA_CENTER, leading=22,
    )
    estilo_lbl = ParagraphStyle(
        "kpi_l", fontName="Helvetica", fontSize=7.5,
        textColor=GRIS, alignment=TA_CENTER, leading=10,
    )
    t = Table(
        [[Paragraph(numero, estilo_num)], [Paragraph(etiqueta, estilo_lbl)]],
        colWidths=[3.8 * cm],
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c5d4e8")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _resumen_ejecutivo(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """Genera la sección de resumen ejecutivo con KPIs."""
    r = resultado
    elementos = []

    elementos.append(Paragraph("Resumen Ejecutivo / Executive Summary",
                               estilos["encabezado_seccion"]))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL,
                                spaceAfter=8))

    # Texto introductorio — enfoque en riesgo primero, costo de cobertura segundo
    _meses_prot = sum(1 for pe in r.periodos if pe.ahorro_mxn > 0)
    _danio_evitado = r.danio_total_evitado_mxn
    _margen_anual = r.costo_total_spot_mxn * r.parametros.margen_utilidad
    _danio_pct_margen = (
        _danio_evitado / _margen_anual * 100 if _margen_anual > 0 else 0.0
    )
    _costo_prot_pct_vol = abs(r.ahorro_total_mxn) / r.costo_total_spot_mxn * 100 if r.costo_total_spot_mxn > 0 else 0.0
    _prima_mensual = abs(r.ahorro_promedio_mensual_mxn)

    texto_es = (
        f"Durante el período <b>{r.fecha_inicio}</b> al <b>{r.fecha_fin}</b>, "
        f"hubo <b>{_meses_prot}</b> meses donde el tipo de cambio se movió "
        f"significativamente en contra de su operación. "
        f"Sin cobertura, estos movimientos habrían costado "
        f"<b>${_danio_evitado:,.0f} MXN</b> — equivalente al "
        f"<b>{_danio_pct_margen:.1f}%</b> de su margen de utilidad. "
        f"La estrategia de cobertura forward habría neutralizado estos golpes "
        f"a un costo de protección de <b>{_costo_prot_pct_vol:.2f}%</b> del volumen operado "
        f"(<b>${_prima_mensual:,.0f} MXN/mes</b>). "
        f"<b>La cobertura es un seguro: su valor está en la certeza presupuestal, "
        f"no en ganarle al mercado.</b>"
    )
    texto_en = (
        f"During the period <b>{r.fecha_inicio}</b> to <b>{r.fecha_fin}</b>, "
        f"there were <b>{_meses_prot}</b> months when the exchange rate moved "
        f"significantly against your operation. "
        f"Without hedging, these moves would have cost "
        f"<b>${_danio_evitado:,.0f} MXN</b> — equivalent to "
        f"<b>{_danio_pct_margen:.1f}%</b> of your profit margin. "
        f"A forward hedging strategy would have neutralized these hits "
        f"at a protection cost of <b>{_costo_prot_pct_vol:.2f}%</b> of FX volume "
        f"(<b>${_prima_mensual:,.0f} MXN/month</b>). "
        f"<b>Hedging is insurance: its value lies in budget certainty, "
        f"not in beating the market.</b>"
    )
    elementos.append(Paragraph(texto_es, estilos["cuerpo"]))
    elementos.append(Paragraph(texto_en, ParagraphStyle(
        "cuerpo_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12, spaceAfter=10,
    )))

    # KPIs — fila 1: resultados de la cobertura
    # Cuando el resultado es negativo se reframea como costo de protección (seguro cambiario)
    _vol_total_mxn = r.costo_total_spot_mxn  # proxy del volumen operado en MXN
    _costo_proteccion_pct = (
        abs(r.ahorro_total_mxn) / _vol_total_mxn * 100
        if _vol_total_mxn > 0 and r.ahorro_total_mxn < 0 else 0.0
    )
    _margen_total_mxn = (
        _vol_total_mxn * r.parametros.margen_utilidad
        if r.parametros.margen_utilidad > 0 else 1.0
    )
    _impacto_margen_pct = (
        abs(r.ahorro_total_mxn) / _margen_total_mxn * 100
        if r.ahorro_total_mxn < 0 else 0.0
    )
    _prima_mensual = abs(r.ahorro_promedio_mensual_mxn) if r.ahorro_promedio_mensual_mxn < 0 else 0.0

    if r.ahorro_total_mxn >= 0:
        kpis_fila1 = [
            (f"${r.ahorro_total_mxn:,.0f}", "Ahorro total MXN\nTotal Savings MXN", False),
            (f"${r.ahorro_promedio_mensual_mxn:,.0f}", "Ahorro promedio mensual\nAvg Monthly Savings", False),
            (f"{r.ahorro_total_porcentaje:.2f}%", "Ahorro sobre costo total\nSavings on Total Cost", False),
            (f"{r.porcentaje_meses_con_ahorro:.0f}%", "Meses con ahorro\nMonths with Savings",
             r.porcentaje_meses_con_ahorro < 50),
            (f"{r.total_meses}", "Meses analizados\nMonths Analyzed", False),
        ]
    else:
        kpis_fila1 = [
            (f"{_costo_proteccion_pct:.2f}%",
             "Costo de protección\n(% del volumen operado)", True),
            (f"${_prima_mensual:,.0f}",
             "Prima de seguro cambiario\nHedging premium / month", True),
            (f"{_impacto_margen_pct:.2f}%",
             "Impacto sobre margen\nImpact on profit margin", True),
            (f"{r.porcentaje_meses_con_ahorro:.0f}%",
             "Meses en que el spot fue peor\nMonths spot exceeded forward", False),
            (f"{r.total_meses}", "Meses analizados\nMonths Analyzed", False),
        ]
    # KPIs — fila 2: desglose de costos transaccionales
    kpis_fila2 = [
        (f"${r.costo_total_forward_teorico_mxn:,.0f}",
         "Forward teórico acumulado\nTheoretical Forward Cost", False),
        (f"${r.costo_total_banco_mxn:,.0f}",
         "Costo total banco (spread)\nTotal Bank Cost (Spread)", True),
        (f"${r.costo_total_markup_hp_mxn:,.0f}",
         "Markup HedgePoint acumulado\nHedgePoint Markup", True),
        (f"${r.costo_total_fee_hp_mxn:,.0f}",
         "Fees HedgePoint acumulados\nHedgePoint Fees", True),
        (f"${r.costo_total_hedgepoint_mxn:,.0f}",
         "Costo total HedgePoint\nTotal HedgePoint Cost", True),
    ]
    fila_kpis = [
        [_kpi_box(num, lbl, neg) for num, lbl, neg in kpis_fila1],
        [_kpi_box(num, lbl, neg) for num, lbl, neg in kpis_fila2],
    ]
    t_kpis = Table(fila_kpis, colWidths=[3.8 * cm] * 5,
                   hAlign="CENTER", spaceBefore=6, spaceAfter=4)
    t_kpis.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    elementos.append(t_kpis)

    # Mejor / peor mes
    if r.mejor_mes and r.peor_mes:
        elementos.append(Spacer(1, 0.5 * cm))
        datos_extremos = [
            ["", "Mes / Month", "Ahorro MXN / Savings MXN", "TC Spot", "TC Forward"],
            [
                Paragraph("<b>Mejor mes / Best month</b>", estilos["tabla_celda_left"]),
                r.mejor_mes.periodo,
                f"${r.mejor_mes.ahorro_mxn:,.2f}",
                f"{r.mejor_mes.spot:.4f}",
                f"{r.mejor_mes.forward_30d:.4f}",
            ],
            [
                Paragraph("<b>Peor mes / Worst month</b>", estilos["tabla_celda_left"]),
                r.peor_mes.periodo,
                f"${r.peor_mes.ahorro_mxn:,.2f}",
                f"{r.peor_mes.spot:.4f}",
                f"{r.peor_mes.forward_30d:.4f}",
            ],
        ]
        t_extremos = Table(datos_extremos,
                           colWidths=[4.5 * cm, 2.5 * cm, 4 * cm, 2.5 * cm, 2.5 * cm])
        t_extremos.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("BACKGROUND", (0, 1), (-1, 1), VERDE_CLARO),
            ("BACKGROUND", (0, 2), (-1, 2), AZUL_CLARO),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elementos.append(t_extremos)

    return elementos


def _tabla_resumen_anual(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """
    Genera una tabla de resumen por año calendario con tendencia FX,
    ahorro/costo total y porcentaje del volumen.
    Solo se incluye si hay datos de 2 o más años distintos.
    """
    resumenes: list[ResumenAnual] = resultado.ahorro_por_anio()
    if len(resumenes) < 2:
        return []  # No vale la pena mostrar si solo hay un año parcial

    elementos = []
    elementos.append(Spacer(1, 0.5 * cm))
    elementos.append(Paragraph(
        "Desempeño por Año / Annual Performance",
        estilos["sub_encabezado"],
    ))
    elementos.append(Spacer(1, 0.2 * cm))

    encabezados = [
        Paragraph("<b>Año\nYear</b>", estilos["tabla_header"]),
        Paragraph("<b>Tendencia FX\nFX Trend</b>", estilos["tabla_header"]),
        Paragraph("<b>Ahorro / Costo\nSavings / Cost (MXN)</b>", estilos["tabla_header"]),
        Paragraph("<b>% del Volumen\n% of Volume</b>", estilos["tabla_header"]),
        Paragraph("<b>TC Spot\nProm.</b>", estilos["tabla_header"]),
        Paragraph("<b>TC Fwd\nProm.</b>", estilos["tabla_header"]),
    ]
    filas = [encabezados]

    for ra in resumenes:
        # Positivo → verde; negativo → gris (costo de seguro, no pérdida)
        _color_hex = "#2d8659" if ra.ahorro_total_mxn >= 0 else "#6b7280"
        signo = "+" if ra.ahorro_total_mxn >= 0 else ""
        tendencia = f"{ra.tendencia_fx}\n{ra.tendencia_fx_en}"
        filas.append([
            Paragraph(f"<b>{ra.anio}</b>", estilos["tabla_celda"]),
            Paragraph(tendencia, estilos["tabla_celda"]),
            Paragraph(
                f"<font color='{_color_hex}'>"
                f"<b>{signo}${ra.ahorro_total_mxn:,.0f}</b></font>",
                estilos["tabla_celda"],
            ),
            Paragraph(
                f"<font color='{_color_hex}'>"
                f"<b>{signo}{ra.ahorro_porcentaje:.2f}%</b></font>",
                estilos["tabla_celda"],
            ),
            Paragraph(f"{ra.tc_promedio_spot:.4f}", estilos["tabla_celda"]),
            Paragraph(f"{ra.tc_promedio_forward:.4f}", estilos["tabla_celda"]),
        ])

    col_widths = [2.0 * cm, 3.5 * cm, 4.5 * cm, 3.0 * cm, 2.5 * cm, 2.5 * cm]
    t = Table(filas, colWidths=col_widths, repeatRows=1)

    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BLANCO, GRIS_CLARO]),
    ]
    t.setStyle(TableStyle(estilo))
    elementos.append(t)
    elementos.append(Spacer(1, 0.3 * cm))

    # Nota explicativa bilingüe
    nota = (
        "Nota: El forward incluye una prima por el diferencial de tasas de interés México-EE.UU. "
        "(TIIE vs SOFR), típicamente 0.3%-0.5% mensual. Para que la cobertura genere ahorro neto, "
        "la depreciación del peso debe superar esta prima. El valor principal de la cobertura no es "
        "generar ahorro, sino eliminar la incertidumbre: fijar el tipo de cambio permite presupuestar con certeza. "
        "/ <i>Note: The forward includes a premium from the Mexico-US interest rate differential "
        "(TIIE vs SOFR), typically 0.3%-0.5% per month. For the hedge to generate net savings, "
        "peso depreciation must exceed this premium. The primary value of hedging is not generating "
        "savings, but eliminating uncertainty: locking in the exchange rate enables budget certainty.</i>"
    )
    elementos.append(Paragraph(nota, ParagraphStyle(
        "nota_anual", fontName="Helvetica", fontSize=7, textColor=GRIS,
        alignment=TA_JUSTIFY, leading=10, spaceAfter=4,
    )))

    return elementos


def _tabla_mensual(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """Genera la tabla detallada mes a mes."""
    elementos = []
    elementos.append(Paragraph("Análisis Mensual / Monthly Breakdown",
                               estilos["encabezado_seccion"]))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL,
                                spaceAfter=6))

    # Abreviaturas para que la tabla quepa en una página A4
    _MESES_ABR = {
        "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr",
        "05": "May", "06": "Jun", "07": "Jul", "08": "Ago",
        "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dic",
    }

    def _abr_periodo(periodo: str) -> str:
        """Convierte '2024-04' → 'Abr-24'."""
        try:
            anio, mes = periodo.split("-")
            return f"{_MESES_ABR.get(mes, mes)}-{anio[2:]}"
        except ValueError:
            return periodo

    encabezados = [
        "Mes",
        "Spot",
        "Fwd",
        "C.Spot\n(MXN)",
        "Fwd\n(MXN)",
        "Spread\n(MXN)",
        "Mkup\n(MXN)",
        "Fee\n(MXN)",
        "Total\nFwd",
        "Resul.\nvs Spot",
        "(%)",
    ]
    filas = [encabezados]

    r = resultado
    for p in r.periodos:
        filas.append([
            _abr_periodo(p.periodo),
            f"{p.spot:.4f}",
            f"{p.forward_30d:.4f}",
            f"${p.costo_spot_mxn:,.0f}",
            f"${p.costo_forward_teorico_mxn:,.0f}",
            f"${p.costo_spread_banco_mxn:,.0f}",
            f"${p.costo_markup_hp_mxn:,.0f}",
            f"${p.costo_fee_hp_mxn:,.0f}",
            f"${p.costo_forward_mxn:,.0f}",
            f"${p.ahorro_mxn:,.0f}",
            f"{p.ahorro_porcentaje:.1f}%",
        ])

    # Fila de totales
    filas.append([
        Paragraph("<b>TOTAL</b>", estilos["tabla_celda"]),
        "", "",
        Paragraph(f"<b>${r.costo_total_spot_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_forward_teorico_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_banco_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_markup_hp_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_fee_hp_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_forward_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.ahorro_total_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>{r.ahorro_total_porcentaje:.1f}%</b>", estilos["tabla_celda"]),
    ])

    col_widths = [1.4 * cm, 1.6 * cm, 1.6 * cm, 2.2 * cm,
                  2.1 * cm, 2.0 * cm, 1.8 * cm, 1.8 * cm,
                  2.1 * cm, 2.1 * cm, 1.3 * cm]
    t = Table(filas, colWidths=col_widths, repeatRows=1)

    # Estilos base
    estilo_tabla = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        # Fondo más claro en columnas de desglose para diferenciarlas visualmente
        ("BACKGROUND", (4, 1), (7, -2), colors.HexColor("#f0f4fa")),
        # Filas alternas (solo en columnas no de desglose)
        ("ROWBACKGROUNDS", (0, 1), (3, -2), [BLANCO, GRIS_CLARO]),
        ("ROWBACKGROUNDS", (8, 1), (-1, -2), [BLANCO, GRIS_CLARO]),
        # Fila de totales
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, AZUL),
    ]

    # Colorear celdas de resultado vs spot (columnas 9 y 10)
    # Verde = el forward ahorró; gris oscuro = costo de protección (no rojo, no es una pérdida)
    for i, p in enumerate(r.periodos, start=1):
        col_ahorro = 9
        col_pct = 10
        if p.ahorro_mxn > 0:
            estilo_tabla.append(("TEXTCOLOR", (col_ahorro, i), (col_pct, i), VERDE))
        else:
            estilo_tabla.append(("TEXTCOLOR", (col_ahorro, i), (col_pct, i), GRIS))

    t.setStyle(TableStyle(estilo_tabla))
    elementos.append(t)
    return elementos


def _seccion_recomendacion(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """Genera la sección de recomendación final."""
    elementos = []
    elementos.append(Paragraph("Recomendación / Recommendation",
                               estilos["encabezado_seccion"]))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=VERDE,
                                spaceAfter=8))

    r = resultado
    p = r.parametros

    # Caja de recomendación en verde claro
    if r.ahorro_total_mxn > 0:
        texto_rec_es = (
            f"Con base en el backtesting de los últimos {r.total_meses} meses, "
            f"la implementación de una estrategia de cobertura forward a 30 días "
            f"habría generado un ahorro acumulado de <b>${r.ahorro_total_mxn:,.0f} MXN</b> "
            f"(<b>{r.ahorro_total_porcentaje:.2f}%</b> del costo total sin cobertura). "
            f"La cobertura fue efectiva en el <b>{r.porcentaje_meses_con_ahorro:.0f}%</b> "
            f"de los meses analizados. "
            f"<b>HedgePoint MX recomienda implementar coberturas forward mensuales</b> "
            f"para proteger el margen de utilidad ante la volatilidad cambiaria."
        )
        texto_rec_en = (
            f"Based on the {r.total_meses}-month backtesting, a 30-day forward hedging "
            f"strategy would have saved <b>${r.ahorro_total_mxn:,.0f} MXN</b> "
            f"(<b>{r.ahorro_total_porcentaje:.2f}%</b> of the unhedged cost), "
            f"outperforming the spot market in <b>{r.porcentaje_meses_con_ahorro:.0f}%</b> "
            f"of months. <b>HedgePoint MX recommends implementing monthly forward hedges</b> "
            f"to protect profit margins against FX volatility."
        )
        bg_color = VERDE_CLARO
        border_color = VERDE
    else:
        texto_rec_es = (
            f"Durante el período analizado, el tipo de cambio forward fue marginalmente "
            f"superior al spot en promedio, lo que indica un mercado con condiciones "
            f"favorables para compradores spot. Sin embargo, la cobertura ofrece "
            f"<b>certeza presupuestal y protección ante escenarios adversos</b>. "
            f"<b>HedgePoint MX recomienda evaluar una estrategia combinada</b> "
            f"(forwards + opciones) para equilibrar costo y protección."
        )
        texto_rec_en = (
            f"During the analyzed period, forward rates were marginally above spot on average. "
            f"However, hedging provides <b>budget certainty and downside protection</b>. "
            f"<b>HedgePoint MX recommends a combined strategy</b> "
            f"(forwards + options) to balance cost and protection."
        )
        bg_color = colors.HexColor("#fff8e1")
        border_color = colors.HexColor("#f59e0b")

    # Tabla de caja de recomendación
    caja = Table(
        [[Paragraph(texto_rec_es, estilos["recomendacion"])],
         [Paragraph(texto_rec_en, ParagraphStyle(
             "rec_en", fontName="Helvetica-Oblique", fontSize=8.5,
             textColor=GRIS, alignment=TA_JUSTIFY, leading=12,
         ))]],
        colWidths=[16 * cm],
    )
    caja.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 1.5, border_color),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elementos.append(caja)
    elementos.append(Spacer(1, 0.6 * cm))

    # Próximos pasos
    elementos.append(Paragraph("Próximos Pasos / Next Steps",
                               estilos["sub_encabezado"]))
    pasos = [
        "1. Agendar reunión de diagnóstico de exposición cambiaria (sin costo).",
        "2. Definir estrategia de cobertura personalizada según perfil de riesgo.",
        "3. Implementar primeras coberturas con monitoreo continuo vía HedgePoint MX.",
        "4. Revisión mensual de resultados y ajuste de la estrategia.",
    ]
    for paso in pasos:
        elementos.append(Paragraph(paso, estilos["cuerpo"]))

    elementos.append(Spacer(1, 0.6 * cm))

    # Disclaimers
    disclaimer = (
        "* Este reporte es un análisis histórico con fines ilustrativos. Los resultados "
        "pasados no garantizan rendimientos futuros. Los precios forward son teóricos y "
        "calculados mediante paridad cubierta de tasas de interés (TIIE/SOFR). "
        "HedgePoint MX no es una institución financiera regulada; para contratos "
        "financieros formales, contacta a un banco o casa de bolsa autorizada."
    )
    disclaimer_en = (
        "* This report is a historical analysis for illustrative purposes only. Past "
        "performance does not guarantee future results. Forward prices are theoretical, "
        "calculated using covered interest rate parity (TIIE/SOFR). HedgePoint MX is not "
        "a regulated financial institution; for formal financial contracts, contact an "
        "authorized bank or brokerage."
    )
    elementos.append(Paragraph(disclaimer, estilos["pie"]))
    elementos.append(Paragraph(disclaimer_en, estilos["pie"]))

    return elementos


# ---------------------------------------------------------------------------
# Sección: desglose de costos transaccionales
# ---------------------------------------------------------------------------

def _seccion_desglose_costos(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """
    Genera la sección de desglose de costos transaccionales.
    Solo se llama si hay costos de spread, markup o fee configurados.
    """
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Desglose de Costos / Cost Breakdown",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    intro_es = (
        "La siguiente tabla muestra el desglose exacto de cada peso pagado en la "
        "estrategia de cobertura. <b>HedgePoint MX cobra de forma transparente.</b> "
        "Los bancos incluyen su spread en el precio del forward sin desglosarlo, "
        "lo que hace imposible comparar costos reales sin este análisis."
    )
    intro_en = (
        "The table below shows the exact breakdown of every peso paid in the hedging "
        "strategy. <b>HedgePoint MX charges transparently.</b> Banks embed their spread "
        "in the forward price without disclosure, making real cost comparison impossible "
        "without this analysis."
    )
    elementos.append(Paragraph(intro_es, estilos["cuerpo"]))
    elementos.append(Paragraph(intro_en, ParagraphStyle(
        "cb_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12, spaceAfter=8,
    )))

    costo_total = r.costo_total_forward_mxn
    volumen_total_usd = sum(p2.volumen_usd for p2 in r.periodos)

    def _pct(val: float) -> str:
        return f"{val / costo_total * 100:.1f}%" if costo_total else "—"

    filas = [
        [
            Paragraph("<b>Concepto / Concept</b>", estilos["tabla_header"]),
            Paragraph("<b>Costo unitario\nUnit cost</b>", estilos["tabla_header"]),
            Paragraph("<b>Costo total periodo\nTotal period cost</b>", estilos["tabla_header"]),
            Paragraph("<b>% del total\n% of total</b>", estilos["tabla_header"]),
        ],
        [
            "Forward teórico (TIIE/SOFR)\nTheoretical Forward (TIIE/SOFR)",
            f"TC forward prom.\nAvg fwd rate",
            f"${r.costo_total_forward_teorico_mxn:,.0f} MXN",
            _pct(r.costo_total_forward_teorico_mxn),
        ],
        [
            "Spread banco / Bank spread",
            f"${p.spread_banco:.2f} MXN/USD",
            f"${r.costo_total_banco_mxn:,.0f} MXN",
            _pct(r.costo_total_banco_mxn),
        ],
        [
            "Markup HedgePoint",
            f"${p.markup_hedgepoint:.2f} MXN/USD",
            f"${r.costo_total_markup_hp_mxn:,.0f} MXN",
            _pct(r.costo_total_markup_hp_mxn),
        ],
        [
            "Fee consultoría HedgePoint\nHedgePoint consulting fee",
            f"${p.fee_mensual:,.0f} MXN/mes",
            f"${r.costo_total_fee_hp_mxn:,.0f} MXN",
            _pct(r.costo_total_fee_hp_mxn),
        ],
        [
            Paragraph("<b>TOTAL</b>", estilos["tabla_celda"]),
            "",
            Paragraph(f"<b>${costo_total:,.0f} MXN</b>", estilos["tabla_celda"]),
            Paragraph("<b>100.0%</b>", estilos["tabla_celda"]),
        ],
    ]

    col_widths = [6.5 * cm, 3.5 * cm, 4.5 * cm, 2.5 * cm]
    t = Table(filas, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [BLANCO, GRIS_CLARO]),
        # Fila forward teórico en azul muy claro (no es costo HP ni banco)
        ("BACKGROUND", (0, 1), (-1, 1), AZUL_CLARO),
        # Filas banco y HP con tono diferenciador
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#fff3cd")),  # banco — amarillo
        ("BACKGROUND", (0, 3), (-1, 4), VERDE_CLARO),                 # HP — verde
        # Totales
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, AZUL),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elementos.append(t)

    # Nota de transparencia
    nota = (
        "† El costo total de HedgePoint MX (markup + fee) fue "
        f"<b>${r.costo_total_hedgepoint_mxn:,.0f} MXN</b> en el período "
        f"({_pct(r.costo_total_hedgepoint_mxn)} del costo total con cobertura). "
        "El costo del spread bancario fue "
        f"<b>${r.costo_total_banco_mxn:,.0f} MXN</b> "
        f"({_pct(r.costo_total_banco_mxn)}), normalmente invisible en el precio forward del banco. "
        "† HedgePoint MX total cost (markup + fee) was "
        f"<b>${r.costo_total_hedgepoint_mxn:,.0f} MXN</b> "
        f"({_pct(r.costo_total_hedgepoint_mxn)} of total hedged cost). "
        "Bank spread cost was "
        f"<b>${r.costo_total_banco_mxn:,.0f} MXN</b> "
        f"({_pct(r.costo_total_banco_mxn)}), typically hidden inside the bank's forward quote."
    )
    elementos.append(Spacer(1, 0.3 * cm))
    elementos.append(Paragraph(nota, estilos["pie"]))

    return elementos


# ---------------------------------------------------------------------------
# Sección: comparativa multi-plazo
# ---------------------------------------------------------------------------

def _grafica_comparativa_plazos(multi: ResultadoMultiPlazo) -> Image:
    """Gráfica de barras agrupadas con el ahorro mensual de los 3 plazos."""
    r30 = multi.plazo_30d.to_dataframe()
    r60 = multi.plazo_60d.to_dataframe()
    r90 = multi.plazo_90d.to_dataframe()

    # Alinear períodos comunes a los 3 plazos
    periodos = sorted(
        set(r30["periodo"]) & set(r60["periodo"]) & set(r90["periodo"])
    )
    if not periodos:
        periodos = r30["periodo"].tolist()

    def _ahorros(df: pd.DataFrame) -> list[float]:
        return [
            float(df.loc[df["periodo"] == p, "ahorro_mxn"].iloc[0]) / 1000
            if p in df["periodo"].values else 0.0
            for p in periodos
        ]

    a30 = _ahorros(r30)
    a60 = _ahorros(r60)
    a90 = _ahorros(r90)

    n = len(periodos)
    x = np.arange(n)
    ancho = 0.26

    fig, ax = plt.subplots(figsize=(10, 4.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f9fafb")

    b30 = ax.bar(x - ancho, a30, ancho, label="30 días", color="#1a365d", alpha=0.85)
    b60 = ax.bar(x,         a60, ancho, label="60 días", color="#2d8659", alpha=0.85)
    b90 = ax.bar(x + ancho, a90, ancho, label="90 días", color="#e67e00", alpha=0.85)

    ax.axhline(0, color="#374151", linewidth=0.8, linestyle="--")

    tick_step = max(1, round(n / 8))
    ax.set_xticks(x[::tick_step])
    ax.set_xticklabels(periodos[::tick_step], rotation=45, ha="right", fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:.0f}k"))
    ax.set_ylabel("Ahorro mensual (miles MXN)", fontsize=10, color="#374151")
    ax.set_title(
        "Ahorro mensual por plazo de cobertura / Monthly Savings by Hedging Tenor",
        fontsize=12, fontweight="bold", color="#1a365d",
    )
    ax.legend(fontsize=8, framealpha=0.9)
    ax.grid(True, axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout(pad=2.0)

    return _imagen_desde_figura(fig, width_cm=16.5)


def _seccion_comparativa_plazos(multi: ResultadoMultiPlazo, estilos: dict) -> list:
    """
    Genera la sección de comparativa de plazos: tabla + gráfica + recomendación automática.
    """
    elementos = []
    elementos.append(Paragraph(
        "Comparativa de Plazos / Tenor Comparison",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    intro_es = (
        "Comparativa del desempeño histórico de coberturas forward a 30, 60 y 90 días "
        "sobre el mismo período y volumen de compra."
    )
    intro_en = (
        "Historical performance comparison of 30-, 60-, and 90-day forward hedges "
        "over the same period and purchase volume."
    )
    elementos.append(Paragraph(intro_es, estilos["cuerpo"]))
    elementos.append(Paragraph(intro_en, ParagraphStyle(
        "cp_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12, spaceAfter=8,
    )))

    # --- Tabla comparativa ---
    mejor = multi.mejor_plazo
    encabezados = [
        Paragraph("<b>Métrica / Metric</b>", estilos["tabla_header"]),
        Paragraph("<b>30 días\n30-day</b>", estilos["tabla_header"]),
        Paragraph("<b>60 días\n60-day</b>", estilos["tabla_header"]),
        Paragraph("<b>90 días\n90-day</b>", estilos["tabla_header"]),
    ]
    filas_comp = [encabezados]

    resultados_ord = [multi.plazo_30d, multi.plazo_60d, multi.plazo_90d]

    def _fila(etiqueta: str, vals: list[str]) -> list:
        return [Paragraph(etiqueta, estilos["tabla_celda_left"])] + vals

    def _fmt_mxn(v: float) -> str:
        return f"${v:,.0f}"

    def _fmt_pct(v: float) -> str:
        return f"{v:.1f}%"

    filas_comp += [
        _fila("Ahorro / costo total\nTotal savings / cost",
              [_fmt_mxn(r.ahorro_total_mxn) for r in resultados_ord]),
        _fila("Ahorro promedio mensual\nAvg monthly savings",
              [_fmt_mxn(r.ahorro_promedio_mensual_mxn) for r in resultados_ord]),
        _fila("% meses con ahorro\n% months with savings",
              [_fmt_pct(r.porcentaje_meses_con_ahorro) for r in resultados_ord]),
        _fila("Desv. estándar mensual\nMonthly std deviation",
              [_fmt_mxn(float(np.std([p.ahorro_mxn for p in r.periodos])))
               for r in resultados_ord]),
        _fila("Mejor mes / Best month",
              [f"{r.mejor_mes.periodo}\n{_fmt_mxn(r.mejor_mes.ahorro_mxn)}"
               if r.mejor_mes else "—" for r in resultados_ord]),
        _fila("Peor mes / Worst month",
              [f"{r.peor_mes.periodo}\n{_fmt_mxn(r.peor_mes.ahorro_mxn)}"
               if r.peor_mes else "—" for r in resultados_ord]),
        _fila("Costo total HedgePoint\nTotal HedgePoint cost",
              [_fmt_mxn(r.costo_total_hedgepoint_mxn) for r in resultados_ord]),
        _fila("Costo total banco\nTotal bank cost",
              [_fmt_mxn(r.costo_total_banco_mxn) for r in resultados_ord]),
    ]

    col_widths = [5.5 * cm, 3.5 * cm, 3.5 * cm, 3.5 * cm]
    t = Table(filas_comp, colWidths=col_widths, repeatRows=1)

    # Resaltar columna del mejor plazo
    idx_mejor = [30, 60, 90].index(mejor.parametros.plazo_forward_dias) + 1

    estilo_comp = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BLANCO, GRIS_CLARO]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        # Resaltar columna ganadora
        ("BACKGROUND", (idx_mejor, 0), (idx_mejor, 0), VERDE),
        ("BOX", (idx_mejor, 1), (idx_mejor, -1), 1.2, VERDE),
    ]
    # Colorear fila de ahorro/costo: verde si positivo, gris neutro si negativo
    for i, r in enumerate(resultados_ord, start=1):
        color = VERDE if r.ahorro_total_mxn >= 0 else GRIS
        estilo_comp.append(("TEXTCOLOR", (i, 1), (i, 1), color))

    t.setStyle(TableStyle(estilo_comp))
    elementos.append(t)
    elementos.append(Spacer(1, 0.4 * cm))

    # --- Gráfica de barras agrupadas ---
    elementos.append(_grafica_comparativa_plazos(multi))
    elementos.append(Spacer(1, 0.4 * cm))

    # --- Recomendación automática de plazo ---
    elementos.append(Paragraph(
        "Recomendación de Plazo / Tenor Recommendation",
        estilos["sub_encabezado"],
    ))

    # Criterios: mejor ahorro neto, menor volatilidad, mayor % meses positivos
    ahorros  = {r.parametros.plazo_forward_dias: r.ahorro_total_mxn for r in resultados_ord}
    vols     = {r.parametros.plazo_forward_dias: float(np.std([p.ahorro_mxn for p in r.periodos]))
                for r in resultados_ord}
    positivos = {r.parametros.plazo_forward_dias: r.porcentaje_meses_con_ahorro
                 for r in resultados_ord}

    ganador_ahorro   = max(ahorros, key=ahorros.get)
    ganador_vol      = min(vols, key=vols.get)       # menor = más estable
    ganador_positivo = max(positivos, key=positivos.get)

    plazo_rec = mejor.parametros.plazo_forward_dias
    criterios_a_favor = sum([
        ganador_ahorro   == plazo_rec,
        ganador_vol      == plazo_rec,
        ganador_positivo == plazo_rec,
    ])

    todos_negativos = all(r.ahorro_total_mxn < 0 for r in resultados_ord)

    if todos_negativos:
        # Plazo óptimo = menor costo adicional (ahorro menos negativo)
        costo_adicional = {r.parametros.plazo_forward_dias: abs(r.ahorro_total_mxn)
                           for r in resultados_ord}
        plazo_menor_costo = min(costo_adicional, key=costo_adicional.get)
        mejor_en_negativo = next(
            r for r in resultados_ord
            if r.parametros.plazo_forward_dias == plazo_menor_costo
        )
        rec_es = (
            f"En el período analizado, el peso se apreció sostenidamente frente al dólar, "
            f"por lo que <b>ningún plazo de cobertura generó ahorro neto</b>. "
            f"Sin embargo, el forward a <b>{plazo_menor_costo} días</b> fue el de "
            f"menor costo adicional "
            f"(<b>${abs(mejor_en_negativo.ahorro_total_mxn):,.0f} MXN</b>) y menor "
            f"volatilidad mensual, lo que lo hace preferible si se decide cubrir. "
            f"En períodos de depreciación cambiaria — que históricamente han sido "
            f"frecuentes — la cobertura habría generado ahorros significativos."
        )
        rec_en = (
            f"During the analyzed period, the peso appreciated steadily against the dollar, "
            f"so <b>no forward tenor produced net savings</b>. "
            f"However, the <b>{plazo_menor_costo}-day forward</b> carried the lowest additional "
            f"cost (<b>${abs(mejor_en_negativo.ahorro_total_mxn):,.0f} MXN</b>) and the lowest "
            f"monthly volatility, making it the preferred option if hedging is desired. "
            f"During periods of peso depreciation — historically frequent — "
            f"hedging would have generated significant savings."
        )
        bg_color = colors.HexColor("#fff8e1")
        border_color = colors.HexColor("#f59e0b")
    else:
        rec_es = (
            f"Con base en tres criterios — <b>mayor ahorro neto</b> ({ganador_ahorro}d), "
            f"<b>menor volatilidad mensual</b> ({ganador_vol}d) y "
            f"<b>mayor porcentaje de meses positivos</b> ({ganador_positivo}d) — "
            f"el plazo recomendado es <b>{plazo_rec} días</b>, "
            f"que cumple {criterios_a_favor} de 3 criterios. "
            f"Ahorro total: <b>${mejor.ahorro_total_mxn:,.0f} MXN</b>, "
            f"promedio mensual: <b>${mejor.ahorro_promedio_mensual_mxn:,.0f} MXN</b>, "
            f"en {mejor.porcentaje_meses_con_ahorro:.0f}% de los meses."
        )
        rec_en = (
            f"Based on three criteria — <b>highest net savings</b> ({ganador_ahorro}d), "
            f"<b>lowest monthly volatility</b> ({ganador_vol}d), and "
            f"<b>highest share of positive months</b> ({ganador_positivo}d) — "
            f"the recommended tenor is <b>{plazo_rec} days</b>, "
            f"satisfying {criterios_a_favor} of 3 criteria. "
            f"Total savings: <b>${mejor.ahorro_total_mxn:,.0f} MXN</b>, "
            f"avg monthly: <b>${mejor.ahorro_promedio_mensual_mxn:,.0f} MXN</b>, "
            f"positive in {mejor.porcentaje_meses_con_ahorro:.0f}% of months."
        )
        bg_color = VERDE_CLARO
        border_color = VERDE

    caja_rec = Table(
        [[Paragraph(rec_es, estilos["recomendacion"])],
         [Paragraph(rec_en, ParagraphStyle(
             "rec_en2", fontName="Helvetica-Oblique", fontSize=8.5,
             textColor=GRIS, alignment=TA_JUSTIFY, leading=12,
         ))]],
        colWidths=[16 * cm],
    )
    caja_rec.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 1.5, border_color),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elementos.append(caja_rec)

    return elementos


def _grafica_impacto_margen(resultado: ResultadoSimulacion) -> Image:
    """
    Barras del resultado de cada mes como % del margen de utilidad mensual.
    Barras rojas intensas cuando la pérdida habría superado el 20% del margen mensual.
    """
    r = resultado
    p = r.parametros
    periodos = r.periodos
    n = len(periodos)

    # Margen de utilidad mensual en MXN estimado sobre el costo spot de cada mes
    margenes_mensuales = [pe.costo_spot_mxn * p.margen_utilidad for pe in periodos]
    # Impacto: ahorro_mxn / margen_mensual * 100
    # Positivo = el forward protegió X% del margen; negativo = costó X% del margen
    impactos = [
        (pe.ahorro_mxn / m * 100) if m > 0 else 0.0
        for pe, m in zip(periodos, margenes_mensuales)
    ]

    tick_step = max(1, round(n / 8))
    tick_indices = list(range(0, n, tick_step))
    tick_labels = [periodos[i].periodo for i in tick_indices]

    # Colores: rojo intenso si habría dañado >20% del margen, verde si protegió, azul claro si costó <20%
    UMBRAL = 20.0
    colores = []
    for imp in impactos:
        if imp > 0:
            colores.append("#2d8659")       # verde: el forward protegió
        elif imp <= -UMBRAL:
            colores.append("#c0392b")       # rojo: golpe severo al margen
        else:
            colores.append("#8eafd4")       # azul claro: costo moderado

    fig, ax = plt.subplots(figsize=(12, 4.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f9fafb")

    ax.bar(range(n), impactos, color=colores, edgecolor="none", width=0.75)
    ax.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    # Línea de umbral -20%
    ax.axhline(-UMBRAL, color="#c0392b", linewidth=1.0, linestyle=":", alpha=0.7)
    ax.text(n - 0.5, -UMBRAL - 2, "−20% margen", color="#c0392b",
            fontsize=7, ha="right", va="top")

    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    ax.set_ylabel("Impacto sobre margen mensual (%)", fontsize=9, color="#374151")
    ax.set_title(
        "Impacto mensual sobre margen de utilidad: Forward vs Sin cobertura\n"
        "Impact on Monthly Profit Margin: Forward vs Unhedged",
        fontsize=10, fontweight="bold", color="#1a365d",
    )

    # Leyenda
    from matplotlib.patches import Patch
    ax.legend(
        handles=[
            Patch(facecolor="#2d8659", label="Cobertura protegió el margen / Hedge protected margin"),
            Patch(facecolor="#8eafd4", label="Costo de cobertura <20% margen / Hedge cost <20% margin"),
            Patch(facecolor="#c0392b", label="Golpe severo al margen >20% / Severe margin hit >20%"),
        ],
        fontsize=7, loc="lower right", framealpha=0.8,
    )
    ax.grid(True, axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout(pad=2.0)
    return _imagen_desde_figura(fig, width_cm=16.5, max_height_cm=14.0)


def _seccion_analisis_riesgo(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """
    Sección de análisis de riesgo: recuadro de impacto, tabla de 5 peores meses,
    gráfica de impacto en margen y recuadro de pregunta final.
    """
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Análisis de Riesgo / Risk Analysis",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=ROJO, spaceAfter=8))

    # --- Pre-cálculos ---
    margen_anual_mxn = r.costo_total_spot_mxn * p.margen_utilidad
    margen_mensual_mxn = margen_anual_mxn / r.total_meses if r.total_meses > 0 else 1.0

    # Meses donde el forward fue mejor (ahorro positivo = spot habría costado más)
    meses_protegidos = [pe for pe in r.periodos if pe.ahorro_mxn > 0]
    n_meses_prot = len(meses_protegidos)

    danio_evitado_pct_margen = (
        r.danio_total_evitado_mxn / margen_anual_mxn * 100
        if margen_anual_mxn > 0 else 0.0
    )
    peor_mes_pct_margen = (
        r.perdida_maxima_un_mes_mxn / margen_mensual_mxn * 100
        if margen_mensual_mxn > 0 else 0.0
    )
    peor_mes_obj = r.mejor_mes  # mejor_mes = mes con mayor ahorro = mes con mayor riesgo sin cobertura

    # --- Recuadro principal de impacto ---
    texto_impacto_es = (
        f"Sin cobertura, en los <b>{n_meses_prot}</b> meses donde el peso se depreció "
        f"más de lo esperado, su empresa habría perdido "
        f"<b>${r.danio_total_evitado_mxn:,.0f} MXN</b> — "
        f"equivalente al <b>{danio_evitado_pct_margen:.1f}%</b> de su margen de utilidad anual. "
        f"El peor mes individual (<b>{peor_mes_obj.periodo if peor_mes_obj else 'N/D'}</b>) "
        f"habría costado <b>${r.perdida_maxima_un_mes_mxn:,.0f} MXN</b> adicionales, "
        f"el <b>{peor_mes_pct_margen:.1f}%</b> de su margen mensual."
    )
    texto_impacto_en = (
        f"Without hedging, in the <b>{n_meses_prot}</b> months when the peso depreciated "
        f"more than expected, your company would have lost "
        f"<b>${r.danio_total_evitado_mxn:,.0f} MXN</b> — "
        f"equivalent to <b>{danio_evitado_pct_margen:.1f}%</b> of your annual profit margin. "
        f"The worst individual month (<b>{peor_mes_obj.periodo if peor_mes_obj else 'N/A'}</b>) "
        f"would have cost <b>${r.perdida_maxima_un_mes_mxn:,.0f} MXN</b> extra, "
        f"<b>{peor_mes_pct_margen:.1f}%</b> of your monthly margin."
    )

    estilo_impacto_es = ParagraphStyle(
        "impacto_es", fontName="Helvetica-Bold", fontSize=10,
        textColor=colors.HexColor("#7b1111"),
        alignment=TA_JUSTIFY, leading=15,
    )
    estilo_impacto_en = ParagraphStyle(
        "impacto_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=colors.HexColor("#922b21"),
        alignment=TA_JUSTIFY, leading=13,
    )
    caja_impacto = Table(
        [[Paragraph(texto_impacto_es, estilo_impacto_es)],
         [Paragraph(texto_impacto_en, estilo_impacto_en)]],
        colWidths=[16 * cm],
    )
    caja_impacto.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf2f2")),
        ("BOX", (0, 0), (-1, -1), 2.0, ROJO),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    elementos.append(caja_impacto)
    elementos.append(Spacer(1, 0.5 * cm))

    # --- Tabla: 5 peores meses sin cobertura ---
    elementos.append(Paragraph(
        "Los 5 Meses de Mayor Exposición / Top 5 Highest-Risk Months",
        estilos["sub_encabezado"],
    ))
    elementos.append(Spacer(1, 0.2 * cm))

    # Ordenar periodos de mayor a menor ahorro (los de mayor ahorro = mayor riesgo sin cobertura)
    top5 = sorted(r.periodos, key=lambda pe: pe.ahorro_mxn, reverse=True)[:5]

    enc_top5 = [
        Paragraph("<b>Mes\nMonth</b>", estilos["tabla_header"]),
        Paragraph("<b>TC Spot\nSpot Rate</b>", estilos["tabla_header"]),
        Paragraph("<b>TC Forward\nFwd Rate</b>", estilos["tabla_header"]),
        Paragraph("<b>Pérdida evitada\nLoss avoided (MXN)</b>", estilos["tabla_header"]),
        Paragraph("<b>% del margen mensual\n% of monthly margin</b>", estilos["tabla_header"]),
    ]
    filas_top5 = [enc_top5]
    for pe in top5:
        _margen_mes = pe.costo_spot_mxn * p.margen_utilidad
        _pct_margen = (pe.ahorro_mxn / _margen_mes * 100) if _margen_mes > 0 and pe.ahorro_mxn > 0 else 0.0
        _es_severo = _pct_margen >= 20.0
        _color_fila = colors.HexColor("#fdf2f2") if _es_severo else BLANCO
        filas_top5.append([
            Paragraph(f"<b>{pe.periodo}</b>", estilos["tabla_celda"]),
            f"{pe.spot:.4f}",
            f"{pe.forward_30d:.4f}",
            Paragraph(
                f"<font color='#c0392b'><b>${pe.ahorro_mxn:,.0f}</b></font>"
                if pe.ahorro_mxn > 0 else f"${pe.ahorro_mxn:,.0f}",
                estilos["tabla_celda"],
            ),
            Paragraph(
                f"<font color='{'#c0392b' if _es_severo else '#2d8659'}'>"
                f"<b>{_pct_margen:.1f}%</b></font>",
                estilos["tabla_celda"],
            ),
        ])

    t_top5 = Table(filas_top5,
                   colWidths=[2.5 * cm, 2.8 * cm, 2.8 * cm, 4.5 * cm, 3.4 * cm])
    estilo_t5 = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BLANCO, GRIS_CLARO]),
    ]
    # Fondo rojo tenue en filas con golpe severo al margen (>20%)
    for i, pe in enumerate(top5, start=1):
        _margen_mes = pe.costo_spot_mxn * p.margen_utilidad
        _pct = (pe.ahorro_mxn / _margen_mes * 100) if _margen_mes > 0 and pe.ahorro_mxn > 0 else 0.0
        if _pct >= 20.0:
            estilo_t5.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fdf2f2")))
    t_top5.setStyle(TableStyle(estilo_t5))
    elementos.append(t_top5)
    elementos.append(Spacer(1, 0.5 * cm))

    # --- Gráfica de impacto en margen ---
    elementos.append(Paragraph(
        "Impacto Mensual sobre el Margen de Utilidad / Monthly Margin Impact",
        estilos["sub_encabezado"],
    ))
    elementos.append(Spacer(1, 0.2 * cm))
    elementos.append(_grafica_impacto_margen(resultado))
    elementos.append(Spacer(1, 0.4 * cm))

    # --- Recuadro de pregunta final ---
    # Meses con golpe severo: >20% del margen mensual
    meses_severos = sum(
        1 for pe in r.periodos
        if pe.ahorro_mxn > 0 and (pe.costo_spot_mxn * p.margen_utilidad) > 0
        and (pe.ahorro_mxn / (pe.costo_spot_mxn * p.margen_utilidad) * 100) >= 20.0
    )
    umbral_pct = 20.0
    texto_pregunta_es = (
        f"La cobertura no busca ganarle al mercado. Busca garantizar que un movimiento "
        f"adverso del tipo de cambio no destruya su margen de operación. "
        f"En <b>{meses_severos}</b> de los <b>{r.total_meses}</b> meses analizados, "
        f"el tipo de cambio se movió en su contra lo suficiente para erosionar más del "
        f"<b>{umbral_pct:.0f}%</b> de su margen mensual. "
        f"<b>¿Puede su empresa absorber esos golpes?</b>"
    )
    texto_pregunta_en = (
        f"Hedging does not aim to beat the market. It aims to ensure that an adverse "
        f"FX move does not destroy your operating margin. "
        f"In <b>{meses_severos}</b> of the <b>{r.total_meses}</b> months analyzed, "
        f"the exchange rate moved against you enough to erode more than "
        f"<b>{umbral_pct:.0f}%</b> of your monthly margin. "
        f"<b>Can your company absorb those hits?</b>"
    )
    estilo_pregunta = ParagraphStyle(
        "pregunta_es", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#374151"),
        alignment=TA_JUSTIFY, leading=14,
    )
    estilo_pregunta_en = ParagraphStyle(
        "pregunta_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=13,
    )
    caja_pregunta = Table(
        [[Paragraph(texto_pregunta_es, estilo_pregunta)],
         [Paragraph(texto_pregunta_en, estilo_pregunta_en)]],
        colWidths=[16 * cm],
    )
    caja_pregunta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL_CLARO),
        ("BOX", (0, 0), (-1, -1), 1.5, AZUL_MEDIO),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
    ]))
    elementos.append(caja_pregunta)

    return elementos


def _recuadro_contexto_seguro(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """
    Genera un recuadro de contexto que reframea el costo/ahorro de la cobertura
    como un seguro cambiario, con impacto sobre el margen de utilidad.
    Siempre se muestra — el mensaje varía según signo del resultado.
    """
    r = resultado
    p = r.parametros

    _vol_total_mxn = r.costo_total_spot_mxn
    _margen_total_mxn = _vol_total_mxn * p.margen_utilidad if p.margen_utilidad > 0 else 1.0

    # Mes con mayor movimiento favorable al importador (mayor spot vs forward)
    _mejor_mes_str = r.mejor_mes.periodo if r.mejor_mes else "N/D"

    # Costo o ahorro como % del margen anual
    _impacto_mxn = abs(r.ahorro_total_mxn)
    _impacto_pct_margen = (_impacto_mxn / _margen_total_mxn * 100) if _margen_total_mxn > 0 else 0.0

    # Riesgo de un solo mes sin cobertura = el mayor "costo de no cubrir"
    # = mes donde el spot fue más caro que el forward (mejor mes para el importador = sin cobertura costó más)
    # En escenario negativo (peso se apreció), el "riesgo" histórico es el mes que más ahorró con cobertura
    _riesgo_mes_mxn = abs(r.mejor_mes.ahorro_mxn) if r.mejor_mes else 0.0
    _riesgo_pct_margen = (
        _riesgo_mes_mxn / (_margen_total_mxn / r.total_meses) * 100
        if r.total_meses > 0 and _margen_total_mxn > 0 else 0.0
    )

    if r.ahorro_total_mxn >= 0:
        _costo_pct_vol = r.ahorro_total_porcentaje
        texto_es = (
            f"La cobertura generó un ahorro equivalente al <b>{_costo_pct_vol:.2f}%</b> "
            f"del volumen operado y al <b>{_impacto_pct_margen:.1f}%</b> del margen de utilidad "
            f"durante el período analizado. "
            f"Compárelo con el riesgo: en el mejor mes (<b>{_mejor_mes_str}</b>), "
            f"un mes sin cobertura habría costado <b>${_riesgo_mes_mxn:,.0f} MXN</b> adicionales, "
            f"equivalente al <b>{_riesgo_pct_margen:.1f}%</b> del margen mensual. "
            f"<b>La cobertura es un costo predecible que elimina un riesgo impredecible.</b>"
        )
        texto_en = (
            f"Hedging generated savings equivalent to <b>{_costo_pct_vol:.2f}%</b> "
            f"of the total FX volume and <b>{_impacto_pct_margen:.1f}%</b> of your profit margin "
            f"over the analyzed period. "
            f"In the best month (<b>{_mejor_mes_str}</b>), a single unhedged month would have "
            f"cost <b>${_riesgo_mes_mxn:,.0f} MXN</b> extra — "
            f"<b>{_riesgo_pct_margen:.1f}%</b> of that month's margin. "
            f"<b>Hedging is a predictable cost that eliminates an unpredictable risk.</b>"
        )
        bg = VERDE_CLARO
        border = VERDE
    else:
        _costo_pct_vol = abs(r.ahorro_total_porcentaje)
        texto_es = (
            f"El costo de la cobertura representa el <b>{_costo_pct_vol:.2f}%</b> "
            f"del volumen operado — equivalente al <b>{_impacto_pct_margen:.1f}%</b> "
            f"del margen de utilidad durante el período. "
            f"Compárelo con el riesgo: en <b>{_mejor_mes_str}</b>, un solo mes de depreciación "
            f"fuerte puede erosionar <b>${_riesgo_mes_mxn:,.0f} MXN</b> del margen "
            f"(<b>{_riesgo_pct_margen:.1f}%</b> del margen mensual). "
            f"<b>La cobertura es un costo predecible que elimina un riesgo impredecible.</b>"
        )
        texto_en = (
            f"The hedging cost represents <b>{_costo_pct_vol:.2f}%</b> of your FX volume "
            f"— equivalent to <b>{_impacto_pct_margen:.1f}%</b> of your annual profit margin. "
            f"Compare this to the risk: a single month of sharp depreciation "
            f"(like <b>{_mejor_mes_str}</b>) can erode "
            f"<b>${_riesgo_mes_mxn:,.0f} MXN</b> of margin "
            f"(<b>{_riesgo_pct_margen:.1f}%</b> of monthly margin). "
            f"<b>Hedging is a predictable cost that eliminates an unpredictable risk.</b>"
        )
        bg = AZUL_CLARO
        border = AZUL_MEDIO

    caja = Table(
        [[Paragraph(texto_es, estilos["cuerpo"])],
         [Paragraph(texto_en, ParagraphStyle(
             "ctx_en", fontName="Helvetica-Oblique", fontSize=8.5,
             textColor=GRIS, alignment=TA_JUSTIFY, leading=12,
         ))]],
        colWidths=[16 * cm],
    )
    caja.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1.2, border),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    return [Spacer(1, 0.4 * cm), caja]


# ---------------------------------------------------------------------------
# Sección: nivel de cobertura y tabla comparativa
# ---------------------------------------------------------------------------

def _seccion_nivel_cobertura(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """
    Genera la sección 'Nivel de Cobertura' con:
    - Indicador del nivel actual configurado.
    - Tabla comparativa de 4 niveles (25 / 50 / 75 / 100 %).
    """
    r = resultado
    p = r.parametros
    nivel_actual = p.cobertura_pct
    elementos = []

    elementos.append(Paragraph(
        "Nivel de Cobertura / Coverage Level",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    # --- Indicador del nivel actual ---
    nivel_txt = ParagraphStyle(
        "nivel_txt", fontName="Helvetica-Bold", fontSize=13,
        textColor=AZUL, alignment=TA_CENTER, leading=18,
    )
    nivel_sub = ParagraphStyle(
        "nivel_sub", fontName="Helvetica", fontSize=9,
        textColor=GRIS, alignment=TA_CENTER, leading=12, spaceAfter=10,
    )
    caja_nivel = Table(
        [[Paragraph(f"Nivel de cobertura: {nivel_actual:.0f}%", nivel_txt)],
         [Paragraph(
             f"El {nivel_actual:.0f}% del volumen mensual (USD ${p.volumen_mensual_usd:,.0f}) "
             f"se cubre con forward. El {100 - nivel_actual:.0f}% restante se compra al tipo de cambio spot del mes. "
             f"/ {nivel_actual:.0f}% of monthly volume hedged with forward; "
             f"{100 - nivel_actual:.0f}% purchased at spot rate.",
             nivel_sub,
         )]],
        colWidths=[16 * cm],
    )
    caja_nivel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AZUL_CLARO),
        ("BOX", (0, 0), (-1, -1), 1.0, AZUL_MEDIO),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elementos.append(caja_nivel)
    elementos.append(Spacer(1, 0.5 * cm))

    # --- Tabla comparativa 4 niveles ---
    elementos.append(Paragraph(
        "Comparativa por Nivel de Cobertura / Coverage Level Comparison",
        estilos["sub_encabezado"],
    ))
    elementos.append(Spacer(1, 0.2 * cm))

    metricas: list[MetricasNivelCobertura] = calcular_metricas_por_nivel(r)

    encabezados = [
        Paragraph("<b>Nivel de\ncobertura</b>", estilos["tabla_header"]),
        Paragraph("<b>Costo anual de\nprotección (MXN)</b>", estilos["tabla_header"]),
        Paragraph("<b>% del\nmargen</b>", estilos["tabla_header"]),
        Paragraph("<b>Pérdida máxima\nevitada (MXN)</b>", estilos["tabla_header"]),
    ]
    filas = [encabezados]

    for m in metricas:
        es_nivel_actual = abs(m.cobertura_pct - nivel_actual) < 0.5
        signo = "+" if m.costo_anual_proteccion_mxn >= 0 else ""
        _color_hex = "#2d8659" if m.costo_anual_proteccion_mxn >= 0 else "#6b7280"

        nivel_cell = Paragraph(
            f"<b>{m.cobertura_pct:.0f}%</b>",
            ParagraphStyle(
                "niv_c", fontName="Helvetica-Bold", fontSize=9,
                textColor=BLANCO if es_nivel_actual else colors.HexColor("#374151"),
                alignment=TA_CENTER,
            ),
        )
        costo_cell = Paragraph(
            f"<font color='{_color_hex}'><b>{signo}${m.costo_anual_proteccion_mxn:,.0f}</b></font>",
            estilos["tabla_celda"],
        )
        pct_cell = Paragraph(
            f"<font color='{_color_hex}'><b>{signo}{m.pct_margen:.2f}%</b></font>",
            estilos["tabla_celda"],
        )
        perdida_cell = Paragraph(
            f"<b>${m.perdida_maxima_evitada_mxn:,.0f}</b>",
            estilos["tabla_celda"],
        )
        filas.append([nivel_cell, costo_cell, pct_cell, perdida_cell])

    col_widths = [3.5 * cm, 5.5 * cm, 3.5 * cm, 5.5 * cm]
    t = Table(filas, colWidths=col_widths, repeatRows=1)

    estilo_t = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BLANCO, GRIS_CLARO]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]

    # Resaltar la fila del nivel actual con fondo azul
    for i, m in enumerate(metricas, start=1):
        if abs(m.cobertura_pct - nivel_actual) < 0.5:
            estilo_t.append(("BACKGROUND", (0, i), (-1, i), AZUL_MEDIO))
            estilo_t.append(("TEXTCOLOR", (0, i), (0, i), BLANCO))

    t.setStyle(TableStyle(estilo_t))
    elementos.append(t)
    elementos.append(Spacer(1, 0.3 * cm))

    # Nota explicativa
    nota = (
        "La fila resaltada corresponde al nivel de cobertura configurado en esta simulación. "
        "'Costo anual de protección' es positivo cuando el forward generó ahorro neto y negativo "
        "cuando la cobertura tuvo un costo adicional frente al spot. 'Pérdida máxima evitada' "
        "muestra el daño acumulado en meses donde el spot habría sido más caro. "
        "/ <i>Highlighted row = configured coverage level. 'Annual protection cost' is positive "
        "when the forward generated net savings, negative when hedging cost more than spot. "
        "'Max loss avoided' shows cumulative damage in months where spot would have cost more.</i>"
    )
    elementos.append(Paragraph(nota, ParagraphStyle(
        "nota_cob", fontName="Helvetica", fontSize=7, textColor=GRIS,
        alignment=TA_JUSTIFY, leading=10, spaceAfter=4,
    )))

    return elementos


# ---------------------------------------------------------------------------
# Secciones PDF — estrategia de opciones
# ---------------------------------------------------------------------------

def _portada_opciones(resultado: ResultadoSimulacionOpciones, estilos: dict) -> list:
    """Portada adaptada para el reporte de opciones put."""
    p = resultado.parametros
    elementos = []

    elementos.append(Spacer(1, 3.5 * cm))
    elementos.append(Paragraph("HedgePoint MX", estilos["titulo_portada"]))
    elementos.append(Spacer(1, 0.5 * cm))
    elementos.append(Paragraph(
        "Gestión de Riesgos Financieros para PyMEs Mexicanas",
        estilos["subtitulo_portada"],
    ))
    elementos.append(Spacer(1, 1.5 * cm))

    elementos.append(Paragraph(
        "SIMULADOR DE COBERTURA CON OPCIONES PUT USD/MXN",
        ParagraphStyle(
            "titulo_rep_op", fontName="Helvetica-Bold", fontSize=16,
            textColor=BLANCO, alignment=TA_CENTER, leading=20,
            spaceBefore=10, spaceAfter=4,
        ),
    ))
    elementos.append(Paragraph(
        "Put Options Hedging Simulator",
        ParagraphStyle(
            "titulo_rep_op_en", fontName="Helvetica", fontSize=12,
            textColor=colors.HexColor("#90adc9"), alignment=TA_CENTER,
        ),
    ))
    elementos.append(Spacer(1, 2.5 * cm))

    datos_tabla = [
        ["Período analizado", f"{resultado.fecha_inicio} — {resultado.fecha_fin}"],
        ["Volumen mensual", f"USD ${p.volumen_mensual_usd:,.0f}"],
        ["Margen de utilidad", f"{p.margen_utilidad * 100:.1f}%"],
        ["Frecuencia de compra", p.frecuencia.capitalize()],
        ["Instrumento evaluado", "Put ATM Garman-Kohlhagen (USD/MXN)"],
        ["Markup banco (prima)", f"{resultado.markup_banco_pct * 100:.0f}% sobre prima teórica"],
    ]
    t = Table(datos_tabla, colWidths=[5.5 * cm, 7 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1e4070")),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#162d50")),
        ("TEXTCOLOR", (0, 0), (-1, -1), BLANCO),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#2a4f82")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    t.hAlign = "CENTER"
    elementos.append(t)
    elementos.append(Spacer(1, 2 * cm))

    _MESES_ES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
    }
    from datetime import date as _date
    _hoy = _date.today()
    elementos.append(Paragraph(
        f"Generado el {_hoy.day} de {_MESES_ES[_hoy.month]} de {_hoy.year}",
        estilos["etiqueta_portada"],
    ))
    elementos.append(Paragraph(
        "Confidencial — Solo para uso interno y presentación a prospectos",
        estilos["etiqueta_portada"],
    ))
    return elementos


def _resumen_ejecutivo_opciones(
    resultado: ResultadoSimulacionOpciones, estilos: dict
) -> list:
    """Resumen ejecutivo con KPIs propios de la estrategia de opciones put."""
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Resumen Ejecutivo / Executive Summary",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=8))

    _margen_anual = r.costo_total_spot_mxn * p.margen_utilidad
    _danio_pct_margen = (
        r.valor_total_ejercicios_mxn / _margen_anual * 100
        if _margen_anual > 0 else 0.0
    )
    _prima_pct_vol = (
        r.prima_total_pagada_mxn / r.costo_total_spot_mxn * 100
        if r.costo_total_spot_mxn > 0 else 0.0
    )

    texto_es = (
        f"Durante el período <b>{r.fecha_inicio}</b> al <b>{r.fecha_fin}</b>, "
        f"el put fue ejercido en <b>{r.meses_ejercidos}</b> de {r.total_meses} meses "
        f"({r.porcentaje_meses_ejercidos:.0f}%), capturando "
        f"<b>${r.valor_total_ejercicios_mxn:,.0f} MXN</b> en valor de ejercicio — "
        f"equivalente al <b>{_danio_pct_margen:.1f}%</b> del margen de utilidad anual. "
        f"La prima total pagada fue <b>${r.prima_total_pagada_mxn:,.0f} MXN</b> "
        f"({_prima_pct_vol:.2f}% del volumen operado, "
        f"equivalente a <b>${r.prima_promedio_mxn_por_usd:.4f} MXN/USD</b> en promedio). "
        f"<b>La opción put actúa como un seguro: limita el costo de compra sin renunciar "
        f"al beneficio cuando el peso se aprecia.</b>"
    )
    texto_en = (
        f"During the period <b>{r.fecha_inicio}</b> to <b>{r.fecha_fin}</b>, "
        f"the put was exercised in <b>{r.meses_ejercidos}</b> of {r.total_meses} months "
        f"({r.porcentaje_meses_ejercidos:.0f}%), capturing "
        f"<b>${r.valor_total_ejercicios_mxn:,.0f} MXN</b> in exercise value — "
        f"<b>{_danio_pct_margen:.1f}%</b> of your annual profit margin. "
        f"Total premiums paid: <b>${r.prima_total_pagada_mxn:,.0f} MXN</b> "
        f"({_prima_pct_vol:.2f}% of FX volume, "
        f"avg <b>${r.prima_promedio_mxn_por_usd:.4f} MXN/USD</b>). "
        f"<b>The put option acts as insurance: it caps your purchase cost without "
        f"forfeiting the benefit when the peso appreciates.</b>"
    )
    elementos.append(Paragraph(texto_es, estilos["cuerpo"]))
    elementos.append(Paragraph(texto_en, ParagraphStyle(
        "re_op_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12, spaceAfter=10,
    )))

    # KPIs fila 1
    _ahorro_pos = r.ahorro_total_vs_spot_mxn >= 0
    kpis_fila1 = [
        (
            f"${r.ahorro_total_vs_spot_mxn:,.0f}",
            "Ahorro / Costo neto total\nNet Savings / Cost vs Spot",
            not _ahorro_pos,
        ),
        (
            f"${r.ahorro_promedio_mensual_mxn:,.0f}",
            "Promedio mensual\nAvg Monthly Result",
            r.ahorro_promedio_mensual_mxn < 0,
        ),
        (
            f"{r.ahorro_total_porcentaje:.2f}%",
            "% sobre costo spot\n% of Spot Cost",
            not _ahorro_pos,
        ),
        (
            f"{r.porcentaje_meses_ejercidos:.0f}%",
            "Meses put ejercido\nMonths Put Exercised",
            False,
        ),
        (
            f"{r.total_meses}",
            "Meses analizados\nMonths Analyzed",
            False,
        ),
    ]
    # KPIs fila 2 — desglose de prima y ejercicios
    kpis_fila2 = [
        (
            f"${r.prima_total_pagada_mxn:,.0f}",
            "Prima total pagada\nTotal Premiums Paid",
            True,
        ),
        (
            f"${r.valor_total_ejercicios_mxn:,.0f}",
            "Valor ejercicios capturado\nExercise Value Captured",
            False,
        ),
        (
            f"${r.prima_promedio_mxn_por_usd:.4f}",
            "Prima promedio por USD\nAvg Premium per USD",
            True,
        ),
        (
            f"{r.vol_promedio * 100:.1f}%",
            "Vol. histórica promedio\nAvg Historical Vol",
            False,
        ),
        (
            f"${r.costo_total_markup_hp_mxn + r.costo_total_fee_hp_mxn:,.0f}",
            "Costo total HedgePoint\nTotal HedgePoint Cost",
            True,
        ),
    ]

    fila_kpis = [
        [_kpi_box(num, lbl, neg) for num, lbl, neg in kpis_fila1],
        [_kpi_box(num, lbl, neg) for num, lbl, neg in kpis_fila2],
    ]
    t_kpis = Table(fila_kpis, colWidths=[3.8 * cm] * 5,
                   hAlign="CENTER", spaceBefore=6, spaceAfter=4)
    t_kpis.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    elementos.append(t_kpis)

    # Mejor / peor mes
    if r.mejor_mes and r.peor_mes:
        elementos.append(Spacer(1, 0.5 * cm))
        datos_extremos = [
            ["", "Mes", "Spot venc.", "Strike", "Ejercida", "Resultado (MXN)"],
            [
                Paragraph("<b>Mejor mes / Best month</b>", estilos["tabla_celda_left"]),
                r.mejor_mes.periodo,
                f"{r.mejor_mes.spot_compra:.4f}",
                f"{r.mejor_mes.strike:.4f}",
                "Sí / Yes" if r.mejor_mes.ejercida else "No",
                f"${r.mejor_mes.ahorro_vs_spot_mxn:,.2f}",
            ],
            [
                Paragraph("<b>Peor mes / Worst month</b>", estilos["tabla_celda_left"]),
                r.peor_mes.periodo,
                f"{r.peor_mes.spot_compra:.4f}",
                f"{r.peor_mes.strike:.4f}",
                "Sí / Yes" if r.peor_mes.ejercida else "No",
                f"${r.peor_mes.ahorro_vs_spot_mxn:,.2f}",
            ],
        ]
        t_ext = Table(
            datos_extremos,
            colWidths=[4.0 * cm, 2.0 * cm, 2.5 * cm, 2.5 * cm, 2.0 * cm, 3.0 * cm],
        )
        t_ext.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("BACKGROUND", (0, 1), (-1, 1), VERDE_CLARO),
            ("BACKGROUND", (0, 2), (-1, 2), AZUL_CLARO),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elementos.append(t_ext)

    return elementos


def _grafica_opciones_resultado(resultado: ResultadoSimulacionOpciones) -> Image:
    """
    Gráfica de barras mensuales del resultado de la estrategia de opciones vs spot,
    con marcadores de meses donde el put fue ejercido.
    """
    r = resultado
    periodos = r.periodos
    n = len(periodos)

    ahorros = [p.ahorro_vs_spot_mxn / 1000 for p in periodos]
    ejercidas = [p.ejercida for p in periodos]

    tick_step = max(1, round(n / 8))
    tick_indices = list(range(0, n, tick_step))
    tick_labels = [periodos[i].periodo for i in tick_indices]

    # Verde si la opción generó ahorro neto, azul claro si costó más que spot,
    # con borde distinto para meses ejercidos vs no ejercidos
    colores_barras = ["#2d8659" if v >= 0 else "#8eafd4" for v in ahorros]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7))
    fig.patch.set_facecolor("white")

    # Panel superior: resultado mensual
    ax1.set_facecolor("#f9fafb")
    bars = ax1.bar(range(n), ahorros, color=colores_barras, edgecolor="none", width=0.75)

    # Marcar meses ejercidos con un punto encima de la barra
    for i, (ej, v) in enumerate(zip(ejercidas, ahorros)):
        if ej:
            ax1.plot(i, v + (0.5 if v >= 0 else -0.5), marker="^",
                     color="#1a365d", markersize=6, zorder=5)

    ax1.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    ax1.set_xticks(tick_indices)
    ax1.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax1.tick_params(axis="y", labelsize=8)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))
    ax1.set_ylabel("Resultado vs Spot (miles MXN)", fontsize=10, color="#374151")
    ax1.set_title(
        "Resultado mensual: Opción Put vs Spot",
        fontsize=12, fontweight="bold", color="#1a365d",
    )
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    ax1.legend(
        handles=[
            Patch(facecolor="#2d8659", label="Ahorro neto / Net saving"),
            Patch(facecolor="#8eafd4", label="Costo de prima / Premium cost"),
            Line2D([0], [0], marker="^", color="#1a365d", linewidth=0,
                   markersize=7, label="Put ejercido / Put exercised"),
        ],
        fontsize=7, loc="upper right", framealpha=0.7,
    )
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    # Panel inferior: acumulado
    ax2.set_facecolor("#f9fafb")
    ahorro_acum = [sum(ahorros[: i + 1]) for i in range(n)]
    positivo = [v >= 0 for v in ahorro_acum]
    x = list(range(n))
    ax2.fill_between(x, 0, ahorro_acum,
                     where=positivo, alpha=0.25, color="#2d8659")
    ax2.fill_between(x, 0, ahorro_acum,
                     where=[not v for v in positivo], alpha=0.20, color="#8eafd4")
    ax2.plot(x, ahorro_acum, color="#1a365d", linewidth=2, marker="o", markersize=4)
    ax2.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    ax2.set_xticks(tick_indices)
    ax2.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax2.tick_params(axis="y", labelsize=8)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))
    ax2.set_ylabel("Resultado acumulado (miles MXN)", fontsize=10, color="#374151")
    ax2.set_title(
        "Resultado acumulado: Opción Put vs Spot",
        fontsize=12, fontweight="bold", color="#1a365d",
    )
    ax2.grid(True, alpha=0.3, linestyle="--")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    plt.tight_layout(pad=2.0)
    return _imagen_desde_figura(fig, width_cm=16.5, max_height_cm=19.0)


def _grafica_prima_y_vol(resultado: ResultadoSimulacionOpciones) -> Image:
    """
    Gráfica dual: volatilidad histórica usada (eje izq.) y prima banco pagada
    por USD (eje der.), mes a mes.
    """
    r = resultado
    periodos = r.periodos
    n = len(periodos)

    vols = [p.vol_historica * 100 for p in periodos]
    primas = [p.prima_banco_mxn for p in periodos]

    tick_step = max(1, round(n / 8))
    tick_indices = list(range(0, n, tick_step))
    tick_labels = [periodos[i].periodo for i in tick_indices]

    fig, ax1 = plt.subplots(figsize=(12, 4.0))
    fig.patch.set_facecolor("white")
    ax1.set_facecolor("#f9fafb")

    color_vol = "#1a365d"
    color_prima = "#2d8659"

    ax1.plot(range(n), vols, color=color_vol, linewidth=1.8,
             marker="o", markersize=4, label="Volatilidad histórica 30d (%)")
    ax1.fill_between(range(n), min(vols) * 0.95, vols, alpha=0.10, color=color_vol)
    ax1.set_ylabel("Volatilidad histórica anualizada (%)", fontsize=9, color=color_vol)
    ax1.tick_params(axis="y", labelcolor=color_vol, labelsize=8)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:.0f}%"))

    ax2 = ax1.twinx()
    ax2.bar(range(n), primas, color=color_prima, alpha=0.55, width=0.6,
            label="Prima banco (MXN/USD)")
    ax2.set_ylabel("Prima banco (MXN por USD)", fontsize=9, color=color_prima)
    ax2.tick_params(axis="y", labelcolor=color_prima, labelsize=8)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.3f}"))

    ax1.set_xticks(tick_indices)
    ax1.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax1.set_title(
        "Volatilidad histórica 30d y Prima del Banco por período\n"
        "Historical Volatility (30d) and Bank Premium per Period",
        fontsize=10, fontweight="bold", color="#1a365d",
    )

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2,
               fontsize=7, loc="upper right", framealpha=0.8)
    ax1.grid(True, axis="y", alpha=0.3, linestyle="--")
    ax1.spines["top"].set_visible(False)

    plt.tight_layout(pad=2.0)
    return _imagen_desde_figura(fig, width_cm=16.5, max_height_cm=12.0)


def _tabla_mensual_opciones(resultado: ResultadoSimulacionOpciones, estilos: dict) -> list:
    """Tabla detallada mes a mes de la estrategia de opciones."""
    r = resultado
    elementos = []

    elementos.append(Paragraph(
        "Análisis Mensual / Monthly Breakdown",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    _MESES_ABR = {
        "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr",
        "05": "May", "06": "Jun", "07": "Jul", "08": "Ago",
        "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dic",
    }

    def _abr(periodo: str) -> str:
        try:
            anio, mes = periodo.split("-")
            return f"{_MESES_ABR.get(mes, mes)}-{anio[2:]}"
        except ValueError:
            return periodo

    encabezados = [
        "Mes", "Spot\nvenc.", "Strike", "Vol\n(%)",
        "Prima\n(MXN/USD)", "Ejerce", "C.Spot\n(MXN)",
        "C.Opción\n(MXN)", "Result.\nvs Spot", "(%)",
    ]
    filas = [encabezados]

    for p in r.periodos:
        filas.append([
            _abr(p.periodo),
            f"{p.spot_compra:.4f}",
            f"{p.strike:.4f}",
            f"{p.vol_historica * 100:.1f}%",
            f"${p.prima_banco_mxn:.4f}",
            "✓" if p.ejercida else "—",
            f"${p.costo_spot_mxn:,.0f}",
            f"${p.costo_opcion_mxn:,.0f}",
            f"${p.ahorro_vs_spot_mxn:,.0f}",
            f"{p.ahorro_porcentaje:.1f}%",
        ])

    # Fila de totales
    filas.append([
        Paragraph("<b>TOTAL</b>", estilos["tabla_celda"]),
        "", "", "", "",
        Paragraph(f"<b>{r.meses_ejercidos}/{r.total_meses}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_spot_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_opciones_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.ahorro_total_vs_spot_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>{r.ahorro_total_porcentaje:.1f}%</b>", estilos["tabla_celda"]),
    ])

    col_widths = [
        1.4 * cm, 1.8 * cm, 1.8 * cm, 1.4 * cm,
        2.2 * cm, 1.2 * cm, 2.4 * cm,
        2.4 * cm, 2.2 * cm, 1.2 * cm,
    ]
    t = Table(filas, colWidths=col_widths, repeatRows=1)

    estilo_tabla = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [BLANCO, GRIS_CLARO]),
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, AZUL),
    ]
    # Colorear resultado: verde si ahorro, gris si costo
    for i, p in enumerate(r.periodos, start=1):
        col = 8
        if p.ahorro_vs_spot_mxn > 0:
            estilo_tabla.append(("TEXTCOLOR", (col, i), (col + 1, i), VERDE))
        else:
            estilo_tabla.append(("TEXTCOLOR", (col, i), (col + 1, i), GRIS))
        # Columna "Ejerce": verde si ejercida
        if p.ejercida:
            estilo_tabla.append(("TEXTCOLOR", (5, i), (5, i), VERDE))
            estilo_tabla.append(("FONTNAME", (5, i), (5, i), "Helvetica-Bold"))

    t.setStyle(TableStyle(estilo_tabla))
    elementos.append(t)

    # Nota explicativa
    nota = (
        "✓ = put ejercido (spot vencimiento > strike); — = put expiró sin valor. "
        "'Prima (MXN/USD)' incluye markup del banco. "
        "'C.Opción' = (strike o spot) × vol + prima total + markup HP + fee. "
        "/ <i>✓ = put exercised (expiry spot > strike); — = put expired worthless. "
        "'Premium (MXN/USD)' includes bank markup. "
        "'C.Option' = (strike or spot) × vol + total premium + HP markup + fee.</i>"
    )
    elementos.append(Spacer(1, 0.2 * cm))
    elementos.append(Paragraph(nota, ParagraphStyle(
        "nota_op", fontName="Helvetica", fontSize=7, textColor=GRIS,
        alignment=TA_JUSTIFY, leading=10, spaceAfter=4,
    )))
    return elementos


def _seccion_desglose_costos_opciones(
    resultado: ResultadoSimulacionOpciones, estilos: dict
) -> list:
    """Desglose de costos de la estrategia de opciones."""
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Desglose de Costos / Cost Breakdown",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    intro = (
        "La siguiente tabla muestra el desglose exacto del costo de la estrategia de opciones. "
        "A diferencia del forward, la prima se pierde si el peso no se deprecia — pero el "
        "importador conserva el beneficio si el peso se aprecia. "
        "/ <i>The table below breaks down every cost of the options strategy. Unlike a forward, "
        "the premium is lost if the peso does not depreciate — but the importer keeps the "
        "upside benefit when the peso appreciates.</i>"
    )
    elementos.append(Paragraph(intro, estilos["cuerpo"]))
    elementos.append(Spacer(1, 0.3 * cm))

    costo_total = r.costo_total_opciones_mxn

    def _pct(val: float) -> str:
        return f"{val / costo_total * 100:.1f}%" if costo_total else "—"

    # Costo subyacente = costo total - primas - markup - fee
    costo_subyacente = (
        costo_total
        - r.prima_total_pagada_mxn
        - r.costo_total_markup_hp_mxn
        - r.costo_total_fee_hp_mxn
    )
    prima_teorica_total = r.prima_total_pagada_mxn / (1 + r.markup_banco_pct)
    markup_banco_total = r.prima_total_pagada_mxn - prima_teorica_total

    filas = [
        [
            Paragraph("<b>Concepto / Concept</b>", estilos["tabla_header"]),
            Paragraph("<b>Detalle / Detail</b>", estilos["tabla_header"]),
            Paragraph("<b>Costo total\nTotal cost (MXN)</b>", estilos["tabla_header"]),
            Paragraph("<b>% del total\n% of total</b>", estilos["tabla_header"]),
        ],
        [
            "Compra subyacente (strike o spot)\nUnderlying purchase (strike or spot)",
            f"Ejercido {r.meses_ejercidos} meses / al spot {r.total_meses - r.meses_ejercidos} meses",
            f"${costo_subyacente:,.0f}",
            _pct(costo_subyacente),
        ],
        [
            "Prima teórica Garman-Kohlhagen\nTheoretical GK premium",
            f"Vol prom. {r.vol_promedio * 100:.1f}% · ${r.prima_promedio_mxn_por_usd / (1 + r.markup_banco_pct):.4f}/USD",
            f"${prima_teorica_total:,.0f}",
            _pct(prima_teorica_total),
        ],
        [
            f"Markup banco sobre prima ({r.markup_banco_pct * 100:.0f}%)\nBank markup on premium",
            f"${r.prima_promedio_mxn_por_usd * r.markup_banco_pct / (1 + r.markup_banco_pct):.4f}/USD prom.",
            f"${markup_banco_total:,.0f}",
            _pct(markup_banco_total),
        ],
        [
            "Markup HedgePoint",
            f"${p.markup_hedgepoint:.2f} MXN/USD",
            f"${r.costo_total_markup_hp_mxn:,.0f}",
            _pct(r.costo_total_markup_hp_mxn),
        ],
        [
            "Fee consultoría HedgePoint\nHedgePoint consulting fee",
            f"${p.fee_mensual:,.0f} MXN/mes",
            f"${r.costo_total_fee_hp_mxn:,.0f}",
            _pct(r.costo_total_fee_hp_mxn),
        ],
        [
            Paragraph("<b>TOTAL</b>", estilos["tabla_celda"]),
            "",
            Paragraph(f"<b>${costo_total:,.0f}</b>", estilos["tabla_celda"]),
            Paragraph("<b>100.0%</b>", estilos["tabla_celda"]),
        ],
    ]

    col_widths = [6.0 * cm, 4.0 * cm, 3.5 * cm, 2.5 * cm]
    t = Table(filas, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("BACKGROUND", (0, 1), (-1, 1), AZUL_CLARO),   # subyacente
        ("BACKGROUND", (0, 2), (-1, 2), GRIS_CLARO),   # prima teórica
        ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#fff3cd")),  # markup banco
        ("BACKGROUND", (0, 4), (-1, 5), VERDE_CLARO),  # HP
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, AZUL),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elementos.append(t)

    nota = (
        f"† El valor capturado en ejercicios fue <b>${r.valor_total_ejercicios_mxn:,.0f} MXN</b>. "
        f"Las primas totales pagadas (incluyendo markup banco) fueron "
        f"<b>${r.prima_total_pagada_mxn:,.0f} MXN</b>. "
        f"Resultado neto de la estrategia de opciones vs spot: "
        f"<b>${r.ahorro_total_vs_spot_mxn:,.0f} MXN</b>."
    )
    elementos.append(Spacer(1, 0.3 * cm))
    elementos.append(Paragraph(nota, estilos["pie"]))
    return elementos


def _seccion_recomendacion_opciones(
    resultado: ResultadoSimulacionOpciones, estilos: dict
) -> list:
    """Recomendación final para la estrategia de opciones."""
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Recomendación / Recommendation",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=VERDE, spaceAfter=8))

    _resultado_positivo = r.ahorro_total_vs_spot_mxn >= 0
    _prima_pct_margen = (
        r.prima_total_pagada_mxn / (r.costo_total_spot_mxn * p.margen_utilidad) * 100
        if p.margen_utilidad > 0 and r.costo_total_spot_mxn > 0 else 0.0
    )

    if _resultado_positivo:
        texto_es = (
            f"La estrategia de opciones put generó un resultado neto positivo de "
            f"<b>${r.ahorro_total_vs_spot_mxn:,.0f} MXN</b> frente a comprar 100% a spot, "
            f"con el put ejercido en <b>{r.porcentaje_meses_ejercidos:.0f}%</b> de los meses. "
            f"La prima total representó el <b>{_prima_pct_margen:.1f}%</b> del margen de utilidad, "
            f"actuando como un costo de seguro predecible. "
            f"<b>HedgePoint MX recomienda la estrategia de opciones put</b> para clientes que "
            f"desean protección ante depreciación sin sacrificar el beneficio cuando el peso se aprecia."
        )
        texto_en = (
            f"The put options strategy generated a net positive result of "
            f"<b>${r.ahorro_total_vs_spot_mxn:,.0f} MXN</b> vs buying 100% at spot, "
            f"with the put exercised in <b>{r.porcentaje_meses_ejercidos:.0f}%</b> of months. "
            f"Total premiums represented <b>{_prima_pct_margen:.1f}%</b> of profit margin — "
            f"a predictable insurance cost. "
            f"<b>HedgePoint MX recommends put options</b> for clients seeking downside protection "
            f"without capping upside when the peso appreciates."
        )
        bg_color = VERDE_CLARO
        border_color = VERDE
    else:
        texto_es = (
            f"Durante el período analizado, el tipo de cambio no se depreció lo suficiente para "
            f"recuperar las primas pagadas (<b>${r.prima_total_pagada_mxn:,.0f} MXN</b>). "
            f"Sin embargo, la estrategia ofrece <b>protección asimétrica</b>: en períodos de "
            f"depreciación fuerte, el put habría limitado el daño de forma significativa. "
            f"El costo de la prima fue del <b>{_prima_pct_margen:.1f}%</b> del margen. "
            f"<b>HedgePoint MX recomienda evaluar una estrategia collar</b> (put + call vendido) "
            f"para reducir el costo neto de prima."
        )
        texto_en = (
            f"During the analyzed period, the FX rate did not depreciate enough to recover "
            f"the premiums paid (<b>${r.prima_total_pagada_mxn:,.0f} MXN</b>). "
            f"However, the strategy provides <b>asymmetric protection</b>: during sharp "
            f"depreciation episodes, the put would have capped the damage significantly. "
            f"Premium cost was <b>{_prima_pct_margen:.1f}%</b> of profit margin. "
            f"<b>HedgePoint MX recommends evaluating a collar strategy</b> (put + sold call) "
            f"to reduce the net premium cost."
        )
        bg_color = colors.HexColor("#fff8e1")
        border_color = colors.HexColor("#f59e0b")

    caja = Table(
        [[Paragraph(texto_es, estilos["recomendacion"])],
         [Paragraph(texto_en, ParagraphStyle(
             "rec_op_en", fontName="Helvetica-Oblique", fontSize=8.5,
             textColor=GRIS, alignment=TA_JUSTIFY, leading=12,
         ))]],
        colWidths=[16 * cm],
    )
    caja.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 1.5, border_color),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elementos.append(caja)
    elementos.append(Spacer(1, 0.6 * cm))

    elementos.append(Paragraph("Próximos Pasos / Next Steps", estilos["sub_encabezado"]))
    pasos = [
        "1. Agendar reunión de diagnóstico de exposición cambiaria (sin costo).",
        "2. Comparar estrategia put vs forward vs collar según perfil de riesgo.",
        "3. Implementar primeras coberturas con monitoreo continuo vía HedgePoint MX.",
        "4. Revisión mensual de resultados y ajuste de volatilidad implícita usada.",
    ]
    for paso in pasos:
        elementos.append(Paragraph(paso, estilos["cuerpo"]))

    elementos.append(Spacer(1, 0.5 * cm))
    disclaimer = (
        "* Este reporte es un análisis histórico con fines ilustrativos. Las primas de opciones "
        "se calculan con el modelo Garman-Kohlhagen usando volatilidad histórica 30d como proxy "
        "de volatilidad implícita — en mercado real las primas pueden diferir. HedgePoint MX "
        "no es una institución financiera regulada. "
        "/ <i>* Historical analysis for illustrative purposes only. Option premiums are computed "
        "with Garman-Kohlhagen using 30d historical vol as an implied vol proxy — real market "
        "premiums may differ. HedgePoint MX is not a regulated financial institution.</i>"
    )
    elementos.append(Paragraph(disclaimer, estilos["pie"]))
    return elementos


# ---------------------------------------------------------------------------
# Collar PDF helpers
# ---------------------------------------------------------------------------

def _portada_collar(resultado: ResultadoSimulacionCollar, estilos: dict) -> list:
    """Portada adaptada para el reporte de estrategia collar."""
    p = resultado.parametros
    elementos = []

    elementos.append(Spacer(1, 3.5 * cm))
    elementos.append(Paragraph("HedgePoint MX", estilos["titulo_portada"]))
    elementos.append(Spacer(1, 0.5 * cm))
    elementos.append(Paragraph(
        "Gestión de Riesgos Financieros para PyMEs Mexicanas",
        estilos["subtitulo_portada"],
    ))
    elementos.append(Spacer(1, 1.5 * cm))

    elementos.append(Paragraph(
        "SIMULADOR DE COBERTURA CON COLLAR USD/MXN",
        ParagraphStyle(
            "titulo_rep_col", fontName="Helvetica-Bold", fontSize=16,
            textColor=BLANCO, alignment=TA_CENTER, leading=20,
            spaceBefore=10, spaceAfter=4,
        ),
    ))
    elementos.append(Paragraph(
        "Collar Strategy Simulator (Put ATM + Call OTM)",
        ParagraphStyle(
            "titulo_rep_col_en", fontName="Helvetica", fontSize=12,
            textColor=colors.HexColor("#90adc9"), alignment=TA_CENTER,
        ),
    ))
    elementos.append(Spacer(1, 2.5 * cm))

    _zc = "Sí / Yes" if resultado.es_zero_cost else "No"
    datos_tabla = [
        ["Período analizado", f"{resultado.fecha_inicio} — {resultado.fecha_fin}"],
        ["Volumen mensual", f"USD ${p.volumen_mensual_usd:,.0f}"],
        ["Margen de utilidad", f"{p.margen_utilidad * 100:.1f}%"],
        ["Frecuencia de compra", p.frecuencia.capitalize()],
        ["Instrumento evaluado", f"Call ATM comprado + Put −{resultado.otm_pct * 100:.1f}% OTM vendido (GK)"],
        ["Markup banco (primas)", f"{resultado.markup_banco_pct * 100:.0f}% sobre prima teórica"],
        ["Zero-cost collar", _zc],
    ]
    t = Table(datos_tabla, colWidths=[5.5 * cm, 7 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1e4070")),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#162d50")),
        ("TEXTCOLOR", (0, 0), (-1, -1), BLANCO),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#2a4f82")),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    t.hAlign = "CENTER"
    elementos.append(t)
    elementos.append(Spacer(1, 2 * cm))

    _MESES_ES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
    }
    from datetime import date as _date
    _hoy = _date.today()
    elementos.append(Paragraph(
        f"Generado el {_hoy.day} de {_MESES_ES[_hoy.month]} de {_hoy.year}",
        estilos["etiqueta_portada"],
    ))
    elementos.append(Paragraph(
        "Confidencial — Solo para uso interno y presentación a prospectos",
        estilos["etiqueta_portada"],
    ))
    return elementos


def _resumen_ejecutivo_collar(
    resultado: ResultadoSimulacionCollar, estilos: dict
) -> list:
    """Resumen ejecutivo con KPIs propios de la estrategia de collar."""
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Resumen Ejecutivo / Executive Summary",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=8))

    _margen_anual = r.costo_total_spot_mxn * p.margen_utilidad
    _pct_prima_margen = (
        r.prima_neta_total_pagada_mxn / _margen_anual * 100
        if _margen_anual > 0 and r.prima_neta_total_pagada_mxn > 0 else 0.0
    )
    _pct_call = r.meses_call_ejercido / r.total_meses * 100 if r.total_meses else 0.0
    _pct_libre = r.meses_zona_libre / r.total_meses * 100 if r.total_meses else 0.0
    _pct_put = r.meses_put_ejercido / r.total_meses * 100 if r.total_meses else 0.0
    _zc_str = "zero-cost (crédito neto)" if r.es_zero_cost else f"prima neta ${r.prima_neta_total_pagada_mxn:,.0f} MXN"

    texto_es = (
        f"Durante el período <b>{r.fecha_inicio}</b> al <b>{r.fecha_fin}</b>, "
        f"el collar fue evaluado en <b>{r.total_meses}</b> meses. "
        f"El call comprado protegió al importador en <b>{r.meses_call_ejercido}</b> meses ({_pct_call:.0f}%) "
        f"cuando el USD subió por encima del strike ATM. "
        f"El put vendido se ejerció en contra en <b>{r.meses_put_ejercido}</b> meses ({_pct_put:.0f}%), "
        f"y en <b>{r.meses_zona_libre}</b> meses ({_pct_libre:.0f}%) el importador compró a precio de mercado. "
        f"La estrategia resultó en <b>{_zc_str}</b>. "
        f"<b>El collar reduce el costo de prima vs el call solo, "
        f"a cambio de limitar el beneficio cuando el peso se aprecia más del "
        f"{r.otm_pct * 100:.1f}%.</b>"
    )
    texto_en = (
        f"Over the period <b>{r.fecha_inicio}</b> to <b>{r.fecha_fin}</b>, "
        f"the collar was evaluated across <b>{r.total_meses}</b> months. "
        f"The bought call protected the importer in <b>{r.meses_call_ejercido}</b> months ({_pct_call:.0f}%) "
        f"when USD rose above the ATM strike. "
        f"The sold put was exercised against the importer in <b>{r.meses_put_ejercido}</b> months ({_pct_put:.0f}%), "
        f"and in <b>{r.meses_zona_libre}</b> months ({_pct_libre:.0f}%) the importer bought at market rate. "
        f"<b>The collar cuts net premium vs a standalone call, "
        f"in exchange for capping the benefit when the peso appreciates beyond "
        f"{r.otm_pct * 100:.1f}%.</b>"
    )
    elementos.append(Paragraph(texto_es, estilos["cuerpo"]))
    elementos.append(Paragraph(texto_en, ParagraphStyle(
        "re_col_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12, spaceAfter=10,
    )))

    # KPIs fila 1: resultado global
    _ahorro_pos = r.ahorro_total_vs_spot_mxn >= 0
    kpis_fila1 = [
        (
            f"${r.ahorro_total_vs_spot_mxn:,.0f}",
            "Ahorro / Costo neto total\nNet Savings / Cost vs Spot",
            not _ahorro_pos,
        ),
        (
            f"${r.ahorro_promedio_mensual_mxn:,.0f}",
            "Promedio mensual\nAvg Monthly Result",
            r.ahorro_promedio_mensual_mxn < 0,
        ),
        (
            f"{r.ahorro_total_porcentaje:.2f}%",
            "% sobre costo spot\n% of Spot Cost",
            not _ahorro_pos,
        ),
        (
            f"{_pct_call:.0f}%",
            "Meses call ejercido (prot.)\nMonths Call Exercised",
            False,
        ),
        (
            f"{r.total_meses}",
            "Meses analizados\nMonths Analyzed",
            False,
        ),
    ]
    # KPIs fila 2: primas y escenarios
    kpis_fila2 = [
        (
            f"${r.prima_neta_total_pagada_mxn:,.0f}",
            "Prima neta total (call−put)\nNet Premium Paid (call−put)",
            r.prima_neta_total_pagada_mxn > 0,
        ),
        (
            f"${r.prima_call_promedio_mxn_por_usd:.4f}",
            "Prima call prom./USD\nAvg Call Premium/USD",
            True,
        ),
        (
            f"${r.prima_put_promedio_mxn_por_usd:.4f}",
            "Prima put recibida/USD\nAvg Put Received/USD",
            False,
        ),
        (
            f"{_pct_put:.0f}%",
            "Meses put vendido activo\nMonths Sold Put Active",
            True,
        ),
        (
            f"${r.costo_total_markup_hp_mxn + r.costo_total_fee_hp_mxn:,.0f}",
            "Costo total HedgePoint\nTotal HedgePoint Cost",
            True,
        ),
    ]

    fila_kpis = [
        [_kpi_box(num, lbl, neg) for num, lbl, neg in kpis_fila1],
        [_kpi_box(num, lbl, neg) for num, lbl, neg in kpis_fila2],
    ]
    t_kpis = Table(fila_kpis, colWidths=[3.8 * cm] * 5,
                   hAlign="CENTER", spaceBefore=6, spaceAfter=4)
    t_kpis.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    elementos.append(t_kpis)

    # Mejor / peor mes
    if r.mejor_mes and r.peor_mes:
        elementos.append(Spacer(1, 0.5 * cm))
        _ESCENARIO_LABEL = {
            "call_ejercido": "Call ejercido (prot.)",
            "zona_libre": "Zona libre",
            "put_ejercido": "Put vendido activo",
        }
        datos_extremos = [
            ["", "Mes", "Spot venc.", "Escenario", "TC efectivo", "Resultado (MXN)"],
            [
                Paragraph("<b>Mejor mes / Best month</b>", estilos["tabla_celda_left"]),
                r.mejor_mes.periodo,
                f"{r.mejor_mes.spot_compra:.4f}",
                _ESCENARIO_LABEL.get(r.mejor_mes.escenario, r.mejor_mes.escenario),
                f"{r.mejor_mes.tc_efectivo:.4f}",
                f"${r.mejor_mes.ahorro_vs_spot_mxn:,.2f}",
            ],
            [
                Paragraph("<b>Peor mes / Worst month</b>", estilos["tabla_celda_left"]),
                r.peor_mes.periodo,
                f"{r.peor_mes.spot_compra:.4f}",
                _ESCENARIO_LABEL.get(r.peor_mes.escenario, r.peor_mes.escenario),
                f"{r.peor_mes.tc_efectivo:.4f}",
                f"${r.peor_mes.ahorro_vs_spot_mxn:,.2f}",
            ],
        ]
        t_ext = Table(
            datos_extremos,
            colWidths=[4.0 * cm, 1.8 * cm, 2.2 * cm, 2.5 * cm, 2.2 * cm, 3.0 * cm],
        )
        t_ext.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), AZUL),
            ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("BACKGROUND", (0, 1), (-1, 1), VERDE_CLARO),
            ("BACKGROUND", (0, 2), (-1, 2), AZUL_CLARO),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elementos.append(t_ext)

    return elementos


def _grafica_collar_resultado(resultado: ResultadoSimulacionCollar) -> "Image":
    """
    Gráfica de barras mensuales del resultado collar vs spot,
    coloreadas por escenario (put/libre/call), con acumulado inferior.
    """
    r = resultado
    periodos = r.periodos
    n = len(periodos)

    ahorros = [p.ahorro_vs_spot_mxn / 1000 for p in periodos]

    _COLOR_ESCENARIO = {
        "put_ejercido": "#2d8659",    # verde: put protegió
        "zona_libre": "#f59e0b",      # ámbar: sin cambio
        "call_ejercido": "#8eafd4",   # azul claro: call ejercido en contra
    }
    colores_barras = [_COLOR_ESCENARIO.get(p.escenario, "#8eafd4") for p in periodos]

    tick_step = max(1, round(n / 8))
    tick_indices = list(range(0, n, tick_step))
    tick_labels = [periodos[i].periodo for i in tick_indices]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7))
    fig.patch.set_facecolor("white")

    # Panel superior: resultado mensual coloreado por escenario
    ax1.set_facecolor("#f9fafb")
    ax1.bar(range(n), ahorros, color=colores_barras, edgecolor="none", width=0.75)
    ax1.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    ax1.set_xticks(tick_indices)
    ax1.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax1.tick_params(axis="y", labelsize=8)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))
    ax1.set_ylabel("Resultado vs Spot (miles MXN)", fontsize=10, color="#374151")
    ax1.set_title(
        "Resultado mensual: Collar vs Spot",
        fontsize=12, fontweight="bold", color="#1a365d",
    )
    from matplotlib.patches import Patch
    ax1.legend(
        handles=[
            Patch(facecolor="#2d8659", label="Put ejercido / Put exercised"),
            Patch(facecolor="#f59e0b", label="Zona libre / Free zone"),
            Patch(facecolor="#8eafd4", label="Call ejercido / Call exercised"),
        ],
        fontsize=7, loc="upper right", framealpha=0.7,
    )
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    # Panel inferior: acumulado
    ax2.set_facecolor("#f9fafb")
    ahorro_acum = [sum(ahorros[: i + 1]) for i in range(n)]
    positivo = [v >= 0 for v in ahorro_acum]
    x = list(range(n))
    ax2.fill_between(x, 0, ahorro_acum,
                     where=positivo, alpha=0.25, color="#2d8659")
    ax2.fill_between(x, 0, ahorro_acum,
                     where=[not v for v in positivo], alpha=0.20, color="#8eafd4")
    ax2.plot(x, ahorro_acum, color="#1a365d", linewidth=2, marker="o", markersize=4)
    ax2.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    ax2.set_xticks(tick_indices)
    ax2.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax2.tick_params(axis="y", labelsize=8)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))
    ax2.set_ylabel("Resultado acumulado (miles MXN)", fontsize=10, color="#374151")
    ax2.set_title(
        "Resultado acumulado: Collar vs Spot",
        fontsize=12, fontweight="bold", color="#1a365d",
    )
    ax2.grid(True, alpha=0.3, linestyle="--")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    plt.tight_layout(pad=2.0)
    return _imagen_desde_figura(fig, width_cm=16.5, max_height_cm=19.0)


def _grafica_collar_primas(resultado: ResultadoSimulacionCollar) -> "Image":
    """
    Gráfica dual: prima call comprada (barras hacia arriba, pagada) y prima put
    vendida (barras hacia abajo, recibida) por período, más prima neta como línea.
    """
    r = resultado
    periodos = r.periodos
    n = len(periodos)

    primas_call_pagada = [p.prima_call_banco_mxn for p in periodos]   # call comprado (paga)
    primas_put_recibida = [-p.prima_put_banco_mxn for p in periodos]  # put vendido (recibe, negativo)
    primas_neta = [p.prima_neta_mxn for p in periodos]

    tick_step = max(1, round(n / 8))
    tick_indices = list(range(0, n, tick_step))
    tick_labels = [periodos[i].periodo for i in tick_indices]

    fig, ax = plt.subplots(figsize=(12, 4.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f9fafb")

    x = list(range(n))
    ax.bar(x, primas_call_pagada, color="#c0392b", alpha=0.65, width=0.4,
           align="edge", label="Prima call comprado pagada (MXN/USD)")
    ax.bar([xi + 0.4 for xi in x], primas_put_recibida, color="#2d8659", alpha=0.65, width=0.4,
           label="Prima put vendido recibida (MXN/USD, negativo = ingreso)")
    ax.plot(x, primas_neta, color="#1a365d", linewidth=2,
            marker="D", markersize=4, label="Prima neta (MXN/USD)")
    ax.axhline(0, color="#374151", linewidth=0.8, linestyle="--")

    ax.set_xticks(tick_indices)
    ax.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax.tick_params(axis="y", labelsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.3f}"))
    ax.set_ylabel("Prima (MXN por USD)", fontsize=9, color="#374151")
    ax.set_title(
        "Prima Call comprado pagada vs Prima Put vendido recibida y Prima Neta por período\n"
        "Bought Call Premium Paid vs Sold Put Premium Received and Net Premium per Period",
        fontsize=10, fontweight="bold", color="#1a365d",
    )
    ax.legend(fontsize=7, loc="upper right", framealpha=0.8)
    ax.grid(True, axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    plt.tight_layout(pad=2.0)
    return _imagen_desde_figura(fig, width_cm=16.5, max_height_cm=12.0)


def _tabla_mensual_collar(resultado: ResultadoSimulacionCollar, estilos: dict) -> list:
    """Tabla detallada mes a mes de la estrategia de collar."""
    r = resultado
    elementos = []

    elementos.append(Paragraph(
        "Análisis Mensual / Monthly Breakdown",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    _MESES_ABR = {
        "01": "Ene", "02": "Feb", "03": "Mar", "04": "Abr",
        "05": "May", "06": "Jun", "07": "Jul", "08": "Ago",
        "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dic",
    }

    def _abr(periodo: str) -> str:
        try:
            anio, mes = periodo.split("-")
            return f"{_MESES_ABR.get(mes, mes)}-{anio[2:]}"
        except ValueError:
            return periodo

    _ESCENARIO_ABREV = {
        "call_ejercido": "Call ✓",   # importador ejerció el call (USD subió — bueno)
        "zona_libre": "Libre",
        "put_ejercido": "Put ✗",     # banco ejerció el put vendido (USD bajó mucho — limita)
    }

    encabezados = [
        "Mes", "Spot\nvenc.", "Strike\nCall", "Prima\nNeta/USD",
        "Escenario", "TC\nEfect.", "C.Spot\n(MXN)",
        "C.Collar\n(MXN)", "Result.\nvs Spot", "(%)",
    ]
    filas = [encabezados]

    for p in r.periodos:
        filas.append([
            _abr(p.periodo),
            f"{p.spot_compra:.4f}",
            f"{p.strike_call_comprado:.4f}",
            f"${p.prima_neta_mxn:.4f}",
            _ESCENARIO_ABREV.get(p.escenario, p.escenario),
            f"{p.tc_efectivo:.4f}",
            f"${p.costo_spot_mxn:,.0f}",
            f"${p.costo_collar_mxn:,.0f}",
            f"${p.ahorro_vs_spot_mxn:,.0f}",
            f"{p.ahorro_porcentaje:.1f}%",
        ])

    # Fila de totales
    filas.append([
        Paragraph("<b>TOTAL</b>", estilos["tabla_celda"]),
        "", "",
        Paragraph(f"<b>${r.prima_neta_total_pagada_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(
            f"<b>C:{r.meses_call_ejercido} L:{r.meses_zona_libre} P:{r.meses_put_ejercido}</b>",
            estilos["tabla_celda"],
        ),
        "",
        Paragraph(f"<b>${r.costo_total_spot_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.costo_total_collar_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>${r.ahorro_total_vs_spot_mxn:,.0f}</b>", estilos["tabla_celda"]),
        Paragraph(f"<b>{r.ahorro_total_porcentaje:.1f}%</b>", estilos["tabla_celda"]),
    ])

    col_widths = [
        1.4 * cm, 1.8 * cm, 1.8 * cm, 2.2 * cm,
        1.8 * cm, 1.8 * cm, 2.4 * cm,
        2.4 * cm, 2.2 * cm, 1.2 * cm,
    ]
    t = Table(filas, colWidths=col_widths, repeatRows=1)

    estilo_tabla = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 6.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [BLANCO, GRIS_CLARO]),
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, AZUL),
    ]
    # Colorear resultado y escenario por fila
    for i, p in enumerate(r.periodos, start=1):
        col_res = 8
        if p.ahorro_vs_spot_mxn > 0:
            estilo_tabla.append(("TEXTCOLOR", (col_res, i), (col_res + 1, i), VERDE))
        else:
            estilo_tabla.append(("TEXTCOLOR", (col_res, i), (col_res + 1, i), GRIS))
        # Colorear columna Escenario
        col_esc = 4
        if p.escenario == "call_ejercido":
            # importador ejerció el call comprado (USD subió — protección activa) → verde
            estilo_tabla.append(("TEXTCOLOR", (col_esc, i), (col_esc, i), VERDE))
            estilo_tabla.append(("FONTNAME", (col_esc, i), (col_esc, i), "Helvetica-Bold"))
        elif p.escenario == "put_ejercido":
            # banco ejerció el put vendido (USD bajó mucho — limita beneficio) → rojo
            estilo_tabla.append(("TEXTCOLOR", (col_esc, i), (col_esc, i), ROJO))
            estilo_tabla.append(("FONTNAME", (col_esc, i), (col_esc, i), "Helvetica-Bold"))

    t.setStyle(TableStyle(estilo_tabla))
    elementos.append(t)

    nota = (
        "Call ✓ = call comprado ejercido (spot > strike_call_ATM) — importador compra al strike ATM. "
        "Libre = spot en zona intermedia, compra a mercado. "
        "Put ✗ = put vendido ejercido en contra (spot < strike_put_OTM) — importador obligado al strike_put. "
        "/ <i>Call ✓ = bought call exercised (USD rose above ATM strike). "
        "Libre = no exercise, buys at market. "
        "Put ✗ = sold put exercised against importer (USD fell below OTM strike).</i>"
    )
    elementos.append(Spacer(1, 0.2 * cm))
    elementos.append(Paragraph(nota, ParagraphStyle(
        "nota_col", fontName="Helvetica", fontSize=7, textColor=GRIS,
        alignment=TA_JUSTIFY, leading=10, spaceAfter=4,
    )))
    return elementos


def _seccion_desglose_costos_collar(
    resultado: ResultadoSimulacionCollar, estilos: dict
) -> list:
    """Desglose de costos de la estrategia de collar."""
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Desglose de Costos / Cost Breakdown",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    intro = (
        "El collar combina un call ATM comprado (protección contra depreciación) y un put OTM vendido (cuya prima recibida reduce el costo del call). "
        "El ingreso del put reduce el costo neto de prima. "
        "Si la prima neta es negativa, la estrategia es zero-cost (el put vendido subsidia el call). "
        "/ <i>The collar combines a bought ATM call (protection against depreciation) and a sold OTM put (whose received premium reduces the cost of the call). "
        "Put proceeds reduce the net premium cost. "
        "A negative net premium means zero-cost (the sold put subsidizes the bought call).</i>"
    )
    elementos.append(Paragraph(intro, estilos["cuerpo"]))
    elementos.append(Spacer(1, 0.3 * cm))

    costo_total = r.costo_total_collar_mxn

    def _pct(val: float) -> str:
        return f"{val / costo_total * 100:.1f}%" if costo_total else "—"

    # Costo subyacente = costo_total - prima_neta - markup HP - fee HP
    costo_subyacente = (
        costo_total
        - r.prima_neta_total_pagada_mxn
        - r.costo_total_markup_hp_mxn
        - r.costo_total_fee_hp_mxn
    )
    # call comprado = prima pagada; put vendido = prima recibida
    call_teorica_total = sum(
        p_mes.prima_call_teorica_mxn * p_mes.volumen_usd for p_mes in r.periodos
    )
    put_teorica_total = sum(
        p_mes.prima_put_teorica_mxn * p_mes.volumen_usd for p_mes in r.periodos
    )
    call_banco_total = sum(
        p_mes.prima_call_banco_mxn * p_mes.volumen_usd for p_mes in r.periodos
    )
    put_banco_total = sum(
        p_mes.prima_put_banco_mxn * p_mes.volumen_usd for p_mes in r.periodos
    )

    filas = [
        [
            Paragraph("<b>Concepto / Concept</b>", estilos["tabla_header"]),
            Paragraph("<b>Detalle / Detail</b>", estilos["tabla_header"]),
            Paragraph("<b>Costo total\nTotal cost (MXN)</b>", estilos["tabla_header"]),
            Paragraph("<b>% del total\n% of total</b>", estilos["tabla_header"]),
        ],
        [
            "Compra subyacente (TC efectivo)\nUnderlying purchase (effective FX)",
            f"Call: {r.meses_call_ejercido}m · Libre: {r.meses_zona_libre}m · Put: {r.meses_put_ejercido}m",
            f"${costo_subyacente:,.0f}",
            _pct(costo_subyacente),
        ],
        [
            "Prima call comprado pagada al banco\nBought call premium paid to bank",
            f"Call ATM GK × (1 + {r.markup_banco_pct * 100:.0f}%)  ·  prom. ${r.prima_call_promedio_mxn_por_usd:.4f}/USD",
            f"${call_banco_total:,.0f}",
            _pct(call_banco_total),
        ],
        [
            "Prima put vendido recibida del banco\nSold put premium received from bank",
            f"Put OTM −{r.otm_pct * 100:.1f}% GK × (1 − {r.markup_banco_pct * 100:.0f}%)  ·  prom. ${r.prima_put_promedio_mxn_por_usd:.4f}/USD",
            f"−${put_banco_total:,.0f}",
            f"−{_pct(put_banco_total)}",
        ],
        [
            f"Prima neta collar (call − put)\nNet collar premium",
            "Positivo = importador paga / Negativo = zero-cost",
            f"${r.prima_neta_total_pagada_mxn:,.0f}",
            _pct(r.prima_neta_total_pagada_mxn) if r.prima_neta_total_pagada_mxn >= 0 else "crédito",
        ],
        [
            "Markup HedgePoint",
            f"${p.markup_hedgepoint:.2f} MXN/USD",
            f"${r.costo_total_markup_hp_mxn:,.0f}",
            _pct(r.costo_total_markup_hp_mxn),
        ],
        [
            "Fee consultoría HedgePoint\nHedgePoint consulting fee",
            f"${p.fee_mensual:,.0f} MXN/mes",
            f"${r.costo_total_fee_hp_mxn:,.0f}",
            _pct(r.costo_total_fee_hp_mxn),
        ],
        [
            Paragraph("<b>TOTAL collar</b>", estilos["tabla_celda"]),
            "",
            Paragraph(f"<b>${costo_total:,.0f}</b>", estilos["tabla_celda"]),
            Paragraph("<b>100.0%</b>", estilos["tabla_celda"]),
        ],
    ]

    col_widths = [6.0 * cm, 4.5 * cm, 3.0 * cm, 2.5 * cm]
    t = Table(filas, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("BACKGROUND", (0, 1), (-1, 1), AZUL_CLARO),   # subyacente
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#fff3cd")),  # put pagada
        ("BACKGROUND", (0, 3), (-1, 3), VERDE_CLARO),  # call recibida
        ("BACKGROUND", (0, 4), (-1, 4), GRIS_CLARO),   # prima neta
        ("BACKGROUND", (0, 5), (-1, 6), colors.HexColor("#e8eef7")),  # HP
        ("BACKGROUND", (0, -1), (-1, -1), AZUL_CLARO),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, AZUL),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    elementos.append(t)

    nota = (
        f"† Prima neta total = prima put banco − prima call banco = "
        f"<b>${r.prima_neta_total_pagada_mxn:,.0f} MXN</b>. "
        f"Resultado neto collar vs spot: <b>${r.ahorro_total_vs_spot_mxn:,.0f} MXN</b>."
    )
    elementos.append(Spacer(1, 0.3 * cm))
    elementos.append(Paragraph(nota, estilos["pie"]))
    return elementos


def _seccion_recomendacion_collar(
    resultado: ResultadoSimulacionCollar, estilos: dict
) -> list:
    """Recomendación final para la estrategia de collar."""
    r = resultado
    p = r.parametros
    elementos = []

    elementos.append(Paragraph(
        "Recomendación / Recommendation",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=VERDE, spaceAfter=8))

    _resultado_positivo = r.ahorro_total_vs_spot_mxn >= 0
    _pct_call = r.meses_call_ejercido / r.total_meses * 100 if r.total_meses else 0.0
    _pct_put = r.meses_put_ejercido / r.total_meses * 100 if r.total_meses else 0.0
    _prima_pct_margen = (
        r.prima_neta_total_pagada_mxn / (r.costo_total_spot_mxn * p.margen_utilidad) * 100
        if p.margen_utilidad > 0 and r.costo_total_spot_mxn > 0
        and r.prima_neta_total_pagada_mxn > 0 else 0.0
    )
    _zc_str = "zero-cost (sin desembolso neto de prima)" if r.es_zero_cost else \
        f"prima neta del {_prima_pct_margen:.1f}% del margen"

    if _resultado_positivo:
        texto_es = (
            f"La estrategia de collar generó un resultado neto positivo de "
            f"<b>${r.ahorro_total_vs_spot_mxn:,.0f} MXN</b> frente a comprar 100% a spot. "
            f"El call comprado protegió al importador en el <b>{_pct_call:.0f}%</b> de los meses "
            f"cuando el USD superó el strike ATM, "
            f"y el put vendido se activó en contra en el <b>{_pct_put:.0f}%</b>. "
            f"La estrategia resultó <b>{_zc_str}</b>. "
            f"<b>HedgePoint MX recomienda el collar</b> como herramienta de bajo costo neto para "
            f"importadores que aceptan limitar el beneficio cuando el peso se aprecia más del "
            f"{r.otm_pct * 100:.1f}%."
        )
        texto_en = (
            f"The collar strategy generated a net positive result of "
            f"<b>${r.ahorro_total_vs_spot_mxn:,.0f} MXN</b> vs buying 100% at spot. "
            f"The bought call protected the importer in <b>{_pct_call:.0f}%</b> of months "
            f"when USD rose above the ATM strike; "
            f"the sold put was activated against the importer in <b>{_pct_put:.0f}%</b>. "
            f"Net premium was <b>{'zero-cost' if r.es_zero_cost else f'{_prima_pct_margen:.1f}% of margin'}</b>. "
            f"<b>HedgePoint MX recommends the collar</b> for importers willing to cap "
            f"the benefit when the peso appreciates beyond {r.otm_pct * 100:.1f}%."
        )
        bg_color = VERDE_CLARO
        border_color = VERDE
    else:
        texto_es = (
            f"Durante el período analizado, el costo del collar superó el ahorro generado. "
            f"El put vendido limitó el beneficio en los meses de fuerte apreciación del peso "
            f"({_pct_put:.0f}% de los meses). "
            f"Sin embargo, el collar ofrece protección real: en escenarios de depreciación "
            f"fuerte, el call comprado habría limitado el daño de forma significativa. "
            f"<b>HedgePoint MX recomienda comparar el collar con el call solo</b> para "
            f"elegir el balance óptimo entre costo de prima y amplitud de la zona libre."
        )
        texto_en = (
            f"During the analyzed period, the collar's cost exceeded the savings generated. "
            f"The sold put capped the benefit during strong peso-appreciation months "
            f"({_pct_put:.0f}% of months). "
            f"However, the collar provides real protection: during sharp USD appreciation, "
            f"the bought call would have significantly limited losses. "
            f"<b>HedgePoint MX recommends comparing the collar vs a standalone call</b> to find "
            f"the optimal balance between net premium cost and free-zone width."
        )
        bg_color = colors.HexColor("#fff8e1")
        border_color = colors.HexColor("#f59e0b")

    caja = Table(
        [[Paragraph(texto_es, estilos["recomendacion"])],
         [Paragraph(texto_en, ParagraphStyle(
             "rec_col_en", fontName="Helvetica-Oblique", fontSize=8.5,
             textColor=GRIS, alignment=TA_JUSTIFY, leading=12,
         ))]],
        colWidths=[16 * cm],
    )
    caja.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("BOX", (0, 0), (-1, -1), 1.5, border_color),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elementos.append(caja)
    elementos.append(Spacer(1, 0.6 * cm))

    elementos.append(Paragraph("Próximos Pasos / Next Steps", estilos["sub_encabezado"]))
    pasos = [
        "1. Agendar reunión de diagnóstico de exposición cambiaria (sin costo).",
        "2. Comparar collar vs forward vs put sola según perfil de riesgo y tolerancia al costo.",
        "3. Ajustar el strike OTM del put vendido (actualmente −{:.1f}%) para optimizar la prima neta.".format(
            r.otm_pct * 100
        ),
        "4. Implementar primeras coberturas con monitoreo continuo vía HedgePoint MX.",
    ]
    for paso in pasos:
        elementos.append(Paragraph(paso, estilos["cuerpo"]))

    elementos.append(Spacer(1, 0.5 * cm))
    disclaimer = (
        "* Este reporte es un análisis histórico con fines ilustrativos. Las primas se calculan "
        "con el modelo Garman-Kohlhagen usando volatilidad histórica 30d como proxy de "
        "volatilidad implícita — en mercado real las primas pueden diferir. HedgePoint MX "
        "no es una institución financiera regulada. "
        "/ <i>* Historical analysis for illustrative purposes only. Premiums use Garman-Kohlhagen "
        "with 30d historical vol as implied vol proxy — real premiums may differ. "
        "HedgePoint MX is not a regulated financial institution.</i>"
    )
    elementos.append(Paragraph(disclaimer, estilos["pie"]))
    return elementos


def generar_pdf_collar(
    resultado: ResultadoSimulacionCollar,
    ruta_salida: str | Path = "output/reporte_collar.pdf",
) -> Path:
    """
    Genera el PDF profesional para la estrategia de cobertura con collar.

    Secciones:
        1. Portada (collar)
        2. Resumen ejecutivo con KPIs de collar
        3. Gráfica de resultado mensual (coloreada por escenario) y acumulado
        4. Gráfica de primas put vs call recibida y prima neta
        5. Desglose de costos (subyacente, put, call, prima neta, markup HP, fee)
        6. Tabla mensual detallada
        7. Recomendación

    Args:
        resultado: Resultado de simulate_collar_strategy().
        ruta_salida: Ruta del archivo PDF de salida.

    Returns:
        Path al archivo PDF generado.

    Raises:
        ValueError: Si el resultado no contiene períodos.
    """
    if not resultado.periodos:
        raise ValueError("El resultado de simulación de collar no contiene períodos.")

    ruta = Path(ruta_salida)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(ruta),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title="HedgePoint MX — Simulador de Collar USD/MXN",
        author="HedgePoint MX",
        subject="Simulación de cobertura con collar (put ATM + call OTM) USD/MXN",
    )

    plantillas = _crear_plantillas(doc)
    doc.addPageTemplates(plantillas)

    estilos = _estilos()
    story = []

    # 1 — Portada
    story.append(NextPageTemplate("portada"))
    story.extend(_portada_collar(resultado, estilos))

    # 2 — Resumen ejecutivo
    story.append(NextPageTemplate("interior"))
    story.append(PageBreak())
    story.extend(_resumen_ejecutivo_collar(resultado, estilos))

    # 3 — Gráfica resultado mensual / acumulado
    story.append(PageBreak())
    story.append(Paragraph(
        "Análisis de Resultado vs Spot por Escenario / Result vs Spot by Scenario",
        estilos["encabezado_seccion"],
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    story.append(_grafica_collar_resultado(resultado))

    # 4 — Gráfica de primas put/call/neta
    story.append(PageBreak())
    story.append(Paragraph(
        "Prima Call Pagada, Prima Put Recibida y Prima Neta / Call Paid, Put Received & Net Premium",
        estilos["encabezado_seccion"],
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    story.append(_grafica_collar_primas(resultado))
    story.append(Spacer(1, 0.3 * cm))
    nota_vol = (
        f"El call comprado se calcula con Garman-Kohlhagen ATM (strike = spot contratación). "
        f"El put vendido tiene strike −{resultado.otm_pct * 100:.1f}% OTM. "
        f"El banco aplica un markup del {resultado.markup_banco_pct * 100:.0f}% sobre la prima call pagada "
        f"y descuenta ese mismo {resultado.markup_banco_pct * 100:.0f}% de la prima put que recibe del importador. "
        "La volatilidad histórica 30d hábiles se usa como proxy de volatilidad implícita. "
        "/ <i>Bought call priced ATM GK; sold put priced OTM GK. Bank adds markup on the call paid "
        "and deducts same markup from the put received from the importer. 30d historical vol used as implied vol proxy.</i>"
    )
    story.append(Paragraph(nota_vol, ParagraphStyle(
        "nota_col_vol", fontName="Helvetica", fontSize=7.5, textColor=GRIS,
        alignment=TA_JUSTIFY, leading=11, spaceAfter=4,
    )))

    # 5 — Desglose de costos
    story.append(PageBreak())
    story.extend(_seccion_desglose_costos_collar(resultado, estilos))

    # 6 — Tabla mensual
    story.append(PageBreak())
    story.extend(_tabla_mensual_collar(resultado, estilos))

    # 7 — Recomendación
    story.append(PageBreak())
    story.extend(_seccion_recomendacion_collar(resultado, estilos))

    doc.build(story)
    logger.info("PDF de collar generado: %s", ruta)
    return ruta


# ---------------------------------------------------------------------------
# Comparativa de estrategias (forward vs opciones vs collar)
# ---------------------------------------------------------------------------

def _grafica_comparativa_estrategias(comparativa: "ResultadoComparativa") -> "Image":
    """Gráfica de barras agrupadas: costo total al 50% por estrategia + mix óptimo."""
    estrategias = comparativa.estrategias_50pct
    etiquetas = [m.instrumento.capitalize() for m in estrategias]
    costos = [m.costo_total_mxn / 1_000_000 for m in estrategias]

    mix = comparativa.mix_optimo
    etiquetas.append("Mix Óptimo")
    costos.append(mix.costo_total_mxn / 1_000_000)

    colores = ["#1a365d", "#2d8659", "#e67e00", "#6b21a8"]
    costo_spot = comparativa.costo_total_spot_mxn / 1_000_000

    fig, ax = plt.subplots(figsize=(9, 4.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f9fafb")

    bars = ax.bar(etiquetas, costos, color=colores, alpha=0.85, edgecolor="white", linewidth=1.2)
    ax.axhline(costo_spot, color="#c0392b", linewidth=1.5, linestyle="--", label=f"100% Spot: ${costo_spot:.2f}M")

    for bar, val in zip(bars, costos):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.02,
            f"${val:.2f}M",
            ha="center", va="bottom", fontsize=9, fontweight="bold",
        )

    ax.set_ylabel("Costo total período (MDP)", fontsize=10, color="#374151")
    ax.set_title(
        "Comparativa de Estrategias al 50% de Cobertura / Strategy Comparison at 50% Coverage",
        fontsize=11, fontweight="bold", color="#1a365d",
    )
    ax.legend(fontsize=8, framealpha=0.9)
    ax.grid(True, axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout(pad=2.0)

    return _imagen_desde_figura(fig, width_cm=15.0)


def _seccion_comparativa_estrategias(
    comparativa: "ResultadoComparativa",
    estilos: dict,
) -> list:
    """
    Genera la sección PDF de comparativa de estrategias:
    tabla al 50%, tabla mix óptimo, gráfica y recomendación.
    """
    elementos: list = []

    elementos.append(Paragraph(
        "Comparativa de Estrategias / Strategy Comparison",
        estilos["encabezado_seccion"],
    ))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))

    intro_es = (
        "Comparación del costo histórico de las tres estrategias de cobertura "
        "(forward, opciones y collar) al 50% de cobertura, junto con la mezcla "
        "óptima que minimiza el ratio costo/protección."
    )
    intro_en = (
        "Historical cost comparison of the three hedging strategies (forward, options, "
        "and collar) at 50% coverage, together with the optimal mix minimizing the "
        "cost-to-protection ratio."
    )
    elementos.append(Paragraph(intro_es, estilos["cuerpo"]))
    elementos.append(Paragraph(intro_en, ParagraphStyle(
        "ce_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12, spaceAfter=10,
    )))

    # --- Tabla comparativa al 50% ---
    def _fmt_mxn(v: float) -> str:
        return f"${v:,.0f}"

    def _fmt_pct(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f}%"

    encabezados = [
        Paragraph("<b>Métrica / Metric</b>", estilos["tabla_header"]),
        Paragraph("<b>Forward</b>", estilos["tabla_header"]),
        Paragraph("<b>Opciones\nOptions</b>", estilos["tabla_header"]),
        Paragraph("<b>Collar</b>", estilos["tabla_header"]),
    ]

    est_by_inst = {m.instrumento: m for m in comparativa.estrategias_50pct}
    fwd = est_by_inst.get("forward")
    op = est_by_inst.get("opcion")
    col = est_by_inst.get("collar")

    def _val(m: "MetricasEstrategia | None", attr: str, fmt_fn) -> str:
        if m is None:
            return "—"
        return fmt_fn(getattr(m, attr))

    def _ratio_fmt(m: "MetricasEstrategia | None") -> str:
        if m is None:
            return "—"
        import math
        if not math.isfinite(m.ratio_costo_proteccion):
            return "∞"
        return f"{m.ratio_costo_proteccion:,.0f}"

    filas = [
        encabezados,
        [
            Paragraph("Costo total / Total cost", estilos["tabla_celda_left"]),
            Paragraph(_val(fwd, "costo_total_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(op, "costo_total_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(col, "costo_total_mxn", _fmt_mxn), estilos["tabla_celda"]),
        ],
        [
            Paragraph("Ahorro vs spot / Savings vs spot", estilos["tabla_celda_left"]),
            Paragraph(_val(fwd, "costo_vs_spot_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(op, "costo_vs_spot_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(col, "costo_vs_spot_mxn", _fmt_mxn), estilos["tabla_celda"]),
        ],
        [
            Paragraph("Impacto en margen / Margin impact", estilos["tabla_celda_left"]),
            Paragraph(_val(fwd, "pct_margen", _fmt_pct), estilos["tabla_celda"]),
            Paragraph(_val(op, "pct_margen", _fmt_pct), estilos["tabla_celda"]),
            Paragraph(_val(col, "pct_margen", _fmt_pct), estilos["tabla_celda"]),
        ],
        [
            Paragraph("Meses con valor / Months with value", estilos["tabla_celda_left"]),
            Paragraph(_val(fwd, "meses_con_valor", str), estilos["tabla_celda"]),
            Paragraph(_val(op, "meses_con_valor", str), estilos["tabla_celda"]),
            Paragraph(_val(col, "meses_con_valor", str), estilos["tabla_celda"]),
        ],
        [
            Paragraph("Mayor protección mensual / Best monthly protection", estilos["tabla_celda_left"]),
            Paragraph(_val(fwd, "peor_mes_evitado_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(op, "peor_mes_evitado_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(col, "peor_mes_evitado_mxn", _fmt_mxn), estilos["tabla_celda"]),
        ],
        [
            Paragraph("Volatilidad mensual / Monthly volatility", estilos["tabla_celda_left"]),
            Paragraph(_val(fwd, "vol_mensual_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(op, "vol_mensual_mxn", _fmt_mxn), estilos["tabla_celda"]),
            Paragraph(_val(col, "vol_mensual_mxn", _fmt_mxn), estilos["tabla_celda"]),
        ],
        [
            Paragraph("Ratio costo/protección\nCost/protection ratio", estilos["tabla_celda_left"]),
            Paragraph(_ratio_fmt(fwd), estilos["tabla_celda"]),
            Paragraph(_ratio_fmt(op), estilos["tabla_celda"]),
            Paragraph(_ratio_fmt(col), estilos["tabla_celda"]),
        ],
    ]

    col_widths = [5.5 * cm, 3.3 * cm, 3.3 * cm, 3.3 * cm]
    t = Table(filas, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BLANCO, AZUL_CLARO]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elementos.append(t)
    elementos.append(Spacer(1, 0.5 * cm))

    # --- Gráfica comparativa ---
    try:
        elementos.append(_grafica_comparativa_estrategias(comparativa))
    except Exception as exc:
        logger.warning("No se pudo generar gráfica comparativa: %s", exc)
    elementos.append(Spacer(1, 0.4 * cm))

    # --- Tabla mix óptimo ---
    mix = comparativa.mix_optimo
    elementos.append(Paragraph(
        "Mezcla Óptima Recomendada / Optimal Recommended Mix",
        estilos["sub_encabezado"],
    ))
    elementos.append(Spacer(1, 0.2 * cm))

    filas_mix = [
        [
            Paragraph("<b>Parámetro / Parameter</b>", estilos["tabla_header"]),
            Paragraph("<b>Valor / Value</b>", estilos["tabla_header"]),
        ],
        [Paragraph("Tipo / Type", estilos["tabla_celda_left"]),
         Paragraph(mix.tipo.capitalize(), estilos["tabla_celda"])],
        [Paragraph("Estrategia / Strategy", estilos["tabla_celda_left"]),
         Paragraph(mix.instrumento_principal, estilos["tabla_celda"])],
        [Paragraph("% Forward", estilos["tabla_celda_left"]),
         Paragraph(f"{mix.pct_forward:.0f}%", estilos["tabla_celda"])],
        [Paragraph("% Opciones / Options", estilos["tabla_celda_left"]),
         Paragraph(f"{mix.pct_opcion:.0f}%", estilos["tabla_celda"])],
        [Paragraph("% Collar", estilos["tabla_celda_left"]),
         Paragraph(f"{mix.pct_collar:.0f}%", estilos["tabla_celda"])],
        [Paragraph("% Sin cubrir / Uncovered", estilos["tabla_celda_left"]),
         Paragraph(f"{mix.pct_sin_cubrir:.0f}%", estilos["tabla_celda"])],
        [Paragraph("Costo total / Total cost", estilos["tabla_celda_left"]),
         Paragraph(_fmt_mxn(mix.costo_total_mxn), estilos["tabla_celda"])],
        [Paragraph("Ahorro vs spot / Savings vs spot", estilos["tabla_celda_left"]),
         Paragraph(_fmt_mxn(mix.costo_vs_spot_mxn), estilos["tabla_celda"])],
        [Paragraph("Impacto en margen / Margin impact", estilos["tabla_celda_left"]),
         Paragraph(_fmt_pct(mix.pct_margen), estilos["tabla_celda"])],
        [Paragraph("Meses protegidos / Protected months", estilos["tabla_celda_left"]),
         Paragraph(str(mix.meses_protegidos), estilos["tabla_celda"])],
        [Paragraph("Ratio costo/protección / Cost-protection ratio", estilos["tabla_celda_left"]),
         Paragraph(f"{mix.ratio_costo_proteccion:,.0f}", estilos["tabla_celda"])],
    ]
    tm = Table(filas_mix, colWidths=[9.0 * cm, 6.4 * cm], repeatRows=1)
    tm.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [BLANCO, VERDE_CLARO]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elementos.append(tm)
    elementos.append(Spacer(1, 0.4 * cm))

    # --- Párrafo de recomendación ---
    recom_es = mix.razon_seleccion
    recom_en = (
        f"The optimal mix ({mix.instrumento_principal}) achieves the best "
        f"cost-to-protection ratio ({mix.ratio_costo_proteccion:,.0f}), "
        f"protecting {mix.meses_protegidos} months out of the analyzed period "
        f"at a total cost of ${mix.costo_total_mxn:,.0f} MXN."
    )

    rec_style = ParagraphStyle(
        "ce_rec", parent=estilos["cuerpo"],
        backColor=VERDE_CLARO,
        borderPad=8,
        borderColor=VERDE,
        borderWidth=1,
        borderRadius=4,
        spaceAfter=6,
    )
    elementos.append(Paragraph(recom_es, rec_style))
    elementos.append(Paragraph(recom_en, ParagraphStyle(
        "ce_rec_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12,
    )))

    return elementos


# ---------------------------------------------------------------------------
# Función principal de generación
# ---------------------------------------------------------------------------

def generar_pdf(
    resultado: ResultadoSimulacion,
    ruta_salida: str | Path = "output/reporte_simulacion.pdf",
    multi_plazo: ResultadoMultiPlazo | None = None,
    comparativa: ResultadoComparativa | None = None,
) -> Path:
    """
    Genera el PDF profesional de simulación de ahorro.

    Args:
        resultado: Resultado de la simulación principal (objeto ResultadoSimulacion).
        ruta_salida: Ruta del archivo PDF de salida.
        multi_plazo: Resultados multi-plazo (30/60/90d). Si se pasa, se agregan
                     las secciones de desglose de costos y comparativa de plazos.
        comparativa: Comparativa de estrategias (forward/opciones/collar). Si se pasa,
                     se agrega la sección de comparativa de estrategias.

    Returns:
        Path al archivo PDF generado.

    Raises:
        ImportError: Si reportlab no está instalado.
        ValueError: Si el resultado no contiene períodos.
    """
    if not resultado.periodos:
        raise ValueError("El resultado de simulación no contiene períodos.")

    ruta = Path(ruta_salida)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(ruta),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title="HedgePoint MX — Simulador de Ahorro por Cobertura Forward",
        author="HedgePoint MX",
        subject="Simulación de cobertura forward USD/MXN",
    )

    plantillas = _crear_plantillas(doc)
    doc.addPageTemplates(plantillas)

    estilos = _estilos()
    df_periodos = resultado.to_dataframe()

    # Cargar datos históricos para la gráfica
    from core.database import get_connection, DB_PATH
    sql = """
        SELECT fecha, AVG(bid) AS tc
        FROM fx_rates WHERE par = 'USD/MXN'
        AND fecha BETWEEN ? AND ?
        GROUP BY fecha ORDER BY fecha ASC
    """
    fecha_ini = str(resultado.fecha_inicio)
    fecha_fin = str(resultado.fecha_fin)
    with get_connection(DB_PATH) as conn:
        rows = conn.execute(sql, (fecha_ini, fecha_fin)).fetchall()
    df_fx = pd.DataFrame([dict(r) for r in rows])
    if not df_fx.empty:
        df_fx["fecha"] = pd.to_datetime(df_fx["fecha"])

    # Construir story (contenido del PDF)
    story = []

    # --- PORTADA ---
    story.append(NextPageTemplate("portada"))
    story.extend(_portada(resultado, estilos))

    # --- SECCIONES INTERIORES ---
    story.append(NextPageTemplate("interior"))
    story.append(PageBreak())

    p = resultado.parametros

    # 2 — Resumen ejecutivo
    story.extend(_resumen_ejecutivo(resultado, estilos))
    story.append(Spacer(1, 0.4 * cm))

    # 3 — Nivel de cobertura + tabla comparativa 4 niveles
    story.append(PageBreak())
    story.extend(_seccion_nivel_cobertura(resultado, estilos))

    # 4 — Desempeño por año (mismo bloque, sin page break propio)
    story.extend(_tabla_resumen_anual(resultado, estilos))

    # 5 — Análisis de Riesgo (sección más importante)
    story.append(PageBreak())
    story.extend(_seccion_analisis_riesgo(resultado, estilos))

    # 6 — Desglose de costos (solo si hay costos transaccionales configurados)
    hay_costos = p.spread_banco > 0 or p.markup_hedgepoint > 0 or p.fee_mensual > 0
    if hay_costos:
        story.append(PageBreak())
        story.extend(_seccion_desglose_costos(resultado, estilos))

    # 7 — TC histórico + Análisis de resultado vs spot
    story.append(PageBreak())
    story.append(Paragraph("Tipo de Cambio Histórico / Historical FX Rate",
                            estilos["encabezado_seccion"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    if not df_fx.empty:
        story.append(_grafica_tc_historico(df_fx, resultado.periodos))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Análisis de Resultado vs Spot / Savings Analysis",
                            estilos["encabezado_seccion"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    story.append(_grafica_ahorro_acumulado(df_periodos))
    story.extend(_recuadro_contexto_seguro(resultado, estilos))

    # 8 — Comparativa de plazos (si se proveyó)
    if multi_plazo is not None:
        story.append(PageBreak())
        story.extend(_seccion_comparativa_plazos(multi_plazo, estilos))

    # 8b — Comparativa de estrategias (si se proveyó)
    if comparativa is not None:
        story.append(PageBreak())
        story.extend(_seccion_comparativa_estrategias(comparativa, estilos))

    # 9 — Tabla mensual
    story.append(PageBreak())
    story.extend(_tabla_mensual(resultado, estilos))

    # 10 — Recomendación
    story.append(PageBreak())
    story.extend(_seccion_recomendacion(resultado, estilos))

    doc.build(story)
    logger.info("PDF generado: %s", ruta)
    return ruta


def generar_pdf_opciones(
    resultado: ResultadoSimulacionOpciones,
    ruta_salida: str | Path = "output/reporte_opciones.pdf",
) -> Path:
    """
    Genera el PDF profesional para la estrategia de cobertura con opciones put.

    Secciones:
        1. Portada (put options)
        2. Resumen ejecutivo con KPIs de opciones
        3. Gráfica de resultado mensual y acumulado vs spot
        4. Gráfica de volatilidad histórica y prima del banco
        5. Desglose de costos (subyacente, prima teórica, markup banco, HP)
        6. Tabla mensual detallada
        7. Recomendación

    Args:
        resultado: Resultado de simulate_options_strategy().
        ruta_salida: Ruta del archivo PDF de salida.

    Returns:
        Path al archivo PDF generado.

    Raises:
        ValueError: Si el resultado no contiene períodos.
    """
    if not resultado.periodos:
        raise ValueError("El resultado de simulación de opciones no contiene períodos.")

    ruta = Path(ruta_salida)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    doc = BaseDocTemplate(
        str(ruta),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
        title="HedgePoint MX — Simulador de Opciones Put USD/MXN",
        author="HedgePoint MX",
        subject="Simulación de cobertura con opciones put USD/MXN",
    )

    plantillas = _crear_plantillas(doc)
    doc.addPageTemplates(plantillas)

    estilos = _estilos()

    story = []

    # 1 — Portada
    story.append(NextPageTemplate("portada"))
    story.extend(_portada_opciones(resultado, estilos))

    # 2 — Resumen ejecutivo
    story.append(NextPageTemplate("interior"))
    story.append(PageBreak())
    story.extend(_resumen_ejecutivo_opciones(resultado, estilos))

    # 3 — Gráfica resultado mensual / acumulado
    story.append(PageBreak())
    story.append(Paragraph(
        "Análisis de Resultado vs Spot / Performance vs Spot",
        estilos["encabezado_seccion"],
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    story.append(_grafica_opciones_resultado(resultado))

    # 4 — Gráfica de volatilidad y prima
    story.append(PageBreak())
    story.append(Paragraph(
        "Volatilidad Histórica y Prima del Banco / Historical Vol & Bank Premium",
        estilos["encabezado_seccion"],
    ))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    story.append(_grafica_prima_y_vol(resultado))
    story.append(Spacer(1, 0.3 * cm))
    nota_vol = (
        "La volatilidad histórica de 30 días hábiles se usa como proxy de la volatilidad "
        "implícita para el pricing con Garman-Kohlhagen. En mercado real, la volatilidad "
        "implícita puede diferir — normalmente es mayor (volatility risk premium). "
        "/ <i>30-day historical volatility is used as an implied vol proxy for "
        "Garman-Kohlhagen pricing. Real implied vol typically exceeds historical vol "
        "(volatility risk premium).</i>"
    )
    story.append(Paragraph(nota_vol, ParagraphStyle(
        "nota_vol", fontName="Helvetica", fontSize=7.5, textColor=GRIS,
        alignment=TA_JUSTIFY, leading=11, spaceAfter=4,
    )))

    # 5 — Desglose de costos
    story.append(PageBreak())
    story.extend(_seccion_desglose_costos_opciones(resultado, estilos))

    # 6 — Tabla mensual
    story.append(PageBreak())
    story.extend(_tabla_mensual_opciones(resultado, estilos))

    # 7 — Recomendación
    story.append(PageBreak())
    story.extend(_seccion_recomendacion_opciones(resultado, estilos))

    doc.build(story)
    logger.info("PDF de opciones generado: %s", ruta)
    return ruta
