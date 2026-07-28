# Build do Tarjador Desktop.
#
#   powershell -ExecutionPolicy Bypass -File desktop\build.ps1                  # Edição Leve
#   powershell -ExecutionPolicy Bypass -File desktop\build.ps1 -Edition full    # Edição Completa
#
# Etapas: bundle (PyInstaller) -> payloads irmãos (tesseract, models) -> smoke
# test (página; na full, também --selftest da IA) -> ZIP + SHA-256 ->
# instalador (Inno Setup). O SHA-256 não é zelo excessivo: é o que uma equipe
# de segurança pede para carimbar o artefato (ver README, "Governança").
#
# Parâmetros:
#   -Edition lite|full   qual edição construir (padrão: lite)
#   -Console             bundle com janela de console (diagnóstico)
#   -SkipBuild           só reempacota o que já está em dist\ (útil no .iss)
#
# As edições usam venvs SEPARADOS (.venv / .venv-full): o spec da Leve exclui
# torch por nome, mas venv sem torch é garantia melhor que exclude — e o
# venv-full nunca contamina o build leve.
[CmdletBinding()]
param(
    [ValidateSet("lite", "full")]
    [string]$Edition = "lite",
    [switch]$Console,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Desktop = $PSScriptRoot
$Dist = Join-Path $Desktop "dist\Tarjador"
$Out = Join-Path $Desktop "Output"
$VenvDir = if ($Edition -eq "full") { ".venv-full" } else { ".venv" }
$Py = Join-Path $Desktop "$VenvDir\Scripts\python.exe"
$PyInstaller = Join-Path $Desktop "$VenvDir\Scripts\pyinstaller.exe"
# Fonte única: desktop/VERSION (o .spec gera o recurso do .exe a partir dele e
# o .iss recebe o mesmo valor em /DVersaoBase).
$VersaoBase = (Get-Content (Join-Path $Desktop "VERSION") -Raw).Trim()
$Versao = "$VersaoBase-$Edition"

function Write-Etapa($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
Write-Host "Edição: $Edition (venv: $VenvDir)" -ForegroundColor Yellow

if (-not (Test-Path $Py)) {
    throw "ambiente de build ausente. Rode: python -m venv desktop\$VenvDir; desktop\$VenvDir\Scripts\python.exe -m pip install -r desktop\requirements-$Edition.txt"
}
if (-not (Test-Path (Join-Path $Desktop "vendor\tesseract\tesseract.exe"))) {
    Write-Warning "desktop\vendor\tesseract ausente: o bundle sairá SEM OCR de selos (ver README)."
}
if ($Edition -eq "full" -and -not (Get-ChildItem (Join-Path $Desktop "vendor\models") -Filter "models--*" -Directory -ErrorAction SilentlyContinue)) {
    throw "edição full exige o snapshot do BERT em desktop\vendor\models (ver README)"
}

if (-not $SkipBuild) {
    Write-Etapa "Pins x produção"
    # Barato e vale a pena antes de gastar 20 min de build: o desktop não pode
    # rodar um conjunto de versões que nunca existiu em produção.
    & $Py (Join-Path $Desktop "checa_pins.py")
    if ($LASTEXITCODE -ne 0) { throw "requirements do desktop divergem da produção (acima)" }

    Write-Etapa "Bundle (PyInstaller)"
    if ($Console) { $env:TARJADOR_CONSOLE = "1" } else { Remove-Item Env:\TARJADOR_CONSOLE -ErrorAction SilentlyContinue }
    $env:TARJADOR_BUILD_EDITION = $Edition
    & $PyInstaller (Join-Path $Desktop "tarjador.spec") --noconfirm `
        --distpath (Join-Path $Desktop "dist") --workpath (Join-Path $Desktop "build") --log-level WARN
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou (exit $LASTEXITCODE)" }
}

if (-not (Test-Path $Dist)) { throw "dist\Tarjador não existe — nada a empacotar" }
New-Item -ItemType Directory -Force $Out | Out-Null

Write-Etapa "Tesseract (pasta irmã, fora do bundle)"
# Copiado DEPOIS do PyInstaller e para fora do _internal, de propósito: dentro
# do bundle, o PyInstaller varre as dependências dessas DLLs e as joga na raiz,
# onde o libcrypto do build MinGW sobrescreve o do Python e derruba o
# `import ssl` na abertura. Ver a nota em tarjador.spec.
$vendorTess = Join-Path $Desktop "vendor\tesseract"
$destTess = Join-Path $Dist "tesseract"
if (Test-Path (Join-Path $vendorTess "tesseract.exe")) {
    # Copia o CONTEÚDO (`\*`), não a pasta. `Copy-Item pasta destino -Recurse`
    # com o destino já existente aninha em `tesseract\tesseract\` em vez de
    # sobrescrever — o que inflou o instalador em 165 MB sem ninguém notar,
    # porque a primeira execução (destino inexistente) faz a coisa certa e só
    # a segunda duplica. Desta forma é idempotente.
    New-Item -ItemType Directory -Force $destTess | Out-Null
    Copy-Item (Join-Path $vendorTess "*") $destTess -Recurse -Force
    $mb = (Get-ChildItem $destTess -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ("OK — tesseract em dist\Tarjador\tesseract ({0:N1} MB)" -f $mb) -ForegroundColor Green
    if (Test-Path (Join-Path $destTess "tesseract")) {
        throw "tesseract aninhado em dist\Tarjador\tesseract\tesseract — apague a pasta e refaça"
    }
} else {
    Write-Warning "vendor\tesseract ausente: bundle SEM OCR de selos"
}

Write-Etapa "Licença e créditos"
# Copiados para a RAIZ do dist: assim entram tanto no ZIP portátil quanto no
# instalador (o [Files] do .iss empacota dist\Tarjador\* recursivamente) com
# um passo só. Redistribuir Tesseract, pdf.js e as libs Apache-2.0 obriga a
# levar os avisos junto; a atribuição do modelo do spaCy (CC BY-SA) idem.
foreach ($doc in @("LICENSE", "THIRD-PARTY-NOTICES.txt")) {
    $origem = Join-Path (Split-Path $Desktop -Parent) $doc
    if (-not (Test-Path $origem)) { throw "$doc não encontrado na raiz do repositório" }
    Copy-Item $origem (Join-Path $Dist $doc) -Force
}
# Textos INTEGRAIS das licenças dos componentes que não são pacotes Python
# (Tesseract, pdf.js e o modelo do spaCy). A cláusula 4(a) da Apache-2.0 exige
# entregar uma CÓPIA da licença a quem recebe o software — citar a URL não
# basta. Os pacotes Python já trazem os seus nos respectivos dist-info.
$destLic = Join-Path $Dist "licenses"
New-Item -ItemType Directory -Force $destLic | Out-Null
Copy-Item (Join-Path $Desktop "vendor\licenses\*") $destLic -Force
Write-Host "OK — LICENSE, THIRD-PARTY-NOTICES.txt e licenses\ no pacote" -ForegroundColor Green

Write-Etapa "Modelos de IA (pasta irmã — só na full)"
# É a PRESENÇA desta pasta que faz o lançador se declarar edição Completa
# (HF_HUB_CACHE + TARJADOR_EDITION=full). Na lite, garantir a AUSÊNCIA é tão
# importante quanto: um resto de build full em dist\ viraria uma "Completa"
# sem torch, que quebra ao ligar o toggle.
$destModels = Join-Path $Dist "models"
if ($Edition -eq "full") {
    New-Item -ItemType Directory -Force $destModels | Out-Null
    Copy-Item (Join-Path $Desktop "vendor\models\*") $destModels -Recurse -Force
    $mb = (Get-ChildItem $destModels -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
    Write-Host ("OK — modelos em dist\Tarjador\models ({0:N1} MB)" -f $mb) -ForegroundColor Green
} elseif (Test-Path $destModels) {
    Remove-Item $destModels -Recurse -Force -Confirm:$false
    Write-Host "removido resto de models de um build full anterior" -ForegroundColor Yellow
}

if ($Edition -eq "full") {
    Write-Etapa "Selftest da IA (no bundle, offline)"
    # Roda `Tarjador.exe --selftest` DENTRO do bundle: importa torch, resolve o
    # BERT do cache irmão sem rede e faz uma inferência real. O smoke test da
    # página não cobre isso (o torch carrega só quando o usuário liga o
    # toggle) — sem este passo, um bundle com a IA quebrada seria publicado.
    $stOut = Join-Path $env:TEMP "tarjador_selftest.txt"
    $p = Start-Process (Join-Path $Dist "Tarjador.exe") -ArgumentList "--selftest" `
        -RedirectStandardOutput $stOut -PassThru -WindowStyle Hidden -Wait
    Get-Content $stOut | ForEach-Object { "  $_" }
    if ($p.ExitCode -ne 0) { throw "selftest da IA falhou (exit $($p.ExitCode)) — ver acima" }
    Write-Host "OK — IA funcional no bundle" -ForegroundColor Green
}

Write-Etapa "Smoke test"
# Sobe o app de verdade e espera o health check do Streamlit. Um bundle que
# compila mas não abre é o modo de falha comum aqui (import que só acontece em
# runtime), então o build não termina sem esta prova.
$env:TARJADOR_NO_BROWSER = "1"
# Sem isto, um Tarjador já instalado e aberto faria o binário recém-compilado
# sair cedo (instância única) e o teste aprovaria o processo ANTIGO.
$env:TARJADOR_NO_SINGLE_INSTANCE = "1"
$proc = Start-Process (Join-Path $Dist "Tarjador.exe") -PassThru -WindowStyle Minimized
$porta = $null; $ok = $false
foreach ($i in 1..90) {
    Start-Sleep -Seconds 1
    # A porta vem do SO, perguntando o que ESTE processo está escutando — não
    # do porta.txt. O arquivo é global e pode conter a porta de um Tarjador
    # instalado, o que faria o teste medir o processo errado.
    $conn = Get-NetTCPConnection -State Listen -OwningProcess $proc.Id -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -eq "127.0.0.1" } | Select-Object -First 1
    if ($conn) {
        $porta = $conn.LocalPort
        try {
            if ((Invoke-WebRequest "http://127.0.0.1:$porta/_stcore/health" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200) { $ok = $true; break }
        } catch {}
    }
    if ($proc.HasExited) { break }
}
# /_stcore/health NÃO basta como prova de vida. Ele responde 200 mesmo quando
# a rota que serve o frontend não foi montada — foi exatamente assim que um
# bundle que dava "404 Not Found" na página passou no smoke test e chegou a
# ser instalado. A página em si é o que o usuário vê, então é ela que o teste
# tem de exigir.
$paginaOk = $false
$paginaInfo = "não testada (servidor não subiu)"
if ($ok) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$porta/" -TimeoutSec 20 -UseBasicParsing
        # Em PS 5.1, .Content já vem como string para respostas de texto —
        # GetString() aqui estoura com "não é possível converter para Byte".
        $corpo = if ($r.Content -is [byte[]]) { [Text.Encoding]::UTF8.GetString($r.Content) } else { [string]$r.Content }
        # O index.html do Streamlit referencia os bundles em ./static/js/.
        $paginaOk = ($r.StatusCode -eq 200) -and ($corpo -match "streamlit|static/js")
        $paginaInfo = "HTTP $($r.StatusCode), $($corpo.Length) bytes"
    } catch {
        $paginaInfo = "ERRO $($_.Exception.Message)"
    }
}
if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -Confirm:$false }
# Stop-Process é assíncrono: sem esperar o processo MORRER de fato, o
# Compress-Archive logo adiante encontra arquivos do dist ainda abertos
# ("sendo usado por outro processo") — falhou assim uma vez; nas anteriores
# foi sorte de timing. Wait-Process garante; o sleep dá folga ao SO para
# soltar os handles de arquivo mapeados.
Wait-Process -Id $proc.Id -Timeout 30 -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Remove-Item Env:\TARJADOR_NO_BROWSER -ErrorAction SilentlyContinue
if (-not $ok) {
    Get-Content (Join-Path $env:LOCALAPPDATA "Tarjador\tarjador.log") -Tail 30
    throw "smoke test falhou: o bundle não respondeu em /_stcore/health (log acima)"
}
if (-not $paginaOk) {
    Get-Content (Join-Path $env:LOCALAPPDATA "Tarjador\tarjador.log") -Tail 30
    throw "smoke test falhou: /_stcore/health respondeu mas a PÁGINA não ($paginaInfo)"
}
Write-Host "OK — health e página respondendo na porta $porta ($paginaInfo)" -ForegroundColor Green

Write-Etapa "ZIP portátil"
$zip = Join-Path $Out "Tarjador-$Versao-portatil.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $Dist "*") -DestinationPath $zip -CompressionLevel Optimal
Write-Host ("{0} -> {1:N1} MB" -f (Split-Path $zip -Leaf), ((Get-Item $zip).Length / 1MB))

Write-Etapa "Instalador (Inno Setup)"
# O winget instala o Inno Setup POR USUÁRIO quando roda sem elevação, então o
# caminho em Program Files não é o único — procurar nos dois evita concluir
# "não está instalado" com ele instalado.
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "/DEdition=$Edition" "/DVersaoBase=$VersaoBase" (Join-Path $Desktop "tarjador.iss")
    if ($LASTEXITCODE -ne 0) { throw "ISCC falhou (exit $LASTEXITCODE)" }
} else {
    Write-Warning "Inno Setup não encontrado. Instale com: winget install JRSoftware.InnoSetup"
    Write-Warning "O ZIP portátil acima já permite testar sem instalador."
}

Write-Etapa "Artefatos"
# Where-Object, não -Exclude: em PS 5.1, Get-ChildItem -File -Exclude sem
# -Recurse retorna VAZIO (quirk conhecido) — a listagem final saía em branco
# e os .sha256 antigos nunca eram sobrescritos, ficando dessincronizados dos
# artefatos novos.
Get-ChildItem $Out -File | Where-Object { $_.Name -notlike "*.sha256" } | ForEach-Object {
    $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    "{0,-42} {1,8:N1} MB  SHA256 {2}" -f $_.Name, ($_.Length / 1MB), $h
    "$h  $($_.Name)" | Out-File "$($_.FullName).sha256" -Encoding utf8
}
"{0,-42} {1,8:N1} MB  (instalado)" -f "dist\Tarjador\", ((Get-ChildItem $Dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
