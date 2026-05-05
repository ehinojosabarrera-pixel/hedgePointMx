"""
Utilidades compartidas para HedgePoint MX.
"""

from __future__ import annotations

import re


def strip_markdown(text: str) -> str:
    """Remove common markdown syntax, leaving clean plain text."""
    # Remove ATX headings (##, ###, etc.)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic (**text**, __text__, *text*, _text_)
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", text)
    # Remove unordered list bullets (- item, * item)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    # Remove ordered list numbers (1. item → item)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Remove inline code
    text = re.sub(r"`(.+?)`", r"\1", text)
    # Remove horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Collapse multiple blank lines into one
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
