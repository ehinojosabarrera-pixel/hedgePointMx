"""
Unit tests for core.security.anonymizer — FieldEncryptor and Anonymizer.

Run:
    pytest tests/test_anonymizer.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_KEY = "test-passphrase-hedgepoint-sprint3"


@pytest.fixture(autouse=True)
def set_encryption_key(monkeypatch):
    """Set HEDGEPOINT_ENCRYPTION_KEY for every test in this module."""
    monkeypatch.setenv("HEDGEPOINT_ENCRYPTION_KEY", TEST_KEY)


@pytest.fixture()
def encryptor():
    from core.security.anonymizer import FieldEncryptor
    return FieldEncryptor()


@pytest.fixture()
def anon():
    from core.security.anonymizer import Anonymizer
    return Anonymizer()


# ===========================================================================
# FieldEncryptor
# ===========================================================================

class TestFieldEncryptor:

    def test_roundtrip_plain_text(self, encryptor):
        plaintext = "contacto@empresa.com"
        assert encryptor.decrypt(encryptor.encrypt(plaintext)) == plaintext

    def test_roundtrip_special_characters(self, encryptor):
        plaintext = "Ñoño García — Año Ümlaut café"
        assert encryptor.decrypt(encryptor.encrypt(plaintext)) == plaintext

    def test_roundtrip_accents(self, encryptor):
        plaintext = "José Martínez Ávalos"
        assert encryptor.decrypt(encryptor.encrypt(plaintext)) == plaintext

    def test_encrypt_empty_string(self, encryptor):
        """Encrypting an empty string must not raise; decrypt must return ''."""
        token = encryptor.encrypt("")
        assert encryptor.decrypt(token) == ""

    def test_encrypt_produces_different_tokens(self, encryptor):
        """Random nonce means two encryptions of the same plaintext differ."""
        plaintext = "mismo texto"
        assert encryptor.encrypt(plaintext) != encryptor.encrypt(plaintext)

    def test_token_is_base64_string(self, encryptor):
        import base64
        token = encryptor.encrypt("test")
        # Must be valid URL-safe base64 — should not raise
        base64.urlsafe_b64decode(token.encode("ascii"))

    def test_tampered_token_raises(self, encryptor):
        from cryptography.exceptions import InvalidTag
        token = encryptor.encrypt("dato sensible")
        # Flip the last character to corrupt the auth tag
        corrupted = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(InvalidTag):
            encryptor.decrypt(corrupted)

    def test_missing_key_raises_value_error(self, monkeypatch):
        import core.security.anonymizer as anon_mod
        monkeypatch.setattr(anon_mod, "load_dotenv", lambda **kw: None)
        monkeypatch.delenv("HEDGEPOINT_ENCRYPTION_KEY", raising=False)
        from core.security.anonymizer import FieldEncryptor
        with pytest.raises(ValueError, match="HEDGEPOINT_ENCRYPTION_KEY"):
            FieldEncryptor()

    def test_empty_key_raises_value_error(self, monkeypatch):
        import core.security.anonymizer as anon_mod
        monkeypatch.setattr(anon_mod, "load_dotenv", lambda **kw: None)
        monkeypatch.setenv("HEDGEPOINT_ENCRYPTION_KEY", "   ")
        from core.security.anonymizer import FieldEncryptor
        with pytest.raises(ValueError, match="HEDGEPOINT_ENCRYPTION_KEY"):
            FieldEncryptor()

    def test_different_keys_cannot_decrypt(self, monkeypatch):
        import core.security.anonymizer as anon_mod
        monkeypatch.setattr(anon_mod, "load_dotenv", lambda **kw: None)
        from core.security.anonymizer import FieldEncryptor
        from cryptography.exceptions import InvalidTag

        monkeypatch.setenv("HEDGEPOINT_ENCRYPTION_KEY", "key-one")
        enc1 = FieldEncryptor()
        token = enc1.encrypt("secreto")

        monkeypatch.setenv("HEDGEPOINT_ENCRYPTION_KEY", "key-two")
        enc2 = FieldEncryptor()
        with pytest.raises(InvalidTag):
            enc2.decrypt(token)


# ===========================================================================
# Anonymizer — entity registration
# ===========================================================================

class TestAnonymizerEntities:

    def test_company_labels_sequential(self, anon):
        assert anon.add_entity("company", "Empresa Uno S.A.")    == "Cliente A"
        assert anon.add_entity("company", "Empresa Dos S.A.")    == "Cliente B"
        assert anon.add_entity("company", "Empresa Tres S.A.")   == "Cliente C"

    def test_person_labels_sequential(self, anon):
        assert anon.add_entity("person", "María López")  == "Contacto 1"
        assert anon.add_entity("person", "Pedro Ruiz")   == "Contacto 2"
        assert anon.add_entity("person", "Laura Torres") == "Contacto 3"

    def test_add_entity_idempotent(self, anon):
        label_first  = anon.add_entity("company", "Acme Corp")
        label_second = anon.add_entity("company", "Acme Corp")
        assert label_first == label_second == "Cliente A"
        # Counter must not have advanced
        assert anon.add_entity("company", "Otra Empresa") == "Cliente B"

    def test_invalid_entity_type_raises(self, anon):
        with pytest.raises(ValueError, match="entity_type"):
            anon.add_entity("organization", "Acme")  # type: ignore[arg-type]

    def test_get_mapping_reflects_registered_entities(self, anon):
        anon.add_entity("company", "Norte S.A.")
        anon.add_entity("person",  "Ana Pérez")
        mapping = anon.get_mapping()
        assert mapping["Cliente A"] == "Norte S.A."
        assert mapping["Contacto 1"] == "Ana Pérez"

    def test_get_mapping_returns_copy(self, anon):
        anon.add_entity("company", "X S.A.")
        mapping = anon.get_mapping()
        mapping["Cliente A"] = "mutated"
        assert anon.get_mapping()["Cliente A"] == "X S.A."


# ===========================================================================
# Anonymizer — anonymize()
# ===========================================================================

class TestAnonymizerAnonymize:

    def test_replaces_registered_company(self, anon):
        anon.add_entity("company", "Importadora del Norte S.A.")
        result = anon.anonymize("Importadora del Norte S.A. compró divisas.")
        assert "Importadora del Norte S.A." not in result
        assert "Cliente A" in result

    def test_replaces_registered_person(self, anon):
        anon.add_entity("person", "Luis Hernández")
        result = anon.anonymize("El director Luis Hernández firmó el contrato.")
        assert "Luis Hernández" not in result
        assert "Contacto 1" in result

    def test_replaces_email(self, anon):
        result = anon.anonymize("Escríbeme a director@empresa.com.mx hoy.")
        assert "director@empresa.com.mx" not in result
        assert "[EMAIL]" in result

    def test_replaces_email_subdomain(self, anon):
        result = anon.anonymize("Correo: ops@mail.hedgepointmx.com")
        assert "[EMAIL]" in result

    def test_replaces_phone_with_prefix(self, anon):
        result = anon.anonymize("Llama al +52 55 1234 5678 urgente.")
        assert "1234 5678" not in result
        assert "[TELÉFONO]" in result

    def test_replaces_phone_without_prefix(self, anon):
        result = anon.anonymize("Mi número es 5512345678.")
        assert "5512345678" not in result
        assert "[TELÉFONO]" in result

    def test_replaces_phone_with_dashes(self, anon):
        result = anon.anonymize("Tel: +52-55-9876-5432")
        assert "9876-5432" not in result
        assert "[TELÉFONO]" in result

    def test_replaces_rfc_persona_moral(self, anon):
        result = anon.anonymize("RFC de la empresa: IMP800101ABC.")
        assert "IMP800101ABC" not in result
        assert "[RFC]" in result

    def test_replaces_rfc_persona_fisica(self, anon):
        result = anon.anonymize("RFC: MARA800101XY3")
        assert "MARA800101XY3" not in result
        assert "[RFC]" in result

    def test_replaces_rfc_with_dashes(self, anon):
        result = anon.anonymize("RFC MARA-800101-XY3 registrado.")
        assert "MARA-800101-XY3" not in result
        assert "[RFC]" in result

    def test_replaces_amount_comma_format(self, anon):
        result = anon.anonymize("Presupuesto de $500,000 para Q2.")
        assert "$500,000" not in result
        assert "[MONTO:" in result

    def test_replaces_amount_usd_prefix(self, anon):
        result = anon.anonymize("Exportó USD 1,200,000 el mes pasado.")
        assert "1,200,000" not in result
        assert "[MONTO:" in result

    def test_replaces_amount_k_suffix(self, anon):
        result = anon.anonymize("Inversión de $500K en inventario.")
        # $500K is replaced — it should only appear inside the [MONTO: ...] bracket,
        # not as a bare amount in the surrounding prose
        assert "[MONTO:" in result
        assert "inventario" in result  # context words survive
        assert result.startswith("Inversi")  # prefix unaffected

    def test_replaces_amount_m_suffix(self, anon):
        result = anon.anonymize("Capital de $1.2M disponible.")
        assert "$1.2M" not in result
        assert "[MONTO:" in result

    def test_amount_range_brackets(self, anon):
        """$547,000 should map to the ~$500K-$600K bracket."""
        result = anon.anonymize("Monto: $547,000.")
        assert "~$500K-$600K" in result

    def test_standalone_currency_code_survives(self, anon):
        """MXN not adjacent to a number must not be replaced."""
        result = anon.anonymize("El tipo de cambio MXN/USD subió hoy.")
        assert "MXN" in result

    def test_no_pii_text_unchanged(self, anon):
        text = "El mercado de divisas abrió al alza esta mañana."
        assert anon.anonymize(text) == text

    def test_longest_entity_replaced_first(self, anon):
        """Longer name must not be partially replaced by a shorter substring."""
        anon.add_entity("company", "Norte")
        anon.add_entity("company", "Importadora del Norte S.A.")
        result = anon.anonymize("Importadora del Norte S.A. opera en Norte de México.")
        assert "Importadora del Norte S.A." not in result
        # Both replacements happened
        assert result.count("Cliente") == 2

    def test_multiple_pii_types_in_one_text(self, anon):
        anon.add_entity("company", "Acme S.A.")
        anon.add_entity("person",  "Carlos Vega")
        text = (
            "Acme S.A. contactó a Carlos Vega al correo cvega@acme.mx, "
            "tel +52 33 9999 0000, RFC VECA801231AB3, monto $800,000."
        )
        result = anon.anonymize(text)
        assert "Acme S.A."      not in result
        assert "Carlos Vega"    not in result
        assert "cvega@acme.mx"  not in result
        assert "9999 0000"      not in result
        assert "VECA801231AB3"  not in result
        assert "$800,000"       not in result

    def test_custom_pattern(self, anon):
        anon.add_pattern(r"Proyecto\s+\w+", "[PROYECTO]")
        result = anon.anonymize("Avances en Proyecto Delta para el cliente.")
        assert "Proyecto Delta" not in result
        assert "[PROYECTO]" in result


# ===========================================================================
# Anonymizer — deanonymize()
# ===========================================================================

class TestAnonymizerDeanonymize:

    def test_deanonymize_restores_company(self, anon):
        anon.add_entity("company", "Distribuidora S.A.")
        clean = anon.anonymize("Distribuidora S.A. tiene buen historial.")
        restored = anon.deanonymize(clean)
        assert "Distribuidora S.A." in restored

    def test_deanonymize_restores_person(self, anon):
        anon.add_entity("person", "Sofía Ramírez")
        clean = anon.anonymize("Gerente: Sofía Ramírez.")
        restored = anon.deanonymize(clean)
        assert "Sofía Ramírez" in restored

    def test_deanonymize_does_not_restore_regex_placeholders(self, anon):
        """[EMAIL], [RFC], etc. cannot be reversed — they must remain."""
        clean = anon.anonymize("Email: info@empresa.com, RFC EMPA001231XY1.")
        restored = anon.deanonymize(clean)
        assert "[EMAIL]" in restored
        assert "[RFC]" in restored

    def test_deanonymize_roundtrip(self, anon):
        anon.add_entity("company", "Grupo Financiero Norte")
        anon.add_entity("person",  "Roberto Ávila")
        original = "Grupo Financiero Norte: contacto Roberto Ávila."
        assert anon.deanonymize(anon.anonymize(original)) == original


# ===========================================================================
# Anonymizer — reset()
# ===========================================================================

class TestAnonymizerReset:

    def test_reset_clears_mapping(self, anon):
        anon.add_entity("company", "Empresa X")
        anon.reset()
        assert anon.get_mapping() == {}

    def test_reset_restarts_label_counter(self, anon):
        anon.add_entity("company", "Primera")
        anon.reset()
        assert anon.add_entity("company", "Segunda") == "Cliente A"

    def test_reset_clears_custom_patterns(self, anon):
        anon.add_pattern(r"secreto", "[SECRETO]")
        anon.reset()
        result = anon.anonymize("Esto es secreto.")
        assert "[SECRETO]" not in result

    def test_reset_does_not_affect_new_session(self, anon):
        anon.add_entity("company", "Vieja Empresa")
        anon.reset()
        anon.add_entity("company", "Nueva Empresa")
        result = anon.anonymize("Nueva Empresa opera bien.")
        assert "Nueva Empresa" not in result
        assert "Cliente A" in result
