# Build do AppImage (edição Leve) a partir do Windows, via Docker.
#
#   powershell -ExecutionPolicy Bypass -File desktop\linux\build.ps1
#   powershell -ExecutionPolicy Bypass -File desktop\linux\build.ps1 -Limpar
#
# Este arquivo só orquestra: quem constrói é desktop/linux/build-appimage.sh,
# dentro da imagem desktop/linux/Dockerfile. A separação existe porque o build
# TEM de acontecer num Linux de glibc antiga (ver a nota no Dockerfile), e o
# Docker Desktop é o Linux que a máquina de desenvolvimento tem.
#
# Parâmetros:
#   -Edition  lite (padrão) ou full. A full embarca torch + os pesos do BERT de
#             desktop\vendor\models e roda o --selftest da IA dentro do bundle.
#   -Limpar   descarta o volume de build (venvs, dist, work) e começa do zero.
#             Use quando trocar versão de dependência ou suspeitar de resíduo.
#   -Rapido   reempacota o bundle que já está no volume, pulando o PyInstaller.
#             Para iterar em AppRun/.desktop/ícone sem pagar a análise de novo.
[CmdletBinding()]
param(
    [ValidateSet("lite", "full")]
    [string]$Edition = "lite",
    [switch]$Limpar,
    [switch]$Rapido
)

$ErrorActionPreference = "Stop"
$Linux = $PSScriptRoot
$Repo = Split-Path (Split-Path $Linux -Parent) -Parent
$Imagem = "tarjador-appimage"
# Volumes nomeados, não pastas do repositório: o pip e o PyInstaller fazem
# dezenas de milhares de operações de arquivo, e o bind mount do Docker Desktop
# (9p sobre WSL2) transforma isso em dezenas de minutos. Só a FONTE atravessa o
# mount — e o artefato final, que é um arquivo só.
$VolBuild = "tarjador-appimage-build"
$VolPip = "tarjador-appimage-pipcache"

function Write-Etapa($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "o Docker Desktop não está rodando (abra-o e tente de novo)" }

if ($Limpar -and $Rapido) { throw "-Limpar e -Rapido se contradizem: um apaga o bundle que o outro reaproveita" }
if ($Limpar) {
    Write-Etapa "Limpando volumes"
    docker volume rm -f $VolBuild $VolPip *> $null
    Write-Host "volumes descartados" -ForegroundColor Yellow
}
$ArgsScript = @("--edition", $Edition)
if ($Rapido) { $ArgsScript += "--sem-bundle" }
Write-Host "Edição: $Edition" -ForegroundColor Yellow

if ($Edition -eq "full" -and -not (Get-ChildItem (Join-Path $Repo "desktop\vendor\models") -Filter "models--*" -Directory -ErrorAction SilentlyContinue)) {
    throw "edição full exige o snapshot do BERT em desktop\vendor\models (ver desktop\README.md)"
}

Write-Etapa "Imagem de build"
docker build -t $Imagem -f (Join-Path $Linux "Dockerfile") $Linux
if ($LASTEXITCODE -ne 0) { throw "docker build falhou" }

Write-Etapa "Build no container"
# --init: sem ele, o processo do Streamlit derrubado no smoke test vira zumbi e
# o `wait` do script pendura o build.
docker run --rm --init `
    -v "${Repo}:/repo" `
    -v "${VolBuild}:/build" `
    -v "${VolPip}:/root/.cache/pip" `
    $Imagem /opt/tarjador-build/build-appimage.sh @ArgsScript
if ($LASTEXITCODE -ne 0) { throw "build-appimage.sh falhou (exit $LASTEXITCODE)" }

Write-Etapa "Artefatos"
Get-ChildItem (Join-Path $Repo "desktop\Output") -Filter "*.AppImage" -ErrorAction SilentlyContinue |
    ForEach-Object {
        "{0,-46} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB)
        Get-Content "$($_.FullName).sha256" -ErrorAction SilentlyContinue
    }
