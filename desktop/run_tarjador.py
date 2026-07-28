"""Ponto de entrada do Tarjador Desktop (Windows).

Sobe o servidor do Streamlit em 127.0.0.1 numa porta livre e abre o navegador
padrão do usuário. A UI é a MESMA de `app.py` — este arquivo não é uma segunda
interface, é só o invólucro que substitui o `streamlit run` da linha de comando.

Decisões que valem registro:

* **Nenhuma conexão de saída.** `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` e a
  telemetria do Streamlit desligada. A edição leve não tem nem torch nem os
  pesos do BERT no bundle, então não haveria o que baixar — mas as variáveis
  ficam explícitas para que "roda com firewall de saída fechado" seja um fato
  verificável, e não uma consequência acidental de quais pacotes entraram.

* **`127.0.0.1`, nunca `0.0.0.0`.** Além do óbvio (não expor o app na rede
  local), bind em loopback não dispara o pedido de regra do Firewall do
  Windows — que exigiria admin e viraria chamado de suporte.

* **stdout/stderr vão para arquivo, sempre.** Num bundle sem console
  (`console=False`) o Python deixa `sys.stderr` como None, e aí duas coisas
  quebram: o `faulthandler.enable()` no topo de `app.py` (exige um stream com
  fileno de verdade) e todo `print(..., file=sys.stderr)` do projeto. Apontar
  os dois para um log real resolve as duas de uma vez e dá onde olhar quando
  um usuário disser "abriu e fechou".

* **Instância única.** Clicar no atalho duas vezes deve abrir outra aba, não
  um segundo servidor. O arquivo de sessão guarda a porta viva.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Tarjador"
PORT_RANGE = range(8501, 8600)
HEALTH_PATH = "/_stcore/health"


def _base_dir() -> Path:
    """Raiz dos recursos: o diretório do bundle quando congelado, senão o
    repositório (permite rodar este mesmo arquivo sem PyInstaller, para
    depurar o lançador sem esperar um build)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Onde o app pode ESCREVER: logs e estado da sessão.

    Nunca ao lado do executável — a instalação vive em Program Files, que é
    somente-leitura para o usuário (e é justamente por isso que ela satisfaz
    a política padrão do AppLocker)."""
    root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _redirect_output(log_path: Path):
    """Manda stdout/stderr para o log e devolve o arquivo aberto.

    Precisa ser um arquivo DE VERDADE (com fileno), não um StringIO: o
    faulthandler do `app.py` grava no descritor do SO, não pelo objeto Python
    — é o que faz um segfault nativo aparecer no log em vez de sumir.
    """
    f = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = f
    sys.stderr = f
    # `sys.__stderr__` também: bibliotecas que querem o stream "original"
    # (logging, warnings) chegariam em None sem isso.
    sys.__stdout__ = f
    sys.__stderr__ = f
    print(f"\n=== {APP_NAME} iniciado em {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    return f


def _porta_viva(port: int, timeout: float = 0.35) -> bool:
    """Há um Streamlit saudável nesta porta? (health check, não só socket
    aberto: qualquer outro serviço pode estar ocupando a porta)."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=timeout
        ) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _porta_livre() -> int:
    for port in PORT_RANGE:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(
        f"nenhuma porta livre entre {PORT_RANGE.start} e {PORT_RANGE.stop - 1}"
    )


def _instancia_existente() -> int | None:
    """Porta de um Tarjador já rodando, se houver."""
    marca = _data_dir() / "porta.txt"
    try:
        port = int(marca.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return port if _porta_viva(port) else None


def _dir_tesseract() -> Path | None:
    """Pasta do Tesseract embarcado, ou None se não houver.

    Fica ao LADO do executável (`dist\\Tarjador\\tesseract`), fora do bundle do
    PyInstaller. Não é organização arbitrária: dentro do bundle, o PyInstaller
    varre as dependências dessas DLLs e as deposita na raiz, onde o
    `libcrypto-3-x64.dll` do build MinGW do Tesseract sobrescreve o do Python
    e derruba o `import ssl` na abertura. Ver a nota longa em `tarjador.spec`.
    """
    if getattr(sys, "frozen", False):
        cand = Path(sys.executable).parent / "tesseract"
    else:
        cand = Path(__file__).resolve().parent / "vendor" / "tesseract"
    return cand if (cand / "tesseract.exe").is_file() else None


def _ativa_tesseract() -> None:
    """Deixa o Tesseract embarcado alcançável pelo PyMuPDF.

    O PyMuPDF chama o EXECUTÁVEL `tesseract` (não a lib), daí o PATH; e
    `TESSDATA_PREFIX` basta para achar os dados, porque `_ensure_tessdata()`
    em tarjador/core/redactor.py já o prefere quando aponta para um diretório
    existente — foi o que permitiu embarcar o OCR sem tocar no código do app.

    A pasta entra no FIM do PATH: as DLLs do Tesseract têm nomes que colidem
    com as do Python (`libcrypto-3-x64.dll`, `zlib1.dll`), e não há motivo para
    deixá-las na frente. O `tesseract.exe` roda como outro processo e resolve
    as próprias DLLs a partir da pasta dele.
    """
    tess = _dir_tesseract()
    if tess is None:
        print("[launcher] AVISO: Tesseract não encontrado — sem OCR de selos")
        return
    os.environ["TESSDATA_PREFIX"] = str(tess / "tessdata")
    os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(tess)
    print(f"[launcher] Tesseract: {tess}")


def _dir_modelos() -> Path | None:
    """Pasta `models` (cache do Hugging Face com o BERT), ou None.

    Como o Tesseract: pasta irmã do executável, copiada pelo build da edição
    Completa. É a PRESENÇA dela que define a edição — um único lançador, sem
    build stamp: o payload diz o que ele é. (Rodando do fonte, usa o vendor
    do build, se já tiver sido baixado.)
    """
    if getattr(sys, "frozen", False):
        cand = Path(sys.executable).parent / "models"
    else:
        cand = Path(__file__).resolve().parent / "vendor" / "models"
    # O snapshot fica em models--pierreguillou--.../; qualquer models--* serve
    # como prova de que há um cache de modelo real, não uma pasta vazia.
    if cand.is_dir() and any(cand.glob("models--*")):
        return cand
    return None


def _configura_ambiente() -> None:
    """Variáveis de ambiente lidas pelo NOSSO código (não pelo Streamlit)."""
    env = os.environ

    # --- Zero rede ---------------------------------------------------------
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")

    # --- Edição: decidida pelo PAYLOAD -------------------------------------
    # Com a pasta `models` ao lado do exe (edição Completa), o cache do HF
    # aponta para ela e o `_ner_bert.py` do app resolve o modelo dali sem
    # rede (HF_HUB_OFFLINE acima) — zero mudança no código do app. Sem a
    # pasta, TARJADOR_EDITION=lite desabilita o toggle da IA em `app.py`.
    # Versão para a interface mostrar (arquivo VERSION embarcado pelo .spec).
    # Ausente ao rodar do fonte sem build — não é motivo para falhar.
    try:
        env["TARJADOR_VERSION"] = (_base_dir() / "VERSION").read_text(
            encoding="utf-8").strip()
    except OSError:
        pass

    modelos = _dir_modelos()
    if modelos is not None:
        env["HF_HUB_CACHE"] = str(modelos)
        env["TARJADOR_EDITION"] = env.get("TARJADOR_EDITION", "full")
        print(f"[launcher] modelos de IA: {modelos}")
    else:
        env["TARJADOR_EDITION"] = env.get("TARJADOR_EDITION", "lite")

    # --- Sem os limites do PoC hospedado ----------------------------------
    # O teto de 50 MB existe porque o HF Space é compartilhado. Rodando na
    # máquina do usuário, o limite é o RAM dele. (Lido por `app.py` via
    # os.environ; o limite do UPLOADER é `server.maxUploadSize`, que vai em
    # `_opcoes_streamlit`.)
    env.setdefault("MAX_FILE_SIZE_MB", "500")


def _opcoes_streamlit(port: int) -> dict:
    """Configuração do servidor Streamlit — via `flag_options`, NUNCA por
    variáveis `STREAMLIT_*`.

    Isso é essencial, não estilo: a partir do Streamlit 1.5x quem lê o
    ambiente é `_update_config_with_sensitive_env_var` (config.py), que ignora
    toda opção não marcada como `sensitive` — e existem só DUAS dessas no
    arquivo inteiro. Medido aqui: com STREAMLIT_SERVER_ADDRESS, _PORT,
    _BROWSER_GATHER_USAGE_STATS e _SERVER_MAX_UPLOAD_SIZE definidas, as
    quatro opções saíram como `<default>` e `is_manually_set` == False.

    O estrago era silencioso e sério: sem `server.address` definido, o
    Streamlit faz bind em `::` (TODAS as interfaces — "Network URL" na rede
    local e risco de prompt de firewall), e a telemetria seguia ligada.

    `flag_options` é o canal do próprio `streamlit run --server.port=…`.
    Nota ao adicionar chaves: `load_config_options` só troca `_` por `.`,
    então os nomes ficam em camelCase, como abaixo.
    """
    return {
        # Loopback, nunca 0.0.0.0/:: — não expõe na rede nem pede firewall.
        "server.address": "127.0.0.1",
        "server.port": port,
        "browser.serverAddress": "127.0.0.1",
        "browser.serverPort": port,
        # Quem abre o navegador é este lançador.
        "server.headless": True,
        # Num bundle nada muda em runtime; o watcher só custa CPU.
        "server.fileWatcherType": "none",
        "browser.gatherUsageStats": False,
        "server.enableStaticServing": False,
        "client.toolbarMode": "minimal",
        "server.maxUploadSize": 500,
        # Sem isto a página dá 404: o default é calculado como
        # `"site-packages" not in __file__`, e num bundle o caminho é
        # `_internal\streamlit\config.py` — o Streamlit conclui que roda de um
        # checkout de fonte e pula o mount que serve o frontend (`if not
        # dev_mode:` em starlette_app.py). Servidor no ar, health check 200,
        # página 404.
        "global.developmentMode": False,
    }


def _abre_navegador(url: str, port: int) -> None:
    """Espera o servidor responder e então abre o navegador.

    Abrir antes do servidor subir mostra 'não foi possível conectar' e o
    usuário conclui que o programa não funciona.
    """
    for _ in range(240):  # ~60s de teto: spaCy e Presidio demoram no 1º import
        if _porta_viva(port):
            break
        time.sleep(0.25)
    else:
        print("[launcher] servidor não respondeu ao health check; abrindo mesmo assim")
    webbrowser.open(url)


def _selftest() -> int:
    """`Tarjador.exe --selftest`: prova, DENTRO do bundle e sem rede, que a
    pilha de IA funciona — imports do torch/transformers, modelo resolvido do
    cache embarcado e uma inferência real com um nome achado.

    Existe porque o smoke test da página não exercita a IA: o torch só carrega
    quando o usuário liga o toggle. Sem isto, um bundle Completo com o torch
    quebrado (DLL faltando, cache mal montado) passaria no build e falharia na
    primeira análise do usuário. O build.ps1 roda isto e aborta se falhar.

    Saída pelo stdout (console=False não tem stdout? tem: o build roda o exe
    com stdout redirecionado para arquivo via Start-Process).
    """
    _configura_ambiente()
    if os.environ.get("TARJADOR_EDITION") != "full":
        print("SELFTEST: edicao leve (sem pasta models) — nada a testar")
        return 0
    if not getattr(sys, "frozen", False):
        # Rodando do fonte: o pacote `tarjador` está na raiz do repositório.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from tarjador.core._ner_bert import detect_person_names_bert
        achados = detect_person_names_bert(
            "O requerente Carlos Alberto de Nobrega compareceu perante a "
            "Lei Complementar n. 123."
        )
        nomes = [a[2] for a in achados]
        print(f"SELFTEST: nomes detectados = {nomes}")
        if not any("Carlos" in n for n in nomes):
            print("SELFTEST FALHOU: o nome esperado nao foi detectado")
            return 1
        if any("Lei" in n for n in nomes):
            print("SELFTEST FALHOU: 'Lei Complementar' detectada como nome")
            return 1
        print("SELFTEST OK")
        return 0
    except BaseException as e:  # noqa: BLE001 — o build precisa do motivo
        import traceback
        traceback.print_exc()
        print(f"SELFTEST FALHOU: {e}")
        return 1


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        # Sem redirecionar para o log: quem chama (build.ps1) captura stdout.
        return _selftest()

    log = _redirect_output(_data_dir() / "tarjador.log")

    # TARJADOR_NO_SINGLE_INSTANCE=1: sobe um servidor próprio mesmo com outro
    # Tarjador aberto. É para o smoke test do build — sem isso, com o app
    # instalado rodando, o lançador recém-compilado sairia cedo e o teste
    # validaria o processo ANTIGO, dando "OK" para um bundle quebrado.
    ja_rodando = (None if os.environ.get("TARJADOR_NO_SINGLE_INSTANCE") == "1"
                  else _instancia_existente())
    if ja_rodando is not None:
        print(f"[launcher] instância já ativa na porta {ja_rodando}; só abrindo o navegador")
        webbrowser.open(f"http://127.0.0.1:{ja_rodando}")
        return 0

    base = _base_dir()
    script = base / "app.py"
    if not script.is_file():
        print(f"[launcher] ERRO: app.py não encontrado em {base}")
        return 2

    # CWD = base do bundle, ANTES de carregar a config: o Streamlit procura
    # $CWD/.streamlit/config.toml, e é de lá que vem o TEMA (teal) — o mesmo
    # arquivo dos deploys hospedados, embarcado pelo .spec. Sem o chdir, o
    # desktop abre com o acento vermelho padrão. (Rodando do fonte, base é a
    # raiz do repositório, onde .streamlit/ já está.)
    os.chdir(base)

    port = _porta_livre()
    _configura_ambiente()
    (_data_dir() / "porta.txt").write_text(str(port), encoding="utf-8")

    url = f"http://127.0.0.1:{port}"
    print(f"[launcher] base={base}")
    print(f"[launcher] servindo {script.name} em {url}")
    # TARJADOR_NO_BROWSER=1 sobe o servidor sem abrir aba — para smoke test do
    # bundle (verificar /_stcore/health) sem sequestrar o navegador de quem
    # está construindo.
    if os.environ.get("TARJADOR_NO_BROWSER") != "1":
        threading.Thread(target=_abre_navegador, args=(url, port), daemon=True).start()

    # Importado só aqui: o import do Streamlit é caro e não deve acontecer no
    # caminho "já tem instância aberta, só abre a aba".
    #
    # E ANTES de mexer no PATH, de propósito: este import puxa
    # starlette -> anyio -> ssl, e o `_ssl` resolve as DLLs de OpenSSL na hora.
    # Com o Tesseract já no PATH, essa resolução pega o libcrypto errado (ver
    # `_ativa_tesseract`). Importar primeiro, mexer no PATH depois.
    from streamlit.web import bootstrap

    _ativa_tesseract()

    # O MESMO dict vai para `load_config_options` e para `run` — é o que o
    # CLI oficial faz (`streamlit run` passa flag_options aos dois): o
    # primeiro aplica agora, o segundo alimenta os watchers de config, que
    # reaplicariam as flags num re-parse. Divergir os dois é reintroduzir a
    # configuração-fantasma por outro caminho.
    opts = _opcoes_streamlit(port)
    bootstrap.load_config_options(flag_options=opts)
    try:
        bootstrap.run(str(script), False, [], opts)
    except TypeError:
        # A assinatura perdeu o parâmetro `is_hello` em versões mais novas do
        # Streamlit. O requirements trava a faixa <1.60, mas o fallback é de
        # graça e evita um build quebrado por bump de versão.
        bootstrap.run(str(script), [], opts)  # type: ignore[call-arg]
    finally:
        log.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
