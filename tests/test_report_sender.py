"""
Tests para agents/reports/report_sender.py

Cobertura:
- enviar_reporte_email: payload con attachment base64 válido y subject correcto
- enviar_reporte_whatsapp: mensaje incluye spot y ahorro (MTM)
- enviar_reporte dry_run: no llama a requests.post ni send_whatsapp_alert
- enviar_reporte_email sin encryption key: retorna False sin crashear
- enviar_reporte_email sin RESEND_API_KEY: retorna False
- enviar_reporte canales parciales: None para canal no solicitado
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Fixtures y helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def pdf_ficticio(tmp_path: Path) -> str:
    """Crea un PDF mínimo (bytes ficticios) y retorna su ruta."""
    p = tmp_path / "reporte.pdf"
    p.write_bytes(b"%PDF-1.4 fake content for testing")
    return str(p)


@pytest.fixture
def prospect_con_datos() -> dict:
    return {
        "id": 1,
        "nombre_enc":   "enc_nombre",
        "empresa_enc":  "enc_empresa",
        "email_enc":    "enc_email",
        "telefono_enc": "enc_telefono",
        "sector":       "Importador",
    }


@pytest.fixture
def datos_reporte_ficticios() -> dict:
    return {
        "resumen_mercado": {"spot": 19.75, "variacion_semanal": -0.22, "volatilidad_30d": 2.88},
        "pnl": {
            "total_mtm_mxn":       48_000.0,
            "total_cubierto_usd":  300_000.0,
            "num_coberturas":      3,
            "proximos_vencimientos": [],
            "coberturas": [],
        },
        "proximos_vencimientos": [{"id": 1}],
        "cliente": {"id": 1},
        "fecha_reporte": __import__("datetime").date.today(),
    }


def _mock_encryptor(monkeypatch, mapping: dict):
    """Parchea FieldEncryptor para que decrypt() use el mapping dado."""
    class FakeEncryptor:
        def decrypt(self, value: str) -> str:
            if value not in mapping:
                raise Exception(f"No mapping for {value!r}")
            return mapping[value]

    monkeypatch.setattr(
        "agents.reports.report_sender.FieldEncryptor",
        FakeEncryptor,
        raising=False,
    )


# ---------------------------------------------------------------------------
# enviar_reporte_email
# ---------------------------------------------------------------------------

class TestEnviarReporteEmail:

    def test_payload_tiene_attachment_base64_valido(
        self, monkeypatch, pdf_ficticio, prospect_con_datos
    ):
        """El payload enviado a Resend debe tener un attachment con base64 válido."""
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        _mock_encryptor(monkeypatch, {
            "enc_email":  "cliente@ejemplo.com",
            "enc_nombre": "Carlos Demo",
        })

        payloads_capturados = []

        def _fake_post(url, json=None, headers=None, timeout=None):
            payloads_capturados.append(json)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"id": "abc123"}
            return resp

        monkeypatch.setattr("agents.reports.report_sender.requests.post", _fake_post)

        from agents.reports.report_sender import enviar_reporte_email
        resultado = enviar_reporte_email(pdf_ficticio, prospect_con_datos)

        assert resultado is True
        assert len(payloads_capturados) == 1
        payload = payloads_capturados[0]

        # Attachment presente
        attachments = payload.get("attachments", [])
        assert len(attachments) == 1
        att = attachments[0]
        assert att["filename"] == "reporte_semanal.pdf"

        # Base64 decodificable y contenido correcto
        decoded = base64.b64decode(att["content"])
        assert decoded == Path(pdf_ficticio).read_bytes()

    def test_subject_contiene_fecha_dd_mm_yyyy(
        self, monkeypatch, pdf_ficticio, prospect_con_datos
    ):
        """El subject debe tener la fecha en formato dd/mm/yyyy."""
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        _mock_encryptor(monkeypatch, {
            "enc_email":  "cliente@ejemplo.com",
            "enc_nombre": "Carlos Demo",
        })

        captured = []

        def _fake_post(url, json=None, **kw):
            captured.append(json)
            r = MagicMock()
            r.status_code = 201
            r.json.return_value = {"id": "x"}
            return r

        monkeypatch.setattr("agents.reports.report_sender.requests.post", _fake_post)

        from agents.reports.report_sender import enviar_reporte_email
        enviar_reporte_email(pdf_ficticio, prospect_con_datos)

        subject = captured[0]["subject"]
        assert "[HedgePoint MX] Reporte Semanal de Coberturas" in subject
        # Fecha en formato dd/mm/yyyy (dos dígitos para día y mes)
        import re
        assert re.search(r"\d{2}/\d{2}/\d{4}", subject), f"Sin fecha dd/mm/yyyy en: {subject}"

    def test_destinatario_correcto(
        self, monkeypatch, pdf_ficticio, prospect_con_datos
    ):
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        _mock_encryptor(monkeypatch, {
            "enc_email":  "destino@empresa.com",
            "enc_nombre": "Ana",
        })

        captured = []

        def _fake_post(url, json=None, **kw):
            captured.append(json)
            r = MagicMock(); r.status_code = 200; r.json.return_value = {"id": "y"}
            return r

        monkeypatch.setattr("agents.reports.report_sender.requests.post", _fake_post)

        from agents.reports.report_sender import enviar_reporte_email
        enviar_reporte_email(pdf_ficticio, prospect_con_datos)

        assert captured[0]["to"] == ["destino@empresa.com"]

    def test_sin_resend_api_key_retorna_false(
        self, monkeypatch, pdf_ficticio, prospect_con_datos
    ):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        _mock_encryptor(monkeypatch, {"enc_email": "a@b.com", "enc_nombre": "A"})

        with patch("agents.reports.report_sender.requests.post") as mock_post:
            from agents.reports.report_sender import enviar_reporte_email
            resultado = enviar_reporte_email(pdf_ficticio, prospect_con_datos)

        assert resultado is False
        mock_post.assert_not_called()

    def test_sin_encryption_key_retorna_false(
        self, monkeypatch, pdf_ficticio, prospect_con_datos
    ):
        """Si FieldEncryptor lanza excepción (clave no configurada), retorna False."""
        monkeypatch.setenv("RESEND_API_KEY", "test-key")

        class BrokenEncryptor:
            def decrypt(self, value: str) -> str:
                raise Exception("HEDGEPOINT_ENCRYPTION_KEY no configurada")

        monkeypatch.setattr(
            "agents.reports.report_sender.FieldEncryptor",
            BrokenEncryptor,
            raising=False,
        )

        with patch("agents.reports.report_sender.requests.post") as mock_post:
            from agents.reports.report_sender import enviar_reporte_email
            resultado = enviar_reporte_email(pdf_ficticio, prospect_con_datos)

        assert resultado is False
        mock_post.assert_not_called()

    def test_sin_email_enc_retorna_false(
        self, monkeypatch, pdf_ficticio
    ):
        """Prospect sin email_enc retorna False sin intentar enviar."""
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        prospect_sin_email = {"id": 1, "nombre_enc": "enc_nombre"}

        with patch("agents.reports.report_sender.requests.post") as mock_post:
            from agents.reports.report_sender import enviar_reporte_email
            resultado = enviar_reporte_email(pdf_ficticio, prospect_sin_email)

        assert resultado is False
        mock_post.assert_not_called()

    def test_resend_api_error_retorna_false(
        self, monkeypatch, pdf_ficticio, prospect_con_datos
    ):
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        _mock_encryptor(monkeypatch, {
            "enc_email":  "a@b.com",
            "enc_nombre": "A",
        })

        def _fake_post(url, json=None, **kw):
            r = MagicMock(); r.status_code = 422; r.text = "Unprocessable"
            return r

        monkeypatch.setattr("agents.reports.report_sender.requests.post", _fake_post)

        from agents.reports.report_sender import enviar_reporte_email
        assert enviar_reporte_email(pdf_ficticio, prospect_con_datos) is False


# ---------------------------------------------------------------------------
# enviar_reporte_whatsapp
# ---------------------------------------------------------------------------

class TestEnviarReporteWhatsapp:

    def test_mensaje_contiene_spot_y_ahorro(
        self, monkeypatch, datos_reporte_ficticios, prospect_con_datos
    ):
        """El mensaje enviado debe incluir el spot y el MTM (ahorro)."""
        _mock_encryptor(monkeypatch, {"enc_telefono": "+5219931701758"})

        mensajes_enviados = []

        def _fake_send(telefono, mensaje):
            mensajes_enviados.append((telefono, mensaje))
            return True

        monkeypatch.setattr(
            "agents.reports.report_sender.send_whatsapp_alert",
            _fake_send,
        )

        from agents.reports.report_sender import enviar_reporte_whatsapp
        resultado = enviar_reporte_whatsapp(
            datos_reporte_ficticios, prospect_con_datos
        )

        assert resultado is True
        assert len(mensajes_enviados) == 1
        _, mensaje = mensajes_enviados[0]

        # Debe contener el spot
        assert "19.75" in mensaje, f"Spot no encontrado en: {mensaje!r}"
        # Debe contener el MTM (ahorro)
        assert "48" in mensaje, f"MTM no encontrado en: {mensaje!r}"

    def test_numero_correcto(
        self, monkeypatch, datos_reporte_ficticios, prospect_con_datos
    ):
        _mock_encryptor(monkeypatch, {"enc_telefono": "+5219931701758"})
        numeros = []

        def _fake_send(tel, msg):
            numeros.append(tel)
            return True

        monkeypatch.setattr("agents.reports.report_sender.send_whatsapp_alert", _fake_send)

        from agents.reports.report_sender import enviar_reporte_whatsapp
        enviar_reporte_whatsapp(datos_reporte_ficticios, prospect_con_datos)

        assert numeros == ["+5219931701758"]

    def test_sin_telefono_enc_retorna_false(
        self, monkeypatch, datos_reporte_ficticios
    ):
        with patch("agents.reports.report_sender.send_whatsapp_alert") as mock_wa:
            from agents.reports.report_sender import enviar_reporte_whatsapp
            resultado = enviar_reporte_whatsapp(datos_reporte_ficticios, {"id": 1})

        assert resultado is False
        mock_wa.assert_not_called()

    def test_sin_encryption_key_retorna_false(
        self, monkeypatch, datos_reporte_ficticios, prospect_con_datos
    ):
        class BrokenEncryptor:
            def decrypt(self, v): raise Exception("no key")

        monkeypatch.setattr(
            "agents.reports.report_sender.FieldEncryptor", BrokenEncryptor, raising=False
        )

        with patch("agents.reports.report_sender.send_whatsapp_alert") as mock_wa:
            from agents.reports.report_sender import enviar_reporte_whatsapp
            resultado = enviar_reporte_whatsapp(datos_reporte_ficticios, prospect_con_datos)

        assert resultado is False
        mock_wa.assert_not_called()


# ---------------------------------------------------------------------------
# enviar_reporte — orquestador
# ---------------------------------------------------------------------------

class TestEnviarReporte:

    def test_dry_run_no_llama_a_api(
        self, monkeypatch, pdf_ficticio, datos_reporte_ficticios, prospect_con_datos
    ):
        """dry_run=True no debe llamar a requests.post ni send_whatsapp_alert."""
        _mock_encryptor(monkeypatch, {
            "enc_email":    "a@b.com",
            "enc_nombre":   "Carlos",
            "enc_telefono": "+52999",
        })

        with patch("agents.reports.report_sender.requests.post") as mock_post, \
             patch("agents.reports.report_sender.send_whatsapp_alert") as mock_wa:

            from agents.reports.report_sender import enviar_reporte
            resultado = enviar_reporte(
                datos_reporte=datos_reporte_ficticios,
                pdf_path=pdf_ficticio,
                prospect=prospect_con_datos,
                canales=["email", "whatsapp"],
                dry_run=True,
            )

        mock_post.assert_not_called()
        mock_wa.assert_not_called()
        assert resultado["email"]    == "dry_run"
        assert resultado["whatsapp"] == "dry_run"

    def test_dry_run_solo_email(
        self, monkeypatch, pdf_ficticio, datos_reporte_ficticios, prospect_con_datos
    ):
        _mock_encryptor(monkeypatch, {"enc_email": "a@b.com", "enc_nombre": "X"})

        with patch("agents.reports.report_sender.requests.post") as mock_post, \
             patch("agents.reports.report_sender.send_whatsapp_alert") as mock_wa:

            from agents.reports.report_sender import enviar_reporte
            resultado = enviar_reporte(
                datos_reporte=datos_reporte_ficticios,
                pdf_path=pdf_ficticio,
                prospect=prospect_con_datos,
                canales=["email"],
                dry_run=True,
            )

        mock_post.assert_not_called()
        mock_wa.assert_not_called()
        assert resultado["email"]    == "dry_run"
        assert resultado["whatsapp"] is None   # canal no solicitado

    def test_canal_no_solicitado_retorna_none(
        self, monkeypatch, pdf_ficticio, datos_reporte_ficticios, prospect_con_datos
    ):
        """Si solo se pide email, whatsapp debe quedar en None (no intentado)."""
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        _mock_encryptor(monkeypatch, {"enc_email": "a@b.com", "enc_nombre": "A"})

        def _fake_post(url, json=None, **kw):
            r = MagicMock(); r.status_code = 200; r.json.return_value = {"id": "z"}
            return r

        monkeypatch.setattr("agents.reports.report_sender.requests.post", _fake_post)

        with patch("agents.reports.report_sender.send_whatsapp_alert") as mock_wa:
            from agents.reports.report_sender import enviar_reporte
            resultado = enviar_reporte(
                datos_reporte=datos_reporte_ficticios,
                pdf_path=pdf_ficticio,
                prospect=prospect_con_datos,
                canales=["email"],
                dry_run=False,
            )

        mock_wa.assert_not_called()
        assert resultado["whatsapp"] is None
        assert resultado["email"] is True

    def test_canales_default_es_email(
        self, monkeypatch, pdf_ficticio, datos_reporte_ficticios, prospect_con_datos
    ):
        """Sin especificar canales, el default debe ser ['email']."""
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        _mock_encryptor(monkeypatch, {"enc_email": "a@b.com", "enc_nombre": "A"})

        def _fake_post(url, json=None, **kw):
            r = MagicMock(); r.status_code = 200; r.json.return_value = {"id": "q"}
            return r

        monkeypatch.setattr("agents.reports.report_sender.requests.post", _fake_post)

        with patch("agents.reports.report_sender.send_whatsapp_alert") as mock_wa:
            from agents.reports.report_sender import enviar_reporte
            resultado = enviar_reporte(
                datos_reporte=datos_reporte_ficticios,
                pdf_path=pdf_ficticio,
                prospect=prospect_con_datos,
            )

        mock_wa.assert_not_called()
        assert resultado["whatsapp"] is None
        assert resultado["email"] is True

    def test_ambos_canales_en_produccion(
        self, monkeypatch, pdf_ficticio, datos_reporte_ficticios, prospect_con_datos
    ):
        """Con email+whatsapp, ambas funciones deben llamarse."""
        monkeypatch.setenv("RESEND_API_KEY", "test-key")
        _mock_encryptor(monkeypatch, {
            "enc_email":    "a@b.com",
            "enc_nombre":   "Carlos",
            "enc_telefono": "+52999",
        })

        def _fake_post(url, json=None, **kw):
            r = MagicMock(); r.status_code = 200; r.json.return_value = {"id": "p"}
            return r

        monkeypatch.setattr("agents.reports.report_sender.requests.post", _fake_post)

        def _fake_send(tel, msg):
            return True

        monkeypatch.setattr("agents.reports.report_sender.send_whatsapp_alert", _fake_send)

        from agents.reports.report_sender import enviar_reporte
        resultado = enviar_reporte(
            datos_reporte=datos_reporte_ficticios,
            pdf_path=pdf_ficticio,
            prospect=prospect_con_datos,
            canales=["email", "whatsapp"],
            dry_run=False,
        )

        assert resultado["email"]    is True
        assert resultado["whatsapp"] is True
