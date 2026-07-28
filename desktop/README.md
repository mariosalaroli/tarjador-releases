# Tarjador Desktop (Windows)

Empacotamento do Tarjador como aplicativo Windows. A UI é a **mesma** de
`app.py`: o que muda é o invólucro — em vez de `streamlit run` num servidor
hospedado, um executável sobe o Streamlit em `127.0.0.1` e abre o navegador
padrão do usuário.

Nada aqui altera os deploys hospedados (HF Space e Streamlit Cloud). A única
mudança em `app.py` é condicionada à variável `TARJADOR_EDITION`, que só o
lançador define.

## Por que existe

O motivo é privacidade, não conveniência. Nos deploys hospedados o usuário faz
upload de um PDF com CPF/RG para um servidor de terceiro. Instalado, o
documento **nunca sai da máquina** — o que remove a discussão de LGPD/operador
de dados e viabiliza o uso em órgão que proíbe upload externo.

Como consequência do bundle ser autocontido, o app roda com o firewall de saída
fechado. Isso é verificável: `run_tarjador.py` fixa `HF_HUB_OFFLINE`,
`TRANSFORMERS_OFFLINE` e desliga a telemetria do Streamlit (esta última via
`flag_options`, não por variável de ambiente — ver abaixo por quê).

## As duas edições

|                  | Leve                              | Completa                    |
|------------------|-----------------------------------|-----------------------------|
| Detecção         | heurísticas + spaCy + OCR de selos| \+ NER BERT LeNER-Br        |
| torch/transformers | não entram no bundle            | entram (`-Edition full`)    |
| Pesos do BERT    | —                                 | pasta irmã `models` (cache HF) |
| `TARJADOR_EDITION` | `lite`                          | `full`                      |
| Instalador / disco | 143 MB / ~580 MB                | 648 MB / ~1,55 GB           |

A edição é decidida pelo **payload**: o lançador vira `full` quando encontra a
pasta `models` ao lado do exe (e aponta `HF_HUB_CACHE` para ela — o
`_ner_bert.py` do app resolve o modelo dali, offline, sem mudança de código).
Build: `build.ps1 -Edition full` usa `.venv-full` (requirements-full.txt),
exige o snapshot do BERT em `vendor\models` (baixe com
`huggingface_hub.snapshot_download('pierreguillou/ner-bert-base-cased-pt-lenerbr',
cache_dir='desktop/vendor/models')`) e só empacota se o
`Tarjador.exe --selftest` — inferência BERT real, offline, dentro do bundle —
passar. Mesmo AppId nas duas: instalar uma por cima da outra troca a edição.

O toggle "Nomes com IA" da sidebar aparece desabilitado na edição Leve, com a
explicação no tooltip — em vez de oferecer um recurso que o bundle não contém.
"Atualizar" é baixar a Completa e instalar por cima: não há download em tempo de
execução, de propósito (ver *Governança*).

## Tamanhos medidos (edição Leve, 2026-07-27)

| Artefato                              | Tamanho  |
|---------------------------------------|----------|
| `TarjadorSetup-1.0.0-lite.exe`        | 143,0 MB |
| `TarjadorSetup-1.0.0-full.exe`        | 648,3 MB |
| ZIP portátil lite / full              | 205,3 / 773,1 MB |
| instalado em disco (lite / full)      | 579,6 MB / 1,55 GB |

Distribuição: **GitHub Releases** (assets fora do histórico git; os botões do
app apontam para `releases/latest/download/TarjadorSetup-{Leve,Completa}.exe`
— nomes SEM versão, então release nova não exige mudar o app).

O instalador é bem menor que o ZIP porque o Inno usa LZMA2/max e o
`Compress-Archive` usa deflate — e os ~93 MB de símbolos de debug do
`libtesseract-5.dll` comprimem muito bem.

Onde o peso está, medido no ambiente de build: spacy 85 MB, pyarrow 79 MB,
pandas 63 MB, pymupdf 47 MB, phonenumbers 42 MB, numpy 51 MB (com
`numpy.libs`), streamlit 31 MB, pydeck 23 MB, blis 22 MB, Tesseract 166 MB
(dos quais ~93 MB são símbolos de debug — ver *Trims conhecidos*).

O `pyarrow` só existe porque o Streamlit o exige para `st.dataframe`/
`st.data_editor`; o `pydeck` vem junto do Streamlit e não é usado pelo app.

## Publicar uma versão nova

1. **Edite `desktop/VERSION`** — é a fonte única. O `.spec` gera dali o recurso
   de versão do `.exe` (não há `version_info*.txt` a manter), o `build.ps1`
   nomeia os artefatos e repassa ao Inno em `/DVersaoBase`, e o valor vai
   embarcado para a interface mostrar.
2. `build.ps1 -Edition lite` e `build.ps1 -Edition full`.
3. Release nova em
   [tarjador-releases](https://github.com/mariosalaroli/tarjador-releases)
   com os assets renomeados para `TarjadorSetup-Leve.exe` e
   `TarjadorSetup-Completa.exe` + `SHA256SUMS.txt`.

O app web **não precisa ser alterado**: os botões apontam para
`releases/latest/download/<nome-fixo>`. E só vale rebuildar quando mudar o que
entra no bundle — `app.py`, `tarjador/` ou `.streamlit/config.toml`.

`checa_pins.py` roda no início do build e **aborta se os requirements do
desktop divergirem dos de produção** (mesmo pacote com faixa diferente, ou a
pilha de IA faltando na edição Completa). São arquivos separados por
necessidade, e nada além disto impede que um mude sem o outro — o desktop
rodando versões que nunca existiram em produção é o cenário que os pins
existem para evitar.

## Como construir

Pré-requisitos: Python 3.12 e Inno Setup 6 (só para o instalador).

```powershell
# 1. Ambiente de build (uma vez)
python -m venv desktop\.venv
desktop\.venv\Scripts\python.exe -m pip install -r desktop\requirements-lite.txt

# 2. Bundle
desktop\.venv\Scripts\pyinstaller.exe desktop\tarjador.spec --noconfirm `
    --distpath desktop\dist --workpath desktop\build

# 3. Instalador
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" desktop\tarjador.iss
```

Saída: `desktop\dist\Tarjador\` (bundle) e
`desktop\Output\TarjadorSetup-1.0.0-lite.exe` (instalador).

Para diagnosticar um build novo, `$env:TARJADOR_CONSOLE=1` antes do passo 2
deixa a janela de console visível.

### Tesseract

`desktop\vendor\tesseract\` não está no repositório (é binário de terceiro) e
precisa ser montado uma vez:

- `tesseract.exe` + todas as `*.dll` de uma instalação do
  [Tesseract 5 para Windows](https://github.com/UB-Mannheim/tesseract/wiki);
- `tessdata\por.traineddata` e `eng.traineddata` de
  [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) — as mesmas
  variantes que o Debian instala em produção via `packages.txt`.

`osd.traineddata` não é necessário: o PyMuPDF não usa detecção de orientação.

## Detalhes que não são óbvios

- **Variáveis `STREAMLIT_*` NÃO configuram o Streamlit 1.5x.** Quem lê o
  ambiente é `_update_config_with_sensitive_env_var`, que ignora toda opção
  não marcada `sensitive` (há duas no config.py inteiro). Medido: com
  `STREAMLIT_SERVER_ADDRESS`, `_PORT`, `_BROWSER_GATHER_USAGE_STATS` e
  `_SERVER_MAX_UPLOAD_SIZE` definidas, as quatro saíram como `<default>`. As
  consequências eram silenciosas: bind em `::` (o app aparecia na rede local
  como "Network URL") e telemetria ligada. A configuração vai TODA por
  `flag_options` em `run_tarjador.py` (`_opcoes_streamlit`) — o mesmo canal do
  `streamlit run --server.port=…`.
- **`global.developmentMode` precisa ser `False` explícito no bundle.** O
  default é `"site-packages" not in __file__`; num bundle o caminho é
  `_internal\streamlit\config.py`, o default vira True e o Streamlit pula o
  mount que serve o frontend: servidor no ar, `/_stcore/health` 200 e a página
  **404 Not Found**. Também só funciona via `flag_options`.
- **O smoke test tem de exigir `GET /`, não só o health check** — o 404 acima
  passa batido num teste que só olha `/_stcore/health`. O `build.ps1` valida a
  página e descobre a porta perguntando ao SO o que o PID recém-iniciado está
  escutando (o `porta.txt` pode ser de outra instância).
- **O Tesseract fica FORA do bundle**, como pasta irmã do executável
  (`dist\Tarjador\tesseract`), copiada pelo `build.ps1` depois do PyInstaller.
  Declarado em `datas`, o PyInstaller reclassifica as DLLs como binários,
  varre as dependências delas e as deposita na raiz do bundle — onde o
  `libcrypto-3-x64.dll` do build MinGW do Tesseract **sobrescreve o do
  Python** e o app morre na abertura com `DLL load failed while importing
  _ssl`. Mexer na ordem do PATH não resolve: a colisão é de arquivo. Fora do
  bundle, o `tesseract.exe` roda como outro processo e resolve as DLLs dele a
  partir da própria pasta.
- **O servidor do próprio Streamlit (>=1.5x) é Starlette + uvicorn.** Não
  confundir com o FastAPI da API HTTP: excluí-los do bundle quebra o app na
  abertura.
- **O Streamlit executa `app.py` como script**, então o PyInstaller não vê os
  imports dele. Eles estão listados à mão em `_APP_IMPORTS`, no
  `tarjador.spec`. *Import novo em `app.py` ou em `tarjador/` que não seja
  stdlib tem de ser adicionado lá*, senão o bundle quebra só em runtime.
- **`tarjador/` vai como código-fonte**, não congelado: o script runner lê
  `app.py` do disco e `pdf_review` resolve o frontend por `__file__`.
- **`console=False` deixa `sys.stderr` como `None`**, o que quebraria o
  `faulthandler.enable()` do topo de `app.py`. O lançador redireciona
  stdout/stderr para `%LOCALAPPDATA%\Tarjador\tarjador.log` antes de subir o
  servidor — que é também o primeiro lugar a olhar num chamado de suporte.
- **`onedir`, não `onefile`**: o `onefile` extrai tudo para `%TEMP%` a cada
  execução, comportamento que antivírus e WDAC costumam barrar.
- **OCR sem tocar no app**: `_ensure_tessdata()` em `tarjador/core/redactor.py`
  já prefere `TESSDATA_PREFIX`; o lançador aponta para o Tesseract embarcado.
- **`build.ps1` e `tarjador.iss` têm de ficar em UTF-8 COM BOM.** O Windows
  PowerShell 5.1 lê `.ps1` como ANSI quando não há BOM, e cada acento vira erro
  de sintaxe (`Token 'nada' inesperado`). Se você editar esses arquivos com uma
  ferramenta que grava UTF-8 sem BOM, regrave:
  ```powershell
  $f = "desktop\build.ps1"
  $t = [IO.File]::ReadAllText($f, [Text.UTF8Encoding]::new($false))
  [IO.File]::WriteAllText($f, $t, [Text.UTF8Encoding]::new($true))
  ```
- **O winget instala o Inno Setup por usuário** (`%LOCALAPPDATA%\Programs\Inno
  Setup 6`) quando roda sem elevação, não em Program Files. O `build.ps1`
  procura nos dois lugares.

## Governança

Decisões tomadas para o artefato ser aprovável por uma equipe de segurança:

- **zero download em runtime.** Nada de `pip install` ou download de modelo
  depois da instalação: um app que baixa e executa binários novos é o padrão
  que EDR classifica como *dropper*, e tornaria o conteúdo instalado diferente
  em cada máquina (nada para auditar).
- **um artefato, um hash**, com as versões travadas em `requirements-lite.txt`.
- **instala em diretório não-gravável** (Program Files) quando há admin.
- **`127.0.0.1`**, nunca `0.0.0.0`: não expõe o app na rede local e não dispara
  pedido de regra do Firewall do Windows.
- **sem UPX**: encolhe pouco e aumenta falso-positivo de antivírus.

Pendente: **assinatura de código**. Sem certificado, o SmartScreen avisa na
primeira execução e antivírus corporativo pode barrar — é o maior risco
prático de adoção. `version_info.txt` já traz os campos que uma regra de
*publisher* do AppLocker usaria depois de assinado.

## Trims conhecidos (não aplicados)

- `libtesseract-5.dll` do build UB Mannheim tem ~97 MB, dos quais ~93 MB são
  símbolos de debug. O build do conda-forge (`tesseract 5.5.2`) vem sem eles.
  Preferiu-se binário oficial não modificado a um alterado à mão — binário
  mexido tende a piorar a heurística de antivírus, que é o risco que mais
  importa aqui.
- `phonenumbers` (~42 MB) poderia ser `phonenumberslite`, mas o
  `presidio-analyzer` declara o primeiro; trocar exige cuidado com o resolver.
