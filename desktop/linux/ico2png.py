"""Extrai o maior ícone de um .ico para .png, sem dependência nenhuma.

    python ico2png.py desktop/assets/tarjador.ico tarjador.png

Existe para o AppDir usar o MESMO ícone do executável do Windows. A alternativa
seria instalar ImageMagick ou icoutils na imagem de build só para converter um
arquivo de 12 KB — ou, pior, manter um PNG separado no repositório, que
divergiria do .ico na primeira vez que o ícone mudasse.

Funciona porque `desktop/assets/tarjador.ico` guarda as sete entradas já
codificadas em PNG (é o normal em .ico moderno a partir de 48x48; o formato
permite tanto PNG quanto DIB cru). Então "converter" aqui é recortar bytes:
lê-se o diretório do .ico, escolhe a maior entrada e grava o bloco como está.
Se a entrada escolhida for DIB em vez de PNG, o script falha em voz alta em
vez de gravar um arquivo inválido que só daria erro lá no appimagetool.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def maior_png(ico: bytes) -> bytes:
    reservado, tipo, n = struct.unpack_from("<HHH", ico, 0)
    if reservado != 0 or tipo != 1 or n == 0:
        raise SystemExit("não parece um .ico (cabeçalho inválido)")

    melhor = None
    for i in range(n):
        # largura/altura 0 significam 256 — o campo tem um byte só.
        larg, alt, _cores, _res, _planos, _bpp, tam, desloc = struct.unpack_from(
            "<BBBBHHII", ico, 6 + 16 * i
        )
        lado = (larg or 256) * (alt or 256)
        if melhor is None or lado > melhor[0]:
            melhor = (lado, desloc, tam, larg or 256, alt or 256)

    _lado, desloc, tam, larg, alt = melhor
    bloco = ico[desloc:desloc + tam]
    if not bloco.startswith(PNG_MAGIC):
        raise SystemExit(
            f"a entrada de {larg}x{alt} está em DIB, não em PNG — este script "
            "só recorta; converta o .ico ou gere o PNG à parte"
        )
    print(f"[ico2png] entrada escolhida: {larg}x{alt} ({tam} bytes)")
    return bloco


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    origem, destino = Path(argv[1]), Path(argv[2])
    destino.write_bytes(maior_png(origem.read_bytes()))
    print(f"[ico2png] {origem} -> {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
