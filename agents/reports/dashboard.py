"""
Dashboard HedgePoint MX — FastAPI + HTML inline.

Rutas consultor (cookie auth):
    GET  /                        — Dashboard: KPIs, alertas, vencimientos
    GET  /clientes                — Lista de clientes con barras de cobertura
    GET  /clientes/nuevo          — Formulario alta de nuevo cliente
    POST /clientes/nuevo          — Guarda nuevo cliente
    GET  /registro/documento      — Subir PDF/imagen para análisis automático
    POST /registro/documento      — Procesa documento y redirige a /registro pre-llenado
    GET  /registro                — Formulario de nueva cobertura (acepta pre-fill via query params)
    POST /registro                — Guarda cobertura
    GET  /estrategia/{id}         — Estrategia escalonada de un cliente
    POST /estrategia/{id}         — Guarda cambios en estrategia
    GET  /mercado                 — Resumen de mercado + cotizaciones
    GET  /cliente/{id}            — Detalle de cliente con link de portal
    POST /cliente/{id}/reporte    — Genera PDF

Rutas cliente (token en query param):
    GET  /portal/{id}             — Resumen del cliente
    GET  /portal/{id}/coberturas  — Coberturas activas + historial + botón upload
    GET  /portal/{id}/upload      — Formulario de upload de documento
    POST /portal/{id}/upload      — Procesa documento y registra cobertura automáticamente
    GET  /portal/{id}/estrategia  — Vista read-only de estrategia

Auth consultor: cookie "session_token" = sha256(DASHBOARD_PASSWORD).
Auth cliente:   ?token=sha256(str(prospect_id) + DASHBOARD_PASSWORD)
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import requests as _http
from fastapi import Cookie, FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from core.database import (
    DB_PATH,
    get_active_hedges,
    get_all_prospects,
    get_client_hedges,
    get_expiring_hedges,
    get_latest_fx_rates,
    get_prospect,
    insert_hedge,
    insert_hedge_strategy,
    get_client_strategy,
    get_strategy_levels,
    update_hedge_strategy,
    update_level_status,
    insert_hedge_pending,
    get_pending_hedges,
    get_client_pending_hedges,
    get_pending_hedge,
    update_pending_status,
)
from core.models.hedge_pnl import resumen_pnl_cliente, calcular_pnl_todos_clientes

logger = logging.getLogger(__name__)

app = FastAPI(title="HedgePoint MX", docs_url=None, redoc_url=None)


def _prefix(path: str) -> str:
    return "/dashboard" + path

_DEFAULT_PWD = "hedgepoint2026"

_BANCOS = ["Banco Base", "Monex", "BBVA", "Banorte", "Citibanamex", "Otro"]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _token_esperado() -> str:
    pwd = os.getenv("DASHBOARD_PASSWORD", _DEFAULT_PWD)
    return hashlib.sha256(pwd.encode()).hexdigest()


def _token_cliente(prospect_id: int) -> str:
    pwd = os.getenv("DASHBOARD_PASSWORD", _DEFAULT_PWD)
    raw = str(prospect_id) + pwd
    return hashlib.sha256(raw.encode()).hexdigest()


def _sesion_valida(session_token: Optional[str]) -> bool:
    return session_token == _token_esperado()


def _portal_valido(prospect_id: int, token: Optional[str]) -> bool:
    return token == _token_cliente(prospect_id)


def _redirect_login() -> RedirectResponse:
    return RedirectResponse(url=_prefix("/login"), status_code=302)


def _db() -> Path:
    import agents.reports.dashboard as _self
    return _self.DB_PATH


# ---------------------------------------------------------------------------
# Decrypt helper
# ---------------------------------------------------------------------------

def _decrypt(value: str, fallback: str) -> str:
    # If no encryption key is set, or the value looks like plain text
    # (base64 ciphertext is always longer than 20 chars and contains only
    # base64 alphabet characters), return the value as-is.
    if not value:
        return fallback
    import re
    if len(value) < 20 or not re.fullmatch(r'[A-Za-z0-9+/=]+', value):
        return value
    try:
        from core.security.anonymizer import FieldEncryptor
        return FieldEncryptor().decrypt(value)
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --sidebar-w: 220px;
  --sidebar-bg: #1A1A1A;
  --sidebar-txt: #A8A8A8;
  --sidebar-active: #FFFFFF;
  --sidebar-active-bg: rgba(255,255,255,0.08);
  --accent: #4F7EF7;
  --accent-green: #22C55E;
  --accent-orange: #F59E0B;
  --accent-red: #EF4444;
  --bg: #FAFAF8;
  --card-bg: #FFFFFF;
  --border: rgba(0,0,0,0.08);
  --text: #111827;
  --muted: #6B7280;
  --mono: 'JetBrains Mono', monospace;
}

body {
  font-family: 'DM Sans', -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  line-height: 1.5;
  min-height: 100vh;
}

/* ── Sidebar ── */
.sidebar {
  position: fixed;
  top: 0; left: 0;
  width: var(--sidebar-w);
  height: 100vh;
  background: var(--sidebar-bg);
  display: flex;
  flex-direction: column;
  z-index: 100;
  overflow-y: auto;
}
.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 24px 20px 20px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.logo-mark {
  width: 34px; height: 34px;
  background: var(--accent);
  border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 14px; color: #fff;
  flex-shrink: 0;
}
.logo-text { font-size: 13px; font-weight: 600; color: #fff; line-height: 1.3; }
.logo-sub  { font-size: 10px; color: var(--sidebar-txt); font-weight: 400; }

.sidebar-nav { flex: 1; padding: 12px 10px; }
.nav-section {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: rgba(255,255,255,0.3);
  text-transform: uppercase;
  padding: 16px 10px 6px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border-radius: 8px;
  color: var(--sidebar-txt);
  text-decoration: none;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s, color 0.15s;
  margin-bottom: 2px;
}
.nav-item:hover { background: rgba(255,255,255,0.06); color: #fff; }
.nav-item.active { background: var(--sidebar-active-bg); color: var(--sidebar-active); }
.nav-item .ti { font-size: 16px; width: 18px; text-align: center; }

.sidebar-footer {
  padding: 16px 10px;
  border-top: 1px solid rgba(255,255,255,0.06);
}
.role-toggle {
  display: flex;
  background: rgba(255,255,255,0.06);
  border-radius: 8px;
  padding: 3px;
  margin-bottom: 10px;
}
.role-btn {
  flex: 1;
  text-align: center;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  color: var(--sidebar-txt);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  text-decoration: none;
}
.role-btn.active { background: var(--accent); color: #fff; }

/* ── Main ── */
.main {
  margin-left: var(--sidebar-w);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 32px;
  border-bottom: 1px solid var(--border);
  background: var(--card-bg);
}
.page-title { font-size: 17px; font-weight: 700; }
.topbar-actions { display: flex; align-items: center; gap: 12px; }
.content { padding: 28px 32px; flex: 1; }

/* ── Cards ── */
.card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  padding: 20px 24px;
  margin-bottom: 20px;
}
.card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 7px;
}

/* ── Metric cards ── */
.metric-row { display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
.metric-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 14px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  padding: 20px 22px 18px;
  flex: 1;
  min-width: 150px;
  position: relative;
  overflow: hidden;
}
.metric-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: var(--m-color, var(--accent));
  border-radius: 14px 14px 0 0;
}
.metric-val {
  font-family: var(--mono);
  font-size: 22px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 4px;
}
.metric-lbl { font-size: 12px; color: var(--muted); font-weight: 500; }
.metric-sub { font-size: 11px; color: var(--muted); margin-top: 4px; }

/* ── Tables ── */
.table-wrap {
  overflow-x: auto;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--card-bg);
  margin-bottom: 20px;
}
table { width: 100%; border-collapse: collapse; }
th {
  padding: 10px 14px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  background: var(--card-bg);
}
td {
  padding: 11px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: #F9F9F7; }
.num { font-family: var(--mono); font-size: 12.5px; }

/* ── Badges ── */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.badge-green  { background: #DCFCE7; color: #15803D; }
.badge-red    { background: #FEE2E2; color: #B91C1C; }
.badge-blue   { background: #DBEAFE; color: #1D4ED8; }
.badge-orange { background: #FEF3C7; color: #B45309; }
.badge-gray   { background: #F3F4F6; color: #374151; }
.badge-purple { background: #EDE9FE; color: #6D28D9; }

/* ── Progress bar ── */
.progress-wrap { width: 100%; }
.progress-bar-bg {
  background: #F3F4F6;
  border-radius: 6px;
  height: 7px;
  overflow: hidden;
  position: relative;
}
.progress-bar-fill {
  height: 100%;
  border-radius: 6px;
  transition: width 0.4s ease;
}
.progress-label { font-size: 11px; color: var(--muted); margin-top: 3px; }

/* ── Alert strip ── */
.alert-strip {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 13px;
  margin-bottom: 10px;
}
.alert-warn { background: #FFFBEB; border: 1px solid #FDE68A; }
.alert-info { background: #EFF6FF; border: 1px solid #BFDBFE; }
.alert-danger { background: #FEF2F2; border: 1px solid #FECACA; }

/* ── Forms ── */
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field label { font-size: 12px; font-weight: 600; color: #374151; }
.field input, .field select, .field textarea {
  padding: 9px 12px;
  border: 1px solid #D1D5DB;
  border-radius: 8px;
  font-size: 13px;
  font-family: inherit;
  background: #fff;
  color: var(--text);
  transition: border-color 0.15s;
}
.field input:focus, .field select:focus, .field textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(79,126,247,0.1);
}
.field-full { grid-column: 1 / -1; }

/* ── Buttons ── */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border-radius: 8px;
  border: none;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  transition: opacity 0.15s;
}
.btn:hover { opacity: 0.88; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-green   { background: var(--accent-green); color: #fff; }
.btn-outline {
  background: transparent;
  color: var(--text);
  border: 1px solid var(--border);
}

/* ── Login ── */
.login-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
}
.login-box {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 40px 36px;
  width: 100%;
  max-width: 380px;
  box-shadow: 0 4px 24px rgba(0,0,0,0.07);
}
.login-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 28px;
  justify-content: center;
}

/* ── Flash ── */
.flash {
  padding: 11px 16px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 16px;
}
.flash-ok    { background: #DCFCE7; color: #15803D; border: 1px solid #BBF7D0; }
.flash-error { background: #FEE2E2; color: #B91C1C; border: 1px solid #FECACA; }

/* ── Levels timeline ── */
.level-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
}
.level-row:last-child { border-bottom: none; }
.level-dot {
  width: 28px; height: 28px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 700;
  flex-shrink: 0;
  margin-top: 2px;
}
.dot-done    { background: var(--accent-green); color: #fff; }
.dot-pending { background: #E5E7EB; color: var(--muted); }
.dot-cancel  { background: #FEE2E2; color: var(--accent-red); }
.level-info { flex: 1; }
.level-name { font-weight: 600; font-size: 13px; margin-bottom: 2px; }
.level-meta { font-size: 12px; color: var(--muted); }

/* ── Hamburger / Mobile ── */
.hamburger {
  display: none;
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px;
  color: var(--text);
}
@media (max-width: 768px) {
  .sidebar { transform: translateX(-100%); transition: transform 0.25s; }
  .sidebar.open { transform: translateX(0); }
  .main { margin-left: 0; }
  .hamburger { display: flex; }
  .content { padding: 20px 16px; }
  .topbar { padding: 14px 16px; }
  .metric-row { gap: 10px; }
  .form-grid { grid-template-columns: 1fr; }
  .field-full { grid-column: 1; }
}
"""

_TABLER_CDN = (
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/'
    '@tabler/icons-webfont@3.6.0/dist/tabler-icons.min.css">'
)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _sidebar_html(active: str, role: str = "consultor", portal_pid: Optional[int] = None) -> str:
    consultor_active = "active" if role == "consultor" else ""
    cliente_active   = "active" if role == "cliente" else ""

    def _item(icon: str, label: str, href: str, key: str) -> str:
        cls = "active" if active == key else ""
        return (
            f'<a href="{href}" class="nav-item {cls}">'
            f'<i class="ti ti-{icon}"></i>{label}</a>'
        )

    if role == "consultor":
        try:
            _n_pending = len(get_pending_hedges(estado="pendiente", db_path=_db()))
        except Exception:
            _n_pending = 0
        _pending_label = f'Pendientes <span style="background:var(--accent-orange);color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;margin-left:4px">{_n_pending}</span>' if _n_pending else "Pendientes"
        nav_items = f"""
    <div class="nav-section">Principal</div>
    {_item("layout-dashboard", "Dashboard", "/", "dashboard")}
    {_item("users", "Clientes", "/clientes", "clientes")}
    {_item("trending-up", "Mercado", "/mercado", "mercado")}
    <div class="nav-section">Operaciones</div>
    {_item("plus-circle", "Registrar Cobertura", "/registro", "registro")}
    <a href="/dashboard/pendientes" class="nav-item {'active' if active == 'pendientes' else ''}">\
<i class="ti ti-clock"></i>{_pending_label}</a>"""
    else:
        # Vista cliente: rutas del portal del primer cliente disponible
        pid = portal_pid or 1
        tok = _token_cliente(pid)
        nav_items = f"""
    <div class="nav-section">Portal cliente</div>
    {_item("layout-dashboard", "Resumen", f"/portal/{pid}?token={tok}", "portal-resumen")}
    {_item("shield-check", "Coberturas", f"/portal/{pid}/coberturas?token={tok}", "portal-coberturas")}
    {_item("stairs", "Estrategia", f"/portal/{pid}/estrategia?token={tok}", "portal-estrategia")}"""

    # Toggle destino: al cambiar a Cliente, va al portal del primer cliente disponible
    first_pid = portal_pid
    if not first_pid:
        try:
            prospects = get_all_prospects(db_path=_db())
            if prospects:
                first_pid = prospects[0]["id"]
        except Exception:
            first_pid = None

    if first_pid:
        tok = _token_cliente(first_pid)
        cliente_href = f"/portal/{first_pid}?token={tok}"
        cliente_btn = f'<a href="{cliente_href}" class="role-btn {cliente_active}" target="_blank" rel="noopener">Cliente</a>'
    else:
        cliente_btn = f'<span class="role-btn {cliente_active}" title="Sin clientes registrados" style="opacity:.45;cursor:not-allowed">Cliente</span>'

    return f"""
<aside class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <div class="logo-mark">HP</div>
    <div class="logo-text">HedgePoint MX<br><span class="logo-sub">Risk Management</span></div>
  </div>
  <nav class="sidebar-nav">{nav_items}
  </nav>
  <div class="sidebar-footer">
    <div class="role-toggle">
      <a href="/dashboard/" class="role-btn {consultor_active}">Consultor</a>
      {cliente_btn}
    </div>
    <a href="/dashboard/logout" class="nav-item" style="font-size:12px">
      <i class="ti ti-logout"></i>Salir
    </a>
  </div>
</aside>"""


def _page(title: str, body: str, sidebar_active: str = "", role: str = "consultor", portal_pid: Optional[int] = None) -> str:
    sidebar = _sidebar_html(sidebar_active, role, portal_pid=portal_pid)
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — HedgePoint MX</title>
  {_TABLER_CDN}
  <style>{_CSS}</style>
</head>
<body>
{sidebar}
<div class="main" id="main">
{body}
</div>
<script>
(function(){{
  var btn = document.getElementById('hamburger');
  var sb  = document.getElementById('sidebar');
  if(btn && sb){{
    btn.addEventListener('click', function(){{ sb.classList.toggle('open'); }});
    document.addEventListener('click', function(e){{
      if(sb.classList.contains('open') && !sb.contains(e.target) && e.target !== btn)
        sb.classList.remove('open');
    }});
  }}
}})();
</script>
</body>
</html>"""


def _login_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — HedgePoint MX</title>
  {_TABLER_CDN}
  <style>{_CSS}</style>
</head>
<body>{body}</body>
</html>"""


def _topbar(title: str, subtitle: str = "", extra: str = "") -> str:
    return f"""
<div class="topbar">
  <div>
    <button class="hamburger" id="hamburger" aria-label="Menu">
      <i class="ti ti-menu-2" style="font-size:20px"></i>
    </button>
    <span class="page-title">{title}</span>
    {"<span style='color:var(--muted);font-size:13px;margin-left:10px'>" + subtitle + "</span>" if subtitle else ""}
  </div>
  <div class="topbar-actions">{extra}</div>
</div>
<div class="content">"""


def _metric_card(label: str, value: str, sub: str = "", color: str = "var(--accent)") -> str:
    return f"""
<div class="metric-card" style="--m-color:{color}">
  <div class="metric-val">{value}</div>
  <div class="metric-lbl">{label}</div>
  {"<div class='metric-sub'>" + sub + "</div>" if sub else ""}
</div>"""


def _badge(text: str, tipo: str = "gray") -> str:
    cls_map = {
        "green": "badge-green", "red": "badge-red", "blue": "badge-blue",
        "orange": "badge-orange", "gray": "badge-gray", "purple": "badge-purple",
    }
    return f'<span class="badge {cls_map.get(tipo, "badge-gray")}">{text}</span>'


def _progress_bar(pct: float, color: str = "var(--accent)") -> str:
    pct_clamped = max(0.0, min(100.0, pct))
    return f"""
<div class="progress-wrap">
  <div class="progress-bar-bg">
    <div class="progress-bar-fill" style="width:{pct_clamped:.1f}%;background:{color}"></div>
  </div>
  <div class="progress-label">{pct_clamped:.1f}%</div>
</div>"""


def _cobertura_color(pct: float, min_pct: float = 40.0) -> str:
    if pct >= 60:
        return "var(--accent)"
    if pct >= min_pct:
        return "var(--accent-orange)"
    return "var(--accent-red)"


# ---------------------------------------------------------------------------
# GET /login
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def get_login(error: str = ""):
    err_html = f'<div class="flash flash-error">{error}</div>' if error else ""
    body = f"""
<div class="login-wrap">
  <div class="login-box">
    <div class="login-logo">
      <div class="logo-mark" style="width:40px;height:40px;font-size:16px">HP</div>
      <div>
        <div style="font-weight:700;font-size:16px">HedgePoint MX</div>
        <div style="font-size:11px;color:var(--muted)">Panel de administración</div>
      </div>
    </div>
    {err_html}
    <form method="post" action="/dashboard/login">
      <div class="field" style="margin-bottom:14px">
        <label>Contraseña</label>
        <input type="password" name="password" autofocus placeholder="Ingresa la contraseña">
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;justify-content:center">
        <i class="ti ti-lock-open"></i>Entrar
      </button>
    </form>
  </div>
</div>"""
    return HTMLResponse(_login_page("Login", body))


# ---------------------------------------------------------------------------
# POST /login
# ---------------------------------------------------------------------------

@app.post("/login")
def post_login(password: str = Form(...)):
    expected_pwd = os.getenv("DASHBOARD_PASSWORD", _DEFAULT_PWD)
    if password == expected_pwd:
        resp = RedirectResponse(url=_prefix("/"), status_code=302)
        resp.set_cookie("session_token", _token_esperado(), httponly=True, samesite="lax")
        return resp
    return RedirectResponse(url=_prefix("/login?error=Contraseña+incorrecta"), status_code=302)


# ---------------------------------------------------------------------------
# GET /logout
# ---------------------------------------------------------------------------

@app.get("/logout")
def logout():
    resp = RedirectResponse(url=_prefix("/login"), status_code=302)
    resp.delete_cookie("session_token")
    return resp


# ---------------------------------------------------------------------------
# GET /  — Dashboard principal
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

    ultimos_fx = get_latest_fx_rates("USDMXN", n=1, db_path=db_path)
    spot = ultimos_fx[0]["bid"] if ultimos_fx else 20.0
    fecha_fx = ultimos_fx[0]["fecha"] if ultimos_fx else "—"

    activas = get_active_hedges(db_path=db_path)
    vencen_30 = get_expiring_hedges(dias=30, db_path=db_path)

    total_cubierto = sum(h.get("monto_usd", 0) for h in activas)
    try:
        pnl_todos = calcular_pnl_todos_clientes(spot, db_path=db_path)
        ahorro_total = sum(c.get("total_mtm_mxn", 0) for c in pnl_todos if c.get("total_mtm_mxn", 0) > 0)
    except Exception:
        ahorro_total = 0.0

    kpis = f"""
<div class="metric-row">
  {_metric_card("Clientes activos", str(len(prospects)), color="var(--accent)")}
  {_metric_card("Coberturas activas", str(len(activas)), color="var(--accent-green)")}
  {_metric_card("Volumen cubierto USD", f"${total_cubierto:,.0f}", sub=f"Spot: ${spot:.4f}", color="var(--accent-orange)")}
  {_metric_card("Ahorro total MXN", f"${ahorro_total:,.0f}", color="#A855F7")}
</div>"""

    # Alertas
    alertas_html = ""
    today = date.today()
    for h in vencen_30:
        try:
            dias = (date.fromisoformat(h["fecha_vencimiento"]) - today).days
        except Exception:
            dias = "?"
        p = get_prospect(h["prospect_id"], db_path=db_path) or {}
        emp = _decrypt(p.get("empresa_enc", ""), f"Cliente {h['prospect_id']}")
        alertas_html += f"""
<div class="alert-strip alert-warn">
  <i class="ti ti-clock" style="color:var(--accent-orange);font-size:16px;margin-top:1px"></i>
  <div><strong>{emp}</strong> — {h['tipo'].upper()} ${h['monto_usd']:,.0f} vence en <strong>{dias} días</strong> ({h['fecha_vencimiento']})</div>
</div>"""

    if not alertas_html:
        alertas_html = '<p style="color:var(--muted);font-size:13px">Sin vencimientos en los próximos 30 días.</p>'

    # Tabla vencimientos
    filas_v = []
    for h in vencen_30:
        try:
            dias = (date.fromisoformat(h["fecha_vencimiento"]) - today).days
        except Exception:
            dias = "—"
        p = get_prospect(h["prospect_id"], db_path=db_path) or {}
        emp = _decrypt(p.get("empresa_enc", ""), f"Cliente {h['prospect_id']}")
        filas_v.append(f"""<tr>
  <td><a href="/dashboard/cliente/{h['prospect_id']}" style="color:var(--accent);text-decoration:none;font-weight:500">{emp}</a></td>
  <td>{_badge(h['tipo'].upper(), 'blue')}</td>
  <td class="num">${h['monto_usd']:,.0f}</td>
  <td class="num">${h['strike']:.4f}</td>
  <td>{h['fecha_vencimiento']}</td>
  <td class="num">{dias} días</td>
</tr>""")

    sin_v = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">Sin vencimientos en 30 días</td></tr>'
    tabla_v = f"""
<div class="table-wrap">
<table>
  <thead><tr>
    <th>Cliente</th><th>Tipo</th><th>Monto USD</th><th>Strike</th><th>Vencimiento</th><th>Días rest.</th>
  </tr></thead>
  <tbody>{"".join(filas_v) if filas_v else sin_v}</tbody>
</table>
</div>"""

    flash_html = f'<div class="flash flash-ok">{flash}</div>' if flash else ""

    try:
        n_pending = len(get_pending_hedges(estado="pendiente", db_path=db_path))
    except Exception:
        n_pending = 0
    pending_alert = (
        f'<div class="alert-strip" style="background:#FFF7ED;border:1px solid #FED7AA;border-radius:10px;margin-bottom:12px">'
        f'<i class="ti ti-clock" style="color:var(--accent-orange);font-size:16px;margin-top:1px"></i>'
        f'<div>Tienes <strong>{n_pending} cobertura{"s" if n_pending != 1 else ""} pendiente{"s" if n_pending != 1 else ""} de aprobación</strong> — '
        f'<a href="/dashboard/pendientes" style="color:var(--accent-orange);font-weight:600">Revisar ahora →</a></div></div>'
    ) if n_pending else ""

    body = (
        _topbar("Dashboard", f"Spot USD/MXN: ${spot:.4f} · {fecha_fx}")
        + pending_alert
        + flash_html
        + kpis
        + '<div class="card"><div class="card-title"><i class="ti ti-bell"></i>Alertas de vencimiento</div>'
        + alertas_html
        + "</div>"
        + '<div class="card"><div class="card-title"><i class="ti ti-calendar-event"></i>Coberturas por vencer en 30 días</div>'
        + tabla_v
        + "</div></div>"
    )

    return HTMLResponse(_page("Dashboard", body, "dashboard"))


# ---------------------------------------------------------------------------
# GET /clientes
# ---------------------------------------------------------------------------

@app.get("/clientes", response_class=HTMLResponse)
def lista_clientes(session_token: Optional[str] = Cookie(default=None), flash: str = ""):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    prospects = get_all_prospects(db_path=db_path) or []
    ultimos_fx = get_latest_fx_rates("USDMXN", n=1, db_path=db_path)
    spot = ultimos_fx[0]["bid"] if ultimos_fx else 20.0

    first_pid = prospects[0]["id"] if prospects else None

    filas = []
    alertas_bajo = []
    for p in prospects:
        pid = p["id"]
        empresa = _decrypt(p.get("empresa_enc", ""), f"Cliente {pid}")
        hedges  = get_client_hedges(pid, estado="activa", db_path=db_path)
        vol_mes = p.get("volumen_usd_mensual", 0) or 1

        total_cubierto = sum(h.get("monto_usd", 0) for h in hedges)
        pct = min((total_cubierto / vol_mes) * 100, 100)

        try:
            strat = get_client_strategy(pid, db_path=db_path)
            min_pct = strat["cobertura_minima_pct"] if strat else 40.0
            movs_usados = len(hedges)
            movs_max    = strat["max_movimientos_mes"] if strat else 3
        except Exception:
            min_pct, movs_usados, movs_max = 40.0, len(hedges), 3

        color = _cobertura_color(pct, min_pct)
        bar = _progress_bar(pct, color)

        if pct < min_pct:
            alertas_bajo.append((empresa, pct, min_pct))

        sector = p.get("sector", "—")
        status = p.get("status", "—")
        sbadge = _badge(status, "blue" if status == "diagnosticado" else "gray")
        tok = _token_cliente(pid)

        filas.append(f"""<tr>
  <td><a href="/dashboard/cliente/{pid}" style="color:var(--accent);text-decoration:none;font-weight:500">{empresa}</a></td>
  <td>{sector}</td>
  <td class="num">${vol_mes:,.0f}</td>
  <td style="min-width:140px">{bar}</td>
  <td class="num">{movs_usados}/{movs_max}</td>
  <td>{sbadge}</td>
  <td><a href="/dashboard/estrategia/{pid}" style="color:var(--accent);font-size:12px">Estrategia →</a></td>
  <td><a href="/dashboard/portal/{pid}?token={tok}" title="Portal cliente" style="color:var(--muted)" target="_blank"><i class="ti ti-external-link"></i></a></td>
</tr>""")

    alertas_html = ""
    for emp, pct, min_pct in alertas_bajo:
        alertas_html += f"""
<div class="alert-strip alert-danger">
  <i class="ti ti-alert-triangle" style="color:var(--accent-red);font-size:16px"></i>
  <div><strong>{emp}</strong> — cobertura actual {pct:.1f}% está por debajo del mínimo {min_pct:.0f}%</div>
</div>"""

    flash_html = f'<div class="flash flash-ok">{flash}</div>' if flash else ""
    sin_f = '<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:20px">Sin clientes registrados</td></tr>'
    tabla = f"""
<div class="table-wrap">
<table>
  <thead><tr>
    <th>Empresa</th><th>Sector</th><th>Exposición USD/mes</th>
    <th>Cobertura actual</th><th>Movs/mes</th><th>Estado</th><th></th><th>Portal</th>
  </tr></thead>
  <tbody>{"".join(filas) if filas else sin_f}</tbody>
</table>
</div>"""

    extra = (
        '<a href="/dashboard/clientes/nuevo" class="btn btn-primary"><i class="ti ti-user-plus"></i>Nuevo cliente</a>'
        ' <a href="/dashboard/registro" class="btn btn-outline"><i class="ti ti-plus"></i>Nueva cobertura</a>'
    )
    body = (
        _topbar("Clientes", f"{len(prospects)} clientes registrados", extra)
        + flash_html
        + (alertas_html if alertas_html else "")
        + tabla
        + "</div>"
    )
    return HTMLResponse(_page("Clientes", body, "clientes", portal_pid=first_pid))


# ---------------------------------------------------------------------------
# GET /clientes/nuevo
# ---------------------------------------------------------------------------

_SECTORES = [
    "Importador", "Exportador", "Construcción", "Manufactura/maquila",
    "Logística", "Empaque/plásticos", "Agroindustria", "Otro",
]

@app.get("/clientes/nuevo", response_class=HTMLResponse)
def get_nuevo_cliente(session_token: Optional[str] = Cookie(default=None), flash: str = ""):
    if not _sesion_valida(session_token):
        return _redirect_login()

    flash_html = f'<div class="flash flash-error">{flash}</div>' if flash else ""
    opts_sectores = "".join(f'<option value="{s}">{s}</option>' for s in _SECTORES)

    form = f"""
<form method="post" action="/dashboard/clientes/nuevo">
  <div class="card" style="margin-bottom:16px">
    <div class="card-title"><i class="ti ti-user"></i>Paso 1 — Contacto</div>
    <div class="form-grid">
      <div class="field">
        <label>Nombre completo</label>
        <input type="text" name="nombre" placeholder="Carlos Mendoza" required>
      </div>
      <div class="field">
        <label>Empresa</label>
        <input type="text" name="empresa" placeholder="AceroMX S.A. de C.V." required>
      </div>
      <div class="field">
        <label>Email</label>
        <input type="email" name="email" placeholder="contacto@empresa.mx">
      </div>
      <div class="field">
        <label>Teléfono</label>
        <input type="tel" name="telefono" placeholder="8112345678">
      </div>
    </div>
  </div>

  <div class="card" style="margin-bottom:20px">
    <div class="card-title"><i class="ti ti-building"></i>Paso 2 — Negocio</div>
    <div class="form-grid">
      <div class="field">
        <label>Sector</label>
        <select name="sector" required>
          <option value="" disabled selected>Selecciona sector...</option>
          {opts_sectores}
        </select>
      </div>
      <div class="field">
        <label>Volumen USD / mes</label>
        <input type="number" name="volumen_usd_mensual" step="1000" min="0" placeholder="100000" required>
      </div>
      <div class="field">
        <label>Margen de utilidad (%)</label>
        <input type="number" name="margen_utilidad" step="0.1" min="0" max="100" placeholder="15.0">
      </div>
      <div class="field">
        <label>Moneda principal</label>
        <select name="moneda_principal">
          <option value="USD" selected>USD</option>
          <option value="EUR">EUR</option>
          <option value="MXN">MXN</option>
        </select>
      </div>
      <div class="field">
        <label>¿Usa coberturas actualmente?</label>
        <select name="usa_coberturas">
          <option value="0" selected>No</option>
          <option value="1">Sí, con banco</option>
          <option value="2">Sí, con otro intermediario</option>
        </select>
      </div>
      <div class="field">
        <label>Banco principal</label>
        <select name="banco_principal">
          <option value="">— No aplica —</option>
          {''.join(f'<option value="{b}">{b}</option>' for b in _BANCOS)}
        </select>
      </div>
    </div>
  </div>

  <div style="display:flex;gap:10px">
    <button type="submit" class="btn btn-primary"><i class="ti ti-user-check"></i>Registrar cliente</button>
    <a href="/dashboard/clientes" class="btn btn-outline">Cancelar</a>
  </div>
</form>"""

    body = (
        _topbar("Nuevo cliente", "", '<a href="/dashboard/clientes" class="btn btn-outline"><i class="ti ti-arrow-left"></i>Volver</a>')
        + flash_html
        + form
        + "</div>"
    )
    return HTMLResponse(_page("Nuevo cliente", body, "clientes"))


# ---------------------------------------------------------------------------
# POST /clientes/nuevo
# ---------------------------------------------------------------------------

@app.post("/clientes/nuevo")
def post_nuevo_cliente(
    session_token: Optional[str] = Cookie(default=None),
    nombre: str = Form(...),
    empresa: str = Form(...),
    email: Optional[str] = Form(default=None),
    telefono: Optional[str] = Form(default=None),
    sector: str = Form(...),
    volumen_usd_mensual: float = Form(...),
    margen_utilidad: Optional[float] = Form(default=None),
    moneda_principal: str = Form(default="USD"),
    usa_coberturas: int = Form(default=0),
    banco_principal: Optional[str] = Form(default=None),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    def _enc_field(value: str) -> str:
        try:
            from core.security.anonymizer import FieldEncryptor
            return FieldEncryptor().encrypt(value)
        except Exception:
            return value  # sin key, guardar en claro

    db_path = _db()
    from core.database import insert_prospect as _insert_prospect
    try:
        data: dict = {
            "nombre_enc":          _enc_field(nombre),
            "empresa_enc":         _enc_field(empresa),
            "sector":              sector,
            "volumen_usd_mensual": volumen_usd_mensual,
            "moneda_principal":    moneda_principal,
            "usa_coberturas":      usa_coberturas,
            "status":              "nuevo",
        }
        if email:
            data["email_enc"] = _enc_field(email)
        if telefono:
            data["telefono_enc"] = _enc_field(telefono)
        if margen_utilidad is not None:
            data["margen_utilidad"] = margen_utilidad / 100.0  # % → decimal
        if banco_principal:
            data["notas"] = f"Banco principal: {banco_principal}"

        _insert_prospect(data, db_path=db_path)
        msg = urllib.parse.quote(f"Cliente '{empresa}' registrado correctamente.")
        return RedirectResponse(url=_prefix(f"/clientes?flash={msg}"), status_code=302)
    except Exception as exc:
        logger.exception("Error registrando cliente")
        msg = urllib.parse.quote(f"Error: {exc}")
        return RedirectResponse(url=_prefix(f"/clientes/nuevo?flash={msg}"), status_code=302)


# ---------------------------------------------------------------------------
# Document analysis helper
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

_EXTRACT_PROMPT = (
    "Extrae todos los datos de la siguiente confirmación de cobertura cambiaria. "
    "Devuelve ÚNICAMENTE JSON válido con los campos: tipo, monto_usd, strike, "
    "strike_call, prima_pagada_mxn, fecha_inicio, fecha_vencimiento, banco_ejecutor, "
    "spot_entrada, spread_banco_centavos. Usa null para campos no encontrados. "
    "Fechas en YYYY-MM-DD. tipo debe ser: forward, put, call, o collar."
)


def _extract_from_image_bytes(data: bytes, mime_type: str) -> dict:
    """Send image bytes to Claude API and parse hedge fields."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {}
    b64 = base64.b64encode(data).decode()
    try:
        resp = _http.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64,
                        }},
                        {"type": "text", "text": _EXTRACT_PROMPT},
                    ],
                }],
            },
            timeout=45,
        )
        resp.raise_for_status()
        import json as _json
        raw = resp.json()["content"][0]["text"].strip()
        return _json.loads(raw)
    except Exception as exc:
        logger.error("_extract_from_image_bytes error: %s", exc)
        return {}


def _extract_from_pdf_bytes(data: bytes) -> dict:
    """Extract text from PDF bytes and parse with LLM."""
    try:
        import pdfplumber
        import json as _json
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join(
                page.extract_text() or "" for page in pdf.pages
            ).strip()
        if not text:
            return {}
        from core.llm_client import HedgePointLLM
        llm = HedgePointLLM()
        return llm.parse_hedge_document(text)
    except Exception as exc:
        logger.error("_extract_from_pdf_bytes error: %s", exc)
        return {}


def _fields_to_qparams(fields: dict) -> str:
    """Convert extracted field dict to URL query string."""
    parts = []
    for k, v in fields.items():
        if v is not None:
            parts.append(f"{k}={urllib.parse.quote(str(v))}")
    return "&".join(parts)


# ---------------------------------------------------------------------------
# GET /registro/documento
# ---------------------------------------------------------------------------

@app.get("/registro/documento", response_class=HTMLResponse)
def get_registro_documento(session_token: Optional[str] = Cookie(default=None)):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    prospects = get_all_prospects(db_path=db_path) or []
    opts = "".join(
        '<option value="{}">{}</option>'.format(
            p["id"], _decrypt(p.get("empresa_enc", ""), f"Cliente {p['id']}")
        )
        for p in prospects
    )

    form = f"""
<form method="post" action="/dashboard/registro/documento" enctype="multipart/form-data">
  <div class="form-grid">
    <div class="field field-full">
      <label>Cliente</label>
      <select name="prospect_id" required>{opts}</select>
    </div>
    <div class="field field-full">
      <label>Documento de confirmación de cobertura</label>
      <input type="file" name="archivo" accept=".pdf,.png,.jpg,.jpeg" required
        style="padding:8px 12px;border:1px dashed #D1D5DB;border-radius:8px;background:#FAFAF8;cursor:pointer">
      <span style="font-size:11px;color:var(--muted)">Formatos: PDF, PNG, JPG · Máximo 10 MB</span>
    </div>
  </div>
  <div style="margin-top:20px;display:flex;gap:10px">
    <button type="submit" class="btn btn-primary">
      <i class="ti ti-sparkles"></i>Analizar documento
    </button>
    <a href="/dashboard/registro" class="btn btn-outline">Ingresar manualmente</a>
  </div>
</form>"""

    body = (
        _topbar("Analizar confirmación bancaria",
                "",
                '<a href="/dashboard/registro" class="btn btn-outline"><i class="ti ti-arrow-left"></i>Manual</a>')
        + '<div class="alert-strip alert-info" style="margin-bottom:16px">'
        + '<i class="ti ti-info-circle" style="color:var(--accent);font-size:16px"></i>'
        + '<div>Sube la confirmación de cobertura que te envió tu banco y extraeremos los datos automáticamente. '
        + 'Podrás revisar y corregir antes de guardar.</div>'
        + '</div>'
        + '<div class="card">'
        + '<div class="card-title"><i class="ti ti-file-search"></i>Subir documento</div>'
        + form
        + "</div></div>"
    )
    return HTMLResponse(_page("Analizar documento", body, "registro"))


# ---------------------------------------------------------------------------
# POST /registro/documento
# ---------------------------------------------------------------------------

@app.post("/registro/documento")
async def post_registro_documento(
    session_token: Optional[str] = Cookie(default=None),
    prospect_id: int = Form(...),
    archivo: UploadFile = File(...),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    # Size guard
    data = await archivo.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        msg = urllib.parse.quote("El archivo excede el límite de 10 MB.")
        return RedirectResponse(url=_prefix(f"/registro/documento?flash={msg}"), status_code=302)

    filename = (archivo.filename or "").lower()
    mime_type = archivo.content_type or ""
    logger.info(
        "Documento recibido: filename=%r mime=%r size=%d bytes prospect_id=%d",
        filename, mime_type, len(data), prospect_id,
    )

    fields: dict = {}
    api_available = bool(os.getenv("ANTHROPIC_API_KEY", ""))

    if api_available:
        if filename.endswith(".pdf") or "pdf" in mime_type:
            fields = _extract_from_pdf_bytes(data)
        elif filename.endswith((".png", ".jpg", ".jpeg")) or mime_type.startswith("image/"):
            img_mime = mime_type if mime_type.startswith("image/") else (
                "image/png" if filename.endswith(".png") else "image/jpeg"
            )
            fields = _extract_from_image_bytes(data, img_mime)

    if not fields or not api_available:
        if not api_available:
            msg = urllib.parse.quote(
                "Análisis automático no disponible (falta ANTHROPIC_API_KEY). "
                "Ingresa los datos manualmente."
            )
        else:
            msg = urllib.parse.quote(
                "No se pudieron extraer datos del documento. Ingresa los datos manualmente."
            )
        return RedirectResponse(
            url=_prefix(f"/registro?prospect_id={prospect_id}&flash={msg}"),
            status_code=302,
        )

    fields["prospect_id"] = prospect_id
    qp = _fields_to_qparams(fields)
    logger.info("Campos extraídos para prospect_id=%d: %s", prospect_id, fields)
    return RedirectResponse(url=_prefix(f"/registro?{qp}&auto=1"), status_code=302)


# ---------------------------------------------------------------------------
# GET /registro
# ---------------------------------------------------------------------------

@app.get("/registro", response_class=HTMLResponse)
def get_registro(
    session_token: Optional[str] = Cookie(default=None),
    flash: str = "",
    # Pre-fill params from document extraction
    auto: int = 0,
    prospect_id: Optional[int] = None,
    tipo: Optional[str] = None,
    monto_usd: Optional[str] = None,
    strike: Optional[str] = None,
    strike_call: Optional[str] = None,
    prima_pagada_mxn: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_vencimiento: Optional[str] = None,
    banco_ejecutor: Optional[str] = None,
    spot_entrada: Optional[str] = None,
    spread_banco_centavos: Optional[str] = None,
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    prospects = get_all_prospects(db_path=db_path) or []
    ultimos_fx = get_latest_fx_rates("USDMXN", n=1, db_path=db_path)
    spot_actual = ultimos_fx[0]["bid"] if ultimos_fx else 20.0

    # Calcular forward teórico
    fwd_html = ""
    try:
        from core.models.pricing import calcular_forward
        fwd = calcular_forward(spot=spot_actual, dias=30)
        fwd_html = f"""
<div class="card" style="margin-bottom:16px">
  <div class="card-title"><i class="ti ti-calculator"></i>Referencia de mercado (spot actual)</div>
  <div style="display:flex;gap:32px;flex-wrap:wrap">
    <div><div class="num" style="font-size:18px">${spot_actual:.4f}</div><div class="metric-lbl">Spot USD/MXN</div></div>
    <div><div class="num" style="font-size:18px">${fwd.forward:.4f}</div><div class="metric-lbl">Forward teórico 30d</div></div>
    <div><div class="num" style="font-size:18px">{fwd.tasa_implicita*100:.2f}%</div><div class="metric-lbl">Tasa implícita</div></div>
  </div>
  <p style="font-size:11px;color:var(--muted);margin-top:10px">Compara el strike que cotiza el banco con el forward teórico para verificar el spread real cobrado.</p>
</div>"""
    except Exception:
        pass

    # Banner si viene de análisis automático
    auto_banner = ""
    if auto:
        auto_banner = """
<div class="alert-strip alert-info" style="margin-bottom:16px">
  <i class="ti ti-sparkles" style="color:var(--accent);font-size:16px"></i>
  <div><strong>Datos extraídos automáticamente</strong> — revisa y corrige antes de guardar.</div>
</div>"""

    # Helper: pre-fill value attribute if param present
    def _val(param: Optional[str], fallback: str = "") -> str:
        return f'value="{param}"' if param is not None else (f'value="{fallback}"' if fallback else "")

    def _sel(name: str, options: list[tuple[str, str]], selected_val: Optional[str]) -> str:
        opts_html = "".join(
            f'<option value="{v}" {"selected" if v == selected_val else ""}>{lbl}</option>'
            for v, lbl in options
        )
        return f'<select name="{name}" required>{opts_html}</select>'

    # Build selects
    tipo_opts = [("forward","Forward"),("put","Put"),("call","Call"),("collar","Collar")]
    banco_opts = [(b, b) for b in _BANCOS]

    pid_selected = str(prospect_id) if prospect_id else None
    opts_clientes = "".join(
        '<option value="{}" {}>{}</option>'.format(
            p["id"],
            "selected" if str(p["id"]) == pid_selected else "",
            _decrypt(p.get("empresa_enc", ""), f"Cliente {p['id']}")
        )
        for p in prospects
    )
    opts_tipo = "".join(
        f'<option value="{v}" {"selected" if v == tipo else ""}>{lbl}</option>'
        for v, lbl in tipo_opts
    )
    opts_banco = "".join(
        f'<option value="{v}" {"selected" if v == banco_ejecutor else ""}>{v}</option>'
        for v in _BANCOS
    )

    today_iso = date.today().isoformat()
    spot_val = spot_entrada or f"{spot_actual:.4f}"
    flash_html = f'<div class="flash flash-ok">{flash}</div>' if flash else ""

    form = f"""
<form method="post" action="/dashboard/registro">
<div class="form-grid">
  <div class="field field-full">
    <label>Cliente</label>
    <select name="prospect_id" required>{opts_clientes}</select>
  </div>
  <div class="field">
    <label>Tipo de cobertura</label>
    <select name="tipo" required>{opts_tipo}</select>
  </div>
  <div class="field">
    <label>Banco ejecutor</label>
    <select name="banco_ejecutor">{opts_banco}</select>
  </div>
  <div class="field">
    <label>Monto USD</label>
    <input type="number" name="monto_usd" step="1000" min="1000" placeholder="100000" {_val(monto_usd)} required>
  </div>
  <div class="field">
    <label>Strike (tipo de cambio pactado)</label>
    <input type="number" name="strike" step="0.0001" min="1" placeholder="20.1500" {_val(strike)} required>
  </div>
  <div class="field">
    <label>Strike call (solo para collar)</label>
    <input type="number" name="strike_call" step="0.0001" min="1" placeholder="21.0000" {_val(strike_call)}>
  </div>
  <div class="field">
    <label>Spot al contratar</label>
    <input type="number" name="spot_entrada" step="0.0001" {_val(spot_val)} required>
  </div>
  <div class="field">
    <label>Prima pagada MXN</label>
    <input type="number" name="prima_pagada_mxn" step="100" min="0" {_val(prima_pagada_mxn, "0")}>
  </div>
  <div class="field">
    <label>Spread cobrado (centavos/USD)</label>
    <input type="number" name="spread_banco_centavos" step="0.1" min="0" placeholder="6.0" {_val(spread_banco_centavos)}>
  </div>
  <div class="field">
    <label>% de exposición que cubre</label>
    <input type="number" name="porcentaje_cobertura" step="0.1" min="0" max="100" placeholder="40.0">
  </div>
  <div class="field">
    <label>Fecha de contratación</label>
    <input type="date" name="fecha_inicio" {_val(fecha_inicio, today_iso)} required>
  </div>
  <div class="field">
    <label>Fecha de vencimiento</label>
    <input type="date" name="fecha_vencimiento" {_val(fecha_vencimiento)} required>
  </div>
  <div class="field field-full">
    <label>Notas</label>
    <textarea name="notas" rows="2" placeholder="Condiciones especiales, contexto de mercado..."></textarea>
  </div>
</div>
<div style="margin-top:20px;display:flex;gap:10px;flex-wrap:wrap">
  <button type="submit" class="btn btn-primary"><i class="ti ti-check"></i>Registrar cobertura</button>
  <a href="/dashboard/registro/documento" class="btn btn-outline"><i class="ti ti-sparkles"></i>Analizar documento</a>
  <a href="/dashboard/clientes" class="btn btn-outline">Cancelar</a>
</div>
</form>"""

    body = (
        _topbar("Registrar cobertura")
        + flash_html
        + auto_banner
        + fwd_html
        + '<div class="card">'
        + '<div class="card-title"><i class="ti ti-file-plus"></i>Datos de la cobertura</div>'
        + form
        + "</div></div>"
    )
    return HTMLResponse(_page("Registrar cobertura", body, "registro"))


# ---------------------------------------------------------------------------
# POST /registro
# ---------------------------------------------------------------------------

@app.post("/registro")
def post_registro(
    session_token: Optional[str] = Cookie(default=None),
    prospect_id: int = Form(...),
    tipo: str = Form(...),
    monto_usd: float = Form(...),
    strike: float = Form(...),
    spot_entrada: float = Form(...),
    fecha_inicio: str = Form(...),
    fecha_vencimiento: str = Form(...),
    strike_call: Optional[float] = Form(default=None),
    prima_pagada_mxn: float = Form(default=0.0),
    banco_ejecutor: Optional[str] = Form(default=None),
    spread_banco_centavos: Optional[float] = Form(default=None),
    porcentaje_cobertura: Optional[float] = Form(default=None),
    notas: Optional[str] = Form(default=None),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    try:
        data = {
            "prospect_id": prospect_id, "tipo": tipo,
            "monto_usd": monto_usd, "strike": strike,
            "spot_entrada": spot_entrada,
            "prima_pagada_mxn": prima_pagada_mxn,
            "fecha_inicio": fecha_inicio,
            "fecha_vencimiento": fecha_vencimiento,
        }
        if strike_call:
            data["strike_call"] = strike_call
        if banco_ejecutor:
            data["banco_ejecutor"] = banco_ejecutor
        if spread_banco_centavos is not None:
            data["spread_banco_centavos"] = spread_banco_centavos
        if porcentaje_cobertura is not None:
            data["porcentaje_cobertura"] = porcentaje_cobertura
            # costo total = prima + spread * monto
            costo = prima_pagada_mxn + (spread_banco_centavos or 0) * monto_usd / 100
            data["costo_total_mxn"] = costo
        if notas:
            data["notas"] = notas

        insert_hedge(data, db_path=db_path)
        msg = urllib.parse.quote(f"Cobertura {tipo.upper()} registrada correctamente.")
        return RedirectResponse(url=_prefix(f"/cliente/{prospect_id}?flash={msg}"), status_code=302)
    except Exception as exc:
        logger.exception("Error registrando cobertura")
        msg = urllib.parse.quote(f"Error: {exc}")
        return RedirectResponse(url=_prefix(f"/registro?flash={msg}"), status_code=302)


# ---------------------------------------------------------------------------
# GET /estrategia/{prospect_id}
# ---------------------------------------------------------------------------

@app.get("/estrategia/{prospect_id}", response_class=HTMLResponse)
def vista_estrategia(
    prospect_id: int,
    session_token: Optional[str] = Cookie(default=None),
    flash: str = "",
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    prospect = get_prospect(prospect_id, db_path=db_path)
    if not prospect:
        return HTMLResponse(_page("No encontrado", "<div class='content'><p>Cliente no encontrado.</p></div>"), status_code=404)

    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {prospect_id}")
    strat   = get_client_strategy(prospect_id, db_path=db_path)
    levels  = get_strategy_levels(strat["id"], db_path=db_path) if strat else []
    hedges  = get_client_hedges(prospect_id, estado="activa", db_path=db_path)

    vol_mes = prospect.get("volumen_usd_mensual", 0) or 1
    total_cubierto = sum(h.get("monto_usd", 0) for h in hedges)
    pct_actual = min((total_cubierto / vol_mes) * 100, 100)

    # Formulario parámetros estrategia
    if strat:
        s = strat
        form_action = f"/estrategia/{prospect_id}"
        fwd_val   = lambda k, d: s.get(k, d)
        form_html = f"""
<form method="post" action="{form_action}">
<div class="form-grid">
  <div class="field">
    <label>Exposición mensual USD</label>
    <input type="number" name="exposicion_mensual_usd" value="{fwd_val('exposicion_mensual_usd',0):.0f}" required>
  </div>
  <div class="field">
    <label>Presupuesto mensual MXN</label>
    <input type="number" name="presupuesto_mensual_mxn" value="{fwd_val('presupuesto_mensual_mxn',0):.0f}" required>
  </div>
  <div class="field">
    <label>Cobertura mínima %</label>
    <input type="number" name="cobertura_minima_pct" value="{fwd_val('cobertura_minima_pct',40)}" min="0" max="100" step="1">
  </div>
  <div class="field">
    <label>Cobertura máxima %</label>
    <input type="number" name="cobertura_maxima_pct" value="{fwd_val('cobertura_maxima_pct',85)}" min="0" max="100" step="1">
  </div>
  <div class="field">
    <label>Máx. movimientos/mes</label>
    <input type="number" name="max_movimientos_mes" value="{fwd_val('max_movimientos_mes',3)}" min="1" max="10">
  </div>
  <div class="field">
    <label>Horizonte (meses)</label>
    <input type="number" name="horizonte_meses" value="{fwd_val('horizonte_meses',3)}" min="1" max="12">
  </div>
</div>
<button type="submit" class="btn btn-primary" style="margin-top:14px"><i class="ti ti-device-floppy"></i>Guardar</button>
</form>"""
    else:
        form_html = "<p style='color:var(--muted)'>Sin estrategia configurada. <a href='/registro'>Registra la primera cobertura</a> para iniciar.</p>"

    # Distribución por tipo
    tipos = {"forward": 0.0, "put": 0.0, "collar": 0.0}
    for h in hedges:
        t = h.get("tipo", "")
        if t in tipos:
            tipos[t] += h.get("monto_usd", 0)
    dist_html = ""
    for t, amt in tipos.items():
        pct_t = (amt / max(total_cubierto, 1)) * 100
        dist_html += f"""
<div style="margin-bottom:12px">
  <div style="display:flex;justify-content:space-between;margin-bottom:4px">
    <span style="font-size:13px;font-weight:500">{t.capitalize()}</span>
    <span class="num" style="font-size:12px">${amt:,.0f} ({pct_t:.1f}%)</span>
  </div>
  {_progress_bar(pct_t)}
</div>"""

    # Niveles de entrada
    levels_html = ""
    for lv in levels:
        estado = lv.get("estado", "esperando")
        dot_cls = {"ejecutado": "dot-done", "cancelado": "dot-cancel"}.get(estado, "dot-pending")
        dot_icon = {"ejecutado": "✓", "cancelado": "✕"}.get(estado, str(lv.get("orden", "?")))
        cond = lv.get("condicion_tipo", "")
        cond_val = lv.get("condicion_valor")
        cond_txt = cond.replace("_", " ").title()
        if cond_val:
            cond_txt += f" ${cond_val:.2f}"
        estado_badge = _badge(estado, {"ejecutado":"green","cancelado":"red"}.get(estado,"gray"))
        levels_html += f"""
<div class="level-row">
  <div class="level-dot {dot_cls}">{dot_icon}</div>
  <div class="level-info">
    <div class="level-name">{lv.get('nombre','')}</div>
    <div class="level-meta">
      Condición: {cond_txt} &nbsp;·&nbsp;
      Acción: {lv.get('accion_tipo','').upper()} {lv.get('accion_pct',0):.0f}% &nbsp;·&nbsp;
      {estado_badge}
    </div>
  </div>
</div>"""

    if not levels_html:
        levels_html = "<p style='color:var(--muted);font-size:13px'>Sin niveles configurados.</p>"

    # Resumen de estado
    min_pct = strat["cobertura_minima_pct"] if strat else 40.0
    max_pct = strat["cobertura_maxima_pct"] if strat else 85.0
    color = _cobertura_color(pct_actual, min_pct)
    presup = strat["presupuesto_mensual_mxn"] if strat else 0
    costo_usado = sum(h.get("costo_total_mxn", 0) or 0 for h in hedges)
    pct_presup = min((costo_usado / max(presup, 1)) * 100, 100)

    flash_html = f'<div class="flash flash-ok">{flash}</div>' if flash else ""
    body = (
        _topbar(f"Estrategia · {empresa}", "", f'<a href="/dashboard/cliente/{prospect_id}" class="btn btn-outline"><i class="ti ti-arrow-left"></i>Ver detalle</a>')
        + flash_html
        + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;flex-wrap:wrap">'
        + '<div>'
        + '<div class="card"><div class="card-title"><i class="ti ti-adjustments"></i>Parámetros</div>' + form_html + "</div>"
        + '<div class="card"><div class="card-title"><i class="ti ti-chart-bar"></i>Distribución por tipo</div>' + dist_html + "</div>"
        + "</div>"
        + '<div>'
        + f"""<div class="card">
  <div class="card-title"><i class="ti ti-target"></i>Estado de cobertura</div>
  <div style="margin-bottom:16px">
    <div style="display:flex;justify-content:space-between;margin-bottom:6px">
      <span style="font-size:13px">Cobertura actual</span>
      <span class="num" style="font-weight:600">{pct_actual:.1f}%</span>
    </div>
    {_progress_bar(pct_actual, color)}
    <div style="display:flex;justify-content:space-between;margin-top:6px">
      <span style="font-size:11px;color:var(--muted)">Mín {min_pct:.0f}%</span>
      <span style="font-size:11px;color:var(--muted)">Máx {max_pct:.0f}%</span>
    </div>
  </div>
  <div style="margin-bottom:16px">
    <div style="display:flex;justify-content:space-between;margin-bottom:6px">
      <span style="font-size:13px">Presupuesto usado</span>
      <span class="num">${costo_usado:,.0f} / ${presup:,.0f}</span>
    </div>
    {_progress_bar(pct_presup, "var(--accent-orange)")}
  </div>
  <div style="display:flex;justify-content:space-between">
    <span style="font-size:13px">Movimientos del mes</span>
    <span class="num">{len(hedges)} / {strat['max_movimientos_mes'] if strat else 3}</span>
  </div>
</div>"""
        + '<div class="card"><div class="card-title"><i class="ti ti-stairs"></i>Niveles de entrada</div>'
        + levels_html
        + "</div>"
        + "</div>"
        + "</div></div>"
    )
    return HTMLResponse(_page(f"Estrategia · {empresa}", body, ""))


# ---------------------------------------------------------------------------
# POST /estrategia/{prospect_id}
# ---------------------------------------------------------------------------

@app.post("/estrategia/{prospect_id}")
def post_estrategia(
    prospect_id: int,
    session_token: Optional[str] = Cookie(default=None),
    exposicion_mensual_usd: float = Form(...),
    presupuesto_mensual_mxn: float = Form(...),
    cobertura_minima_pct: float = Form(default=40.0),
    cobertura_maxima_pct: float = Form(default=85.0),
    max_movimientos_mes: int = Form(default=3),
    horizonte_meses: int = Form(default=3),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    data = {
        "exposicion_mensual_usd": exposicion_mensual_usd,
        "presupuesto_mensual_mxn": presupuesto_mensual_mxn,
        "cobertura_minima_pct": cobertura_minima_pct,
        "cobertura_maxima_pct": cobertura_maxima_pct,
        "max_movimientos_mes": max_movimientos_mes,
        "horizonte_meses": horizonte_meses,
    }
    try:
        strat = get_client_strategy(prospect_id, db_path=db_path)
        if strat:
            update_hedge_strategy(strat["id"], data, db_path=db_path)
        else:
            data["prospect_id"] = prospect_id
            insert_hedge_strategy(data, db_path=db_path)
        msg = urllib.parse.quote("Estrategia actualizada.")
    except Exception as exc:
        logger.exception("Error guardando estrategia")
        msg = urllib.parse.quote(f"Error: {exc}")

    return RedirectResponse(url=_prefix(f"/estrategia/{prospect_id}?flash={msg}"), status_code=302)


# ---------------------------------------------------------------------------
# GET /mercado
# ---------------------------------------------------------------------------

@app.get("/mercado", response_class=HTMLResponse)
def mercado(session_token: Optional[str] = Cookie(default=None)):
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
    var_color = "var(--accent-red)" if var > 0 else "var(--accent-green)"

    kpis = f"""
<div class="metric-row">
  {_metric_card("Spot USD/MXN", f"${resumen['spot']:.4f}", color="var(--accent)")}
  {_metric_card("Variación semanal", f"{var:+.2f}%", color=var_color)}
  {_metric_card("Volatilidad 30d", f"{resumen['volatilidad_30d']:.2f}%", color="var(--accent-orange)")}
</div>"""

    filas_fx = []
    for r in ultimos_fx:
        filas_fx.append(f"""<tr>
  <td>{r.get("fecha","")}</td>
  <td>{r.get("hora","")}</td>
  <td class="num">${r.get("bid",0):.4f}</td>
  <td class="num">${r.get("ask",0):.4f}</td>
  <td>{_badge("USDMXN","blue")}</td>
  <td style="color:var(--muted)">{r.get("source","")}</td>
</tr>""")

    sin_fx = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">Sin datos de mercado</td></tr>'
    tabla = f"""
<div class="table-wrap">
<table>
  <thead><tr>
    <th>Fecha</th><th>Hora</th><th>Bid</th><th>Ask</th><th>Par</th><th>Fuente</th>
  </tr></thead>
  <tbody>{"".join(filas_fx) if filas_fx else sin_fx}</tbody>
</table>
</div>"""

    body = (
        _topbar("Mercado", "USD / MXN")
        + kpis
        + '<div class="card"><div class="card-title"><i class="ti ti-history"></i>Últimas cotizaciones USDMXN</div>'
        + tabla
        + "</div></div>"
    )
    return HTMLResponse(_page("Mercado", body, "mercado"))


# ---------------------------------------------------------------------------
# GET /cliente/{prospect_id}  — detalle (mantiene ruta legacy para tests)
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
        return HTMLResponse(
            _page("No encontrado", "<div class='content'><p>Cliente no encontrado.</p></div>"),
            status_code=404,
        )

    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {prospect_id}")
    nombre  = _decrypt(prospect.get("nombre_enc", ""), "—")

    ultimos_fx = get_latest_fx_rates("USDMXN", n=1, db_path=db_path)
    spot = ultimos_fx[0]["bid"] if ultimos_fx else 20.0

    try:
        pnl = resumen_pnl_cliente(prospect_id, spot, db_path=db_path)
    except Exception:
        pnl = {"coberturas": [], "total_mtm_mxn": 0.0, "total_cubierto_usd": 0.0,
               "num_coberturas": 0, "proximos_vencimientos": []}

    coberturas_pnl = pnl.get("coberturas", [])
    proximos = get_expiring_hedges(dias=30, db_path=db_path)
    proximos = [h for h in proximos if h["prospect_id"] == prospect_id]

    flash_html = f'<div class="flash flash-ok">{flash}</div>' if flash else ""

    mtm = pnl.get("total_mtm_mxn", 0.0)
    mtm_color = "var(--accent-green)" if mtm >= 0 else "var(--accent-red)"
    kpis = f"""
<div class="metric-row">
  {_metric_card("Spot USD/MXN", f"${spot:.4f}")}
  {_metric_card("Total cubierto USD", f"${pnl.get('total_cubierto_usd',0):,.0f}", color="var(--accent-green)")}
  {_metric_card("MTM total MXN", f"${mtm:,.0f}", color=mtm_color)}
  {_metric_card("Coberturas activas", str(pnl.get("num_coberturas",0)), color="var(--accent-orange)")}
</div>"""

    # Info card
    info = f"""
<div class="card">
  <div class="card-title"><i class="ti ti-user"></i>Datos del cliente</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px 24px">
    <div><span style="font-size:11px;color:var(--muted)">Empresa</span><div style="font-weight:600">{empresa}</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Contacto</span><div>{nombre}</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Sector</span><div>{prospect.get("sector","—")}</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Volumen USD/mes</span><div class="num">${prospect.get("volumen_usd_mensual",0):,.0f}</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Margen utilidad</span><div>{prospect.get("margen_utilidad",0)*100:.1f}%</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Status</span><div>{_badge(prospect.get("status","—"), "blue")}</div></div>
  </div>
</div>"""

    # Coberturas
    filas_cob = []
    for c in coberturas_pnl:
        pnl_tipo = "green" if c.pnl_vs_spot_mxn >= 0 else "red"
        banco = getattr(c, "banco_ejecutor", None) or "—"
        filas_cob.append(f"""<tr>
  <td>{_badge(c.tipo.upper(),"blue")}</td>
  <td class="num">${c.monto_usd:,.0f}</td>
  <td class="num">${c.strike:.4f}</td>
  <td style="text-align:center">{c.dias_restantes}</td>
  <td class="num">${c.mtm_mxn:,.0f}</td>
  <td>{_badge(f"${c.pnl_vs_spot_mxn:,.0f}", pnl_tipo)}</td>
</tr>""")

    sin_cob = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:20px">Sin coberturas activas</td></tr>'
    tabla_cob = f"""
<div class="card">
  <div class="card-title"><i class="ti ti-shield-check"></i>Coberturas activas</div>
  <div class="table-wrap" style="margin-bottom:0;border:none">
  <table>
    <thead><tr>
      <th>Tipo</th><th>Monto USD</th><th>Strike</th><th>Días rest.</th><th>MTM MXN</th><th>Ahorro vs Spot</th>
    </tr></thead>
    <tbody>{"".join(filas_cob) if filas_cob else sin_cob}</tbody>
  </table>
  </div>
</div>"""

    # Próximos vencimientos
    filas_v = []
    today = date.today()
    for h in proximos:
        try:
            dias = (date.fromisoformat(h["fecha_vencimiento"]) - today).days
        except Exception:
            dias = "—"
        filas_v.append(f"""<tr>
  <td>{_badge(h.get("tipo","").upper(),"blue")}</td>
  <td class="num">${h.get("monto_usd",0):,.0f}</td>
  <td class="num">${h.get("strike",0):.4f}</td>
  <td>{h.get("fecha_vencimiento","")}</td>
  <td class="num">{dias} días</td>
</tr>""")

    sin_v = '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">Sin vencimientos en 30 días</td></tr>'
    tabla_v = f"""
<div class="card">
  <div class="card-title"><i class="ti ti-calendar-stats"></i>Próximos vencimientos (30 días)</div>
  <div class="table-wrap" style="margin-bottom:0;border:none">
  <table>
    <thead><tr>
      <th>Tipo</th><th>Monto USD</th><th>Strike</th><th>Fecha</th><th>Días rest.</th>
    </tr></thead>
    <tbody>{"".join(filas_v) if filas_v else sin_v}</tbody>
  </table>
  </div>
</div>"""

    accion = f"""
<form method="post" action="/dashboard/cliente/{prospect_id}/reporte">
  <button type="submit" class="btn btn-green"><i class="ti ti-file-text"></i>Generar reporte</button>
</form>"""

    # Recuadro copiable del portal cliente
    tok = _token_cliente(prospect_id)
    portal_url = f"/dashboard/portal/{prospect_id}?token={tok}"
    portal_card = f"""
<div class="card" style="margin-top:16px">
  <div class="card-title"><i class="ti ti-share"></i>Compartir portal con cliente</div>
  <p style="font-size:12px;color:var(--muted);margin-bottom:10px">
    Envía este enlace al cliente para que vea su resumen de coberturas y estrategia (solo lectura).
  </p>
  <div style="display:flex;align-items:center;gap:8px">
    <input id="portal-url" type="text" value="{portal_url}" readonly
      style="flex:1;font-family:var(--mono);font-size:12px;padding:8px 12px;
             border:1px solid var(--border);border-radius:8px;background:#F9F9F7;color:var(--text)">
    <button onclick="navigator.clipboard.writeText(document.getElementById('portal-url').value);this.textContent='✓ Copiado';setTimeout(()=>this.textContent='Copiar',1500)"
      class="btn btn-outline" style="white-space:nowrap;font-size:12px" type="button">Copiar</button>
    <a href="{portal_url}" target="_blank" class="btn btn-outline" style="font-size:12px">
      <i class="ti ti-external-link"></i>
    </a>
  </div>
</div>"""

    extra = f'<a href="/dashboard/estrategia/{prospect_id}" class="btn btn-outline"><i class="ti ti-stairs"></i>Ver estrategia</a>'
    body = (
        _topbar(empresa, prospect.get("sector",""), extra)
        + flash_html
        + kpis
        + info
        + tabla_cob
        + tabla_v
        + accion
        + portal_card
        + "</div>"
    )
    return HTMLResponse(_page(empresa, body, "clientes"))


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
        from agents.reports.report_generator import generar_datos_reporte, generar_pdf_reporte
        datos = generar_datos_reporte(prospect_id, db_path=db_path)
        datos["_db_path"] = db_path
        path_pdf = generar_pdf_reporte(datos)
        flash_msg = f"Reporte generado: {path_pdf}"
    except Exception as exc:
        logger.exception("Error generando reporte para prospect_id=%d", prospect_id)
        flash_msg = f"Error al generar reporte: {exc}"

    encoded = urllib.parse.quote(flash_msg)
    return RedirectResponse(url=_prefix(f"/cliente/{prospect_id}?flash={encoded}"), status_code=302)


# ---------------------------------------------------------------------------
# GET /portal/{prospect_id}  — Vista cliente
# ---------------------------------------------------------------------------

@app.get("/portal/{prospect_id}", response_class=HTMLResponse)
def portal_cliente(
    prospect_id: int,
    token: Optional[str] = None,
    session_token: Optional[str] = Cookie(default=None),
):
    if not _portal_valido(prospect_id, token):
        return HTMLResponse(_login_page("Acceso denegado", """
<div class="login-wrap">
  <div class="login-box" style="text-align:center">
    <div style="font-size:40px;margin-bottom:12px">🔒</div>
    <h2 style="margin-bottom:8px">Acceso denegado</h2>
    <p style="color:var(--muted)">Token inválido o expirado. Contacta a tu asesor HedgePoint.</p>
  </div>
</div>"""), status_code=403)

    db_path = _db()
    prospect = get_prospect(prospect_id, db_path=db_path)
    if not prospect:
        return HTMLResponse(_login_page("No encontrado", "<div class='login-wrap'><div class='login-box'><p>Cliente no encontrado.</p></div></div>"), status_code=404)

    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {prospect_id}")
    nombre  = _decrypt(prospect.get("nombre_enc", ""), "—")

    ultimos_fx = get_latest_fx_rates("USDMXN", n=1, db_path=db_path)
    spot = ultimos_fx[0]["bid"] if ultimos_fx else 20.0

    try:
        pnl = resumen_pnl_cliente(prospect_id, spot, db_path=db_path)
    except Exception:
        pnl = {"coberturas": [], "total_mtm_mxn": 0.0, "total_cubierto_usd": 0.0,
               "num_coberturas": 0}

    strat  = get_client_strategy(prospect_id, db_path=db_path)
    hedges = get_client_hedges(prospect_id, estado="activa", db_path=db_path)
    vol_mes = prospect.get("volumen_usd_mensual", 0) or 1
    total_cubierto = sum(h.get("monto_usd", 0) for h in hedges)
    pct = min((total_cubierto / vol_mes) * 100, 100)
    min_pct = strat["cobertura_minima_pct"] if strat else 40.0
    max_pct = strat["cobertura_maxima_pct"] if strat else 85.0
    color = _cobertura_color(pct, min_pct)
    mtm = pnl.get("total_mtm_mxn", 0.0)

    is_consultor = _sesion_valida(session_token)
    back_link = '<a href="/dashboard/" style="color:#a8a8a8;text-decoration:none;font-size:12px;margin-right:12px">← Panel consultor</a>' if is_consultor else ""
    t_param = f"?token={token}" if token else ""
    nav = f"""
<div style="background:var(--sidebar-bg);padding:14px 24px;display:flex;align-items:center;justify-content:space-between">
  <div style="display:flex;align-items:center;gap:10px">
    {back_link}
    <div class="logo-mark">HP</div>
    <span style="color:#fff;font-weight:600;font-size:14px">{empresa}</span>
  </div>
  <div style="display:flex;gap:16px">
    <a href="/dashboard/portal/{prospect_id}{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Resumen</a>
    <a href="/dashboard/portal/{prospect_id}/coberturas{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Coberturas</a>
    <a href="/dashboard/portal/{prospect_id}/estrategia{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Estrategia</a>
  </div>
</div>"""

    kpis = f"""
<div class="metric-row" style="margin-top:24px">
  {_metric_card("Spot USD/MXN", f"${spot:.4f}")}
  {_metric_card("Monto cubierto USD", f"${total_cubierto:,.0f}", color="var(--accent-green)")}
  {_metric_card("Ahorro MTM MXN", f"${mtm:,.0f}", color="var(--accent-green)" if mtm>=0 else "var(--accent-red)")}
  {_metric_card("Coberturas activas", str(len(hedges)), color="var(--accent-orange)")}
</div>"""

    bar_section = f"""
<div class="card">
  <div class="card-title"><i class="ti ti-target"></i>Nivel de cobertura</div>
  <div style="margin-bottom:8px;display:flex;justify-content:space-between">
    <span style="font-size:13px">Cobertura actual vs. exposición mensual</span>
    <span class="num" style="font-weight:700;font-size:16px">{pct:.1f}%</span>
  </div>
  {_progress_bar(pct, color)}
  <div style="display:flex;justify-content:space-between;margin-top:8px">
    <span style="font-size:11px;color:var(--muted)">Mínimo recomendado: {min_pct:.0f}%</span>
    <span style="font-size:11px;color:var(--muted)">Máximo: {max_pct:.0f}%</span>
  </div>
</div>"""

    body = f"""
{nav}
<div style="max-width:1000px;margin:0 auto;padding:24px 20px">
  <h2 style="font-size:18px;font-weight:700;margin-bottom:20px">Resumen de cobertura</h2>
  {kpis}
  {bar_section}
</div>"""

    return HTMLResponse(_login_page(f"Portal · {empresa}", body))


# ---------------------------------------------------------------------------
# GET /portal/{prospect_id}/coberturas
# ---------------------------------------------------------------------------

@app.get("/portal/{prospect_id}/coberturas", response_class=HTMLResponse)
def portal_coberturas(
    prospect_id: int,
    token: Optional[str] = None,
    session_token: Optional[str] = Cookie(default=None),
):
    if not _portal_valido(prospect_id, token):
        return HTMLResponse(_login_page("Acceso denegado", ""), status_code=403)

    db_path = _db()
    prospect = get_prospect(prospect_id, db_path=db_path)
    if not prospect:
        return HTMLResponse(_login_page("No encontrado", ""), status_code=404)

    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {prospect_id}")
    activas  = get_client_hedges(prospect_id, estado="activa", db_path=db_path)
    historial = [h for h in get_client_hedges(prospect_id, db_path=db_path) if h.get("estado") != "activa"]

    is_consultor = _sesion_valida(session_token)
    back_link = '<a href="/dashboard/" style="color:#a8a8a8;text-decoration:none;font-size:12px;margin-right:12px">← Panel consultor</a>' if is_consultor else ""
    t_param = f"?token={token}" if token else ""
    nav = f"""
<div style="background:var(--sidebar-bg);padding:14px 24px;display:flex;align-items:center;justify-content:space-between">
  <div style="display:flex;align-items:center;gap:10px">
    {back_link}
    <div class="logo-mark">HP</div>
    <span style="color:#fff;font-weight:600;font-size:14px">{empresa}</span>
  </div>
  <div style="display:flex;gap:16px">
    <a href="/dashboard/portal/{prospect_id}{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Resumen</a>
    <a href="/dashboard/portal/{prospect_id}/coberturas{t_param}" style="color:#fff;text-decoration:none;font-size:13px;font-weight:600">Coberturas</a>
    <a href="/dashboard/portal/{prospect_id}/estrategia{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Estrategia</a>
  </div>
</div>"""

    def _fila_hedge(h: dict) -> str:
        estado = h.get("estado", "activa")
        color = {"activa":"green","liquidada":"blue","vencida":"gray","cancelada":"red"}.get(estado,"gray")
        banco = h.get("banco_ejecutor") or "—"
        return f"""<tr>
  <td>{_badge(h.get("tipo","").upper(),"blue")}</td>
  <td class="num">${h.get("monto_usd",0):,.0f}</td>
  <td class="num">${h.get("strike",0):.4f}</td>
  <td>{h.get("fecha_inicio","")}</td>
  <td>{h.get("fecha_vencimiento","")}</td>
  <td>{banco}</td>
  <td>{_badge(estado, color)}</td>
</tr>"""

    def _tabla(filas_html: str, empty: str) -> str:
        return f"""
<div class="table-wrap">
<table>
  <thead><tr>
    <th>Tipo</th><th>Monto USD</th><th>Strike</th><th>Inicio</th><th>Vencimiento</th><th>Banco</th><th>Estado</th>
  </tr></thead>
  <tbody>{"".join(filas_html) if filas_html else f'<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:20px">{empty}</td></tr>'}</tbody>
</table>
</div>"""

    filas_a = [_fila_hedge(h) for h in activas]
    filas_h = [_fila_hedge(h) for h in historial]

    pendientes = get_client_pending_hedges(prospect_id, estado="pendiente", db_path=db_path)
    pending_section = ""
    if pendientes:
        filas_p = []
        for p in pendientes:
            banco = p.get("banco_ejecutor") or "—"
            filas_p.append(f"""<tr>
  <td>{_badge((p.get("tipo") or "—").upper(), "blue")}</td>
  <td class="num">${(p.get("monto_usd") or 0):,.0f}</td>
  <td class="num">${(p.get("strike") or 0):.4f}</td>
  <td>{p.get("fecha_vencimiento") or "—"}</td>
  <td>{banco}</td>
  <td>{p.get("documento_nombre") or "—"}</td>
  <td>{_badge("Pendiente de aprobación", "orange")}</td>
</tr>""")
        pending_section = f"""
  <div class="card" style="border-color:#FED7AA">
    <div class="card-title" style="color:var(--accent-orange)"><i class="ti ti-clock"></i>En revisión por el consultor</div>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Tipo</th><th>Monto USD</th><th>Strike</th><th>Vencimiento</th><th>Banco</th><th>Documento</th><th>Estado</th>
      </tr></thead>
      <tbody>{"".join(filas_p)}</tbody>
    </table></div>
  </div>"""

    upload_url = f"/portal/{prospect_id}/upload{t_param}"
    body = f"""
{nav}
<div style="max-width:1000px;margin:0 auto;padding:24px 20px">
  <div style="display:flex;justify-content:flex-end;margin-bottom:16px">
    <a href="{upload_url}" class="btn btn-primary" style="font-size:13px">
      <i class="ti ti-upload"></i>Subir confirmación de cobertura
    </a>
  </div>
  {pending_section}
  <div class="card">
    <div class="card-title"><i class="ti ti-shield-check"></i>Coberturas activas</div>
    {_tabla(filas_a, "Sin coberturas activas")}
  </div>
  <div class="card">
    <div class="card-title"><i class="ti ti-history"></i>Historial</div>
    {_tabla(filas_h, "Sin historial")}
  </div>
</div>"""

    return HTMLResponse(_login_page(f"Coberturas · {empresa}", body))


# ---------------------------------------------------------------------------
# GET /portal/{prospect_id}/upload
# ---------------------------------------------------------------------------

@app.get("/portal/{prospect_id}/upload", response_class=HTMLResponse)
def portal_upload_get(
    prospect_id: int,
    token: Optional[str] = None,
    flash: str = "",
    session_token: Optional[str] = Cookie(default=None),
):
    if not _portal_valido(prospect_id, token):
        return HTMLResponse(_login_page("Acceso denegado", ""), status_code=403)

    db_path = _db()
    prospect = get_prospect(prospect_id, db_path=db_path)
    if not prospect:
        return HTMLResponse(_login_page("No encontrado", ""), status_code=404)

    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {prospect_id}")
    is_consultor = _sesion_valida(session_token)
    back_link = '<a href="/dashboard/" style="color:#a8a8a8;text-decoration:none;font-size:12px;margin-right:12px">← Panel consultor</a>' if is_consultor else ""
    t_param = f"?token={token}" if token else ""
    flash_html = f'<div class="flash flash-ok" style="margin-bottom:16px">{flash}</div>' if flash else ""

    nav = f"""
<div style="background:var(--sidebar-bg);padding:14px 24px;display:flex;align-items:center;justify-content:space-between">
  <div style="display:flex;align-items:center;gap:10px">
    {back_link}
    <div class="logo-mark">HP</div>
    <span style="color:#fff;font-weight:600;font-size:14px">{empresa}</span>
  </div>
  <div style="display:flex;gap:16px">
    <a href="/dashboard/portal/{prospect_id}{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Resumen</a>
    <a href="/dashboard/portal/{prospect_id}/coberturas{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Coberturas</a>
    <a href="/dashboard/portal/{prospect_id}/estrategia{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Estrategia</a>
  </div>
</div>"""

    tok_param = f"token={token}&" if token else ""
    form = f"""
<form method="post" action="/dashboard/portal/{prospect_id}/upload?{tok_param}" enctype="multipart/form-data">
  <div class="field" style="margin-bottom:16px">
    <label>Documento de confirmación de cobertura</label>
    <input type="file" name="archivo" accept=".pdf,.png,.jpg,.jpeg" required
      style="padding:8px 12px;border:1px dashed #D1D5DB;border-radius:8px;background:#FAFAF8;cursor:pointer">
    <span style="font-size:11px;color:var(--muted)">Formatos: PDF, PNG, JPG · Máximo 10 MB</span>
  </div>
  <div style="display:flex;gap:10px">
    <button type="submit" class="btn btn-primary">
      <i class="ti ti-sparkles"></i>Analizar y registrar
    </button>
    <a href="/dashboard/portal/{prospect_id}/coberturas{t_param}" class="btn btn-outline">Cancelar</a>
  </div>
</form>"""

    body = f"""
{nav}
<div style="max-width:700px;margin:0 auto;padding:24px 20px">
  {flash_html}
  <div style="background:var(--card-bg);border:1px solid var(--border);border-radius:14px;padding:28px 28px">
    <div style="font-size:15px;font-weight:700;margin-bottom:6px;display:flex;align-items:center;gap:8px">
      <i class="ti ti-upload" style="color:var(--accent)"></i>Subir confirmación de cobertura
    </div>
    <p style="font-size:13px;color:var(--muted);margin-bottom:20px">
      Sube la confirmación que te envió tu banco. Extraeremos los datos automáticamente
      y registraremos la cobertura como pendiente de verificación.
    </p>
    {form}
  </div>
</div>"""

    return HTMLResponse(_login_page(f"Subir cobertura · {empresa}", body))


# ---------------------------------------------------------------------------
# POST /portal/{prospect_id}/upload
# ---------------------------------------------------------------------------

@app.post("/portal/{prospect_id}/upload")
async def portal_upload_post(
    prospect_id: int,
    token: Optional[str] = None,
    archivo: UploadFile = File(...),
):
    if not _portal_valido(prospect_id, token):
        return HTMLResponse(_login_page("Acceso denegado", ""), status_code=403)

    t_param = f"?token={token}" if token else ""
    coberturas_url = _prefix(f"/portal/{prospect_id}/coberturas{t_param}")
    upload_url = _prefix(f"/portal/{prospect_id}/upload{t_param}")

    data = await archivo.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        msg = urllib.parse.quote("El archivo excede el límite de 10 MB.")
        return RedirectResponse(url=f"{upload_url}{'&' if token else '?'}flash={msg}", status_code=302)

    filename = (archivo.filename or "").lower()
    mime_type = archivo.content_type or ""
    logger.info(
        "Portal upload: prospect_id=%d filename=%r size=%d",
        prospect_id, filename, len(data),
    )

    fields: dict = {}
    api_available = bool(os.getenv("ANTHROPIC_API_KEY", ""))

    if api_available:
        if filename.endswith(".pdf") or "pdf" in mime_type:
            fields = _extract_from_pdf_bytes(data)
        elif filename.endswith((".png", ".jpg", ".jpeg")) or mime_type.startswith("image/"):
            img_mime = mime_type if mime_type.startswith("image/") else (
                "image/png" if filename.endswith(".png") else "image/jpeg"
            )
            fields = _extract_from_image_bytes(data, img_mime)

    if not fields or not api_available:
        reason = "sin ANTHROPIC_API_KEY" if not api_available else "extracción fallida"
        msg = urllib.parse.quote(
            f"No se pudieron extraer datos del documento ({reason}). "
            "Contacta a tu asesor para registrar la cobertura manualmente."
        )
        sep = "&" if token else "?"
        return RedirectResponse(url=f"{upload_url}{sep}flash={msg}", status_code=302)

    db_path = _db()
    try:
        pending_data: dict = {
            "prospect_id":       prospect_id,
            "tipo":              fields.get("tipo") or "forward",
            "monto_usd":         float(fields.get("monto_usd") or 0),
            "strike":            float(fields.get("strike") or 0),
            "spot_entrada":      float(fields.get("spot_entrada") or fields.get("strike") or 0),
            "prima_pagada_mxn":  float(fields.get("prima_pagada_mxn") or 0),
            "fecha_inicio":      fields.get("fecha_inicio") or date.today().isoformat(),
            "fecha_vencimiento": fields.get("fecha_vencimiento") or date.today().isoformat(),
            "notas":             "Subida vía portal cliente — pendiente de verificación",
            "documento_nombre":  archivo.filename or "",
        }
        if fields.get("strike_call"):
            pending_data["strike_call"] = float(fields["strike_call"])
        if fields.get("banco_ejecutor"):
            pending_data["banco_ejecutor"] = fields["banco_ejecutor"]
        if fields.get("spread_banco_centavos"):
            pending_data["spread_banco_centavos"] = float(fields["spread_banco_centavos"])

        insert_hedge_pending(pending_data, db_path=db_path)
        logger.info("Cobertura pendiente guardada vía portal: prospect_id=%d tipo=%s", prospect_id, pending_data["tipo"])
        msg = urllib.parse.quote("Documento recibido. Tu asesor lo revisará y aprobará la cobertura.")
    except Exception as exc:
        logger.exception("Error guardando cobertura pendiente desde portal upload")
        msg = urllib.parse.quote(f"Error al registrar: {exc}")

    sep = "&" if token else "?"
    return RedirectResponse(url=f"{coberturas_url}{sep}flash={msg}", status_code=302)


# ---------------------------------------------------------------------------
# GET /portal/{prospect_id}/estrategia
# ---------------------------------------------------------------------------

@app.get("/portal/{prospect_id}/estrategia", response_class=HTMLResponse)
def portal_estrategia(
    prospect_id: int,
    token: Optional[str] = None,
    session_token: Optional[str] = Cookie(default=None),
):
    if not _portal_valido(prospect_id, token):
        return HTMLResponse(_login_page("Acceso denegado", ""), status_code=403)

    db_path = _db()
    prospect = get_prospect(prospect_id, db_path=db_path)
    if not prospect:
        return HTMLResponse(_login_page("No encontrado", ""), status_code=404)

    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {prospect_id}")
    strat   = get_client_strategy(prospect_id, db_path=db_path)
    levels  = get_strategy_levels(strat["id"], db_path=db_path) if strat else []

    is_consultor = _sesion_valida(session_token)
    back_link = '<a href="/dashboard/" style="color:#a8a8a8;text-decoration:none;font-size:12px;margin-right:12px">← Panel consultor</a>' if is_consultor else ""
    t_param = f"?token={token}" if token else ""
    nav = f"""
<div style="background:var(--sidebar-bg);padding:14px 24px;display:flex;align-items:center;justify-content:space-between">
  <div style="display:flex;align-items:center;gap:10px">
    {back_link}
    <div class="logo-mark">HP</div>
    <span style="color:#fff;font-weight:600;font-size:14px">{empresa}</span>
  </div>
  <div style="display:flex;gap:16px">
    <a href="/dashboard/portal/{prospect_id}{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Resumen</a>
    <a href="/dashboard/portal/{prospect_id}/coberturas{t_param}" style="color:#a8a8a8;text-decoration:none;font-size:13px">Coberturas</a>
    <a href="/dashboard/portal/{prospect_id}/estrategia{t_param}" style="color:#fff;text-decoration:none;font-size:13px;font-weight:600">Estrategia</a>
  </div>
</div>"""

    levels_html = ""
    for lv in levels:
        estado = lv.get("estado","esperando")
        dot_cls = {"ejecutado":"dot-done","cancelado":"dot-cancel"}.get(estado,"dot-pending")
        dot_icon = {"ejecutado":"✓","cancelado":"✕"}.get(estado, str(lv.get("orden","?")))
        cond_val = lv.get("condicion_valor")
        cond_txt = lv.get("condicion_tipo","").replace("_"," ").title()
        if cond_val:
            cond_txt += f" ${cond_val:.2f}"
        levels_html += f"""
<div class="level-row">
  <div class="level-dot {dot_cls}">{dot_icon}</div>
  <div class="level-info">
    <div class="level-name">{lv.get("nombre","")}</div>
    <div class="level-meta">{cond_txt} · {lv.get("accion_tipo","").upper()} {lv.get("accion_pct",0):.0f}% · {_badge(estado, {"ejecutado":"green","cancelado":"red"}.get(estado,"gray"))}</div>
  </div>
</div>"""

    params_html = ""
    if strat:
        params_html = f"""
<div class="card" style="margin-bottom:16px">
  <div class="card-title"><i class="ti ti-adjustments"></i>Parámetros de cobertura</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px 24px">
    <div><span style="font-size:11px;color:var(--muted)">Cobertura mínima</span><div class="num">{strat["cobertura_minima_pct"]:.0f}%</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Cobertura máxima</span><div class="num">{strat["cobertura_maxima_pct"]:.0f}%</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Exposición mensual</span><div class="num">${strat["exposicion_mensual_usd"]:,.0f} USD</div></div>
    <div><span style="font-size:11px;color:var(--muted)">Máx. movimientos/mes</span><div class="num">{strat["max_movimientos_mes"]}</div></div>
  </div>
</div>"""

    body = f"""
{nav}
<div style="max-width:1000px;margin:0 auto;padding:24px 20px">
  {params_html}
  <div class="card">
    <div class="card-title"><i class="ti ti-stairs"></i>Niveles de entrada</div>
    {levels_html if levels_html else "<p style='color:var(--muted)'>Sin niveles configurados.</p>"}
  </div>
</div>"""

    return HTMLResponse(_login_page(f"Estrategia · {empresa}", body))


# ---------------------------------------------------------------------------
# GET /pendientes  — lista de coberturas pendientes de aprobación
# ---------------------------------------------------------------------------

@app.get("/pendientes", response_class=HTMLResponse)
def pendientes_lista(
    session_token: Optional[str] = Cookie(default=None),
    flash: str = "",
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    items = get_pending_hedges(estado="pendiente", db_path=db_path)

    flash_html = f'<div class="flash flash-ok">{flash}</div>' if flash else ""

    filas = []
    for p in items:
        prospect = get_prospect(p["prospect_id"], db_path=db_path) or {}
        empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {p['prospect_id']}")
        banco = p.get("banco_ejecutor") or "—"
        filas.append(f"""<tr>
  <td><a href="/dashboard/cliente/{p['prospect_id']}" style="color:var(--accent);text-decoration:none;font-weight:500">{empresa}</a></td>
  <td>{_badge((p.get("tipo") or "—").upper(), "blue")}</td>
  <td class="num">${(p.get("monto_usd") or 0):,.0f}</td>
  <td class="num">${(p.get("strike") or 0):.4f}</td>
  <td>{banco}</td>
  <td style="font-size:12px;color:var(--muted)">{p.get("created_at","")[:16]}</td>
  <td>
    <div style="display:flex;gap:6px">
      <a href="/dashboard/pendientes/{p['id']}" class="btn btn-outline" style="font-size:12px;padding:4px 10px">Ver detalle</a>
      <form method="post" action="/dashboard/pendientes/{p['id']}/aprobar" style="display:inline">
        <button type="submit" class="btn btn-primary" style="font-size:12px;padding:4px 10px;background:var(--accent-green);border-color:var(--accent-green)">Aprobar</button>
      </form>
      <form method="post" action="/dashboard/pendientes/{p['id']}/rechazar" style="display:inline">
        <button type="submit" class="btn btn-outline" style="font-size:12px;padding:4px 10px;color:var(--accent-red);border-color:var(--accent-red)">Rechazar</button>
      </form>
    </div>
  </td>
</tr>""")

    sin_p = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Sin coberturas pendientes</td></tr>'
    tabla = f"""
<div class="table-wrap">
<table>
  <thead><tr>
    <th>Cliente</th><th>Tipo</th><th>Monto USD</th><th>Strike</th><th>Banco</th><th>Fecha subida</th><th>Acciones</th>
  </tr></thead>
  <tbody>{"".join(filas) if filas else sin_p}</tbody>
</table>
</div>"""

    body = (
        _topbar("Coberturas pendientes de aprobación", f"{len(items)} pendiente{'s' if len(items) != 1 else ''}")
        + flash_html
        + '<div class="card">'
        + tabla
        + "</div></div>"
    )
    return HTMLResponse(_page("Pendientes", body, "pendientes"))


# ---------------------------------------------------------------------------
# GET /pendientes/{pending_id}  — detalle de una cobertura pendiente
# ---------------------------------------------------------------------------

@app.get("/pendientes/{pending_id}", response_class=HTMLResponse)
def pendiente_detalle(
    pending_id: int,
    session_token: Optional[str] = Cookie(default=None),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    p = get_pending_hedge(pending_id, db_path=db_path)
    if not p:
        return HTMLResponse(_page("No encontrado", _topbar("No encontrado", "") + "<p style='padding:20px;color:var(--muted)'>Cobertura pendiente no encontrada.</p></div>", "pendientes"), status_code=404)

    prospect = get_prospect(p["prospect_id"], db_path=db_path) or {}
    empresa = _decrypt(prospect.get("empresa_enc", ""), f"Cliente {p['prospect_id']}")

    def _row(label: str, value) -> str:
        return f'<div style="display:flex;gap:16px;padding:10px 0;border-bottom:1px solid var(--border)"><span style="width:200px;color:var(--muted);font-size:13px">{label}</span><span style="font-weight:500">{value or "—"}</span></div>'

    campos = (
        _row("Cliente", f'<a href="/dashboard/cliente/{p["prospect_id"]}" style="color:var(--accent)">{empresa}</a>')
        + _row("Tipo", _badge((p.get("tipo") or "—").upper(), "blue"))
        + _row("Monto USD", f'${(p.get("monto_usd") or 0):,.0f}')
        + _row("Strike (put / floor)", f'${(p.get("strike") or 0):.4f}')
        + _row("Strike call (cap)", f'${p["strike_call"]:.4f}' if p.get("strike_call") else "—")
        + _row("Spot entrada", f'${(p.get("spot_entrada") or 0):.4f}')
        + _row("Prima pagada MXN", f'${(p.get("prima_pagada_mxn") or 0):,.0f}')
        + _row("Fecha inicio", p.get("fecha_inicio") or "—")
        + _row("Fecha vencimiento", p.get("fecha_vencimiento") or "—")
        + _row("Banco ejecutor", p.get("banco_ejecutor") or "—")
        + _row("Spread banco (centavos)", str(p.get("spread_banco_centavos") or "—"))
        + _row("Documento", p.get("documento_nombre") or "—")
        + _row("Notas", p.get("notas") or "—")
        + _row("Fecha subida", (p.get("created_at") or "")[:16])
    )

    acciones = f"""
<div style="display:flex;gap:12px;margin-top:24px">
  <form method="post" action="/dashboard/pendientes/{pending_id}/aprobar">
    <button type="submit" class="btn btn-primary" style="background:var(--accent-green);border-color:var(--accent-green)">
      <i class="ti ti-check"></i>Aprobar y registrar cobertura
    </button>
  </form>
  <form method="post" action="/dashboard/pendientes/{pending_id}/rechazar">
    <button type="submit" class="btn btn-outline" style="color:var(--accent-red);border-color:var(--accent-red)">
      <i class="ti ti-x"></i>Rechazar
    </button>
  </form>
  <a href="/dashboard/pendientes" class="btn btn-outline">← Volver</a>
</div>"""

    body = (
        _topbar(f"Detalle de cobertura pendiente #{pending_id}", empresa)
        + f'<div class="card"><div class="card-title"><i class="ti ti-file-description"></i>Datos extraídos del documento</div>{campos}{acciones}</div>'
        + "</div>"
    )
    return HTMLResponse(_page("Detalle pendiente", body, "pendientes"))


# ---------------------------------------------------------------------------
# POST /pendientes/{pending_id}/aprobar
# ---------------------------------------------------------------------------

@app.post("/pendientes/{pending_id}/aprobar")
def pendiente_aprobar(
    pending_id: int,
    session_token: Optional[str] = Cookie(default=None),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    db_path = _db()
    p = get_pending_hedge(pending_id, db_path=db_path)
    if not p:
        return RedirectResponse(url=_prefix("/pendientes"), status_code=302)

    hedge_data: dict = {
        "prospect_id":       p["prospect_id"],
        "tipo":              p.get("tipo") or "forward",
        "monto_usd":         float(p.get("monto_usd") or 0),
        "strike":            float(p.get("strike") or 0),
        "spot_entrada":      float(p.get("spot_entrada") or p.get("strike") or 0),
        "prima_pagada_mxn":  float(p.get("prima_pagada_mxn") or 0),
        "fecha_inicio":      p.get("fecha_inicio") or date.today().isoformat(),
        "fecha_vencimiento": p.get("fecha_vencimiento") or date.today().isoformat(),
        "notas":             f"Aprobada desde portal — doc: {p.get('documento_nombre') or ''}",
    }
    if p.get("strike_call"):
        hedge_data["strike_call"] = float(p["strike_call"])
    if p.get("banco_ejecutor"):
        hedge_data["banco_ejecutor"] = p["banco_ejecutor"]
    if p.get("spread_banco_centavos"):
        hedge_data["spread_banco_centavos"] = float(p["spread_banco_centavos"])

    insert_hedge(hedge_data, db_path=db_path)
    update_pending_status(pending_id, "aprobada", db_path=db_path)
    msg = urllib.parse.quote("Cobertura aprobada y registrada.")
    return RedirectResponse(url=_prefix(f"/pendientes?flash={msg}"), status_code=302)


# ---------------------------------------------------------------------------
# POST /pendientes/{pending_id}/rechazar
# ---------------------------------------------------------------------------

@app.post("/pendientes/{pending_id}/rechazar")
def pendiente_rechazar(
    pending_id: int,
    session_token: Optional[str] = Cookie(default=None),
):
    if not _sesion_valida(session_token):
        return _redirect_login()

    update_pending_status(pending_id, "rechazada", db_path=_db())
    msg = urllib.parse.quote("Cobertura rechazada.")
    return RedirectResponse(url=_prefix(f"/pendientes?flash={msg}"), status_code=302)


# ---------------------------------------------------------------------------
# Factory + entry point
# ---------------------------------------------------------------------------

def create_app(db_path: Path = DB_PATH) -> FastAPI:
    return app


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run("agents.reports.dashboard:app", host="0.0.0.0", port=8000, reload=False)
