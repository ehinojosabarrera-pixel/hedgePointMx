"""
PDF diagnostic report generator for HedgePoint MX prospect onboarding.

Produces a 5-page confidential diagnostic document from the dict returned by
DiagnosticOrchestrator.run_full_diagnostic().

Usage:
    from agents.onboarding.pdf_diagnostic import generar_pdf_diagnostico

    path = generar_pdf_diagnostico(diagnostic_result)
    # Returns the path of the generated PDF.
"""

from __future__ import annotations

import io
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
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

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Brand palette  (copied — no imports from agents/simulator/)
# ---------------------------------------------------------------------------

AZUL        = colors.HexColor("#1a365d")
AZUL_MEDIO  = colors.HexColor("#2a4f82")
AZUL_CLARO  = colors.HexColor("#e8eef7")
VERDE       = colors.HexColor("#2d8659")
VERDE_CLARO = colors.HexColor("#e6f4ed")
GRIS        = colors.HexColor("#6b7280")
GRIS_CLARO  = colors.HexColor("#f3f4f6")
ROJO        = colors.HexColor("#c0392b")
BLANCO      = colors.white

PAGE_W, PAGE_H = A4     # 595 × 842 pt  (≈ 210 × 297 mm)

CONTACTO_EMAIL = "contacto@hedgepointmx.com"
CONTACTO_WEB   = "www.hedgepointmx.com"
CONTACTO_WA    = "+52 (993) 170-1758"


# ---------------------------------------------------------------------------
# Typography styles
# ---------------------------------------------------------------------------

def _estilos() -> dict:
    s = {}

    # Cover page
    s["titulo_portada"] = ParagraphStyle(
        "titulo_portada", fontName="Helvetica-Bold", fontSize=30,
        textColor=BLANCO, alignment=TA_CENTER, leading=36, spaceAfter=6,
    )
    s["subtitulo_portada"] = ParagraphStyle(
        "subtitulo_portada", fontName="Helvetica", fontSize=15,
        textColor=colors.HexColor("#b8d4f0"), alignment=TA_CENTER, leading=20,
    )
    s["empresa_portada"] = ParagraphStyle(
        "empresa_portada", fontName="Helvetica-Bold", fontSize=18,
        textColor=BLANCO, alignment=TA_CENTER, leading=22, spaceBefore=18,
    )
    s["etiqueta_portada"] = ParagraphStyle(
        "etiqueta_portada", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#90adc9"), alignment=TA_CENTER,
    )
    s["confidencial"] = ParagraphStyle(
        "confidencial", fontName="Helvetica-Bold", fontSize=8,
        textColor=colors.HexColor("#90adc9"), alignment=TA_CENTER, spaceBefore=6,
    )

    # Interior headings
    s["seccion"] = ParagraphStyle(
        "seccion", fontName="Helvetica-Bold", fontSize=13,
        textColor=AZUL, spaceBefore=14, spaceAfter=4, leading=16,
    )
    s["sub_seccion"] = ParagraphStyle(
        "sub_seccion", fontName="Helvetica-Bold", fontSize=10,
        textColor=AZUL_MEDIO, spaceBefore=8, spaceAfter=2,
    )

    # Body text
    s["cuerpo"] = ParagraphStyle(
        "cuerpo", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#374151"),
        alignment=TA_JUSTIFY, leading=13, spaceAfter=4,
    )
    s["cuerpo_left"] = ParagraphStyle(
        "cuerpo_left", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#374151"), leading=13, spaceAfter=4,
    )

    # Tables
    s["th"] = ParagraphStyle(
        "th", fontName="Helvetica-Bold", fontSize=8,
        textColor=BLANCO, alignment=TA_CENTER,
    )
    s["td"] = ParagraphStyle(
        "td", fontName="Helvetica", fontSize=8,
        textColor=colors.HexColor("#374151"), alignment=TA_CENTER,
    )
    s["td_left"] = ParagraphStyle(
        "td_left", fontName="Helvetica", fontSize=8,
        textColor=colors.HexColor("#374151"), alignment=TA_LEFT,
    )
    s["td_bold"] = ParagraphStyle(
        "td_bold", fontName="Helvetica-Bold", fontSize=8,
        textColor=colors.HexColor("#374151"), alignment=TA_CENTER,
    )

    # Alert / recommendation box
    s["alerta"] = ParagraphStyle(
        "alerta", fontName="Helvetica-Bold", fontSize=10,
        textColor=ROJO, alignment=TA_CENTER, spaceBefore=6, spaceAfter=6,
    )
    s["recomendacion"] = ParagraphStyle(
        "recomendacion", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#1a4731"),
        alignment=TA_JUSTIFY, leading=13,
    )
    s["cta"] = ParagraphStyle(
        "cta", fontName="Helvetica-Bold", fontSize=10,
        textColor=AZUL, alignment=TA_CENTER, spaceBefore=6, spaceAfter=4,
    )
    s["cta_sub"] = ParagraphStyle(
        "cta_sub", fontName="Helvetica", fontSize=9,
        textColor=GRIS, alignment=TA_CENTER, leading=14,
    )

    return s


# ---------------------------------------------------------------------------
# Page templates
# ---------------------------------------------------------------------------

def _fondo_portada(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(AZUL)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=True, stroke=False)
    # Accent stripe at bottom
    canvas.setFillColor(VERDE)
    canvas.rect(0, 0, PAGE_W, 3.5 * cm, fill=True, stroke=False)
    canvas.setFillColor(AZUL_MEDIO)
    canvas.rect(0, 3.5 * cm, PAGE_W, 0.4 * cm, fill=True, stroke=False)
    canvas.restoreState()


def _encabezado_pie(canvas, doc) -> None:
    canvas.saveState()
    # Header bar
    canvas.setFillColor(AZUL)
    canvas.rect(1.8 * cm, PAGE_H - 1.8 * cm, PAGE_W - 3.6 * cm, 0.9 * cm,
                fill=True, stroke=False)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(BLANCO)
    canvas.drawString(2.2 * cm, PAGE_H - 1.35 * cm, "HedgePoint MX")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - 2.2 * cm, PAGE_H - 1.35 * cm,
                           "Diagnóstico Confidencial")
    # Footer rule
    canvas.setStrokeColor(AZUL_CLARO)
    canvas.setLineWidth(0.5)
    canvas.line(1.8 * cm, 1.5 * cm, PAGE_W - 1.8 * cm, 1.5 * cm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GRIS)
    canvas.drawString(1.8 * cm, 1.1 * cm,
                      "HedgePoint MX — Gestión de Riesgos Financieros para PyMEs")
    canvas.drawRightString(PAGE_W - 1.8 * cm, 1.1 * cm, f"Página {doc.page}")
    canvas.restoreState()


def _crear_plantillas(doc: BaseDocTemplate) -> list[PageTemplate]:
    margen = 1.8 * cm

    frame_portada = Frame(0, 0, PAGE_W, PAGE_H,
                          leftPadding=0, rightPadding=0,
                          topPadding=0, bottomPadding=0)
    pt_portada = PageTemplate(id="portada", frames=[frame_portada],
                              onPage=_fondo_portada)

    frame_interior = Frame(
        margen, margen + 1.2 * cm,
        PAGE_W - 2 * margen, PAGE_H - 2 * margen - 2 * cm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    pt_interior = PageTemplate(id="interior", frames=[frame_interior],
                               onPage=_encabezado_pie)

    return [pt_portada, pt_interior]


# ---------------------------------------------------------------------------
# KPI box  (local copy — no import from agents/simulator/)
# ---------------------------------------------------------------------------

def _kpi_box(numero: str, etiqueta: str, es_negativo: bool = False) -> Table:
    """Single KPI tile with a number and a label."""
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
        ("BACKGROUND",    (0, 0), (-1, -1), AZUL_CLARO),
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#c5d4e8")),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    return t


def _fila_kpis(*kpis) -> Table:
    """Wrap several KPI tiles in a single horizontal row."""
    gap = 0.35 * cm
    n   = len(kpis)
    available = PAGE_W - 2 * 1.8 * cm
    col_w = (available - gap * (n - 1)) / n

    # Re-create boxes with the correct width
    cells = []
    for numero, etiqueta, es_neg in kpis:
        color_num = GRIS if es_neg else VERDE
        sn = ParagraphStyle("_kn", fontName="Helvetica-Bold", fontSize=16,
                            textColor=color_num, alignment=TA_CENTER, leading=20)
        sl = ParagraphStyle("_kl", fontName="Helvetica", fontSize=7.5,
                            textColor=GRIS, alignment=TA_CENTER, leading=10)
        t = Table([[Paragraph(numero, sn)], [Paragraph(etiqueta, sl)]],
                  colWidths=[col_w])
        t.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), AZUL_CLARO),
            ("BOX",           (0, 0), (-1, -1), 0.5, colors.HexColor("#c5d4e8")),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ]))
        cells.append(t)

    row = Table([cells], colWidths=[col_w] * n, hAlign="CENTER")
    row.setStyle(TableStyle([
        ("LEFTPADDING",   (0, 0), (-1, -1), gap / 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), gap / 2),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    return row


# ---------------------------------------------------------------------------
# Matplotlib helpers
# ---------------------------------------------------------------------------

def _imagen_desde_figura(fig: plt.Figure, width_cm: float = 15.0,
                         max_height_cm: float = 10.0) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)

    width_pt      = width_cm * cm
    max_height_pt = max_height_cm * cm

    img_tmp = Image(buf)
    aspect  = (img_tmp.imageHeight / img_tmp.imageWidth
               if img_tmp.imageWidth > 0 else 1.0)
    height_pt = min(width_pt * aspect, max_height_pt)

    buf.seek(0)
    img = Image(buf, width=width_pt, height=height_pt)
    img.hAlign = "CENTER"
    return img


def _grafica_riesgo(exposure: dict) -> Image:
    """Horizontal bar chart: potential losses at 5 / 10 / 15% FX move."""
    margen_mxn = exposure["exposicion_anual_mxn"] * (
        # We don't have margen_utilidad in exposure — show a reference line at 10%
        0.10
    )
    valores = [
        exposure["perdida_potencial_5pct"],
        exposure["perdida_potencial_10pct"],
        exposure["perdida_potencial_15pct"],
    ]
    etiquetas = [
        "Depreciación\ndel peso 5%",
        "Depreciación\ndel peso 10%",
        "Depreciación\ndel peso 15%",
    ]
    colores_barra = ["#f0a500", "#e07b00", "#c0392b"]

    fig, ax = plt.subplots(figsize=(10, 3.6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f9fafb")

    bars = ax.barh(etiquetas, valores, color=colores_barra,
                   height=0.5, edgecolor="none")

    # Value labels inside bars
    for bar, val in zip(bars, valores):
        ax.text(bar.get_width() * 0.97, bar.get_y() + bar.get_height() / 2,
                f"${val:,.0f}", va="center", ha="right",
                fontsize=8, color="white", fontweight="bold")

    # Reference line at 10% of exposure (proxy for margin)
    ref = exposure["exposicion_anual_mxn"] * 0.10
    ax.axvline(ref, color="#2d8659", linewidth=1.5, linestyle="--", alpha=0.8,
               label="Pérdida 10% exposición")

    ax.xaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"${x/1_000_000:.1f}M")
    )
    ax.set_xlabel("Pérdida potencial (MXN)", fontsize=8, color="#6b7280")
    ax.tick_params(axis="both", labelsize=8, colors="#6b7280")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#e5e7eb")
    ax.set_title("Pérdida potencial según movimiento del tipo de cambio",
                 fontsize=9, color="#1a365d", fontweight="bold", pad=10)
    ax.legend(fontsize=7.5, framealpha=0)

    plt.tight_layout()
    return _imagen_desde_figura(fig, width_cm=15.5, max_height_cm=9.0)


# ---------------------------------------------------------------------------
# Table style helper
# ---------------------------------------------------------------------------

def _ts_base(header_rows: int = 1) -> list:
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, header_rows - 1), AZUL),
        ("TEXTCOLOR",     (0, 0), (-1, header_rows - 1), BLANCO),
        ("FONTNAME",      (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, header_rows - 1), 8),
        ("ALIGN",         (0, 0), (-1, header_rows - 1), "CENTER"),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1),
         [colors.white, AZUL_CLARO]),
        ("FONTNAME",      (0, header_rows), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, header_rows), (-1, -1), 8),
        ("ALIGN",         (0, header_rows), (-1, -1), "CENTER"),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]
    return cmds


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _pagina_portada(empresa: str, fecha_str: str, st: dict) -> list:
    """Cover page content (runs inside a frame with no padding)."""
    story = []
    # Vertical spacer to push content to the visual center of the blue area
    story.append(Spacer(1, 7.5 * cm))
    story.append(Paragraph("HedgePoint MX", st["titulo_portada"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Diagnóstico de Exposición Cambiaria",
                            st["subtitulo_portada"]))
    story.append(Spacer(1, 1.2 * cm))
    story.append(HRFlowable(width="60%", thickness=0.5,
                             color=colors.HexColor("#4a7ab5"),
                             hAlign="CENTER"))
    story.append(Spacer(1, 1.0 * cm))
    story.append(Paragraph(empresa, st["empresa_portada"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(f"Generado el {fecha_str}", st["etiqueta_portada"]))
    story.append(Spacer(1, 5.5 * cm))
    story.append(Paragraph("DOCUMENTO CONFIDENCIAL", st["confidencial"]))
    return story


def _pagina_resumen(prospect: dict, exposure: dict, st: dict) -> list:
    story = []

    story.append(Paragraph("Resumen Ejecutivo", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.4 * cm))

    # --- 4 KPI tiles ---
    exp_usd  = exposure["exposicion_anual_usd"]
    exp_mxn  = exposure["exposicion_anual_mxn"]
    p10      = exposure["perdida_potencial_10pct"]
    costo_fw = exposure["costo_estimado_forward_mensual"]

    row = _fila_kpis(
        (f"${exp_usd/1_000_000:.1f}M", "Exposición anual USD",         False),
        (f"${exp_mxn/1_000_000:.1f}M", "Exposición anual MXN",         False),
        (f"${p10/1_000_000:.1f}M",     "Pérdida potencial 10%",        True),
        (f"${costo_fw:,.0f}",          "Costo mensual est. cobertura", True),
    )
    story.append(row)
    story.append(Spacer(1, 0.5 * cm))

    # --- Margen en riesgo alert ---
    if exposure.get("margen_en_riesgo"):
        story.append(Paragraph(
            "⚠ Su margen de utilidad está en riesgo: una depreciación del 10% "
            "superaría su margen de ganancia.", st["alerta"]))
        story.append(Spacer(1, 0.2 * cm))

    # --- Prospect profile table ---
    story.append(Paragraph("Perfil del prospecto", st["sub_seccion"]))

    margen_pct = prospect.get("margen_utilidad", 0) * 100
    coberturas = "Sí" if prospect.get("usa_coberturas") else "No"
    freq       = str(prospect.get("frecuencia_compra", "")).capitalize()
    plazo      = prospect.get("plazo_pago_dias", 30)

    data_perfil = [
        ["Campo", "Valor"],
        ["Sector",                  prospect.get("sector", "—")],
        ["Volumen mensual USD",     f"${prospect.get('volumen_usd_mensual', 0):,.0f}"],
        ["Frecuencia de compra",    freq],
        ["Plazo de pago",           f"{plazo} días"],
        ["Margen de utilidad",      f"{margen_pct:.1f}%"],
        ["Ha usado coberturas",     coberturas],
        ["TC utilizado",            f"${exposure.get('tipo_cambio_usado', 0):.4f}"],
    ]
    col_w = [(PAGE_W - 3.6 * cm) * p for p in (0.55, 0.45)]
    t = Table(data_perfil, colWidths=col_w)
    cmds = _ts_base(1)
    cmds += [
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 1), (0, -1), AZUL_MEDIO),
    ]
    t.setStyle(TableStyle(cmds))
    story.append(t)

    return story


def _pagina_riesgo(exposure: dict, market_context: str, st: dict) -> list:
    story = []

    story.append(Paragraph("Análisis de Riesgo Cambiario", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))

    # Bar chart
    story.append(_grafica_riesgo(exposure))
    story.append(Spacer(1, 0.5 * cm))

    # Scenarios table
    story.append(Paragraph("Tabla de escenarios", st["sub_seccion"]))
    tc      = exposure.get("tipo_cambio_usado", 17.5)
    exp_usd = exposure["exposicion_anual_usd"]

    escenarios = [
        ("5%",  tc * 1.05, exposure["perdida_potencial_5pct"]),
        ("10%", tc * 1.10, exposure["perdida_potencial_10pct"]),
        ("15%", tc * 1.15, exposure["perdida_potencial_15pct"]),
    ]

    header = ["Si el dólar sube…", "TC resultante", "Pérdida estimada (MXN)",
              "Impacto sobre ingreso anual"]
    rows   = [header]
    for pct, tc_esc, perdida in escenarios:
        impacto = perdida / (exp_usd * tc) * 100 if tc > 0 else 0
        rows.append([
            pct,
            f"${tc_esc:.4f}",
            f"${perdida:,.0f}",
            f"{impacto:.1f}%",
        ])

    col_w = [(PAGE_W - 3.6 * cm) * p for p in (0.2, 0.22, 0.32, 0.26)]
    t = Table(rows, colWidths=col_w)
    cmds = _ts_base(1)
    cmds.append(("FONTNAME", (2, 1), (2, -1), "Helvetica-Bold"))
    cmds.append(("TEXTCOLOR", (2, 1), (2, -1), ROJO))
    t.setStyle(TableStyle(cmds))
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))

    # Market context
    if market_context and "no disponible" not in market_context.lower():
        story.append(Paragraph("Contexto de mercado", st["sub_seccion"]))
        for para in _split_paragraphs(market_context):
            story.append(Paragraph(para, st["cuerpo"]))

    return story


def _pagina_diagnostico(insights: str, st: dict) -> list:
    story = []

    story.append(Paragraph("Diagnóstico y Recomendación", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))

    # Split the LLM text into labeled sections
    sections = _parse_insights(insights)

    for title, body in sections:
        is_next_step = "siguiente" in title.lower() or "paso" in title.lower()

        if title:
            story.append(Paragraph(title, st["sub_seccion"]))

        if is_next_step and body:
            # Highlight the next-step block in a green box
            inner = ParagraphStyle(
                "_ns", fontName="Helvetica", fontSize=9,
                textColor=colors.HexColor("#1a4731"),
                alignment=TA_JUSTIFY, leading=13,
            )
            cell = Table(
                [[Paragraph(body, inner)]],
                colWidths=[PAGE_W - 3.6 * cm],
            )
            cell.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), VERDE_CLARO),
                ("BOX",           (0, 0), (-1, -1), 0.5, VERDE),
                ("TOPPADDING",    (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING",   (0, 0), (-1, -1), 12),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
            ]))
            story.append(cell)
        else:
            for para in _split_paragraphs(body):
                story.append(Paragraph(para, st["cuerpo"]))

        story.append(Spacer(1, 0.2 * cm))

    return story


def _pagina_cta(exposure: dict, prospect: dict, st: dict) -> list:
    story = []

    story.append(Paragraph("Costo de No Cubrirse", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))

    costo_fw  = exposure["costo_estimado_forward_mensual"]
    costo_anual = costo_fw * 12
    p10       = exposure["perdida_potencial_10pct"]
    p15       = exposure["perdida_potencial_15pct"]
    exp_mxn   = exposure["exposicion_anual_mxn"]

    # Cost comparison table
    story.append(Paragraph("Cobertura vs. exposición sin protección", st["sub_seccion"]))

    rows_cmp = [
        ["", "Sin cobertura", "Con cobertura forward"],
        ["Pérdida potencial (10% mov.)",
         f"${p10:,.0f} MXN",
         "Fija antes del movimiento"],
        ["Pérdida potencial (15% mov.)",
         f"${p15:,.0f} MXN",
         "Fija antes del movimiento"],
        ["Costo mensual estimado",
         "Variable (impredecible)",
         f"${costo_fw:,.0f} MXN"],
        ["Costo anual estimado",
         "Variable (impredecible)",
         f"${costo_anual:,.0f} MXN"],
        ["Certeza en presupuesto",
         "No",
         "Sí"],
    ]

    col_w = [(PAGE_W - 3.6 * cm) * p for p in (0.40, 0.30, 0.30)]
    t = Table(rows_cmp, colWidths=col_w)
    cmds = _ts_base(1)
    cmds += [
        ("ALIGN",    (0, 1), (0, -1), "LEFT"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (1, 1), (1, -1), ROJO),
        ("TEXTCOLOR", (2, 1), (2, -1), VERDE),
    ]
    t.setStyle(TableStyle(cmds))
    story.append(t)
    story.append(Spacer(1, 0.6 * cm))

    # Value proposition message
    story.append(KeepTogether([
        Paragraph(
            f"Por <b>${costo_fw:,.0f} MXN/mes</b> protege "
            f"<b>${exp_mxn/1_000_000:.1f}M MXN</b> de exposición cambiaria.",
            st["cta"],
        ),
        Spacer(1, 0.4 * cm),
        HRFlowable(width="80%", thickness=0.5, color=AZUL_CLARO, hAlign="CENTER"),
        Spacer(1, 0.4 * cm),
        Paragraph("¿Listo para proteger su negocio?", st["cta"]),
        Spacer(1, 0.2 * cm),
        Paragraph(
            f"Contáctenos hoy para diseñar su estrategia de cobertura personalizada.<br/>"
            f"<b>Email:</b> {CONTACTO_EMAIL} &nbsp;|&nbsp; "
            f"<b>WhatsApp:</b> {CONTACTO_WA} &nbsp;|&nbsp; "
            f"<b>Web:</b> {CONTACTO_WEB}",
            st["cta_sub"],
        ),
    ]))

    return story


# ---------------------------------------------------------------------------
# LLM text parsing helpers
# ---------------------------------------------------------------------------

_RE_SECTION = re.compile(
    r"^\s*(?:\d+[\.\)]\s*)?([A-ZÁÉÍÓÚ][A-ZÁÉÍÓÚ\s/\-]+):\s*$",
    re.MULTILINE,
)


def _parse_insights(text: str) -> list[tuple[str, str]]:
    """
    Split the LLM insights text into (title, body) pairs.

    Detects lines like "1. DIAGNÓSTICO DE SITUACIÓN:" as section headers.
    If no structure is found, returns the whole text as a single body.
    """
    # Try to detect numbered or ALL-CAPS section headers
    pattern = re.compile(
        r"(?:^|\n)\s*(?:\d+[\.\)]\s*)?([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑA-Za-záéíóúñ\s/\-]{3,50}):",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))

    if not matches:
        return [("", text.strip())]

    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip().title()
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body  = text[start:end].strip()
        if body:
            sections.append((title, body))

    return sections if sections else [("", text.strip())]


def _split_paragraphs(text: str) -> list[str]:
    """Split text on blank lines, return non-empty paragraphs."""
    paras = re.split(r"\n\s*\n", text.strip())
    return [p.replace("\n", " ").strip() for p in paras if p.strip()]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generar_pdf_diagnostico(
    diagnostic_result: dict,
    output_path: Optional[str] = None,
) -> str:
    """Generate a 5-page PDF diagnostic report and return its path.

    Parameters
    ----------
    diagnostic_result : dict
        The dict returned by ``DiagnosticOrchestrator.run_full_diagnostic()``.
        Required keys: ``prospect_data``, ``exposure``, ``insights``,
        ``market_context``.
    output_path : str, optional
        Destination file path.  If ``None``, the file is saved to
        ``output/diagnostico_{empresa}_{YYYY-MM-DD}.pdf``.

    Returns
    -------
    str
        Absolute path of the generated PDF.
    """
    prospect      = diagnostic_result.get("prospect_data", {})
    exposure      = diagnostic_result.get("exposure", {})
    insights      = diagnostic_result.get("insights", "")
    market_context = diagnostic_result.get("market_context", "")

    empresa = prospect.get("empresa", "prospecto")
    # Sanitise company name for use in filename
    empresa_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", empresa)[:40]
    fecha_str    = date.today().strftime("%Y-%m-%d")

    if output_path is None:
        out_dir = Path(__file__).parent.parent.parent / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"diagnostico_{empresa_slug}_{fecha_str}.pdf")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    st = _estilos()

    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=0,
        rightMargin=0,
        topMargin=0,
        bottomMargin=0,
    )
    doc.addPageTemplates(_crear_plantillas(doc))

    story: list = []

    # Page 1 — Cover
    story += _pagina_portada(empresa, fecha_str, st)
    story.append(NextPageTemplate("interior"))
    story.append(PageBreak())

    # Page 2 — Executive summary
    story += _pagina_resumen(prospect, exposure, st)
    story.append(PageBreak())

    # Page 3 — Risk analysis
    story += _pagina_riesgo(exposure, market_context, st)
    story.append(PageBreak())

    # Page 4 — Diagnostic & recommendation
    story += _pagina_diagnostico(insights, st)
    story.append(PageBreak())

    # Page 5 — CTA / cost of not hedging
    story += _pagina_cta(exposure, prospect, st)

    doc.build(story)
    logger.info("PDF de diagnóstico generado: %s", output_path)
    return output_path
