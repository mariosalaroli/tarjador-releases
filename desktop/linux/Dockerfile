# Imagem de build do Tarjador Desktop para Linux (AppImage).
#
#   docker build -t tarjador-appimage -f desktop/linux/Dockerfile desktop/linux
#
# Por que manylinux e não "um Ubuntu qualquer": binário do PyInstaller só roda
# em glibc IGUAL OU MAIS NOVA que a da máquina onde foi construído. Construir na
# distro do desenvolvedor entrega um AppImage que morre em toda máquina mais
# antiga que a dele — e o público-alvo (estação de órgão público) é justamente
# o lado antigo dessa conta. Esta imagem é glibc 2.28, o que cobre Debian 10+,
# Ubuntu 18.10+, RHEL/Rocky/Alma 8+, Fedora 29+ e openSUSE 15.1+.
#
# Ela também resolve o problema do interpretador: a pilha do Tarjador roda em
# Python 3.12 (mesma versão do build Windows e da produção), e nenhuma distro
# com glibc velha empacota 3.12 — o deadsnakes parou no 3.9 para o focal.
FROM quay.io/pypa/manylinux_2_28_x86_64

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Tesseract vem do EPEL 8: é o 4.1.1, um degrau abaixo do 5.x que o build
# Windows vendoriza. Para o que o app faz com ele — OCR de selo gov.br
# rasterizado, ver tarjador/core/redactor.py — a diferença é irrelevante: o
# motor LSTM que reconhece o texto do selo é o mesmo desde o 4.0.
# Só o motor: os modelos de idioma vêm à parte, logo abaixo.
RUN dnf install -y -q epel-release \
 && dnf install -y -q \
      tesseract \
      file \
      squashfs-tools \
      desktop-file-utils \
 && dnf clean all \
 && command -v patchelf   # vem com a imagem (o auditwheel depende dele)

# Modelos de idioma do tessdata_fast, e não os que vêm no RPM.
#
# Motivo é paridade: o build Windows e a produção (packages.txt, via Debian)
# usam as variantes `fast`, e OCR com modelo diferente devolve texto diferente
# — o que faria o mesmo PDF ter selo detectado numa plataforma e não na outra,
# a pior classe de divergência para depurar. De quebra, o `eng.traineddata` que
# o pacote do el8 embute é a variante completa, de 23 MB, contra 4 MB da fast.
#
# `por` é o que `_page_ocr_textpage` pede primeiro; `eng` é o fallback dele.
ARG TESSDATA_TAG=4.1.0
RUN mkdir -p /opt/tessdata \
 && for lang in por eng; do \
      curl -fsSL "https://github.com/tesseract-ocr/tessdata_fast/raw/${TESSDATA_TAG}/${lang}.traineddata" \
        -o "/opt/tessdata/${lang}.traineddata"; \
    done \
 && ls -la /opt/tessdata

# appimagetool: distribuído como AppImage, e AppImage precisa de FUSE, que não
# existe dentro de container sem --privileged. Extrair de uma vez aqui evita
# depender disso no build (o --appimage-extract-and-run faria a mesma extração
# a CADA execução, para o mesmo resultado).
#
# O runtime vai junto e explícito: é o do type2-runtime, com fuse3 ligado
# ESTATICAMENTE. Sem ele o usuário precisaria instalar `libfuse2` à mão — que
# o Ubuntu 22.04+ não traz mais — e o sintoma é o AppImage não abrir com uma
# mensagem que ninguém entende. É a diferença entre "baixou e usou" e um
# chamado de suporte por usuário.
ARG APPIMAGETOOL_URL=https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
ARG RUNTIME_URL=https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64
RUN curl -fsSL "$APPIMAGETOOL_URL" -o /tmp/appimagetool.AppImage \
 && chmod +x /tmp/appimagetool.AppImage \
 && cd /opt && /tmp/appimagetool.AppImage --appimage-extract > /dev/null \
 && mv /opt/squashfs-root /opt/appimagetool \
 && ln -s /opt/appimagetool/AppRun /usr/local/bin/appimagetool \
 && rm -f /tmp/appimagetool.AppImage \
 && curl -fsSL "$RUNTIME_URL" -o /opt/runtime-x86_64 \
 && chmod +x /opt/runtime-x86_64

# CPython próprio, compilado com --enable-shared.
#
# Os interpretadores que a imagem já traz em /opt/python/cp3XX-cp3XX NÃO servem:
# são estáticos (é o que faz sentido para construir wheels) e o PyInstaller
# recusa com "Python was built without a shared library" — ele precisa embutir
# libpython3.12.so.1.0 no bundle. Compilar aqui custa uma camada de alguns
# minutos, cacheada, e entrega um CPython comum, sem as peculiaridades de
# distribuições redistribuíveis tipo python-build-standalone.
#
# Sem --enable-optimizations de propósito: o PGO roda a suíte de testes e
# multiplica o tempo do build por algo entre três e cinco, para ganhar uns 10%
# num arranque que é dominado pelo import do spaCy e do Presidio, não pelo
# interpretador. O -rpath evita depender de LD_LIBRARY_PATH para achar a
# própria libpython.
ARG PY_VERSION=3.12.13
RUN dnf install -y -q \
      openssl-devel bzip2-devel libffi-devel zlib-devel xz-devel \
      sqlite-devel readline-devel \
 && curl -fsSL "https://www.python.org/ftp/python/${PY_VERSION}/Python-${PY_VERSION}.tgz" \
      -o /tmp/python.tgz \
 && tar -xf /tmp/python.tgz -C /tmp \
 && cd "/tmp/Python-${PY_VERSION}" \
 && ./configure --prefix=/opt/python-shared --enable-shared --without-static-libpython \
      LDFLAGS="-Wl,-rpath,/opt/python-shared/lib" > /dev/null \
 && make -j"$(nproc)" > /dev/null \
 && make install > /dev/null \
 && rm -rf /tmp/python.tgz "/tmp/Python-${PY_VERSION}" \
 && dnf clean all \
 && /opt/python-shared/bin/python3.12 -c "import ssl, sqlite3, lzma, ctypes; print('CPython', __import__('sys').version)"

ENV PATH=/opt/python-shared/bin:$PATH \
    ARCH=x86_64 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# Os scripts entram na IMAGEM em vez de virem do bind mount porque nascem num
# checkout Windows: com core.autocrlf, `build-appimage.sh` e `AppRun` chegariam
# com CRLF, e o kernel responde a um shebang terminado em \r com
# "bad interpreter: /bin/bash^M" — erro que não diz nada sobre a causa. O `sed`
# abaixo normaliza de uma vez. (O código do app continua vindo de /repo: é ele
# que muda a cada build; estes quatro arquivos, não.)
COPY . /opt/tarjador-build/
RUN find /opt/tarjador-build -type f \
        \( -name '*.sh' -o -name 'AppRun' -o -name '*.py' -o -name '*.desktop' \) \
        -exec sed -i 's/\r$//' {} + \
 && chmod +x /opt/tarjador-build/build-appimage.sh /opt/tarjador-build/AppRun

WORKDIR /build
