"""
Dashboard interno HedgePoint MX — FastAPI + HTML inline con Jinja2.

Rutas:
    GET  /              — Lista de clientes con KPIs
    GET  /cliente/{id}  — Detalle de cliente + coberturas
    POST /cliente/{id}/reporte — Genera PDF y redirige
    GET  /mercado       — Resumen de mercado + últimas cotizaciones
    GET  /login         — Formulario de login
    POST /login         — Autentica y setea cookie
    GET  /logout        — Borra cookie

Autenticación: cookie "session_token" = sha256(DASHBOARD_PASSWORD).
Ejecución: python -m agents.reports.dashboard
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import Cookie, FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import (
    DB_PATH,
    get_active_hedges,
    get_all_prospects,
    get_client_hedges,
    get_expiring_hedges,
    get_latest_fx_rates,
    get_prospect,
)
from core.models.hedge_pnl import resumen_pnl_cliente

logger = logging.getLogger(__name__)

app = FastAPI(title="HedgePoint MX — Dashboard Interno", docs_url=None, redoc_url=None)

_DEFAULT_PWD = "hedgepoint2026"


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _token_esperado() -> str:
    pwd = os.getenv("DASHBOARD_PASSWORD", _DEFAULT_PWD)
    return hashlib.sha256(pwd.encode()).hexdigest()


def _sesion_valida(session_token: Optional[str]) -> bool:
    return session_token == _token_esperado()


def _redirect_login() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def _db() -> Path:
    """Retorna el DB_PATH actual del módulo (resoluble en tiempo de ejecución)."""
    import agents.reports.dashboard as _self
    return _self.DB_PATH


# ---------------------------------------------------------------------------
# Decrypt helper
# ---------------------------------------------------------------------------

def _decrypt(value: str, fallback: str) -> str:
    try:
        from core.security.anonymizer import FieldEncryptor
        return FieldEncryptor().decrypt(value)
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# CSS + Layout base
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    background: #f3f4f6; color: #1f2937; font-size: 14px; line-height: 1.5;
}
header {
    background: #1a365d; color: #fff; padding: 12px 24px;
    display: flex; align-items: center; justify-content: space-between;
}
header h1 { font-size: 18px; font-weight: 700; }
nav a {
    color: #a8c4e0; text-decoration: none; margin-left: 20px; font-size: 13px;
}
nav a:hover { color: #fff; }
.container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }
h2 { font-size: 18px; color: #1a365d; margin-bottom: 16px; }
h3 { font-size: 14px; color: #2a4f82; margin: 20px 0 8px; }
table {
    width: 100%; border-collapse: collapse; background: #fff;
    border-radius: 6px; overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 24px;
}
th {
    background: #1a365d; color: #fff; padding: 10px 12px;
    text-align: left; font-size: 12px; font-weight: 600; white-space: nowrap;
}
td { padding: 9px 12px; border-bottom: 1px solid #e5e7eb; font-size: 13px; }
tr:last-child td { border-bottom: none; }
tr:nth-child(even) td { background: #f9fafb; }
tr:hover td { background: #eff6ff; }
a { color: #1a365d; text-decoration: none; }
a:hover { text-decoration: underline; }
.badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; font-weight: 600;
}
.badge-verde  { background: #d1fae5; color: #065f46; }
.badge-rojo   { background: #fee2e2; color: #991b1b; }
.badge-azul   { background: #dbeafe; color: #1e40af; }
.badge-gris   { background: #f3f4f6; color: #374151; }
.kpi-row {
    display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap;
}
.kpi {
    background: #fff; border-radius: 6px; padding: 16px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,.1); flex: 1; min-width: 140px;
}
.kpi-val { font-size: 22px; font-weight: 700; color: #2d8659; }
.kpi-val.neg { color: #c0392b; }
.kpi-lbl { font-size: 11px; color: #6b7280; margin-top: 2px; }
.flash {
    background: #d1fae5; color: #065f46; border: 1px solid #6ee7b7;
    border-radius: 6px; padding: 10px 16px; margin-bottom: 16px;
    font-size: 13px;
}
.flash.error { background: #fee2e2; color: #991b1b; border-color: #fca5a5; }
.btn {
    background: #1a365d; color: #fff; border: none; padding: 9px 20px;
    border-radius: 5px; cursor: pointer; font-size: 13px; font-weight: 600;
}
.btn:hover { background: #2a4f82; }
.btn-verde { background: #2d8659; }
.btn-verde:hover { background: #236b47; }
.login-box {
    max-width: 360px; margin: 80px auto; background: #fff;
    border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,.15);
    padding: 36px 32px;
}
.login-box h2 { text-align: center; margin-bottom: 24px; }
.field { margin-bottom: 16px; }
.field label { display: block; font-size: 12px; font-weight: 600;
               color: #374151; margin-bottom: 4px; }
.field input[type=password], .field input[type=text] {
    width: 100%; padding: 9px 12px; border: 1px solid #d1d5db;
    border-radius: 5px; font-size: 13px;
}
.field input:focus { outline: none; border-color: #1a365d; }
footer {
    text-align: center; padding: 20px; color: #9ca3af; font-size: 11px;
    border-top: 1px solid #e5e7eb; margin-top: 32px;
}
@media (max-width: 600px) {
    .kpi-row { flex-direction: column; }
    table { font-size: 12px; }
    th, td { padding: 7px 8px; }
}
"""

_HEADER = """
<header>
  <h1>HedgePoint MX &mdash; Panel Interno</h1>
  <nav>
    <a href="/">Clientes</a>
    <a href="/mercado">Mercado</a>
    <a href="/logout">Salir</a>
  </nav>
</header>
"""

_FOOTER = """
<footer>HedgePoint MX &mdash; Dashboard interno. No compartir.</footer>
"""


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — HedgePoint MX</title>
  <style>{_CSS}</style>
</head>
<body>
{_HEADER}
<div class="container">
{body}
</div>
{_FOOTER}
</body>
</html>"""


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def get_login(error: str = ""):
    err_html = f'<p class="flash error">{error}</p>' if error else ""
    body = f"""
<div class="login-box">
  <h2>HedgePoint MX</h2>
  {err_html}
  <form method="post" action="/login">
    <div class="field">
      <label>Contrasena</label>
      <input type="password" name="password" autofocus placeholder="Ingresa la contrasena">
    </div>
    <button type="submit" class="btn" style="width:100%">Entrar</button>
  </form>
</div>"""
    return HTMLResponse(_page("Login", body))


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@app.post("/login")
def post_login(password: str = Form(...)):
    expected_pwd = os.getenv("DASHBOARD_PASSWORD", _DEFAULT_PWD)
    if password == expected_pwd:
        resp = RedirectResponse(url="/", status_code=302)
        resp.set_cookie("session_token", _token_esperado(), httponly=True, samesite="lax")
        return resp
    return RedirectResponse(url="/login?error=Contrasena+incorrecta", status_code=302)


# ---------------------------------------------------------------------------
# GET /logout
# ---------------------------------------------------------------------------

@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("session_token")
    return resp


# ---------------------------------------------------------------------------
# GET /  — Lista de clientes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    session_token: Optional[str] = Cookie(default=None),
    flash: str = "",
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    prospects = get_all_prospects(db_path=db_path) or []

    # Spot actual
    ultimos_fx = get_latest_fx_rates("USDMXN", n=1, db_path=db_path)
    spot = ultimos_fx[0]["bid"] if ultimos_fx else None
    fecha_fx = ultimos_fx[0]["fecha"] if ultimos_fx else "—"

    filas = []
    for p in prospects:
        pid = p["id"]
        empresa = _decrypt(p.get("empresa_enc", ""), f"Cliente {pid}")
        coberturas = get_client_hedges(pid, estado="activa", db_path=db_path)
        n_cob = len(coberturas)

        spot_val = spot if spot else 20.0
        try:
            pnl = resumen_pnl_cliente(pid, spot_val, db_path=db_path)
            mtm = pnl.get("total_mtm_mxn", 0.0)
        except Exception:
            mtm = 0.0

        mtm_clase = "badge-verde" if mtm >= 0 else "badge-rojo"
        status = p.get("status", "—")
        status_clase = "badge-azul" if status == "diagnosticado" else "badge-gris"

        filas.append(f"""
<tr>
  <td><a href="/cliente/{pid}">{pid}</a></td>
  <td><a href="/cliente/{pid}">{empresa}</a></td>
  <td>{p.get("sector", "—")}</td>
  <td>${p.get("volumen_usd_mensual", 0):,.0f}</td>
  <td style="text-align:center">{n_cob}</td>
  <td><span class="badge {mtm_clase}">${mtm:,.0f}</span></td>
  <td><span class="badge {status_clase}">{status}</span></td>
</tr>""")

    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    spot_html = (
        f'<p style="color:#6b7280;font-size:12px;margin-top:8px">'
        f'Spot USD/MXN: <strong>${spot:.4f}</strong> &mdash; '
        f'Ultima actualizacion: {fecha_fx}</p>'
    ) if spot else ""

    body = f"""
{flash_html}
<h2>Clientes</h2>
<table>
  <thead>
    <tr>
      <th>ID</th><th>Empresa</th><th>Sector</th>
      <th>Volumen USD/mes</th><th>Cob. activas</th>
      <th>MTM total MXN</th><th>Status</th>
    </tr>
  </thead>
  <tbody>
    {"".join(filas) if filas else '<tr><td colspan="7" style="text-align:center;color:#9ca3af">Sin clientes registrados</td></tr>'}
  </tbody>
</table>
{spot_html}"""

    return HTMLResponse(_page("Clientes", body))


# ---------------------------------------------------------------------------
# GET /cliente/{prospect_id}
# ---------------------------------------------------------------------------

@app.get("/cliente/{prospect_id}", response_class=HTMLResponse)
def detalle_cliente(
    prospect_id: int,
    session_token: Optional[str] = Cookie(default=None),
    flash: str = "",
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    prospect = get_prospect(prospect_id, db_path=db_path)
    if not prospect:
        return HTMLResponse(_page("No encontrado", "<p>Cliente no encontrado.</p>"), status_code=404)

    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {prospect_id}")
    nombre  = _decrypt(prospect.get("nombre_enc", ""), "—")

    # Spot actual
    ultimos_fx = get_latest_fx_rates("USDMXN", n=1, db_path=db_path)
    spot = ultimos_fx[0]["bid"] if ultimos_fx else 20.0

    # PnL
    try:
        pnl = resumen_pnl_cliente(prospect_id, spot, db_path=db_path)
    except Exception:
        pnl = {"coberturas": [], "total_mtm_mxn": 0.0, "total_cubierto_usd": 0.0,
               "num_coberturas": 0, "proximos_vencimientos": []}

    coberturas_pnl = pnl.get("coberturas", [])
    proximos = get_expiring_hedges(dias=30, db_path=db_path)
    proximos = [h for h in proximos if h["prospect_id"] == prospect_id]

    flash_html = f'<div class="flash">{flash}</div>' if flash else ""

    # Sección 1: datos del cliente
    sec_datos = f"""
<h3>Datos del cliente</h3>
<table>
  <thead><tr><th>Campo</th><th>Valor</th></tr></thead>
  <tbody>
    <tr><td>ID</td><td>{prospect_id}</td></tr>
    <tr><td>Empresa</td><td>{empresa}</td></tr>
    <tr><td>Contacto</td><td>{nombre}</td></tr>
    <tr><td>Sector</td><td>{prospect.get("sector", "—")}</td></tr>
    <tr><td>Volumen USD/mes</td><td>${prospect.get("volumen_usd_mensual", 0):,.0f}</td></tr>
    <tr><td>Margen utilidad</td><td>{prospect.get("margen_utilidad", 0)*100:.1f}%</td></tr>
    <tr><td>Status</td><td>{prospect.get("status", "—")}</td></tr>
  </tbody>
</table>"""

    # KPIs resumen
    mtm = pnl.get("total_mtm_mxn", 0.0)
    mtm_clase = "" if mtm >= 0 else "neg"
    sec_kpis = f"""
<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-val">${spot:.4f}</div>
    <div class="kpi-lbl">Spot USD/MXN</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">${pnl.get("total_cubierto_usd", 0):,.0f}</div>
    <div class="kpi-lbl">Total cubierto USD</div>
  </div>
  <div class="kpi">
    <div class="kpi-val {mtm_clase}">${mtm:,.0f}</div>
    <div class="kpi-lbl">MTM total MXN</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{pnl.get("num_coberturas", 0)}</div>
    <div class="kpi-lbl">Coberturas activas</div>
  </div>
</div>"""

    # Sección 2: coberturas
    filas_cob = []
    for c in coberturas_pnl:
        pnl_clase = "badge-verde" if c.pnl_vs_spot_mxn >= 0 else "badge-rojo"
        filas_cob.append(f"""
<tr>
  <td>{c.tipo.upper()}</td>
  <td>${c.monto_usd:,.0f}</td>
  <td>${c.strike:.4f}</td>
  <td style="text-align:center">{c.dias_restantes}</td>
  <td>${c.mtm_mxn:,.0f}</td>
  <td><span class="badge {pnl_clase}">${c.pnl_vs_spot_mxn:,.0f}</span></td>
</tr>""")

    sin_cob = '<tr><td colspan="6" style="text-align:center;color:#9ca3af">Sin coberturas activas</td></tr>'
    sec_coberturas = f"""
<h3>Coberturas activas</h3>
<table>
  <thead>
    <tr>
      <th>Tipo</th><th>Monto USD</th><th>Strike</th>
      <th>Dias rest.</th><th>MTM MXN</th><th>Ahorro vs Spot</th>
    </tr>
  </thead>
  <tbody>{"".join(filas_cob) if filas_cob else sin_cob}</tbody>
</table>"""

    # Sección 3: próximos vencimientos
    filas_venc = []
    today = date.today()
    for h in proximos:
        try:
            venc = date.fromisoformat(h["fecha_vencimiento"])
            dias = max((venc - today).days, 0)
        except Exception:
            dias = "—"
        filas_venc.append(f"""
<tr>
  <td>{h.get("tipo", "").upper()}</td>
  <td>${h.get("monto_usd", 0):,.0f}</td>
  <td>${h.get("strike", 0):.4f}</td>
  <td>{h.get("fecha_vencimiento", "")}</td>
  <td style="text-align:center">{dias}</td>
</tr>""")

    sin_venc = '<tr><td colspan="5" style="text-align:center;color:#9ca3af">Sin vencimientos en 30 dias</td></tr>'
    sec_vencimientos = f"""
<h3>Proximos vencimientos (30 dias)</h3>
<table>
  <thead>
    <tr>
      <th>Tipo</th><th>Monto USD</th><th>Strike</th>
      <th>Fecha vencimiento</th><th>Dias restantes</th>
    </tr>
  </thead>
  <tbody>{"".join(filas_venc) if filas_venc else sin_venc}</tbody>
</table>"""

    # Botón generar reporte
    sec_accion = f"""
<form method="post" action="/cliente/{prospect_id}/reporte">
  <button type="submit" class="btn btn-verde">Generar reporte ahora</button>
</form>"""

    body = f"""
{flash_html}
<h2>{empresa}</h2>
{sec_kpis}
{sec_datos}
{sec_coberturas}
{sec_vencimientos}
{sec_accion}"""

    return HTMLResponse(_page(empresa, body))


# ---------------------------------------------------------------------------
# POST /cliente/{prospect_id}/reporte
# ---------------------------------------------------------------------------

@app.post("/cliente/{prospect_id}/reporte")
def generar_reporte(
    prospect_id: int,
    session_token: Optional[str] = Cookie(default=None),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()

    try:
        from agents.reports.report_generator import (
            generar_datos_reporte,
            generar_pdf_reporte,
        )
        datos = generar_datos_reporte(prospect_id, db_path=db_path)
        datos["_db_path"] = db_path
        path_pdf = generar_pdf_reporte(datos)
        nombre_archivo = Path(path_pdf).name
        flash_msg = f"Reporte generado: {path_pdf}"
    except Exception as exc:
        logger.exception("Error generando reporte para prospect_id=%d", prospect_id)
        flash_msg = f"Error al generar reporte: {exc}"

    import urllib.parse
    encoded = urllib.parse.quote(flash_msg)
    return RedirectResponse(
        url=f"/cliente/{prospect_id}?flash={encoded}",
        status_code=302,
    )


# ---------------------------------------------------------------------------
# GET /mercado
# ---------------------------------------------------------------------------

@app.get("/mercado", response_class=HTMLResponse)
def mercado(
    session_token: Optional[str] = Cookie(default=None),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    ultimos_fx = get_latest_fx_rates("USDMXN", n=10, db_path=db_path)

    if ultimos_fx:
        from agents.reports.report_generator import _calcular_resumen_mercado
        spot = ultimos_fx[0]["bid"]
        resumen = _calcular_resumen_mercado(ultimos_fx, spot)
    else:
        spot = None
        resumen = {"spot": 0, "variacion_semanal": 0, "volatilidad_30d": 0}

    var = resumen["variacion_semanal"]
    var_clase = "" if var <= 0 else "neg"

    kpis_html = f"""
<div class="kpi-row">
  <div class="kpi">
    <div class="kpi-val">${resumen["spot"]:.4f}</div>
    <div class="kpi-lbl">Spot USD/MXN</div>
  </div>
  <div class="kpi">
    <div class="kpi-val {var_clase}">{var:+.2f}%</div>
    <div class="kpi-lbl">Variacion semanal</div>
  </div>
  <div class="kpi">
    <div class="kpi-val">{resumen["volatilidad_30d"]:.2f}%</div>
    <div class="kpi-lbl">Volatilidad 30d (anualizada)</div>
  </div>
</div>"""

    filas_fx = []
    for r in ultimos_fx:
        filas_fx.append(f"""
<tr>
  <td>{r.get("fecha", "")}</td>
  <td>{r.get("hora", "")}</td>
  <td>${r.get("bid", 0):.4f}</td>
  <td>${r.get("ask", 0):.4f}</td>
  <td>{r.get("source", "")}</td>
</tr>""")

    sin_fx = '<tr><td colspan="5" style="text-align:center;color:#9ca3af">Sin datos de mercado</td></tr>'
    tabla_fx = f"""
<h3>Ultimas 10 cotizaciones USDMXN</h3>
<table>
  <thead>
    <tr><th>Fecha</th><th>Hora</th><th>Bid</th><th>Ask</th><th>Fuente</th></tr>
  </thead>
  <tbody>{"".join(filas_fx) if filas_fx else sin_fx}</tbody>
</table>"""

    body = f"""
<h2>Resumen de Mercado</h2>
{kpis_html}
{tabla_fx}"""

    return HTMLResponse(_page("Mercado", body))


# ---------------------------------------------------------------------------
# Entry point directo: python -m agents.reports.dashboard
# ---------------------------------------------------------------------------

def create_app(db_path: Path = DB_PATH) -> FastAPI:
    """Factory para tests — permite inyectar db_path."""
    # Sobreescribir el db_path por defecto en todas las rutas no es trivial
    # con FastAPI sin DI explícita; los tests usan override de dependencias.
    return app


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run("agents.reports.dashboard:app", host="0.0.0.0", port=8000, reload=False)
