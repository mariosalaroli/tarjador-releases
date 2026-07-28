#!/bin/bash
# Build do Tarjador Desktop para Linux (AppImage). Roda DENTRO da imagem
# desktop/linux/Dockerfile; ver desktop/linux/build.ps1 para o invólucro.
#
# Etapas: venv -> pins x produção -> bundle (PyInstaller) -> Tesseract irmão ->
# modelos de IA (só full) -> licenças -> selftest da IA (só full) -> smoke test
# (health + PÁGINA) -> AppDir -> AppImage -> SHA-256. É deliberadamente a MESMA
# sequência do build.ps1 do Windows: o que muda é o empacotador do final
# (appimagetool no lugar do Inno Setup) e o vendoring do OCR (ELF com RPATH em
# vez de DLLs numa pasta).
#
# Contratos com o host (definidos em build.ps1):
#   /repo    fonte, montado do Windows. Só se ESCREVE em /repo/desktop/Output.
#   /build   volume do container: venv, dist, work, AppDir. Fora do bind mount
#            de propósito — o pip e o PyInstaller fazem dezenas de milhares de
#            operações de arquivo, e no mount 9p do Docker Desktop isso é a
#            diferença entre 4 e 40 minutos.
#
#   --edition lite|full   qual edição construir (padrão: lite)
#   --sem-bundle          reempacota o que já está em /build/dist, pulando o
#                         PyInstaller (equivale ao -SkipBuild do build.ps1).
#                         Serve para iterar em AppRun/.desktop/ícone sem pagar
#                         os minutos da análise de novo.
set -euo pipefail

SEM_BUNDLE=0
EDITION=lite
while [ $# -gt 0 ]; do
    case "$1" in
        --sem-bundle) SEM_BUNDLE=1 ;;
        --edition)    shift; EDITION=${1:-}     ;;
        --edition=*)  EDITION=${1#*=}           ;;
        *) echo "argumento desconhecido: $1"; exit 2 ;;
    esac
    shift
done
case "$EDITION" in
    lite|full) ;;
    *) echo "edição inválida: '$EDITION' (use lite ou full)"; exit 2 ;;
esac

REPO=/repo
DESKTOP=$REPO/desktop
ASSETS=/opt/tarjador-build         # cópias de AppRun/.desktop/ico2png (sem CR)
BUILD=/build
# Um venv POR EDIÇÃO, como no Windows (.venv / .venv-full). O spec da Leve
# exclui torch por nome, mas venv sem torch é garantia melhor que exclude — e
# assim o venv da full nunca contamina o build leve.
VENV=$BUILD/venv-$EDITION
DIST=$BUILD/dist-$EDITION/Tarjador
APPDIR=$BUILD/AppDir-$EDITION
OUT=$DESKTOP/Output

etapa() { printf '\n\033[36m=== %s ===\033[0m\n' "$1"; }
ok()    { printf '\033[32mOK — %s\033[0m\n' "$1"; }

VERSAO_BASE=$(tr -d '[:space:]' < "$DESKTOP/VERSION")
VERSAO="$VERSAO_BASE-$EDITION"
APPIMAGE="$OUT/Tarjador-$VERSAO-x86_64.AppImage"
echo "Tarjador $VERSAO — glibc do build: $(ldd --version | head -1 | grep -oE '[0-9]+\.[0-9]+$')"

if [ "$SEM_BUNDLE" = 0 ]; then
# ---------------------------------------------------------------------------
etapa "Ambiente Python"
# ---------------------------------------------------------------------------
# O venv mora no volume /build e sobrevive entre builds (é o que evita
# reinstalar 500 MB de wheels a cada rodada). Só que ele guarda um caminho
# ABSOLUTO para o interpretador que o criou: se a imagem passa a embarcar outro
# Python, o venv antigo continua apontando para o anterior e o build inteiro
# roda no interpretador errado — foi assim que o "Python was built without a
# shared library" sobreviveu à correção que trocou o interpretador da imagem.
# Comparar o base_prefix é a checagem barata que fecha esse buraco.
PY_ATUAL=$(python3.12 -c 'import sys; print(sys.base_prefix)')
PY_VENV=$("$VENV/bin/python" -c 'import sys; print(sys.base_prefix)' 2>/dev/null || echo "")
if [ "$PY_VENV" != "$PY_ATUAL" ]; then
    [ -n "$PY_VENV" ] && echo "venv aponta para $PY_VENV, mas a imagem traz $PY_ATUAL — recriando"
    rm -rf "$VENV"
    python3.12 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip wheel
"$VENV/bin/python" -m pip install --quiet -r "$DESKTOP/requirements-$EDITION.txt"
ok "$("$VENV/bin/python" --version)"

# ---------------------------------------------------------------------------
etapa "Pins x produção"
# ---------------------------------------------------------------------------
# Mesmo motivo do Windows: barato, e o desktop não pode rodar um conjunto de
# versões que nunca existiu em produção.
"$VENV/bin/python" "$DESKTOP/checa_pins.py"
fi   # SEM_BUNDLE

# ---------------------------------------------------------------------------
etapa "Bundle (PyInstaller)"
# ---------------------------------------------------------------------------
rm -rf "$APPDIR"
if [ "$SEM_BUNDLE" = 1 ]; then
    echo "--sem-bundle: reaproveitando $DIST"
else
    rm -rf "$BUILD/dist-$EDITION"
    TARJADOR_BUILD_EDITION=$EDITION "$VENV/bin/pyinstaller" "$DESKTOP/tarjador.spec" \
        --noconfirm --distpath "$BUILD/dist-$EDITION" --workpath "$BUILD/work-$EDITION" \
        --log-level WARN
fi
[ -x "$DIST/Tarjador" ] || { echo "não há bundle em $DIST"; exit 1; }
ok "$(du -sh "$DIST" | cut -f1) em $DIST"

# ---------------------------------------------------------------------------
etapa "Tesseract (pasta irmã, fora do bundle)"
# ---------------------------------------------------------------------------
# O binário do sistema mais as libs dele, com RPATH reescrito para $ORIGIN.
# Assim `tesseract` roda sem LD_LIBRARY_PATH nenhum — que é o que permite ao
# AppRun não exportar a variável e, com isso, não contaminar o navegador que o
# app abre (ver a nota no AppRun).
#
# Libs da glibc NÃO entram: misturar a libc do container com o ld.so do usuário
# é o jeito mais rápido de produzir um binário que só funciona nesta máquina.
# Elas são justamente as que o piso de glibc 2.28 garante existir no destino.
TESS_DEST=$DIST/tesseract
TESS_BIN=$(command -v tesseract)
# Do zero, sempre. Com --sem-bundle o $DIST sobrevive entre rodadas, e copiar
# por cima deixaria resíduo de uma configuração anterior (um traineddata que se
# resolveu não embarcar, por exemplo) viajando no artefato sem ninguém notar.
rm -rf "$TESS_DEST"
mkdir -p "$TESS_DEST/lib" "$TESS_DEST/tessdata"
cp "$TESS_BIN" "$TESS_DEST/tesseract"

_GLIBC_CORE='^(ld-linux.*|libc|libm|libpthread|libdl|librt|libresolv|libutil|libnsl|libanl)\.so'
copia_deps() {
    local alvo=$1
    ldd "$alvo" 2>/dev/null | awk '/=> \//{print $3}' | while read -r so; do
        local base; base=$(basename "$so")
        [[ $base =~ $_GLIBC_CORE ]] && continue
        [ -e "$TESS_DEST/lib/$base" ] && continue
        cp -L "$so" "$TESS_DEST/lib/$base"
        copia_deps "$TESS_DEST/lib/$base"   # deps das deps (leptonica -> libtiff -> ...)
    done
}
copia_deps "$TESS_DEST/tesseract"

# --force-rpath grava DT_RPATH, não DT_RUNPATH. A diferença importa: RUNPATH
# não é herdado pelas dependências das dependências, então com ele a leptonica
# acharia a si mesma e perderia a libtiff. Ainda assim o $ORIGIN é gravado em
# TODAS as libs, para não depender dessa sutileza.
patchelf --force-rpath --set-rpath '$ORIGIN/lib' "$TESS_DEST/tesseract"
for so in "$TESS_DEST"/lib/*.so*; do
    patchelf --force-rpath --set-rpath '$ORIGIN' "$so" 2>/dev/null || true
done

# tessdata: só por e eng, das variantes `fast` baixadas na imagem (ver
# Dockerfile — as do RPM divergiriam de produção). `osd` (detecção de
# orientação) fica de fora pelo mesmo motivo do build Windows: o PyMuPDF não a
# usa, e são megabytes mortos.
cp /opt/tessdata/{por,eng}.traineddata "$TESS_DEST/tessdata/"
# Nada de `configs/`/`tessconfigs/` junto: são presets de formato de saída
# (hocr, pdf) que o PyMuPDF não usa — ele pede a TextPage direto. O bundle
# Windows também só leva os traineddata.

# Prova de que o vendoring funcionou: nenhuma lib pode ficar "not found", e o
# binário tem de listar os idiomas a partir do tessdata embarcado. Sem esta
# checagem o build entrega um OCR que só falha no PDF do usuário.
if ldd "$TESS_DEST/tesseract" | grep -q "not found"; then
    ldd "$TESS_DEST/tesseract" | grep "not found"
    echo "ERRO: dependência não resolvida no Tesseract vendorizado"; exit 1
fi
LANGS=$(cd / && TESSDATA_PREFIX="$TESS_DEST/tessdata" "$TESS_DEST/tesseract" --list-langs 2>&1 | tail -n +2 | tr '\n' ' ')
echo "$LANGS" | grep -q por || { echo "ERRO: 'por' ausente ($LANGS)"; exit 1; }
ok "tesseract $("$TESS_DEST/tesseract" --version 2>&1 | head -1 | awk '{print $2}') — idiomas: $LANGS ($(du -sh "$TESS_DEST" | cut -f1))"

# ---------------------------------------------------------------------------
etapa "Licença e créditos"
# ---------------------------------------------------------------------------
# Redistribuir Tesseract, leptonica, pdf.js e as libs Apache-2.0 obriga a levar
# os avisos junto; a atribuição do modelo do spaCy (CC BY-SA) idem. Ficam na
# raiz do payload, que é o que o AppDir empacota inteiro.
for doc in LICENSE THIRD-PARTY-NOTICES.txt; do
    cp "$REPO/$doc" "$DIST/$doc"
done
mkdir -p "$DIST/licenses"
cp "$DESKTOP"/vendor/licenses/* "$DIST/licenses/"
ok "LICENSE, THIRD-PARTY-NOTICES.txt e licenses/ no pacote"

# ---------------------------------------------------------------------------
etapa "Modelos de IA (pasta irmã — só na full)"
# ---------------------------------------------------------------------------
# É a PRESENÇA desta pasta que faz o lançador se declarar edição Completa
# (HF_HUB_CACHE + TARJADOR_EDITION=full) — não há carimbo de build. Na leve,
# garantir a AUSÊNCIA importa tanto quanto: um resto de build full sobrevivendo
# no volume viraria uma "Completa" sem torch, que quebra ao ligar o toggle.
DEST_MODELOS=$DIST/models
rm -rf "$DEST_MODELOS"
if [ "$EDITION" = full ]; then
    ls -d "$DESKTOP"/vendor/models/models--* >/dev/null 2>&1 || {
        echo "edição full exige o snapshot do BERT em desktop/vendor/models (ver desktop/README.md)"
        exit 1
    }
    mkdir -p "$DEST_MODELOS"
    cp -a "$DESKTOP"/vendor/models/. "$DEST_MODELOS/"
    ok "modelos em $DEST_MODELOS ($(du -sh "$DEST_MODELOS" | cut -f1))"
else
    echo "edição leve: sem pasta models (garantido)"
fi

if [ "$EDITION" = full ]; then
# ---------------------------------------------------------------------------
etapa "Selftest da IA (no bundle, offline)"
# ---------------------------------------------------------------------------
# Roda `Tarjador --selftest` DENTRO do bundle: importa torch, resolve o BERT do
# cache irmão sem rede e faz uma inferência real. O smoke test da página não
# cobre isso — o torch só carrega quando o usuário liga o toggle —, então sem
# este passo um bundle com a IA quebrada passaria no build e falharia na
# primeira análise do usuário.
"$DIST/Tarjador" --selftest
ok "IA funcional no bundle"
fi

# ---------------------------------------------------------------------------
etapa "AppDir"
# ---------------------------------------------------------------------------
mkdir -p "$APPDIR/usr/lib/tarjador" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
         "$APPDIR/usr/share/metainfo"
cp -a "$DIST"/. "$APPDIR/usr/lib/tarjador/"
install -m 755 "$ASSETS/AppRun" "$APPDIR/AppRun"
# python3.12 e não o do venv: o ico2png só usa stdlib, e assim o --sem-bundle
# funciona mesmo sem venv montado.
python3.12 "$ASSETS/ico2png.py" "$DESKTOP/assets/tarjador.ico" "$APPDIR/tarjador.png"
# O nome do arquivo tem de casar com o do .desktop: é assim que o appimagetool
# procura o metainfo (a convenção do AppStream moderno, nome = id em DNS
# reverso, ele não conhece). Com o nome "certo" o aviso de metadados ausentes
# continuaria aparecendo a cada build, e aviso que nunca some é aviso que se
# aprende a ignorar.
cp "$ASSETS/tarjador.appdata.xml" "$APPDIR/usr/share/metainfo/tarjador.appdata.xml"

# O .desktop e o ícone precisam existir na RAIZ do AppDir (é de lá que o
# appimagetool os lê) E em usr/share (é de lá que o desktop do usuário os lê,
# se ele integrar o AppImage ao menu). Duplicar é o padrão do formato.
cp "$ASSETS/tarjador.desktop" "$APPDIR/tarjador.desktop"
cp "$APPDIR/tarjador.desktop" "$APPDIR/usr/share/applications/"
cp "$APPDIR/tarjador.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
desktop-file-validate "$APPDIR/tarjador.desktop"
ok "AppDir montado ($(du -sh "$APPDIR" | cut -f1))"

# ---------------------------------------------------------------------------
etapa "Smoke test"
# ---------------------------------------------------------------------------
# Roda o AppDir pelo AppRun — o mesmo caminho de código do AppImage montado,
# sem precisar de FUSE (que não existe dentro do container). Um bundle que
# compila mas não abre é o modo de falha comum aqui.
rm -rf "$HOME/.local/share/Tarjador"
export TARJADOR_NO_BROWSER=1 TARJADOR_NO_SINGLE_INSTANCE=1
"$APPDIR/AppRun" & APP_PID=$!
PORTA=""; SAUDE=0
for _ in $(seq 1 90); do
    sleep 1
    kill -0 $APP_PID 2>/dev/null || break
    # `porta-<edição>.txt`, não `porta.txt`: o marcador foi separado por edição
    # para a Leve e a Completa não se atropelarem (ver `_marca_porta` no
    # lançador). Este era o consumidor esquecido quando o contrato mudou — o
    # smoke test nunca achava a porta e acusava "o servidor não subiu" num
    # bundle que estava perfeito.
    PORTA=$(cat "$HOME/.local/share/Tarjador/porta-$EDITION.txt" 2>/dev/null || true)
    [ -n "$PORTA" ] || continue
    if curl -fsS --max-time 2 "http://127.0.0.1:$PORTA/_stcore/health" >/dev/null 2>&1; then
        SAUDE=1; break
    fi
done

# /_stcore/health NÃO basta como prova de vida: ele responde 200 mesmo quando a
# rota que serve o frontend não foi montada — foi assim que um bundle que dava
# 404 na página passou no smoke test do Windows e chegou a ser instalado.
PAGINA=0; INFO="não testada (servidor não subiu)"
if [ "$SAUDE" = 1 ]; then
    CORPO=$(curl -fsS --max-time 20 "http://127.0.0.1:$PORTA/" 2>/dev/null || true)
    if grep -qE "streamlit|static/js" <<< "$CORPO"; then
        PAGINA=1; INFO="${#CORPO} bytes"
    else
        INFO="corpo inesperado (${#CORPO} bytes)"
    fi
fi
kill $APP_PID 2>/dev/null || true
wait $APP_PID 2>/dev/null || true
unset TARJADOR_NO_BROWSER TARJADOR_NO_SINGLE_INSTANCE

if [ "$SAUDE" != 1 ] || [ "$PAGINA" != 1 ]; then
    echo "--- log do app ---"
    tail -40 "$HOME/.local/share/Tarjador/tarjador.log" 2>/dev/null || echo "(sem log)"
    echo "smoke test FALHOU: saude=$SAUDE pagina=$INFO"
    exit 1
fi
ok "health e página respondendo na porta $PORTA ($INFO)"

# ---------------------------------------------------------------------------
etapa "AppImage"
# ---------------------------------------------------------------------------
mkdir -p "$OUT"
rm -f "$APPIMAGE" "$APPIMAGE.sha256"
# --runtime-file: o runtime com fuse3 estático (ver Dockerfile). Sem ele o
# usuário de Ubuntu 22.04+ precisaria instalar libfuse2 à mão.
ARCH=x86_64 appimagetool --runtime-file /opt/runtime-x86_64 "$APPDIR" "$APPIMAGE"
[ -f "$APPIMAGE" ] || { echo "appimagetool não produziu $APPIMAGE"; exit 1; }
chmod +x "$APPIMAGE"

# ---------------------------------------------------------------------------
etapa "Artefato"
# ---------------------------------------------------------------------------
# O SHA-256 não é zelo excessivo: é o que uma equipe de segurança pede para
# carimbar o artefato antes de liberar o download (ver desktop/README.md).
( cd "$OUT" && sha256sum "$(basename "$APPIMAGE")" | tee "$(basename "$APPIMAGE").sha256" )
printf '%-46s %8s\n' "$(basename "$APPIMAGE")" "$(du -h "$APPIMAGE" | cut -f1)"
