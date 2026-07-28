"""Gera THIRD-PARTY-NOTICES.txt a partir do ambiente de build.

Por que gerado e não escrito à mão: uma lista manual de dependências apodrece
no primeiro `pip install`, e aí o arquivo de créditos passa a mentir — que é
pior do que não existir. Aqui os pacotes Python saem dos metadados realmente
instalados; só os componentes que NÃO são pacotes Python (Tesseract, pdf.js,
modelos) ficam declarados à mão, porque não há metadado de onde extraí-los.

    desktop\\.venv-full\\Scripts\\python.exe desktop\\gera_notices.py

Roda no venv da edição COMPLETA de propósito: ele é o superconjunto (tem
torch/transformers), então o arquivo cobre as duas edições. O que sobra na
Leve é crédito a mais, nunca a menos.
"""
from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent
SAIDA = DESKTOP.parent / "THIRD-PARTY-NOTICES.txt"

CABECALHO = """AVISOS DE TERCEIROS — Tarjador
==============================

O Tarjador é distribuído sob a GNU Affero General Public License v3.0
(ver LICENSE). Este arquivo lista os componentes de terceiros incluídos na
distribuição e suas respectivas licenças, cumprindo as obrigações de
atribuição de cada uma.

ARQUIVO GERADO por desktop/gera_notices.py — não editar à mão.

"""

# Componentes que não são pacotes Python: não há metadado a consultar.
NAO_PYTHON = [
    ("Tesseract OCR", "5.4.0", "Apache-2.0",
     "https://github.com/tesseract-ocr/tesseract",
     "Copyright Google Inc. e colaboradores. Executável e bibliotecas\n"
     "     redistribuídos sem modificação (build UB Mannheim para Windows)."),
    ("Tesseract tessdata (por, eng)", "4.0.0", "Apache-2.0",
     "https://github.com/tesseract-ocr/tessdata_fast",
     "Modelos de reconhecimento de texto, redistribuídos sem modificação."),
    ("PDF.js", "4.x", "Apache-2.0",
     "https://github.com/mozilla/pdf.js",
     "Copyright 2024 Mozilla Foundation. Redistribuído sem modificação."),
    ("spaCy pt_core_news_sm", "3.8.0", "CC BY-SA 4.0",
     "https://spacy.io/models/pt",
     "Modelo de língua portuguesa. ATRIBUIÇÃO EXIGIDA pela licença:\n"
     "     criado pela Explosion AI, licenciado sob Creative Commons\n"
     "     Attribution-ShareAlike 4.0 International."),
    ("BERTimbau LeNER-Br (só na Edição Completa)", "—", "ver observação",
     "https://huggingface.co/pierreguillou/ner-bert-base-cased-pt-lenerbr",
     "Modelo de NER jurídico, por Pierre Guillou, derivado do BERTimbau\n"
     "     (neuralmind/bert-base-portuguese-cased, licença MIT).\n"
     "     OBSERVAÇÃO: o autor não declarou licença para o modelo afinado.\n"
     "     Os pesos são redistribuídos sem modificação, com crédito integral."),
]


def pacotes_python() -> list[tuple[str, str, str]]:
    """(nome, versão, licença) de tudo instalado, menos as ferramentas de
    build que não vão para o bundle."""
    fora = {"pyinstaller", "pyinstaller-hooks-contrib", "pip", "setuptools",
            "wheel", "altgraph", "pefile", "pywin32-ctypes"}
    itens = []
    for dist in md.distributions():
        nome = dist.metadata["Name"]
        if not nome or nome.lower() in fora:
            continue
        lic = (dist.metadata.get("License-Expression")
               or dist.metadata.get("License") or "").strip().splitlines()
        lic = lic[0] if lic else ""
        if not lic or len(lic) > 40:
            # Muitos pacotes põem o texto inteiro no campo License; nesse caso
            # o classificador é mais confiável.
            cls = [c for c in (dist.metadata.get_all("Classifier") or [])
                   if c.startswith("License ::")]
            lic = cls[0].split("::")[-1].strip() if cls else (lic[:40] or "ver pacote")
        itens.append((nome, dist.version, lic))
    return sorted(itens, key=lambda x: x[0].lower())


def main() -> int:
    linhas = [CABECALHO]

    linhas.append("COMPONENTES PRINCIPAIS\n" + "-" * 70 + "\n")
    linhas.append(
        "PyMuPDF — GNU AGPL v3.0 (ou licença comercial Artifex)\n"
        "     https://github.com/pymupdf/PyMuPDF\n"
        "     É por causa desta dependência que o Tarjador é distribuído sob\n"
        "     AGPL-3.0: a licença é copyleft e alcança a obra combinada.\n")
    for nome, ver, lic, url, obs in NAO_PYTHON:
        linhas.append(f"{nome} — {lic} (versão {ver})\n     {url}\n     {obs}\n")

    linhas.append("\nBIBLIOTECAS PYTHON\n" + "-" * 70 + "\n")
    pkgs = pacotes_python()
    for nome, ver, lic in pkgs:
        linhas.append(f"  {nome:<28} {ver:<14} {lic}")

    linhas.append(
        "\n\nONDE ESTÃO OS TEXTOS DAS LICENÇAS\n" + "-" * 70 + "\n"
        "  licenses/Apache-2.0.txt     — Tesseract, pdf.js e as bibliotecas\n"
        "                                Python sob Apache 2.0\n"
        "  licenses/CC-BY-SA-4.0.txt   — modelo pt_core_news_sm do spaCy\n"
        "  LICENSE                     — AGPL-3.0, do Tarjador e do PyMuPDF\n"
        "  _internal/*.dist-info/      — licenças de cada biblioteca Python\n"
        "\nA cláusula 4(a) da Apache 2.0 exige entregar uma CÓPIA da licença a\n"
        "quem recebe o software, não apenas citá-la — por isso os textos\n"
        "integrais acompanham a instalação.\n")

    SAIDA.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"{SAIDA.name}: {len(pkgs)} pacotes Python + "
          f"{len(NAO_PYTHON) + 1} componentes declarados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
