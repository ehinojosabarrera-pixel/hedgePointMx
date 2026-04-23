"""
Security utilities for HedgePoint MX — encryption and anonymization.

Classes
-------
FieldEncryptor
    AES-256-GCM symmetric encryption for sensitive database fields (names,
    emails, phone numbers, RFC).  The key is derived from an environment
    variable so that no secret ever lives in source code.

Anonymizer
    Text middleware that scrubs personally-identifiable information before
    any string is sent to an external LLM API (Claude).  Supports named
    entities (companies, people), regex-based patterns (emails, phones,
    RFC, monetary amounts), and arbitrary custom patterns.  Includes a
    reverse mapping for internal audit logs.

Usage
-----
    # --- FieldEncryptor ---
    import os
    os.environ["HEDGEPOINT_ENCRYPTION_KEY"] = "mi-passphrase-secreto-32chars+"

    from core.security.anonymizer import FieldEncryptor

    enc = FieldEncryptor()
    token = enc.encrypt("Juan Pérez García")
    name  = enc.decrypt(token)          # "Juan Pérez García"

    # --- Anonymizer ---
    from core.security.anonymizer import Anonymizer

    anon = Anonymizer()
    label_co = anon.add_entity("company", "Importadora del Norte S.A.")
    label_pe = anon.add_entity("person",  "Ana Martínez")

    clean = anon.anonymize(
        "Importadora del Norte S.A. factura $1,547,000 USD al mes. "
        "Contacto: Ana Martínez, ana@importadora.com, +52 55 1234 5678, "
        "RFC MARA-800101-ABC."
    )
    # "Cliente A factura [MONTO: ~$1.5M-$1.6M] USD al mes.
    #  Contacto: Contacto 1, [EMAIL], [TELÉFONO], [RFC]."

    internal_log = anon.deanonymize(clean)  # restores original names only
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import os
import re
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENV_KEY = "HEDGEPOINT_ENCRYPTION_KEY"
_NONCE_BYTES = 12  # 96-bit nonce recommended for AES-GCM

# Alphabetic labels for anonymized entities
_COMPANY_LABELS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]   # A-Z
_PERSON_LABELS  = list(range(1, 100))                                  # 1-99

EntityType = Literal["company", "person"]


# ---------------------------------------------------------------------------
# FieldEncryptor
# ---------------------------------------------------------------------------

class FieldEncryptor:
    """
    AES-256-GCM encryption for sensitive SQLite fields.

    The encryption key is derived by computing SHA-256 of the passphrase
    stored in the environment variable ``HEDGEPOINT_ENCRYPTION_KEY``.
    This gives a deterministic 256-bit key without requiring the caller
    to manage raw bytes.

    The on-disk token format is::

        base64( nonce_12B || ciphertext || auth_tag_16B )

    The authentication tag is appended automatically by AESGCM and verified
    on decryption, so any tampering raises ``cryptography.exceptions.InvalidTag``.

    Parameters
    ----------
    env_var : str, optional
        Name of the environment variable that holds the passphrase.
        Defaults to ``HEDGEPOINT_ENCRYPTION_KEY``.

    Raises
    ------
    ValueError
        If the environment variable is not set or is empty.

    Examples
    --------
    ::

        import os
        os.environ["HEDGEPOINT_ENCRYPTION_KEY"] = "mi-passphrase-super-secreto"

        enc = FieldEncryptor()

        token = enc.encrypt("contacto@empresa.com")
        plain = enc.decrypt(token)   # "contacto@empresa.com"
    """

    def __init__(self, env_var: str = _ENV_KEY) -> None:
        passphrase = os.environ.get(env_var, "").strip()
        if not passphrase:
            raise ValueError(
                f"La variable de entorno '{env_var}' no está definida. "
                "Para generar una clave segura ejecuta:\n\n"
                "    python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n\n"
                f"Luego agrega al archivo .env:\n    {env_var}=<clave_generada>"
            )
        # Derive a 256-bit key deterministically from the passphrase
        self._key = hashlib.sha256(passphrase.encode("utf-8")).digest()
        self._aesgcm = AESGCM(self._key)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a UTF-8 string and return a base64-encoded token.

        The token embeds the nonce so each call produces a different output
        even for the same plaintext (nonce is random 12 bytes from os.urandom).

        Parameters
        ----------
        plaintext : str
            The sensitive value to encrypt (name, email, phone, RFC, etc.).

        Returns
        -------
        str
            URL-safe base64 string: ``base64(nonce || ciphertext || tag)``.
        """
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext_and_tag = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        token_bytes = nonce + ciphertext_and_tag
        return base64.urlsafe_b64encode(token_bytes).decode("ascii")

    def decrypt(self, token: str) -> str:
        """
        Decrypt a base64-encoded token produced by :meth:`encrypt`.

        Parameters
        ----------
        token : str
            The base64 token returned by :meth:`encrypt`.

        Returns
        -------
        str
            The original plaintext string.

        Raises
        ------
        cryptography.exceptions.InvalidTag
            If the ciphertext was tampered with or the key is wrong.
        ValueError
            If the token is malformed (too short to contain a nonce).
        """
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        if len(raw) < _NONCE_BYTES:
            raise ValueError("Token demasiado corto — no contiene nonce válido.")
        nonce = raw[:_NONCE_BYTES]
        ciphertext_and_tag = raw[_NONCE_BYTES:]
        plaintext_bytes = self._aesgcm.decrypt(nonce, ciphertext_and_tag, None)
        return plaintext_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# Anonymizer
# ---------------------------------------------------------------------------

class Anonymizer:
    """
    Text anonymization middleware for LLM API calls.

    Replaces personally-identifiable information with opaque placeholders
    before any text is sent to an external service (Claude API).  A reverse
    mapping is kept in memory for internal audit logs; it is **never**
    serialized or transmitted externally.

    Named entities (companies, people) receive deterministic human-readable
    labels:

    * Companies  → ``Cliente A``, ``Cliente B``, … ``Cliente Z``
    * People     → ``Contacto 1``, ``Contacto 2``, …

    Built-in regex patterns (applied after named-entity replacement):

    * Email addresses                → ``[EMAIL]``
    * Mexican phone numbers           → ``[TELÉFONO]``
    * Mexican RFC                    → ``[RFC]``
    * Monetary amounts (MXN / USD)   → ``[MONTO: ~$XK-$YK]`` (rounded range)
    * Custom patterns via :meth:`add_pattern`

    Examples
    --------
    ::

        anon = Anonymizer()

        # Register known entities
        anon.add_entity("company", "Importadora del Norte S.A.")
        anon.add_entity("person",  "Ana Martínez")

        text = (
            "Importadora del Norte S.A. factura $1,547,000 MXN al mes. "
            "Contacto: Ana Martínez — ana@importadora.com, RFC MARA800101ABC."
        )
        clean = anon.anonymize(text)
        # "Cliente A factura [MONTO: ~$1.5M-$1.6M] MXN al mes.
        #  Contacto: Contacto 1 — [EMAIL], [RFC]."

        # Reverse for internal logs only
        restored = anon.deanonymize(clean)

        # Inspect the mapping
        mapping = anon.get_mapping()
        # {"Cliente A": "Importadora del Norte S.A.", "Contacto 1": "Ana Martínez"}

        # Start a new session
        anon.reset()
    """

    def __init__(self) -> None:
        # label -> original name (for deanonymization)
        self._label_to_name: dict[str, str] = {}
        # original name -> label (for fast lookup during anonymize)
        self._name_to_label: dict[str, str] = {}

        self._company_count: int = 0
        self._person_count: int  = 0

        # List of (compiled_pattern, replacement) for custom patterns
        self._custom_patterns: list[tuple[re.Pattern[str], str]] = []

    # ------------------------------------------------------------------
    # Public entity registration
    # ------------------------------------------------------------------

    def add_entity(self, entity_type: EntityType, name: str) -> str:
        """
        Register a named entity and return its anonymized label.

        If the same name is registered twice, the existing label is returned
        without creating a duplicate entry.

        Parameters
        ----------
        entity_type : {"company", "person"}
            Category of the entity.
        name : str
            The real name to anonymize (e.g. ``"Importadora del Norte S.A."``).

        Returns
        -------
        str
            The label assigned to this entity (e.g. ``"Cliente A"``).

        Raises
        ------
        ValueError
            If ``entity_type`` is not ``"company"`` or ``"person"``.
        ValueError
            If the company label space (A-Z, 26 slots) is exhausted.
        """
        if entity_type not in ("company", "person"):
            raise ValueError(f"entity_type debe ser 'company' o 'person', recibido: {entity_type!r}")

        if name in self._name_to_label:
            return self._name_to_label[name]

        if entity_type == "company":
            if self._company_count >= len(_COMPANY_LABELS):
                raise ValueError("Se agotaron las etiquetas de empresa (A-Z). Usa reset() para reiniciar.")
            label = f"Cliente {_COMPANY_LABELS[self._company_count]}"
            self._company_count += 1
        else:
            label = f"Contacto {_PERSON_LABELS[self._person_count % len(_PERSON_LABELS)]}"
            self._person_count += 1

        self._label_to_name[label] = name
        self._name_to_label[name]  = label
        logger.debug("Anonymizer: registered %s -> %s", name, label)
        return label

    def add_pattern(self, pattern: str | re.Pattern[str], replacement: str) -> None:
        """
        Register a custom regex pattern for anonymization.

        Custom patterns are applied after all built-in rules.

        Parameters
        ----------
        pattern : str or re.Pattern
            A regex pattern string or compiled pattern.
        replacement : str
            The literal string to substitute each match with.

        Examples
        --------
        ::

            anon.add_pattern(r"Proyecto\\s+\\w+", "[PROYECTO]")
        """
        if isinstance(pattern, str):
            pattern = re.compile(pattern, re.IGNORECASE)
        self._custom_patterns.append((pattern, replacement))

    # ------------------------------------------------------------------
    # Core anonymization
    # ------------------------------------------------------------------

    def anonymize(self, text: str) -> str:
        """
        Return a copy of *text* with all PII replaced by placeholders.

        Replacement order
        -----------------
        1. Registered company names (longest first to avoid partial matches)
        2. Registered person names (longest first)
        3. Email addresses
        4. Mexican phone numbers (10-digit, with/without +52 prefix)
        5. Mexican RFC (3-4 letters + 6 digits + 3 alphanumeric)
        6. Monetary amounts  →  rounded range bracket
        7. Custom patterns registered via :meth:`add_pattern`

        Parameters
        ----------
        text : str
            Raw text that may contain PII.

        Returns
        -------
        str
            Anonymized text safe to send to external APIs.
        """
        result = text

        # 1 & 2 — Named entities (longest name first prevents partial replacement)
        companies = sorted(
            [(n, l) for n, l in self._name_to_label.items() if l.startswith("Cliente")],
            key=lambda x: len(x[0]),
            reverse=True,
        )
        for name, label in companies:
            result = result.replace(name, label)

        persons = sorted(
            [(n, l) for n, l in self._name_to_label.items() if l.startswith("Contacto")],
            key=lambda x: len(x[0]),
            reverse=True,
        )
        for name, label in persons:
            result = result.replace(name, label)

        # 3 — Email
        result = _RE_EMAIL.sub("[EMAIL]", result)

        # 4 — Mexican phone numbers
        result = _RE_PHONE_MX.sub("[TELÉFONO]", result)

        # 5 — RFC mexicano
        result = _RE_RFC.sub("[RFC]", result)

        # 6 — Monetary amounts
        result = _RE_AMOUNT.sub(_replace_amount, result)

        # 7 — Custom patterns
        for pat, repl in self._custom_patterns:
            result = pat.sub(repl, result)

        return result

    def deanonymize(self, text: str) -> str:
        """
        Restore registered entity labels to their original names.

        .. warning::
            This method is intended **only** for internal audit logs.
            Never pass the output to an external API or client-facing surface.

        Only named-entity labels (``Cliente A``, ``Contacto 1``, …) are
        restored.  Regex placeholders (``[EMAIL]``, ``[RFC]``, etc.) cannot
        be reversed as the original values were never stored in memory.

        Parameters
        ----------
        text : str
            Anonymized text containing entity labels.

        Returns
        -------
        str
            Text with entity labels replaced by original names.
        """
        result = text
        # Replace longest labels first to avoid substring collisions
        for label, name in sorted(self._label_to_name.items(), key=lambda x: len(x[0]), reverse=True):
            result = result.replace(label, name)
        return result

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def get_mapping(self) -> dict[str, str]:
        """
        Return a snapshot of the current label → original-name mapping.

        Returns
        -------
        dict[str, str]
            Keys are labels (e.g. ``"Cliente A"``), values are original names.
        """
        return dict(self._label_to_name)

    def reset(self) -> None:
        """
        Clear all registered entities and custom patterns.

        Call this between prospect sessions to avoid label collisions.
        """
        self._label_to_name.clear()
        self._name_to_label.clear()
        self._company_count = 0
        self._person_count  = 0
        self._custom_patterns.clear()
        logger.debug("Anonymizer: state reset.")


# ---------------------------------------------------------------------------
# Private regex patterns
# ---------------------------------------------------------------------------

# Email — simple but effective for business contexts
_RE_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    re.IGNORECASE,
)

# Mexican phone numbers:
#   +52 55 1234 5678  |  +52-55-1234-5678  |  5512345678  |  55 1234 5678
_RE_PHONE_MX = re.compile(
    r"(?:\+52[\s\-]?)?(?:\d{2}[\s\-]?\d{4}[\s\-]?\d{4}|\d{10})\b"
)

# RFC mexicano — persona moral (3 letras) o física (4 letras), fecha 6 dígitos,
# homoclave 3 alfanuméricos.  Acepta guiones opcionales entre secciones.
_RE_RFC = re.compile(
    r"\b[A-ZÑ&]{3,4}-?\d{6}-?[A-Z0-9]{3}\b",
    re.IGNORECASE,
)

# Monetary amounts — varios formatos comunes en México:
#   $1,547,000    $1547000    $500K    USD 1.2M    1,200,000 MXN
#   500000 USD    $1.5M       1.2 millones
#
# The number part is mandatory; prefix/suffix are optional but must not
# produce a zero-length match.  We use an atomic alternation: the two
# branches require at least one digit each.
_RE_AMOUNT = re.compile(
    r"""
    (?:                                # optional currency prefix
        (?:USD|MXN)\s+                 #   USD 1,000 / MXN 1,000  (word + space)
      | \$\s*                          #   $1,000 / $ 1,000
    )?
    (?:
        \d{1,3}(?:,\d{3})+            # thousands-separated:  1,234  /  1,234,567
        (?:\.\d+)?
      |
        \d+\.\d+                       # decimal:  1.5 / 1.25
      |
        \d+(?=[KkMmBb]\b)             # integer before K/M/B suffix (any length)
      |
        \d{4,}                         # bare integer ≥ 4 digits (no suffix needed)
    )
    (?:                                # optional magnitude suffix  (K / M / B)
        [KkMmBb]\b                     #   $500K  $1.2M  — but NOT $500 MXN
    )?
    (?:                                # optional currency suffix (word boundary)
        \s+(?:USD|MXN|pesos?|dólares?)
        \b
    )?
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _to_number(match_str: str) -> float | None:
    """
    Parse a raw amount string (possibly with K/M/B suffixes and currency
    symbols) into a plain float.  Returns None if the string cannot be
    parsed as a meaningful monetary amount.
    """
    s = match_str.strip()
    # Remove currency symbols and words
    s = re.sub(r"[\$]", "", s)
    s = re.sub(r"\b(?:USD|MXN|pesos?|dólares?)\b", "", s, flags=re.IGNORECASE)
    s = s.strip()

    # Detect suffix multiplier
    multiplier = 1.0
    suffix_match = re.search(r"([KkMmBb])(?:illones?)?$", s, re.IGNORECASE)
    if suffix_match:
        letter = suffix_match.group(1).upper()
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[letter]
        s = s[: suffix_match.start()].strip()

    # Remove thousands separators
    s = s.replace(",", "")
    try:
        value = float(s) * multiplier
    except ValueError:
        return None

    # Ignore trivially small numbers that aren't monetary amounts
    if value < 100:
        return None

    return value


def _format_range(low: float, high: float) -> str:
    """Return a human-readable range string like ~$500K-$600K or ~$1.5M-$1.6M."""

    def _fmt(v: float) -> str:
        if v >= 1_000_000_000:
            return f"${v / 1_000_000_000:.1f}B"
        if v >= 1_000_000:
            return f"${v / 1_000_000:.1f}M"
        if v >= 1_000:
            return f"${v / 1_000:.0f}K"
        return f"${v:.0f}"

    return f"[MONTO: ~{_fmt(low)}-{_fmt(high)}]"


def _round_to_range(value: float) -> tuple[float, float]:
    """
    Round *value* to the nearest bracket so the range reveals rough
    magnitude without exposing the exact figure.

    Bracket logic
    -------------
    * < 10 K      → nearest 1 K
    * < 100 K     → nearest 10 K
    * < 1 M       → nearest 100 K
    * < 10 M      → nearest 500 K
    * < 100 M     → nearest 5 M
    * ≥ 100 M     → nearest 50 M
    """
    if value < 10_000:
        step = 1_000
    elif value < 100_000:
        step = 10_000
    elif value < 1_000_000:
        step = 100_000
    elif value < 10_000_000:
        step = 500_000
    elif value < 100_000_000:
        step = 5_000_000
    else:
        step = 50_000_000

    low  = math.floor(value / step) * step
    high = low + step
    return low, high


def _replace_amount(m: re.Match[str]) -> str:
    """Regex substitution callback for ``_RE_AMOUNT``."""
    raw = m.group(0)
    value = _to_number(raw)
    if value is None:
        return raw  # not a monetary amount — leave unchanged
    low, high = _round_to_range(value)
    return _format_range(low, high)
