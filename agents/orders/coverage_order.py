"""
Generación de PDF de Orden de Cobertura — HedgePoint MX.

Produce un PDF de 2 páginas que el cliente entrega a su banco para contratar
una cobertura cambiaria.  Toda la generación es local; no se llama a ninguna
API externa.

Funciones públicas:
    construir_datos_orden   — recopila todos los datos necesarios (BD + mercado + pricing)
    generar_pdf_orden       — genera el PDF y retorna su ruta absoluta
    datos_demo              — devuelve un dict de datos ficticios para --demo
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Brand palette  (idéntica a report_generator.py y pdf_diagnostic.py)
# ---------------------------------------------------------------------------

AZUL        = colors.HexColor("#1a365d")
AZUL_MEDIO  = colors.HexColor("#2a4f82")
AZUL_CLARO  = colors.HexColor("#e8eef7")
VERDE       = colors.HexColor("#2d8659")
VERDE_CLARO = colors.HexColor("#e6f4ed")
GRIS        = colors.HexColor("#6b7280")
GRIS_CLARO  = colors.HexColor("#f3f4f6")
ROJO        = colors.HexColor("#c0392b")
NARANJA     = colors.HexColor("#E8A838")
BLANCO      = colors.white

PAGE_W, PAGE_H = A4

CONTACTO_EMAIL = "contacto@hedgepointmx.com"
CONTACTO_WEB   = "www.hedgepointmx.com"
CONTACTO_WA    = "+52 (993) 170-1758"

_MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}

_DISCLAIMER = (
    "Este documento es una referencia de consultoría generada por HedgePoint MX. "
    "HedgePoint MX no ejecuta ni intermedia operaciones financieras. "
    "Los precios teóricos son de referencia y no constituyen una cotización vinculante. "
    "La decisión de contratar la cobertura y la negociación de condiciones finales "
    "corresponde exclusivamente al cliente y a su institución bancaria."
)


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class DatosOrden:
    """Todos los datos necesarios para generar la Orden de Cobertura."""
    # Cliente
    empresa: str
    contacto: str
    telefono: str

    # Instrumento
    tipo: str                       # forward | opcion | collar
    monto_usd: float
    plazo_dias: int
    strike: float
    strike_call: Optional[float]    # sólo collar
    fecha_inicio: date
    fecha_vencimiento: date
    capa: str                       # "Base" | "Táctica 1" | etc.

    # Mercado
    spot: float
    bid: float
    ask: float
    spread: float
    volatilidad_30d: float          # % anualizada
    hora_cotizacion: str
    fuente_mercado: str

    # Pricing teórico
    forward_teorico: Optional[float]
    prima_put: Optional[float]      # MXN por USD
    prima_call: Optional[float]     # MXN por USD (collar: call vendido)
    prima_neta: Optional[float]     # put - call para collar, put para opción

    # Posición actual del cliente
    coberturas_activas: list[dict] = field(default_factory=list)
    monto_cubierto_usd: float = 0.0
    pct_cubierto_actual: float = 0.0
    strike_promedio: float = 0.0
    volumen_mensual_usd: float = 0.0

    # Justificación
    justificacion: str = ""
    prospect_id: Optional[int] = None
    fecha_generacion: date = field(default_factory=date.today)


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------

def _estilos() -> dict:
    s = {}

    s["titulo_portada"] = ParagraphStyle(
        "titulo_portada", fontName="Helvetica-Bold", fontSize=30,
        textColor=BLANCO, alignment=TA_CENTER, leading=36, spaceAfter=6,
    )
    s["subtitulo_portada"] = ParagraphStyle(
        "subtitulo_portada", fontName="Helvetica", fontSize=14,
        textColor=colors.HexColor("#b8d4f0"), alignment=TA_CENTER, leading=18,
    )
    s["empresa_portada"] = ParagraphStyle(
        "empresa_portada", fontName="Helvetica-Bold", fontSize=20,
        textColor=BLANCO, alignment=TA_CENTER, leading=24, spaceBefore=18,
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
    s["cuerpo_bold"] = ParagraphStyle(
        "cuerpo_bold", fontName="Helvetica-Bold", fontSize=9,
        textColor=colors.HexColor("#374151"), leading=13, spaceAfter=4,
    )
    s["disclaimer"] = ParagraphStyle(
        "disclaimer", fontName="Helvetica", fontSize=7,
        textColor=GRIS, alignment=TA_JUSTIFY, leading=10,
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
    s["firma_label"] = ParagraphStyle(
        "firma_label", fontName="Helvetica", fontSize=8,
        textColor=GRIS, alignment=TA_CENTER,
    )
    s["kpi_n"] = ParagraphStyle(
        "kpi_n", fontName="Helvetica-Bold", fontSize=16,
        textColor=VERDE, alignment=TA_CENTER, leading=20,
    )
    s["kpi_l"] = ParagraphStyle(
        "kpi_l", fontName="Helvetica", fontSize=7.5,
        textColor=GRIS, alignment=TA_CENTER, leading=10,
    )
    s["alerta"] = ParagraphStyle(
        "alerta", fontName="Helvetica-Bold", fontSize=9,
        textColor=NARANJA, alignment=TA_CENTER, spaceBefore=4, spaceAfter=4,
    )

    return s


# ---------------------------------------------------------------------------
# Page templates
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
                           "Orden de Cobertura Cambiaria")
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
# Helpers
# ---------------------------------------------------------------------------

def _ts_base(header_rows: int = 1) -> list:
    return [
        ("BACKGROUND",     (0, 0), (-1, header_rows - 1), AZUL),
        ("TEXTCOLOR",      (0, 0), (-1, header_rows - 1), BLANCO),
        ("FONTNAME",       (0, 0), (-1, header_rows - 1), "Helvetica-Bold"),
        ("FONTSIZE",       (0, 0), (-1, header_rows - 1), 8),
        ("ALIGN",          (0, 0), (-1, header_rows - 1), "CENTER"),
        ("ROWBACKGROUNDS", (0, header_rows), (-1, -1), [BLANCO, AZUL_CLARO]),
        ("FONTNAME",       (0, header_rows), (-1, -1), "Helvetica"),
        ("FONTSIZE",       (0, header_rows), (-1, -1), 8),
        ("ALIGN",          (0, header_rows), (-1, -1), "CENTER"),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
    ]


def _kv_table(filas: list[tuple[str, str]], st: dict) -> Table:
    """Tabla de dos columnas etiqueta/valor con fondo alterno."""
    W = PAGE_W - 3.6 * cm
    rows = []
    for label, value in filas:
        rows.append([
            Paragraph(f"<b>{label}</b>", st["td_left"]),
            Paragraph(value, st["td"]),
        ])
    t = Table(rows, colWidths=[W * 0.45, W * 0.55])
    t.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [BLANCO, AZUL_CLARO]),
        ("GRID",           (0, 0), (-1, -1), 0.3, colors.HexColor("#d1d5db")),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("LEFTPADDING",    (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 6),
        ("VALIGN",         (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _fila_kpis(kpis: list[tuple[str, str, bool]]) -> Table:
    """Fila de KPI tiles.  kpis = [(numero, etiqueta, es_negativo), ...]"""
    gap   = 0.35 * cm
    n     = len(kpis)
    avail = PAGE_W - 3.6 * cm
    col_w = (avail - gap * (n - 1)) / n

    cells = []
    for numero, etiqueta, es_neg in kpis:
        color_num = ROJO if es_neg else VERDE
        sn = ParagraphStyle("_kn", fontName="Helvetica-Bold", fontSize=15,
                            textColor=color_num, alignment=TA_CENTER, leading=19)
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


def _fecha_larga(d: date) -> str:
    return f"{d.day} de {_MESES_ES[d.month]} de {d.year}"


def _nombre_instrumento(tipo: str) -> str:
    return {
        "forward": "Forward USD/MXN",
        "opcion":  "Opción Put USD/MXN (Garman-Kohlhagen)",
        "collar":  "Collar USD/MXN (Put comprada + Call vendida)",
    }.get(tipo, tipo.title())


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _pagina_portada(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Spacer(1, 6.5 * cm))
    story.append(Paragraph("HedgePoint MX", st["titulo_portada"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("Orden de Cobertura Cambiaria", st["subtitulo_portada"]))
    story.append(Spacer(1, 1.0 * cm))
    story.append(HRFlowable(width="60%", thickness=0.5,
                             color=colors.HexColor("#4a7ab5"), hAlign="CENTER"))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph(d.empresa, st["empresa_portada"]))
    story.append(Spacer(1, 0.4 * cm))

    tipo_str = _nombre_instrumento(d.tipo)
    story.append(Paragraph(tipo_str, st["subtitulo_portada"]))
    story.append(Spacer(1, 0.4 * cm))

    monto_str = f"USD ${d.monto_usd:,.0f} · {d.plazo_dias} días · Capa: {d.capa}"
    story.append(Paragraph(monto_str, st["etiqueta_portada"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(f"Generado el {_fecha_larga(d.fecha_generacion)}", st["etiqueta_portada"]))
    story.append(Spacer(1, 4.0 * cm))
    story.append(Paragraph("DOCUMENTO CONFIDENCIAL — USO EXCLUSIVO DEL CLIENTE", st["confidencial"]))
    return story


def _seccion_cliente(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Paragraph("Datos del Cliente", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))
    filas = [
        ("Empresa", d.empresa),
        ("Contacto", d.contacto),
        ("Teléfono", d.telefono),
    ]
    if d.prospect_id is not None:
        filas.append(("ID cliente HedgePoint", str(d.prospect_id)))
    story.append(_kv_table(filas, st))
    return story


def _seccion_instrumento(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Paragraph("Instrumento Solicitado", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))

    filas = [
        ("Instrumento",       _nombre_instrumento(d.tipo)),
        ("Monto a cubrir",    f"USD ${d.monto_usd:,.0f}"),
        ("Plazo",             f"{d.plazo_dias} días naturales"),
        ("Fecha de inicio",   d.fecha_inicio.isoformat()),
        ("Fecha de vencimiento", d.fecha_vencimiento.isoformat()),
        ("Capa de cobertura", d.capa),
    ]

    if d.tipo == "forward":
        filas.append(("Strike (forward)",  f"${d.strike:.4f} MXN/USD"))
    elif d.tipo == "opcion":
        filas.append(("Strike (put ATM)",  f"${d.strike:.4f} MXN/USD"))
    elif d.tipo == "collar":
        filas.append(("Strike put comprada",  f"${d.strike:.4f} MXN/USD"))
        if d.strike_call:
            filas.append(("Strike call vendida", f"${d.strike_call:.4f} MXN/USD"))

    story.append(_kv_table(filas, st))
    return story


def _seccion_mercado(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Paragraph("Condiciones de Mercado al Momento", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))

    story.append(_fila_kpis([
        (f"${d.spot:.4f}",          "Spot USD/MXN",          False),
        (f"${d.bid:.4f}",           "Bid",                   False),
        (f"${d.ask:.4f}",           "Ask",                   False),
        (f"{d.volatilidad_30d:.1f}%", "Volatilidad 30d anual", False),
    ]))
    story.append(Spacer(1, 0.3 * cm))

    filas = [
        ("Spread bid/ask",    f"${d.spread:.4f} MXN/USD ({d.spread/d.spot*100:.3f}%)"),
        ("Fuente de precios", d.fuente_mercado),
        ("Hora de cotización", d.hora_cotizacion),
        ("Fecha de cotización", d.fecha_generacion.isoformat()),
    ]
    story.append(_kv_table(filas, st))
    return story


def _seccion_precio_teorico(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Paragraph("Referencia de Precio Teórico", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "Los valores siguientes son precios teóricos calculados con modelos financieros estándar "
        "(paridad cubierta de tasas / Garman-Kohlhagen) para referencia del cliente al negociar "
        "con su banco.  El banco puede cotizar condiciones distintas.",
        st["cuerpo"],
    ))
    story.append(Spacer(1, 0.2 * cm))

    filas = []
    if d.forward_teorico is not None:
        filas.append(("Forward teórico (IRP)",  f"${d.forward_teorico:.4f} MXN/USD"))
    if d.prima_put is not None:
        filas.append(("Prima put teórica (GK)",  f"${d.prima_put:.4f} MXN/USD"))
    if d.prima_call is not None:
        filas.append(("Prima call teórica (GK)", f"${d.prima_call:.4f} MXN/USD"))
    if d.prima_neta is not None:
        etiqueta = "Prima neta collar (put − call)" if d.tipo == "collar" else "Prima neta"
        filas.append((etiqueta, f"${d.prima_neta:.4f} MXN/USD"))

    if filas:
        story.append(_kv_table(filas, st))
    else:
        story.append(Paragraph("Sin cálculo teórico disponible.", st["cuerpo"]))

    return story


def _seccion_disclaimer(st: dict) -> list:
    return [
        Spacer(1, 0.4 * cm),
        HRFlowable(width="100%", thickness=0.3, color=GRIS),
        Spacer(1, 0.2 * cm),
        Paragraph(_DISCLAIMER, st["disclaimer"]),
    ]


def _seccion_justificacion(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Paragraph("Justificación de la Operación", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))
    texto = d.justificacion or (
        "Cobertura programada conforme al plan de gestión de riesgo cambiario del cliente."
    )
    story.append(Paragraph(texto, st["cuerpo"]))
    return story


def _seccion_posicion_actual(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Paragraph("Posición Actual del Cliente", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))

    # KPIs de posición
    monto_despues = d.monto_cubierto_usd + d.monto_usd
    pct_despues = (
        (monto_despues / d.volumen_mensual_usd * 100)
        if d.volumen_mensual_usd > 0 else 0.0
    )
    story.append(_fila_kpis([
        (f"${d.monto_cubierto_usd:,.0f}", "Cubierto actual USD",      False),
        (f"{d.pct_cubierto_actual:.0f}%",  "% cubierto actual",        False),
        (f"${monto_despues:,.0f}",          "Cubierto después de orden", False),
        (f"{pct_despues:.0f}%",             "% cubierto después",        False),
    ]))
    story.append(Spacer(1, 0.3 * cm))

    if d.strike_promedio > 0:
        story.append(Paragraph(
            f"Strike promedio ponderado de coberturas activas: "
            f"<b>${d.strike_promedio:.4f} MXN/USD</b>",
            st["cuerpo_left"],
        ))
        story.append(Spacer(1, 0.2 * cm))

    if d.coberturas_activas:
        story.append(Paragraph("Coberturas activas vigentes", st["sub_seccion"]))
        header = ["Tipo", "Monto USD", "Strike", "Vencimiento", "Estado"]
        rows = [header]
        for h in d.coberturas_activas:
            rows.append([
                h.get("tipo", "").upper(),
                f"${h.get('monto_usd', 0):,.0f}",
                f"${h.get('strike', 0):.4f}",
                h.get("fecha_vencimiento", ""),
                h.get("estado", "").capitalize(),
            ])
        W = PAGE_W - 3.6 * cm
        t = Table(rows, colWidths=[W * p for p in (0.12, 0.22, 0.18, 0.28, 0.20)],
                  repeatRows=1)
        t.setStyle(TableStyle(_ts_base(1)))
        story.append(t)
    else:
        story.append(Paragraph(
            "El cliente no tiene coberturas activas previas a esta orden.",
            st["cuerpo"],
        ))

    return story


def _seccion_autorizacion(d: DatosOrden, st: dict) -> list:
    story = []
    story.append(Paragraph("Autorización del Cliente", st["seccion"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=AZUL_CLARO))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "El suscrito autoriza a proceder con la contratación de la cobertura descrita en "
        "este documento con su institución bancaria, conforme a las condiciones acordadas "
        "en la negociación directa con el banco.  Este documento <b>no obliga</b> a "
        "HedgePoint MX ni a ningún intermediario financiero.",
        st["cuerpo"],
    ))
    story.append(Spacer(1, 1.2 * cm))

    # Líneas de firma
    W = PAGE_W - 3.6 * cm
    half = W * 0.46
    gap  = W * 0.08

    firma_rows = [
        [
            Table(
                [[Paragraph("_" * 38, st["td"])],
                 [Paragraph(d.empresa, st["firma_label"])],
                 [Paragraph("Nombre y firma del representante legal", st["firma_label"])]],
                colWidths=[half],
            ),
            Spacer(gap, 1),
            Table(
                [[Paragraph("_" * 38, st["td"])],
                 [Paragraph("", st["firma_label"])],
                 [Paragraph("Fecha", st["firma_label"])]],
                colWidths=[half],
            ),
        ]
    ]
    firma_t = Table(firma_rows, colWidths=[half, gap, half])
    firma_t.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(firma_t)
    story.append(Spacer(1, 0.6 * cm))

    # Contacto HedgePoint
    story.append(KeepTogether([
        HRFlowable(width="80%", thickness=0.5, color=AZUL_CLARO, hAlign="CENTER"),
        Spacer(1, 0.3 * cm),
        Paragraph(
            f"<b>Email:</b> {CONTACTO_EMAIL} &nbsp;|&nbsp; "
            f"<b>WhatsApp:</b> {CONTACTO_WA} &nbsp;|&nbsp; "
            f"<b>Web:</b> {CONTACTO_WEB}",
            st["cuerpo_left"],
        ),
    ]))

    return story


# ---------------------------------------------------------------------------
# Public API — data assembly
# ---------------------------------------------------------------------------

def construir_datos_orden(
    prospect_id: int,
    tipo: str,
    monto_usd: float,
    plazo_dias: int,
    capa: str,
    justificacion: str,
    strike: Optional[float] = None,
    db_path=None,
) -> DatosOrden:
    """Recopila todos los datos desde BD, mercado y pricing para la orden.

    Parameters
    ----------
    prospect_id : int
        ID del cliente en la tabla prospects.
    tipo : str
        Instrumento: ``'forward'``, ``'opcion'`` o ``'collar'``.
    monto_usd : float
        Monto a cubrir en USD.
    plazo_dias : int
        Plazo de la cobertura en días naturales.
    capa : str
        Etiqueta de la capa (ej. ``'Base'``, ``'Táctica 1'``).
    justificacion : str
        Texto libre con la justificación de la operación.
    strike : float, optional
        Strike sugerido.  Si es None, se calcula automáticamente.
    db_path : Path, optional
        Ruta a la BD SQLite.  Si es None usa DB_PATH del módulo.

    Returns
    -------
    DatosOrden
    """
    from core.database import DB_PATH, get_prospect, get_client_hedges
    from core.models.pricing import (
        calcular_forward, calcular_opcion_gk, get_tasas_actuales,
    )
    from core.security.anonymizer import FieldEncryptor

    if db_path is None:
        db_path = DB_PATH

    # --- Cliente ---
    prospect = get_prospect(prospect_id, db_path=db_path)
    if prospect is None:
        raise ValueError(f"Prospect ID {prospect_id} no encontrado en la BD.")

    # Desencriptar campos sensibles
    try:
        enc = FieldEncryptor()
        empresa  = enc.decrypt(prospect.get("empresa_enc", ""))
        contacto = enc.decrypt(prospect.get("nombre_enc", ""))
        telefono = enc.decrypt(prospect.get("telefono_enc") or "") if prospect.get("telefono_enc") else ""
    except Exception as exc:
        logger.warning("No se pudo desencriptar datos del cliente: %s", exc)
        empresa  = f"Cliente {prospect_id}"
        contacto = ""
        telefono = ""

    volumen_mensual = prospect.get("volumen_usd_mensual", 0.0) or 0.0

    # --- Spot actual ---
    spot, bid, ask, spread, hora_cot, fuente = _obtener_mercado()

    # --- Volatilidad 30d desde BD ---
    volatilidad = _calcular_vol_30d(db_path)

    # --- Pricing ---
    _tasas = get_tasas_actuales()
    fwd_result = calcular_forward(spot, plazo_dias, _tasas["tiie"], _tasas["sofr"])
    strike_calc = strike if strike is not None else fwd_result.forward

    prima_put   = None
    prima_call  = None
    prima_neta  = None
    strike_call = None
    forward_teo = fwd_result.forward

    if tipo in ("opcion", "collar"):
        try:
            gk = calcular_opcion_gk(spot, strike_calc, plazo_dias,
                                    volatilidad / 100, _tasas["tiie"], _tasas["sofr"])
            prima_put = gk.put
            if tipo == "collar":
                # Call OTM al 3% sobre el forward
                strike_call = fwd_result.forward * 1.03
                gk_call = calcular_opcion_gk(spot, strike_call, plazo_dias,
                                             volatilidad / 100, _tasas["tiie"], _tasas["sofr"])
                prima_call = gk_call.call
                prima_neta = prima_put - prima_call
            else:
                prima_neta = prima_put
        except Exception as exc:
            logger.warning("Error en pricing GK: %s", exc)

    if tipo == "forward":
        forward_teo = fwd_result.forward

    # --- Coberturas activas del cliente ---
    coberturas = get_client_hedges(prospect_id, estado="activa", db_path=db_path)
    monto_cubierto = sum(h.get("monto_usd", 0.0) for h in coberturas)
    pct_cubierto   = (monto_cubierto / volumen_mensual * 100) if volumen_mensual > 0 else 0.0

    # Strike promedio ponderado
    total_ponderado = sum(
        h.get("monto_usd", 0.0) * h.get("strike", 0.0) for h in coberturas
    )
    strike_prom = (total_ponderado / monto_cubierto) if monto_cubierto > 0 else 0.0

    fecha_inicio    = date.today()
    fecha_vencimiento = fecha_inicio + timedelta(days=plazo_dias)

    return DatosOrden(
        empresa=empresa,
        contacto=contacto,
        telefono=telefono,
        tipo=tipo,
        monto_usd=monto_usd,
        plazo_dias=plazo_dias,
        strike=strike_calc,
        strike_call=strike_call,
        fecha_inicio=fecha_inicio,
        fecha_vencimiento=fecha_vencimiento,
        capa=capa,
        spot=spot,
        bid=bid,
        ask=ask,
        spread=spread,
        volatilidad_30d=volatilidad,
        hora_cotizacion=hora_cot,
        fuente_mercado=fuente,
        forward_teorico=forward_teo,
        prima_put=prima_put,
        prima_call=prima_call,
        prima_neta=prima_neta,
        coberturas_activas=coberturas,
        monto_cubierto_usd=monto_cubierto,
        pct_cubierto_actual=pct_cubierto,
        strike_promedio=strike_prom,
        volumen_mensual_usd=volumen_mensual,
        justificacion=justificacion,
        prospect_id=prospect_id,
    )


def datos_demo(
    tipo: str = "forward",
    monto_usd: float = 200_000.0,
    plazo_dias: int = 90,
    capa: str = "Táctica 1",
    justificacion: str = (
        "Trigger activado: USD/MXN superó el nivel de alerta de $18.80. "
        "Se activa la Capa Táctica 1 de 3 conforme al plan de cobertura escalonada. "
        "Volatilidad implícita en máximos de 6 meses; forward teórico ofrece protección "
        "favorable respecto al presupuesto del trimestre."
    ),
) -> DatosOrden:
    """Genera datos ficticios para modo --demo (sin BD ni API keys)."""
    spot = 18.95
    bid  = 18.93
    ask  = 18.97
    spread = ask - bid
    vol  = 12.4

    from core.models.pricing import (
        calcular_forward, calcular_opcion_gk, get_tasas_actuales,
    )

    _tasas_demo = get_tasas_actuales()
    fwd = calcular_forward(spot, plazo_dias, _tasas_demo["tiie"], _tasas_demo["sofr"])
    strike = fwd.forward

    prima_put   = None
    prima_call  = None
    prima_neta  = None
    strike_call = None

    if tipo in ("opcion", "collar"):
        try:
            gk = calcular_opcion_gk(spot, strike, plazo_dias, vol / 100,
                                    _tasas_demo["tiie"], _tasas_demo["sofr"])
            prima_put = gk.put
            if tipo == "collar":
                strike_call = fwd.forward * 1.03
                gk_c = calcular_opcion_gk(spot, strike_call, plazo_dias,
                                          vol / 100, _tasas_demo["tiie"], _tasas_demo["sofr"])
                prima_call = gk_c.call
                prima_neta = prima_put - prima_call
            else:
                prima_neta = prima_put
        except Exception:
            pass

    coberturas_activas = [
        {
            "tipo": "forward", "monto_usd": 150_000, "strike": 18.65,
            "fecha_vencimiento": "2026-05-30", "estado": "activa",
        },
        {
            "tipo": "put", "monto_usd": 100_000, "strike": 18.50,
            "fecha_vencimiento": "2026-06-30", "estado": "activa",
        },
    ]
    monto_cubierto = sum(c["monto_usd"] for c in coberturas_activas)
    volumen_mensual = 500_000.0
    pct_cubierto    = monto_cubierto / volumen_mensual * 100
    strike_prom     = sum(c["monto_usd"] * c["strike"] for c in coberturas_activas) / monto_cubierto

    return DatosOrden(
        empresa="Importaciones Demo S.A. de C.V.",
        contacto="Lic. Juan Ejemplo",
        telefono="+52 (993) 555-1234",
        tipo=tipo,
        monto_usd=monto_usd,
        plazo_dias=plazo_dias,
        strike=strike,
        strike_call=strike_call,
        fecha_inicio=date.today(),
        fecha_vencimiento=date.today() + timedelta(days=plazo_dias),
        capa=capa,
        spot=spot,
        bid=bid,
        ask=ask,
        spread=spread,
        volatilidad_30d=vol,
        hora_cotizacion="09:15:00",
        fuente_mercado="Banxico FIX (referencia demo)",
        forward_teorico=fwd.forward,
        prima_put=prima_put,
        prima_call=prima_call,
        prima_neta=prima_neta,
        coberturas_activas=coberturas_activas,
        monto_cubierto_usd=monto_cubierto,
        pct_cubierto_actual=pct_cubierto,
        strike_promedio=strike_prom,
        volumen_mensual_usd=volumen_mensual,
        justificacion=justificacion,
        prospect_id=None,
    )


# ---------------------------------------------------------------------------
# Public API — PDF generation
# ---------------------------------------------------------------------------

def generar_pdf_orden(
    datos: DatosOrden,
    output_path: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Genera el PDF de Orden de Cobertura.

    Parameters
    ----------
    datos : DatosOrden
    output_path : str, optional
        Ruta exacta del archivo de salida.  Si se omite, se construye
        automáticamente en ``output_dir`` con el nombre estándar.
    output_dir : str, optional
        Directorio de salida.  Default: ``output/ordenes/`` en el root del proyecto.

    Returns
    -------
    str
        Ruta absoluta del PDF generado.
    """
    nombre_slug = datos.empresa.replace(" ", "_").replace(".", "").replace(",", "")[:30]
    fecha_str   = datos.fecha_generacion.strftime("%Y%m%d")
    nombre_pdf  = f"Orden_Cobertura_{nombre_slug}_{fecha_str}.pdf"

    if output_path is None:
        if output_dir is None:
            root = Path(__file__).parent.parent.parent
            output_dir = str(root / "output" / "ordenes")
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / nombre_pdf)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    st = _estilos()

    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=0, rightMargin=0,
        topMargin=0, bottomMargin=0,
    )
    doc.addPageTemplates(_crear_plantillas(doc))

    story: list = []

    # --- Página 1: portada (fondo azul) ---
    story += _pagina_portada(datos, st)
    story.append(NextPageTemplate("interior"))
    story.append(PageBreak())

    # --- Página 2: datos de la operación ---
    story += _seccion_cliente(datos, st)
    story.append(Spacer(1, 0.3 * cm))
    story += _seccion_instrumento(datos, st)
    story.append(Spacer(1, 0.3 * cm))
    story += _seccion_mercado(datos, st)
    story.append(Spacer(1, 0.3 * cm))
    story += _seccion_precio_teorico(datos, st)
    story += _seccion_disclaimer(st)
    story.append(PageBreak())

    # --- Página 3: contexto y autorización ---
    story += _seccion_justificacion(datos, st)
    story.append(Spacer(1, 0.3 * cm))
    story += _seccion_posicion_actual(datos, st)
    story.append(Spacer(1, 0.4 * cm))
    story += _seccion_autorizacion(datos, st)
    story += _seccion_disclaimer(st)

    doc.build(story)
    logger.info("PDF de Orden de Cobertura generado: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Internal helpers — market & vol
# ---------------------------------------------------------------------------

_SPOT_FALLBACK = 20.0
_PAR = "USDMXN"


def _obtener_mercado() -> tuple[float, float, float, float, str, str]:
    """Retorna (spot, bid, ask, spread, hora, fuente).

    Intenta Banxico; si falla usa el último registro de BD; si falla usa fallback.
    """
    import math
    from datetime import datetime

    try:
        from core.data.market_data import fetch_usdmxn_banxico
        df = fetch_usdmxn_banxico(days=5)
        spot = float(df["tipo_cambio"].iloc[-1])
        hora = datetime.now().strftime("%H:%M:%S")
        spread = spot * 0.0015          # spread estimado ~15 pips
        bid    = round(spot - spread / 2, 4)
        ask    = round(spot + spread / 2, 4)
        return spot, bid, ask, round(spread, 4), hora, "Banxico API"
    except Exception as exc:
        logger.debug("Banxico API no disponible: %s", exc)

    try:
        from core.database import DB_PATH, get_latest_fx_rates
        registros = get_latest_fx_rates(_PAR, n=1)
        if registros:
            r = registros[0]
            bid  = r["bid"]
            ask  = r["ask"]
            spot = (bid + ask) / 2
            return spot, bid, ask, round(ask - bid, 4), r.get("hora", "—"), r.get("source", "BD local")
    except Exception as exc:
        logger.debug("BD FX no disponible: %s", exc)

    hora = datetime.now().strftime("%H:%M:%S")
    spot = _SPOT_FALLBACK
    bid  = spot - 0.02
    ask  = spot + 0.02
    return spot, bid, ask, 0.04, hora, "Fallback (sin conexión)"


def _calcular_vol_30d(db_path=None) -> float:
    """Volatilidad 30d anualizada (%) desde registros de fx_rates en BD."""
    import math
    try:
        from core.database import DB_PATH, get_latest_fx_rates
        if db_path is None:
            db_path = DB_PATH
        registros = get_latest_fx_rates(_PAR, n=32, db_path=db_path)
        if len(registros) < 2:
            return 12.0
        bids = [r["bid"] for r in registros if r.get("bid")]
        returns = [
            math.log(bids[i] / bids[i + 1])
            for i in range(len(bids) - 1)
            if bids[i] > 0 and bids[i + 1] > 0
        ]
        if not returns:
            return 12.0
        n    = len(returns)
        mean = sum(returns) / n
        var  = sum((r - mean) ** 2 for r in returns) / n
        return round(math.sqrt(var) * math.sqrt(252) * 100, 2)
    except Exception:
        return 12.0
