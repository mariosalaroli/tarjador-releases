"""Falha se os requirements do desktop divergirem dos de produção.

Por que existe: `requirements.txt` (raiz) e `desktop/requirements-*.txt` são
arquivos separados, e nada impede que um mude sem o outro. O desktop passaria
a rodar um conjunto de versões que nunca existiu em produção — exatamente o
que os pins existem para evitar (o segfault do pyarrow 25 nasceu de resolver
versão livremente). O `build.ps1` roda isto e aborta na divergência, então o
erro aparece no build e não na máquina do usuário.

A regra é só uma: **pacote presente nos dois arquivos tem de ter o MESMO
especificador de versão**. O que só existe num lado é legítimo e ignorado:

  * `pyinstaller` — ferramenta de build, não vai para produção;
  * `torch`/`transformers` — ausentes de propósito na edição Leve.

    python desktop/checa_pins.py            # confere lite e full
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESKTOP = Path(__file__).resolve().parent

# Linhas que não são dependências nomeadas (índice extra, wheel por URL).
_IGNORAR = ("--", "http://", "https://", "-r ", "-e ")
_NOME = re.compile(r"^([A-Za-z0-9._-]+)\s*(.*)$")


def le_pins(caminho: Path) -> dict[str, str]:
    """{nome normalizado: especificador} de um requirements.txt."""
    pins: dict[str, str] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if not linha or linha.startswith(_IGNORAR):
            continue
        m = _NOME.match(linha)
        if not m:
            continue
        # Normalização do PEP 503: `-`, `_` e `.` são equivalentes no nome.
        nome = re.sub(r"[-_.]+", "-", m.group(1)).lower()
        pins[nome] = m.group(2).replace(" ", "")
    return pins


def compara(raiz: dict[str, str], outro: dict[str, str], rotulo: str) -> list[str]:
    erros = []
    for nome, spec in outro.items():
        if nome in raiz and raiz[nome] != spec:
            erros.append(
                f"  {nome}: requirements.txt tem '{raiz[nome]}', "
                f"{rotulo} tem '{spec}'"
            )
    return erros


def main() -> int:
    raiz = le_pins(RAIZ / "requirements.txt")
    erros: list[str] = []
    for arquivo in ("requirements-lite.txt", "requirements-full.txt"):
        erros += compara(raiz, le_pins(DESKTOP / arquivo), arquivo)

    # A edição Completa tem de trazer a pilha de IA nas MESMAS versões de
    # produção — é o ponto inteiro dela.
    full = le_pins(DESKTOP / "requirements-full.txt")
    for obrigatorio in ("torch", "transformers"):
        if obrigatorio in raiz and obrigatorio not in full:
            erros.append(
                f"  {obrigatorio}: está em requirements.txt mas falta em "
                f"requirements-full.txt (a edição Completa ficaria sem ele)"
            )

    if erros:
        print("PINS DIVERGENTES entre produção e desktop:", file=sys.stderr)
        print("\n".join(erros), file=sys.stderr)
        print("\nAlinhe os arquivos: o desktop não deve rodar um conjunto de "
              "versões que nunca existiu em produção.", file=sys.stderr)
        return 1

    print(f"pins conferem ({len(raiz)} pacotes em requirements.txt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
