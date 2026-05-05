"""
Generación de datos y PDF para reportes periódicos de clientes — HedgePoint MX.

Recopila en un solo dict toda la información necesaria para producir un
reporte: datos del cliente, contexto de mercado calculado desde la BD,
P&L mark-to-market de coberturas y coberturas próximas a vencer.

Funciones públicas:
    generar_datos_reporte   — recopila datos del cliente desde BD
    generar_pdf_reporte     — produce el PDF de 5 páginas con reportlab
    generar_reportes_todos  — genera PDFs para todos los clientes con coberturas activas
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import KeepTogether

from core.database import (
    DB_PATH,
    get_client_hedges,
    get_expiring_hedges,
    get_latest_fx_rates,
    get_prospect,
)
from core.models.hedge_pnl import resumen_pnl_cliente
from core.utils import strip_markdown

logger = logging.getLogger(__name__)

_DISCLAIMER_IA = (
    "Este análisis fue generado con asistencia de inteligencia artificial y datos de mercado "
    "públicos. No constituye asesoría financiera. Consulte a un profesional antes de tomar "
    "decisiones de inversión."
)

_MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

# ---------------------------------------------------------------------------
# Brand palette  (copied from agents/onboarding/pdf_diagnostic.py)
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

PAGE_W, PAGE_H = A4

CONTACTO_EMAIL = "contacto@hedgepointmx.com"
CONTACTO_WEB   = "www.hedgepointmx.com"
CONTACTO_WA    = "+52 (993) 170-1758"


# ---------------------------------------------------------------------------
# Typography styles  (copied from agents/onboarding/pdf_diagnostic.py)
# ---------------------------------------------------------------------------

def _estilos() -> dict:
    s = {}

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
    s["seccion"] = ParagraphStyle(
        "seccion", fontName="Helvetica-Bold", fontSize=13,
        textColor=AZUL, spaceBefore=14, spaceAfter=4, leading=16,
    )
    s["sub_seccion"] = ParagraphStyle(
        "sub_seccion", fontName="Helvetica-Bold", fontSize=10,
        textColor=AZUL_MEDIO, spaceBefore=8, spaceAfter=2,
    )
    s["cuerpo"] = ParagraphStyle(
        "cuerpo", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#374151"),
        alignment=TA_JUSTIFY, leading=13, spaceAfter=4,
    )
    s["cuerpo_left"] = ParagraphStyle(
        "cuerpo_left", fontName="Helvetica", fontSize=9,
        textColor=colors.HexColor("#374151"), leading=13, spaceAfter=4,
    )
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
    s["disclaimer_ia"] = ParagraphStyle(
        "disclaimer_ia", fontName="Helvetica", fontSize=7,
        textColor=GRIS, alignment=TA_CENTER, leading=10, spaceBefore=10,
    )

    return s


# ---------------------------------------------------------------------------
# Page templates  (copied from agents/onboarding/pdf_diagnostic.py,
#                  _encabezado_pie modificado: "Reporte Semanal de Coberturas")
# ---------------------------------------------------------------------------

def _fondo_portada(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(AZUL)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=True, stroke=False)
    canvas.setFillColor(VERDE)
    canvas.rect(0, 0, PAGE_W, 3.5 * cm, fill=True, stroke=False)
    canvas.setFillColor(AZUL_MEDIO)
    canvas.rect(0, 3.5 * cm, PAGE_W, 0.4 * cm, fill=True, stroke=False)
    canvas.restoreState()


def _encabezado_pie(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(AZUL)
    canvas.rect(1.8 * cm, PAGE_H - 1.8 * cm, PAGE_W - 3.6 * cm, 0.9 * cm,
                fill=True, stroke=False)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.setFillColor(BLANCO)
    canvas.drawString(2.2 * cm, PAGE_H - 1.35 * cm, "HedgePoint MX")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(PAGE_W - 2.2 * cm, PAGE_H - 1.35 * cm,
                           "Reporte Semanal de Coberturas")   # ← modificado
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
# KPI box  (copied from agents/onboarding/pdf_diagnostic.py)
# ---------------------------------------------------------------------------

def _kpi_box(numero: str, etiqueta: str, es_negativo: bool = False) -> Table:
    """Single KPI tile with a number and a label."""
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
    gap   = 0.35 * cm
    n     = len(kpis)
    avail = PAGE_W - 2 * 1.8 * cm
    col_w = (avail - gap * (n - 1)) / n

    cells = []
    for numero, etiqueta, es_neg in kpis:
        color_num = ROJO if es_neg else VERDE
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
        ("LEFTPADDING",  (0, 0), (-1, -1), gap / 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), gap / 2),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))
    return row


# ---------------------------------------------------------------------------
# Table style helper
# ---------------------------------------------------------------------------

def _ts_base(header_rows: int = 1) -> list:
    return [
        ("BACKGROUND",     (0, 0), (-1, header_rows - 1), AZUL),
        ("TEXTCOLOR",      (0, 0), (-1, header_rows - 1), BLANCO),
        ("FONTNAME",       (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, header_rows - 1), 8),
        ("ALIGN",          (0, 0), (-1, header_rows - 1), "CENTER"),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [colors.white, AZUL_CLARO]),
        ("FONTNAME",       (0, header_rows), (-1, -1), "Helvetica"),
        ("FONTSIZE",       (0, header_rows), (-1, -1), 8),
        ("ALIGN",          (0, header_rows), (-1, -1), "CENTER"),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
    ]


def _split_paragraphs(text: str) -> list[str]:
    paras = re.split(r"\n\s*\n", text.strip())
    return [p.replace("\n", " ").strip() for p in paras if p.strip()]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _pagina_portada_reporte(nombre_cliente: str, fecha_rep: date, st: dict) -> list:
    fecha_larga = f"{fecha_rep.day} de {_MESES_ES[fecha_rep.month]} de {fecha_rep.year}"
    story = []
    story.append(Spacer(1, 7.5 * cm))
    story.append(Paragraph("HedgePoint MX", st["titulo_portada"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Reporte Semanal de Coberturas", st["subtitulo_portada"]))
    story.append(Spacer(1, 1.2 * cm))
    story.append(HRFlowable(width="60%", thickness=0.5,
                             color=colors.HexColor("#4a7ab5"), hAlign="CENTER"))
    story.append(Spacer(1, 1.0 * cm))
    story.append(Paragraph(nombre_cliente, st["empresa_portada"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(f"Generado el {fecha_larga}", st["etiqueta_portada"]))
    story.append(Spacer(1, 5.5 * cm))
    story.append(Paragraph("DOCUMENTO CONFIDENCIAL", st["confidencial"]))
    return story


def _pagina_mercado(resumen_mercado: dict, ultimos_fx: list[dict], st: dict) -> list:
    story = []
    story.append(Paragraph("Resumen de Mercado", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.4 * cm))

    spot   = resumen_mercado.get("spot", 0.0)
    var    = resumen_mercado.get("variacion_semanal", 0.0)
    vol    = resumen_mercado.get("volatilidad_30d", 0.0)

    # Variación: verde si el peso se fortaleció (dólar baja = var negativa)
    var_neg = var > 0   # dólar sube → peso débil → rojo
    var_str = f"{var:+.2f}%"

    story.append(_fila_kpis(
        (f"${spot:.4f}", "Spot USD/MXN", False),
        (var_str,        "Variación semanal", var_neg),
        (f"{vol:.1f}%",  "Volatilidad 30d (anualizada)", False),
    ))
    story.append(Spacer(1, 0.5 * cm))

    if ultimos_fx:
        story.append(Paragraph("Últimas 5 cotizaciones USDMXN", st["sub_seccion"]))
        header = ["Fecha", "Hora", "Bid", "Ask", "Fuente"]
        rows = [header]
        for r in ultimos_fx:
            rows.append([
                r.get("fecha", ""),
                r.get("hora", ""),
                f"${r.get('bid', 0):.4f}",
                f"${r.get('ask', 0):.4f}",
                r.get("source", ""),
            ])
        W = PAGE_W - 3.6 * cm
        t = Table(rows, colWidths=[W * p for p in (0.22, 0.18, 0.20, 0.20, 0.20)],
                  repeatRows=1)
        t.setStyle(TableStyle(_ts_base(1)))
        story.append(t)
    else:
        story.append(Paragraph("Sin datos de mercado disponibles.", st["cuerpo"]))

    return story


def _pagina_posicion(pnl_resumen: dict, st: dict) -> list:
    story = []
    story.append(Paragraph("Posición del Cliente", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.4 * cm))

    cubierto   = pnl_resumen.get("total_cubierto_usd", 0.0)
    mtm        = pnl_resumen.get("total_mtm_mxn", 0.0)
    residual   = pnl_resumen.get("exposicion_residual_usd") or 0.0
    num_cob    = pnl_resumen.get("num_coberturas", 0)

    story.append(_fila_kpis(
        (f"${cubierto:,.0f}",  "Total cubierto USD",       False),
        (f"${mtm:,.0f}",       "MTM total MXN",            mtm < 0),
        (f"${residual:,.0f}",  "Exposición residual USD",  residual > 0),
        (str(num_cob),         "Coberturas activas",        False),
    ))
    story.append(Spacer(1, 0.5 * cm))

    coberturas_pnl = pnl_resumen.get("coberturas", [])
    if coberturas_pnl:
        story.append(Paragraph("Detalle de coberturas activas", st["sub_seccion"]))
        header = ["Tipo", "Monto USD", "Strike", "Días rest.", "MTM MXN", "Ahorro vs Spot"]
        rows = [header]
        for c in coberturas_pnl:
            pnl_color = VERDE if c.pnl_vs_spot_mxn >= 0 else ROJO
            rows.append([
                c.tipo.upper(),
                f"${c.monto_usd:,.0f}",
                f"${c.strike:.4f}",
                str(c.dias_restantes),
                f"${c.mtm_mxn:,.0f}",
                f"${c.pnl_vs_spot_mxn:,.0f}",
            ])
        W = PAGE_W - 3.6 * cm
        t = Table(rows,
                  colWidths=[W * p for p in (0.12, 0.18, 0.14, 0.12, 0.22, 0.22)],
                  repeatRows=1)
        cmds = _ts_base(1)
        # Color P&L column by sign
        for i, c in enumerate(coberturas_pnl, start=1):
            color = VERDE if c.pnl_vs_spot_mxn >= 0 else ROJO
            cmds.append(("TEXTCOLOR", (5, i), (5, i), color))
            cmds.append(("FONTNAME",  (5, i), (5, i), "Helvetica-Bold"))
        t.setStyle(TableStyle(cmds))
        story.append(t)
    else:
        story.append(Paragraph("No hay coberturas activas.", st["cuerpo"]))

    return story


def _pagina_recomendaciones(resumen_mercado: dict, pnl_resumen: dict, st: dict) -> list:
    story = []
    story.append(Paragraph("Recomendaciones", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))

    texto_rec = _FALLBACK_REPORT_TEXT
    try:
        from core.llm_client import HedgePointLLM
        llm = HedgePointLLM()
        texto_rec = llm.generate_report_recommendations(resumen_mercado, pnl_resumen)
    except Exception as exc:
        logger.warning("LLM no disponible para recomendaciones: %s", exc)

    for para in _split_paragraphs(strip_markdown(texto_rec)):
        story.append(Paragraph(para, st["cuerpo"]))

    story.append(Spacer(1, 0.3 * cm))
    story.append(HRFlowable(width="100%", thickness=0.3, color=GRIS))
    story.append(Paragraph(_DISCLAIMER_IA, st["disclaimer_ia"]))

    return story


def _pagina_vencimientos_cta(proximos: list[dict], st: dict) -> list:
    story = []
    story.append(Paragraph("Próximos Vencimientos", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.4 * cm))

    if proximos:
        header = ["Tipo", "Monto USD", "Strike", "Fecha vencimiento", "Días restantes"]
        rows = [header]
        today = date.today()
        for h in proximos:
            from datetime import date as _date
            try:
                venc = _date.fromisoformat(h["fecha_vencimiento"])
                dias = max((venc - today).days, 0)
            except Exception:
                dias = "—"
            rows.append([
                h.get("tipo", "").upper(),
                f"${h.get('monto_usd', 0):,.0f}",
                f"${h.get('strike', 0):.4f}",
                h.get("fecha_vencimiento", ""),
                str(dias),
            ])
        W = PAGE_W - 3.6 * cm
        t = Table(rows,
                  colWidths=[W * p for p in (0.12, 0.22, 0.18, 0.28, 0.20)],
                  repeatRows=1)
        t.setStyle(TableStyle(_ts_base(1)))
        story.append(t)
    else:
        story.append(Paragraph(
            "No hay coberturas por vencer en los próximos 30 días.",
            st["cuerpo"],
        ))

    story.append(Spacer(1, 0.8 * cm))

    # CTA
    story.append(KeepTogether([
        HRFlowable(width="80%", thickness=0.5, color=AZUL_CLARO, hAlign="CENTER"),
        Spacer(1, 0.4 * cm),
        Paragraph("¿Desea renovar o ajustar sus coberturas?", st["cta"]),
        Spacer(1, 0.15 * cm),
        Paragraph(
            "Contáctenos para revisar su posición y explorar nuevas estrategias "
            "adaptadas a las condiciones actuales del mercado.",
            st["cta_sub"],
        ),
        Spacer(1, 0.3 * cm),
        Paragraph(
            f"<b>Email:</b> {CONTACTO_EMAIL} &nbsp;|&nbsp; "
            f"<b>WhatsApp:</b> {CONTACTO_WA} &nbsp;|&nbsp; "
            f"<b>Web:</b> {CONTACTO_WEB}",
            st["cta_sub"],
        ),
    ]))

    return story


# ---------------------------------------------------------------------------
# Fallback text (importado aquí para que _pagina_recomendaciones lo use)
# ---------------------------------------------------------------------------

_FALLBACK_REPORT_TEXT = (
    "Recomendaciones no disponibles temporalmente. "
    "Las métricas cuantitativas de su posición están actualizadas en este reporte."
)

_SPOT_FALLBACK = 20.0
_PAR = "USDMXN"


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def generar_pdf_reporte(
    datos: dict,
    output_path: Optional[str] = None,
) -> str:
    """Genera el PDF de reporte semanal de coberturas y retorna su ruta.

    Parameters
    ----------
    datos : dict
        Dict devuelto por ``generar_datos_reporte()``.
    output_path : str, optional
        Ruta destino.  Si es None se usa
        ``output/reports/{prospect_id}/{fecha}/reporte.pdf``.

    Returns
    -------
    str
        Ruta absoluta del PDF generado.
    """
    cliente      = datos.get("cliente") or {}
    pnl_resumen  = datos.get("pnl") or {}
    resumen_merc = datos.get("resumen_mercado") or {}
    proximos     = datos.get("proximos_vencimientos") or []
    fecha_rep    = datos.get("fecha_reporte") or date.today()
    prospect_id  = cliente.get("id", 0)

    # Intentar desencriptar nombre de empresa
    nombre_cliente = f"Cliente {prospect_id}"
    try:
        from core.security.anonymizer import FieldEncryptor
        empresa_enc = cliente.get("empresa_enc", "")
        if empresa_enc:
            nombre_cliente = FieldEncryptor().decrypt(empresa_enc)
    except Exception:
        pass

    fecha_str = fecha_rep.strftime("%Y-%m-%d") if hasattr(fecha_rep, "strftime") else str(fecha_rep)

    if output_path is None:
        out_dir = (
            Path(__file__).parent.parent.parent
            / "output" / "reports" / str(prospect_id) / fecha_str
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / "reporte.pdf")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Últimos 5 registros FX para la tabla de mercado
    db_path = datos.get("_db_path", DB_PATH)
    try:
        ultimos_fx = get_latest_fx_rates(_PAR, n=5, db_path=db_path)
    except Exception:
        ultimos_fx = []

    st = _estilos()

    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=0, rightMargin=0,
        topMargin=0, bottomMargin=0,
    )
    doc.addPageTemplates(_crear_plantillas(doc))

    story: list = []

    # Página 1 — Portada
    story += _pagina_portada_reporte(nombre_cliente, fecha_rep, st)
    story.append(NextPageTemplate("interior"))
    story.append(PageBreak())

    # Página 2 — Resumen de mercado
    story += _pagina_mercado(resumen_merc, ultimos_fx, st)
    story.append(PageBreak())

    # Página 3 — Posición del cliente
    story += _pagina_posicion(pnl_resumen, st)
    story.append(PageBreak())

    # Página 4 — Recomendaciones LLM
    story += _pagina_recomendaciones(resumen_merc, pnl_resumen, st)
    story.append(PageBreak())

    # Página 5 — Próximos vencimientos + CTA
    story += _pagina_vencimientos_cta(proximos, st)

    doc.build(story)
    logger.info("PDF de reporte generado: %s", output_path)
    return output_path


def generar_reportes_todos(db_path: Path = DB_PATH) -> list[str]:
    """Genera PDFs para todos los clientes con coberturas activas.

    Obtiene los prospect_ids únicos de la tabla ``hedges`` con estado='activa',
    genera los datos y el PDF para cada uno.

    Parameters
    ----------
    db_path : Path, optional
        Ruta a la base de datos SQLite.

    Returns
    -------
    list[str]
        Lista de rutas absolutas de los PDFs generados.
    """
    from core.database import get_connection

    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT prospect_id FROM hedges WHERE estado = 'activa'"
        ).fetchall()

    paths: list[str] = []
    for row in rows:
        pid = row["prospect_id"]
        try:
            datos = generar_datos_reporte(pid, db_path=db_path)
            # Pasar db_path para que generar_pdf_reporte pueda leer fx_rates
            datos["_db_path"] = db_path
            path = generar_pdf_reporte(datos)
            paths.append(path)
            logger.info("Reporte generado para prospect_id=%d: %s", pid, path)
        except Exception as exc:
            logger.error("Error generando reporte para prospect_id=%d: %s", pid, exc)

    return paths


def generar_datos_reporte(
    prospect_id: int,
    db_path: Path = DB_PATH,
) -> dict:
    """Recopila todos los datos necesarios para el reporte de un cliente.

    Parameters
    ----------
    prospect_id : int
        ID del prospecto/cliente en la BD.
    db_path : Path, optional
        Ruta a la base de datos SQLite.

    Returns
    -------
    dict
        Diccionario con las siguientes claves:

        - ``cliente`` (dict | None): fila completa del prospecto.
        - ``spot_actual`` (float): último bid de ``fx_rates`` para USDMXN;
          fallback ``20.0`` si no hay registros.
        - ``resumen_mercado`` (dict): ``spot``, ``variacion_semanal`` (% vs
          registro de hace ~5 días), ``volatilidad_30d`` (desv. estándar
          anualizada de returns diarios sobre los últimos 30 registros).
        - ``pnl`` (dict): resultado de ``resumen_pnl_cliente()``.
        - ``coberturas`` (list[dict]): coberturas activas del cliente.
        - ``proximos_vencimientos`` (list[dict]): coberturas activas de este
          cliente que vencen en los próximos 30 días.
        - ``fecha_reporte`` (date): fecha de generación.
    """
    cliente = get_prospect(prospect_id, db_path=db_path)

    # --- Spot actual ---
    ultimos = get_latest_fx_rates(_PAR, n=35, db_path=db_path)
    if ultimos:
        spot_actual = ultimos[0]["bid"]
    else:
        spot_actual = _SPOT_FALLBACK

    # --- Resumen de mercado ---
    resumen_mercado = _calcular_resumen_mercado(ultimos, spot_actual)

    # --- P&L coberturas ---
    pnl = resumen_pnl_cliente(prospect_id, spot_actual, db_path=db_path)

    # --- Coberturas activas del cliente ---
    coberturas = get_client_hedges(prospect_id, estado="activa", db_path=db_path)

    # --- Próximos vencimientos (30 días) filtrados por este cliente ---
    todos_expiring = get_expiring_hedges(dias=30, db_path=db_path)
    proximos_vencimientos = [h for h in todos_expiring if h["prospect_id"] == prospect_id]

    return {
        "cliente": cliente,
        "spot_actual": spot_actual,
        "resumen_mercado": resumen_mercado,
        "pnl": pnl,
        "coberturas": coberturas,
        "proximos_vencimientos": proximos_vencimientos,
        "fecha_reporte": date.today(),
    }


def _calcular_resumen_mercado(registros: list[dict], spot_actual: float) -> dict:
    """Calcula variación semanal y volatilidad 30d a partir de registros de BD.

    Parameters
    ----------
    registros : list[dict]
        Registros de ``fx_rates`` ordenados por fecha DESC (más reciente primero).
        Se esperan hasta 35 registros para tener margen de ~5 días y 30 de vol.
    spot_actual : float
        Bid del registro más reciente (ya extraído por el caller).

    Returns
    -------
    dict
        ``spot``, ``variacion_semanal`` (float, porcentaje),
        ``volatilidad_30d`` (float, porcentaje anualizado).
    """
    variacion_semanal = 0.0
    volatilidad_30d = 0.0

    if len(registros) >= 2:
        # Variación semanal: comparar spot actual con el registro índice 4
        # (aproximadamente 5 registros atrás, asumiendo ~1 por día hábil).
        idx_semana = min(4, len(registros) - 1)
        spot_semana = registros[idx_semana]["bid"]
        if spot_semana and spot_semana != 0:
            variacion_semanal = (spot_actual - spot_semana) / spot_semana * 100

    if len(registros) >= 2:
        # Volatilidad 30d: desv. estándar de returns diarios * sqrt(252)
        bids = [r["bid"] for r in registros[:30] if r["bid"]]
        # Returns: ln(S_t / S_{t+1}) — registros en orden DESC
        returns = [
            math.log(bids[i] / bids[i + 1])
            for i in range(len(bids) - 1)
            if bids[i] > 0 and bids[i + 1] > 0
        ]
        if returns:
            n = len(returns)
            media = sum(returns) / n
            varianza = sum((r - media) ** 2 for r in returns) / n
            vol_diaria = math.sqrt(varianza)
            volatilidad_30d = vol_diaria * math.sqrt(252) * 100  # porcentaje anualizado

    return {
        "spot": spot_actual,
        "variacion_semanal": round(variacion_semanal, 4),
        "volatilidad_30d": round(volatilidad_30d, 4),
    }
