"""Detecção de PII em PDFs — motor COMPLETO (Presidio + spaCy).

Requer: presidio-analyzer, spacy e o modelo pt_core_news_sm (ver requirements.txt).

No Streamlit Cloud, selecione **Python 3.11** nas configurações do app
(spaCy 3.7 não possui wheel para Python 3.12+). Se o import abaixo falhar,
o app exibe o erro em vez de degradar silenciosamente para regex.
"""
from __future__ import annotations

from ._detector_full import (  # noqa: F401
    DEFAULT_ENTITIES,
    EntityGroup,
    Occurrence,
    detect_entities,
)

# Identifica o motor ativo (usado como indicador na UI).
BACKEND = "Presidio + spaCy"
