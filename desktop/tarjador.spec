# -*- mode: python ; coding: utf-8 -*-
"""Bundle do Tarjador Desktop — edição LEVE (sem IA).

    desktop\\.venv\\Scripts\\pyinstaller.exe desktop\\tarjador.spec --noconfirm

Duas coisas explicam quase todo o conteúdo deste arquivo:

1. **O Streamlit executa `app.py` como SCRIPT, em runtime.** O PyInstaller
   analisa `run_tarjador.py`, que só importa o Streamlit — os imports de
   `app.py` (fitz, presidio, spacy...) são invisíveis para a análise estática.
   Por isso eles aparecem à mão em `_APP_IMPORTS`: não é redundância, é a
   única forma de entrarem no bundle. Import novo em `app.py` ou em
   `tarjador/` que não seja stdlib PRECISA ser adicionado ali.

2. **`tarjador/` e `app.py` vão como CÓDIGO-FONTE, não congelados.** O script
   runner do Streamlit lê e compila `app.py` do disco, e
   `tarjador/ui/pdf_review` resolve o frontend por
   `Path(__file__).parent / "frontend"`. Mantendo os `.py` reais dentro do
   bundle, os dois funcionam sem gambiarra de caminho.

`onedir` (não `onefile`) de propósito: o `onefile` extrai tudo para %TEMP% a
cada execução — comportamento que antivírus e WDAC costumam barrar, e é
exatamente o atrito que se quer evitar em máquina corporativa.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

SPEC_DIR = Path(SPECPATH)          # noqa: F821 — injetado pelo PyInstaller
REPO = SPEC_DIR.parent
VENDOR = SPEC_DIR / "vendor"

# Console visível ajuda a diagnosticar build novo; o entregável vai sem.
#   $env:TARJADOR_CONSOLE=1  antes do build  -> janela de console
CONSOLE = os.environ.get("TARJADOR_CONSOLE") == "1"

# Edição: "lite" (padrão) ou "full". Definida por $env:TARJADOR_BUILD_EDITION
# (o build.ps1 -Edition full seta). O que muda: a pilha de IA entra nos
# collect_all em vez de nos excludes, e o version_info correspondente. A pasta
# `models` NÃO passa por aqui — como o Tesseract, é pasta irmã copiada pelo
# build.ps1 (440 MB de pesos não têm por que atravessar a Analysis).
EDITION = os.environ.get("TARJADOR_BUILD_EDITION", "lite")
FULL = EDITION == "full"

# FONTE ÚNICA da versão: desktop/VERSION. O recurso de versão do .exe é
# GERADO daqui (antes eram dois version_info*.txt estáticos, e subir de
# versão exigia editar quatro arquivos à mão — receita para publicar um
# instalador que se declara 1.0.0 sendo 1.1.0). O .iss recebe o mesmo valor
# por /DVersaoBase, vindo do build.ps1, que também lê este arquivo.
VERSAO = (SPEC_DIR / "VERSION").read_text(encoding="utf-8").strip()
print(f"[spec] edição: {EDITION}, versão: {VERSAO}")


def _gera_version_info() -> str:
    """Escreve o recurso de versão do .exe e devolve o caminho.

    Vai para o workpath (desktop/build), que é descartável e ignorado pelo
    git: é artefato de build, não fonte.
    """
    nums = [int(p) for p in VERSAO.split(".")]
    while len(nums) < 4:
        nums.append(0)
    rotulo = "Completa" if FULL else "Leve"
    conteudo = f"""# GERADO por tarjador.spec a partir de desktop/VERSION — não editar.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({nums[0]}, {nums[1]}, {nums[2]}, {nums[3]}),
    prodvers=({nums[0]}, {nums[1]}, {nums[2]}, {nums[3]}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '041604B0',
        [
          StringStruct('CompanyName', 'Projeto Tarjador'),
          StringStruct('FileDescription',
                       'Tarjador — remoção de dados pessoais em PDF'),
          StringStruct('FileVersion', '{VERSAO}'),
          StringStruct('InternalName', 'Tarjador'),
          StringStruct('OriginalFilename', 'Tarjador.exe'),
          StringStruct('ProductName', 'Tarjador Desktop (Edição {rotulo})'),
          StringStruct('ProductVersion', '{VERSAO}-{EDITION}'),
          StringStruct('LegalCopyright',
                       'Projeto sem fins lucrativos — uso livre'),
        ])
    ]),
    VarFileInfo([VarStruct('Translation', [1046, 1200])])
  ]
)
"""
    destino = SPEC_DIR / "build" / f"version_info_{EDITION}.txt"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(conteudo, encoding="utf-8")
    return str(destino)

# ---------------------------------------------------------------------------
# Pacotes que precisam vir INTEIROS (dados, plugins ou entry points)
# ---------------------------------------------------------------------------
# `collect_all` traz submódulos + arquivos de dados + binários. É o martelo,
# e aqui ele se justifica: quase todos estes descobrem coisas em runtime
# (entry points do catalogue, YAML de reconhecedores, assets do frontend) —
# coisa que a análise estática não vê.
_COLLECT_ALL = [
    "streamlit",          # assets do frontend + metadata (usa importlib.metadata)
    "presidio_analyzer",  # YAML/conf dos reconhecedores
    "spacy",              # registry via catalogue/entry points
    "pt_core_news_sm",    # o modelo de português em si
    "thinc",
    "blis",
    "srsly",
    "catalogue",
    "cymem",
    "preshed",
    "murmurhash",
    "wasabi",
    "confection",
    "weasel",
    "spacy_legacy",
    "spacy_loggers",
    "phonenumbers",       # dados de numeração (validação de DDD)
    "altair",             # schemas Vega-Lite
    # O servidor do PRÓPRIO Streamlit (>=1.5x) é Starlette + uvicorn — não é
    # resquício do FastAPI da API HTTP. Excluí-los quebra o app na abertura
    # ("No module named 'starlette'"), e o uvicorn ainda importa protocolo e
    # loop por string, o que exige o collect_all em vez de hiddenimport solto.
    "starlette",
    "uvicorn",
]

if FULL:
    # Pilha de IA. transformers e torch são os campeões de import dinâmico
    # (backends, kernels, plugins por entry point) — collect_all ou nada.
    _COLLECT_ALL += [
        "torch",
        "transformers",
        "tokenizers",
        "safetensors",
        "huggingface_hub",
        "regex",
    ]

datas, binaries, hiddenimports = [], [], []
for pkg in _COLLECT_ALL:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Metadata de quem consulta a própria versão em runtime (ou é achado por
# entry point). Sem isso o erro típico é
# `importlib.metadata.PackageNotFoundError` já na abertura.
_METADATA = ["streamlit", "presidio-analyzer", "spacy", "pt-core-news-sm",
             "thinc", "pymupdf", "pandas", "numpy", "pyarrow", "altair",
             "pydantic", "pydantic-settings", "phonenumbers",
             "typing-extensions"]
if FULL:
    # transformers consulta a própria versão e a do torch por metadata.
    _METADATA += ["torch", "transformers", "tokenizers", "safetensors",
                  "huggingface-hub", "regex", "requests", "packaging",
                  "pyyaml", "filelock", "tqdm"]
for dist in _METADATA:
    try:
        datas += copy_metadata(dist)
    except Exception as e:  # noqa: BLE001 — dist opcional não deve matar o build
        print(f"[spec] aviso: sem metadata de {dist} ({e})")

# ---------------------------------------------------------------------------
# Imports de app.py / tarjador/ (invisíveis à análise — ver nota 1 no topo)
# ---------------------------------------------------------------------------
_APP_IMPORTS = [
    "fitz",                  # PyMuPDF
    "pymupdf",
    "presidio_analyzer",
    "phonenumbers",
    "pydantic",
    "pydantic_settings",
    "pandas",
    "numpy",
    "pyarrow",
    # O Streamlit carrega estes por caminho dinâmico:
    "streamlit.runtime.scriptrunner.magic_funcs",
    "streamlit.web.bootstrap",
    "streamlit.components.v1",
]
hiddenimports += _APP_IMPORTS

# ---------------------------------------------------------------------------
# Fonte do app + Tesseract embarcado
# ---------------------------------------------------------------------------
datas += [(str(REPO / "app.py"), ".")]

# O TEMA (teal) vive em .streamlit/config.toml e o Streamlit o procura em
# $CWD/.streamlit — por isso o lançador faz os.chdir(base) antes de carregar a
# config. Sem este arquivo o desktop abre com o acento vermelho padrão,
# divergindo do web. É o MESMO arquivo dos deploys hospedados de propósito:
# ajuste de tema feito no repositório vale nos três lugares sem tocar no
# desktop. (As opções de servidor dele são inofensivas aqui: fileWatcherType
# já é "none" nas flags, e flags têm precedência sobre arquivos.)
datas += [(str(REPO / ".streamlit" / "config.toml"), ".streamlit")]

# A versão vai junto para o lançador a exibir na interface: sem isso, um
# chamado de suporte começa com "qual versão você tem?" e ninguém sabe
# responder (o .exe tem a versão nas propriedades, mas ninguém olha lá).
datas += [(str(SPEC_DIR / "VERSION"), ".")]

# `tarjador/` como fonte, sem __pycache__ e sem a API (FastAPI não entra no
# desktop: a UI local não fala HTTP com ninguém).
for py in (REPO / "tarjador").rglob("*.py"):
    rel = py.relative_to(REPO)
    if "__pycache__" in rel.parts:
        continue
    if rel.parts[1:2] == ("api",) or rel.name == "main.py":
        continue
    datas.append((str(py), str(rel.parent)))

# Frontend do visualizador (pdf.js vendorado + html/css/js).
for asset in (REPO / "tarjador/ui/pdf_review/frontend").rglob("*"):
    if asset.is_file():
        datas.append((str(asset), str(asset.parent.relative_to(REPO))))

# ---------------------------------------------------------------------------
# O Tesseract NÃO entra aqui — de propósito
# ---------------------------------------------------------------------------
# Tentou-se declará-lo em `datas` (destino "tesseract/"). O PyInstaller
# reclassifica arquivos binários de `datas` como binários, varre as
# dependências deles e deposita o resultado na RAIZ do bundle. Como o build
# MinGW do Tesseract traz `libcrypto-3-x64.dll` e `zlib1.dll` — mesmos nomes
# dos do Python —, o libcrypto do Python era sobrescrito pelo do Tesseract, e
# o app morria na abertura com:
#
#     ImportError: DLL load failed while importing _ssl:
#     não foi possível encontrar o procedimento especificado
#
# (o `libssl` do Python pareado com o `libcrypto` alheio). Mexer na ordem do
# PATH não resolve: a colisão é de arquivo, não de busca.
#
# Então o Tesseract fica FORA do bundle, como pasta irmã do executável:
# `build.ps1` copia `vendor\tesseract` para `dist\Tarjador\tesseract` depois
# do PyInstaller, e o instalador leva junto (o [Files] do .iss é recursivo).
# `run_tarjador.py` o procura ao lado do .exe. Além de resolver a colisão, é
# mais correto: `tesseract.exe` roda como processo separado e resolve as DLLs
# dele a partir da própria pasta, sem disputar nada com o Python.

# ---------------------------------------------------------------------------
# Fora do bundle
# ---------------------------------------------------------------------------
excludes = [
    # A API HTTP não faz parte do desktop. (Só o `fastapi`: o starlette e o
    # uvicorn são do servidor do Streamlit — ver _COLLECT_ALL.)
    "fastapi",
    # Nada de GUI/notebook/teste.
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest",
    "sphinx", "setuptools._distutils", "test", "tests",
    "scipy", "sklearn",
]
if not FULL:
    # É a edição leve: a IA não entra. Ficam excluídos mesmo se o venv de
    # build tiver a pilha instalada (proteção contra construir a Leve com o
    # .venv-full por engano).
    excludes += [
        "torch", "torchvision", "torchaudio", "transformers", "tokenizers",
        "safetensors", "huggingface_hub", "sympy", "networkx",
    ]
else:
    # GPU nunca; sympy/networkx ficam porque o torch 2.13 os importa em
    # caminhos difíceis de prever (fx/dynamo). Cortá-los é otimização a medir
    # DEPOIS de um build funcionando, não antes.
    excludes += ["torchvision", "torchaudio"]

a = Analysis(                       # noqa: F821
    [str(SPEC_DIR / "run_tarjador.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)                   # noqa: F821

exe = EXE(                          # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tarjador",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX encolhe pouco e piora falso-positivo de AV
    console=CONSOLE,
    disable_windowed_traceback=False,
    # Cadeado (mesmo emoji do page_icon do app no navegador) — é o que aparece
    # no Menu Iniciar, na Área de Trabalho e na barra de tarefas. Gerado do
    # Segoe UI Emoji em desktop/assets/; sem ele o exe sai com o ícone
    # genérico do PyInstaller, que grita "script empacotado" para qualquer AV
    # heurístico e para o usuário.
    icon=str(SPEC_DIR / "assets" / "tarjador.ico"),
    version=_gera_version_info(),
)
coll = COLLECT(                     # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Tarjador",
)
