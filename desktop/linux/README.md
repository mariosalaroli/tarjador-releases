# Tarjador Desktop — Linux (AppImage)

Produz `desktop/Output/Tarjador-<versão>-lite-x86_64.AppImage`: um arquivo
único, sem instalação, sem root, que o usuário baixa e executa. É o equivalente
Linux do ZIP portátil do Windows — mesma UI, mesmo `app.py`, mesmo lançador.

Medido na 1.0.1 (edição Leve): **202 MB** de AppImage, 633 MB descompactado.
Para comparar, o ZIP portátil do Windows da mesma versão tem 205 MB.

## Construir

```powershell
powershell -ExecutionPolicy Bypass -File desktop\linux\build.ps1
powershell -ExecutionPolicy Bypass -File desktop\linux\build.ps1 -Limpar   # do zero
```

Precisa do Docker Desktop rodando, e só. O build acontece inteiro dentro do
container; do repositório sai apenas o código-fonte e entra apenas o artefato
final em `desktop/Output/`.

Para depurar de dentro:

```powershell
docker run --rm -it -v "${PWD}:/repo" -v "tarjador-appimage-build:/build" tarjador-appimage bash
```

## Por que um container, e por que manylinux

Binário do PyInstaller só roda em glibc **igual ou mais nova** que a da máquina
onde foi construído. Construir na distro do desenvolvedor entrega um AppImage
que morre em toda máquina mais antiga que a dele — e o público-alvo, estação de
órgão público, é exatamente o lado antigo dessa conta.

A imagem `quay.io/pypa/manylinux_2_28_x86_64` fixa o piso em **glibc 2.28**, o
que cobre Debian 10+, Ubuntu 18.10+, RHEL/Rocky/Alma 8+, Fedora 29+ e openSUSE
15.1+. Não é piso teórico: varrendo todos os ELF do bundle com `objdump -T`, a
maior versão exigida é exatamente `GLIBC_2.28`, imposta por `libpython3.12.so`,
`libarrow.so` e `libsqlite3.so`.

O interpretador, porém, ela **não** resolve — e essa é a pegadinha do caminho.
Os CPython que a imagem traz em `/opt/python/cp3XX-cp3XX` são compilados
estaticamente (faz sentido para construir wheels), e o PyInstaller recusa com
`Python was built without a shared library`: ele precisa embutir
`libpython3.12.so.1.0` no bundle. Por isso o Dockerfile compila um CPython 3.12
próprio com `--enable-shared`. Trocar de distro não ajudaria: nenhuma com glibc
antiga empacota 3.12 — o deadsnakes parou no 3.9 para o Ubuntu 20.04.

## O que difere do build Windows

O `tarjador.spec` e o `run_tarjador.py` são os MESMOS arquivos, com três
ramificações por plataforma e nada além disso — duas listas de imports para
manter sincronizadas seria a receita para a edição Linux quebrar num import que
ninguém lembrou de adicionar.

| | Windows | Linux |
|---|---|---|
| Empacotador final | Inno Setup (`.exe`) + ZIP | `appimagetool` |
| Ícone | recurso do PE (`.ico`) | PNG na raiz do AppDir (extraído do mesmo `.ico` por `ico2png.py`) |
| Versão do arquivo | `VSVersionInfo` | nome do arquivo + `.desktop` |
| Dados do usuário | `%LOCALAPPDATA%\Tarjador` | `$XDG_DATA_HOME/Tarjador` (`~/.local/share/Tarjador`) |
| Tesseract | pasta irmã com DLLs | pasta irmã com ELF + `RPATH=$ORIGIN` |

O Tesseract continua **fora** do bundle do PyInstaller nas duas plataformas.
No Windows porque as DLLs do build MinGW colidem com as do Python (ver a nota
longa em `tarjador.spec`); no Linux porque as libs dele (leptonica, libtiff,
libwebp — 12 no total) resolvem por `RPATH=$ORIGIN` gravado no build, e não
devem disputar nome na raiz do bundle.

O motor é o 4.1.1 do EPEL 8, um degrau abaixo do 5.x vendorizado no Windows —
irrelevante para o que o app faz com ele, que é OCR de selo gov.br rasterizado:
o LSTM que reconhece esse texto é o mesmo desde o 4.0. Já os `traineddata`
**não** vêm do RPM: são baixados do `tessdata_fast`, as mesmas variantes que o
Windows vendoriza e que o Debian instala em produção via `packages.txt`. OCR
com modelo diferente devolve texto diferente, e o sintoma seria o mesmo PDF ter
selo detectado numa plataforma e não na outra.

## Verificações que o build faz

* **Pins x produção** (`checa_pins.py`) — antes de gastar os minutos de bundle.
* **Smoke test** — sobe o AppDir pelo `AppRun` e exige `/_stcore/health` **e** a
  página. Só o health não basta: ele responde 200 mesmo quando a rota do
  frontend não foi montada, que foi como um bundle que dava 404 passou no
  equivalente do Windows e chegou a ser instalado.
* **Tesseract** — nenhuma lib pode ficar `not found` no `ldd`, e `--list-langs`
  tem de enxergar `por` a partir do `tessdata` embarcado.

O que o build **não** cobre, e por isso vale rodar à mão ao mexer no vendoring
do OCR: uma extração de verdade. Renderizar texto como imagem num PDF e passar
pelo `get_textpage_ocr` prova a cadeia inteira (PyMuPDF acha o executável, o
executável acha as libs e o `tessdata`) — coisa que o smoke test da página não
toca, porque o OCR só roda quando há PDF com selo.

## FUSE

O AppImage é empacotado com o runtime do `type2-runtime`, que traz **fuse3
ligado estaticamente**. Sem isso o usuário precisaria instalar `libfuse2` à
mão — pacote que o Ubuntu 22.04+ não traz mais —, e o sintoma seria o arquivo
não abrir com uma mensagem que ninguém entende.

Para depurar sem FUSE (dentro de container, por exemplo):

```bash
./Tarjador-*.AppImage --appimage-extract && ./squashfs-root/AppRun
```

## Como o usuário final executa

1. Baixar o `.AppImage`.
2. Marcar como executável — pelo gerenciador de arquivos (botão direito →
   Propriedades → Permissões → *Permitir execução como programa*) ou por
   `chmod +x Tarjador-*.AppImage`.
3. Dois cliques. O navegador padrão abre em `http://127.0.0.1:<porta>`.

Esse passo 2 é o atrito conhecido do formato. É o que motiva o `.deb` como
segundo entregável para a família Debian/Ubuntu/Mint, que instala no menu de
aplicativos e dispensa a marcação manual.

## Limitações atuais

* **x86_64 apenas.** ARM64 exigiria uma segunda imagem de build e outro
  runtime; não há demanda conhecida.
* **Só a edição Leve.** A Completa (BERT embarcado) precisa dos pins de torch
  CPU e de rodar o `--selftest` no bundle, como no Windows.
* Sem assinatura nem `zsync` (atualização delta): o AppImage é publicado
  inteiro a cada versão, com o SHA-256 ao lado.
