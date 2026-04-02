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

from agents.simulator.savings_simulator import ResultadoSimulacion, ResultadoMultiPlazo

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

    # --- Panel superior: ahorro mensual en barras ---
    colores_barras = ["#2d8659" if v >= 0 else "#c0392b"
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
    ax1.set_ylabel("Ahorro mensual (miles MXN)", fontsize=10, color="#374151")
    ax1.set_title("Ahorro mensual: Forward vs Spot",
                  fontsize=12, fontweight="bold", color="#1a365d")
    ax1.grid(True, alpha=0.3, linestyle="--")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    # --- Panel inferior: ahorro acumulado ---
    ax2.set_facecolor("#f9fafb")
    ahorro_acum = df_periodos["ahorro_acumulado_mxn"] / 1000
    positivo = ahorro_acum >= 0
    ax2.fill_between(range(n), 0, ahorro_acum,
                     where=positivo, alpha=0.25, color="#2d8659")
    ax2.fill_between(range(n), 0, ahorro_acum,
                     where=~positivo, alpha=0.25, color="#c0392b")
    ax2.plot(range(n), ahorro_acum,
             color="#1a365d", linewidth=2, marker="o", markersize=4)
    ax2.axhline(0, color="#374151", linewidth=0.8, linestyle="--")
    ax2.set_xticks(tick_indices)
    ax2.set_xticklabels(tick_labels, rotation=45, ha="right", fontsize=8)
    ax2.tick_params(axis="y", labelsize=8)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:.0f}k"))
    ax2.set_ylabel("Ahorro acumulado (miles MXN)", fontsize=10, color="#374151")
    ax2.set_title("Ahorro acumulado total",
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

    # Fecha de generación
    elementos.append(Paragraph(
        f"Generado el {date.today().strftime('%d de %B de %Y')}",
        estilos["etiqueta_portada"],
    ))
    elementos.append(Paragraph(
        "Confidencial — Solo para uso interno y presentación a prospectos",
        estilos["etiqueta_portada"],
    ))

    return elementos


def _kpi_box(numero: str, etiqueta: str, es_negativo: bool = False) -> Table:
    """Genera un cuadro KPI individual."""
    color_num = ROJO if es_negativo else VERDE
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

    # Texto introductorio — lógica diferenciada según signo del ahorro
    if r.ahorro_total_mxn >= 0:
        texto_es = (
            f"Este análisis simula el comportamiento de una estrategia de cobertura "
            f"mediante contratos forward a 30 días sobre el par USD/MXN, "
            f"aplicada a las compras de divisas de su empresa durante el período "
            f"<b>{r.fecha_inicio}</b> al <b>{r.fecha_fin}</b>. "
            f"Con un volumen mensual de <b>USD ${r.parametros.volumen_mensual_usd:,.0f}</b>, "
            f"la estrategia de cobertura habría generado un ahorro total de "
            f"<b>${r.ahorro_total_mxn:,.0f} MXN</b> "
            f"(<b>{r.ahorro_total_porcentaje:.2f}%</b> del costo total), "
            f"demostrando el valor de proteger su exposición cambiaria."
        )
        texto_en = (
            f"This analysis simulates the performance of a 30-day forward hedging strategy "
            f"on the USD/MXN currency pair applied to your company's FX purchases "
            f"from <b>{r.fecha_inicio}</b> to <b>{r.fecha_fin}</b>. "
            f"With a monthly volume of <b>USD ${r.parametros.volumen_mensual_usd:,.0f}</b>, "
            f"the hedging strategy would have generated total savings of "
            f"<b>${r.ahorro_total_mxn:,.0f} MXN</b> "
            f"(<b>{r.ahorro_total_porcentaje:.2f}%</b> of total unhedged cost), "
            f"demonstrating the value of protecting your FX exposure."
        )
    else:
        mejor_mes_str = r.mejor_mes.periodo if r.mejor_mes else "N/D"
        peor_costo_str = (
            f"${abs(r.peor_mes.ahorro_mxn):,.0f}" if r.peor_mes else "N/D"
        )
        texto_es = (
            f"Este análisis simula el comportamiento de una estrategia de cobertura "
            f"mediante contratos forward a 30 días sobre el par USD/MXN, "
            f"aplicada a las compras de divisas de su empresa durante el período "
            f"<b>{r.fecha_inicio}</b> al <b>{r.fecha_fin}</b>. "
            f"Con un volumen mensual de <b>USD ${r.parametros.volumen_mensual_usd:,.0f}</b>, "
            f"la estrategia de cobertura habría representado un costo adicional de "
            f"<b>${abs(r.ahorro_total_mxn):,.0f} MXN</b> "
            f"(<b>{abs(r.ahorro_total_porcentaje):.2f}%</b> del costo total). "
            f"Sin embargo, habría eliminado la exposición a movimientos adversos — "
            f"en <b>{mejor_mes_str}</b>, un solo mes sin cobertura habría costado "
            f"<b>{peor_costo_str} MXN</b> adicionales. "
            f"La cobertura es un seguro: su valor está en la certeza presupuestal, "
            f"no en ganarle al mercado."
        )
        texto_en = (
            f"This analysis simulates the performance of a 30-day forward hedging strategy "
            f"on the USD/MXN currency pair applied to your company's FX purchases "
            f"from <b>{r.fecha_inicio}</b> to <b>{r.fecha_fin}</b>. "
            f"With a monthly volume of <b>USD ${r.parametros.volumen_mensual_usd:,.0f}</b>, "
            f"the hedging strategy would have represented an additional cost of "
            f"<b>${abs(r.ahorro_total_mxn):,.0f} MXN</b> "
            f"(<b>{abs(r.ahorro_total_porcentaje):.2f}%</b> of total cost). "
            f"However, it would have eliminated exposure to adverse FX moves — "
            f"in <b>{mejor_mes_str}</b>, a single unhedged month would have cost "
            f"<b>{peor_costo_str} MXN</b> extra. "
            f"Hedging is insurance: its value lies in budget certainty, "
            f"not in beating the market."
        )
    elementos.append(Paragraph(texto_es, estilos["cuerpo"]))
    elementos.append(Paragraph(texto_en, ParagraphStyle(
        "cuerpo_en", fontName="Helvetica-Oblique", fontSize=8.5,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=12, spaceAfter=10,
    )))

    # KPIs — fila 1: resultados de la cobertura
    kpis_fila1 = [
        (f"${r.ahorro_total_mxn:,.0f}", "Ahorro total MXN\nTotal Savings MXN",
         r.ahorro_total_mxn < 0),
        (f"${r.ahorro_promedio_mensual_mxn:,.0f}", "Ahorro promedio mensual\nAvg Monthly Savings",
         r.ahorro_promedio_mensual_mxn < 0),
        (f"{r.ahorro_total_porcentaje:.2f}%", "Ahorro sobre costo total\nSavings on Total Cost",
         r.ahorro_total_porcentaje < 0),
        (f"{r.porcentaje_meses_con_ahorro:.0f}%", "Meses con ahorro\nMonths with Savings",
         r.porcentaje_meses_con_ahorro < 50),
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
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#fdecea")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        elementos.append(t_extremos)

    return elementos


def _tabla_mensual(resultado: ResultadoSimulacion, estilos: dict) -> list:
    """Genera la tabla detallada mes a mes."""
    elementos = []
    elementos.append(Paragraph("Análisis Mensual / Monthly Breakdown",
                               estilos["encabezado_seccion"]))
    elementos.append(HRFlowable(width="100%", thickness=1.5, color=AZUL,
                                spaceAfter=6))

    encabezados = [
        "Mes\nMonth",
        "TC Spot\nSpot Rate",
        "TC Fwd\nFwd Rate",
        "Costo Spot\n(MXN)",
        "Fwd Teórico\n(MXN)",
        "Spread Banco\n(MXN)",
        "Markup HP\n(MXN)",
        "Fee HP\n(MXN)",
        "Costo Fwd\nTotal (MXN)",
        "Ahorro\n(MXN)",
        "Ahorro\n(%)",
    ]
    filas = [encabezados]

    r = resultado
    for p in r.periodos:
        filas.append([
            p.periodo,
            f"{p.spot:.4f}",
            f"{p.forward_30d:.4f}",
            f"${p.costo_spot_mxn:,.0f}",
            f"${p.costo_forward_teorico_mxn:,.0f}",
            f"${p.costo_spread_banco_mxn:,.0f}",
            f"${p.costo_markup_hp_mxn:,.0f}",
            f"${p.costo_fee_hp_mxn:,.0f}",
            f"${p.costo_forward_mxn:,.0f}",
            f"${p.ahorro_mxn:,.0f}",
            f"{p.ahorro_porcentaje:.2f}%",
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
        Paragraph(f"<b>{r.ahorro_total_porcentaje:.2f}%</b>", estilos["tabla_celda"]),
    ])

    col_widths = [1.6 * cm, 1.7 * cm, 1.7 * cm, 2.4 * cm,
                  2.4 * cm, 2.2 * cm, 2.0 * cm, 2.0 * cm,
                  2.4 * cm, 2.4 * cm, 1.6 * cm]
    t = Table(filas, colWidths=col_widths, repeatRows=1)

    # Estilos base
    estilo_tabla = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("TEXTCOLOR", (0, 0), (-1, 0), BLANCO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.0),
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

    # Colorear celdas de ahorro (columnas 9 y 10)
    for i, p in enumerate(r.periodos, start=1):
        col_ahorro = 9
        col_pct = 10
        if p.ahorro_mxn > 0:
            estilo_tabla.append(("TEXTCOLOR", (col_ahorro, i), (col_pct, i), VERDE))
        else:
            estilo_tabla.append(("TEXTCOLOR", (col_ahorro, i), (col_pct, i), ROJO))

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
    # Colorear filas de ahorro según positivo/negativo
    for i, r in enumerate(resultados_ord, start=1):
        color = VERDE if r.ahorro_total_mxn >= 0 else ROJO
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

    caja_rec = Table(
        [[Paragraph(rec_es, estilos["recomendacion"])],
         [Paragraph(rec_en, ParagraphStyle(
             "rec_en2", fontName="Helvetica-Oblique", fontSize=8.5,
             textColor=GRIS, alignment=TA_JUSTIFY, leading=12,
         ))]],
        colWidths=[16 * cm],
    )
    caja_rec.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), VERDE_CLARO),
        ("BOX", (0, 0), (-1, -1), 1.5, VERDE),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    elementos.append(caja_rec)

    return elementos


# ---------------------------------------------------------------------------
# Función principal de generación
# ---------------------------------------------------------------------------

def generar_pdf(
    resultado: ResultadoSimulacion,
    ruta_salida: str | Path = "output/reporte_simulacion.pdf",
    multi_plazo: ResultadoMultiPlazo | None = None,
) -> Path:
    """
    Genera el PDF profesional de simulación de ahorro.

    Args:
        resultado: Resultado de la simulación principal (objeto ResultadoSimulacion).
        ruta_salida: Ruta del archivo PDF de salida.
        multi_plazo: Resultados multi-plazo (30/60/90d). Si se pasa, se agregan
                     las secciones de desglose de costos y comparativa de plazos.

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

    # Resumen ejecutivo
    story.extend(_resumen_ejecutivo(resultado, estilos))
    story.append(Spacer(1, 0.4 * cm))

    # Desglose de costos (solo si hay costos transaccionales configurados)
    p = resultado.parametros
    hay_costos = p.spread_banco > 0 or p.markup_hedgepoint > 0 or p.fee_mensual > 0
    if hay_costos:
        story.append(PageBreak())
        story.extend(_seccion_desglose_costos(resultado, estilos))

    # Gráfica de TC histórico
    story.append(PageBreak())
    story.append(Paragraph("Tipo de Cambio Histórico / Historical FX Rate",
                            estilos["encabezado_seccion"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    if not df_fx.empty:
        story.append(_grafica_tc_historico(df_fx, resultado.periodos))
    story.append(Spacer(1, 0.3 * cm))

    # Gráfica de ahorro acumulado (plazo principal)
    story.append(Paragraph("Análisis de Ahorro / Savings Analysis",
                            estilos["encabezado_seccion"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=AZUL, spaceAfter=6))
    story.append(_grafica_ahorro_acumulado(df_periodos))

    # Comparativa multi-plazo (si se proveyó)
    if multi_plazo is not None:
        story.append(PageBreak())
        story.extend(_seccion_comparativa_plazos(multi_plazo, estilos))

    # Tabla mensual (en nueva página)
    story.append(PageBreak())
    story.extend(_tabla_mensual(resultado, estilos))

    # Recomendación
    story.append(PageBreak())
    story.extend(_seccion_recomendacion(resultado, estilos))

    doc.build(story)
    logger.info("PDF generado: %s", ruta)
    return ruta
