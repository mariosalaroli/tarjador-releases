"""NER de nomes de pessoa via BERT afinado em texto jurídico brasileiro.

Modelo: `pierreguillou/ner-bert-base-cased-pt-lenerbr` (BERTimbau + LeNER-Br).
Diferente do spaCy genérico, ele reconhece legislação/órgão como tais (não
como pessoa) — some com falsos positivos tipo "Lei Complementar". Em troca,
tende a marcar endereços de e-mail como nome; por isso descartamos spans que
caem dentro de um e-mail (que já tratamos em separado).

Carregado sob demanda (lazy): o `torch`/`transformers` só entram em memória
quando o usuário liga a opção. Custa ~350 MB de RAM e alguns segundos no
primeiro uso.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time

from ._detector_full import _limpa_nome


def _tem_token() -> bool:
    """Há credencial do Hugging Face Hub no ambiente?"""
    return bool(os.environ.get("HF_TOKEN")
                or os.environ.get("HUGGING_FACE_HUB_TOKEN"))


# NENHUMA variável do huggingface_hub é forçada aqui: a biblioteca fica com os
# padrões dela (backend "xet" ligado), e o modelo carrega em ~5s nos dois
# deploys — inclusive SEM token.
#
# Registro do erro, para não repeti-lo: em 2026-07-14 o xet passou a responder
# 401 em download anônimo de repo público. Diante disso o código passou a
# forçar HF_HUB_DISABLE_XET=1 — o que empurrou o download para o caminho HTTP
# tradicional e AÍ ele deixou de completar a partir do Streamlit Cloud (nem em
# 240s, com ou sem token). O 401 era um INCIDENTE PASSAGEIRO do Hugging Face;
# o "conserto" é que virou o problema permanente, e por dias se procurou a
# causa em rate limit, token e memória. Lição: incidente de terceiro se
# confirma antes de virar mudança de configuração no nosso lado — e, se virar,
# tem que ser revisitado quando o incidente passa.
#
# As duas variáveis continuam valendo se vierem do ambiente/secrets (dá para
# isolar problema de rede em produção sem novo deploy):
#   HF_HUB_DISABLE_XET=1       → volta ao download HTTP tradicional
#   HF_HUB_DOWNLOAD_TIMEOUT=30 → corta socket pendurado
# O Dockerfile do HF Space fixa a primeira: produção não fica à mercê nem do
# padrão da biblioteca nem de experimento feito para o outro deploy.

_MODEL = "pierreguillou/ner-bert-base-cased-pt-lenerbr"
_pipe = None
_falha: str | None = None  # mensagem da falha DEFINITIVA (não o "ainda vindo")
_thread: threading.Thread | None = None  # carga em segundo plano
_resultado: dict = {}                    # {"pipe": ...} ou {"erro": ...}
_lock = threading.Lock()
# Diagnóstico do carregamento, para a UI mostrar sem depender dos logs do
# servidor (no Streamlit Cloud o painel de logs despeja o build inteiro e
# corta o final, que é justamente onde estaria a informação útil).
_diag: str | None = None

# Teto de espera (wall clock) da ANÁLISE pelo modelo. Não é o tempo do
# download: este começa em `iniciar_carga()`, assim que o app abre, e segue em
# segundo plano mesmo se o teto estourar. Com a rede saudável o modelo fica
# pronto em ~5s (medido nos dois deploys); o teto é folgado para absorver um
# cold start ruim, não para acomodar um download quebrado. Estourou: o app NÃO
# trava — segue com as heurísticas, avisa, e a próxima análise pega o modelo
# pronto (a thread continua baixando).
_TIMEOUT_S = float(os.environ.get("TARJADOR_BERT_TIMEOUT", "180"))

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


class NerBertUnavailable(RuntimeError):
    """O modelo BERT não pôde ser carregado (download travado, sem rede,
    memória insuficiente...). Quem chama deve seguir sem a IA, não quebrar."""


def _ambiente() -> str:
    return (f"HF_TOKEN={'sim' if _tem_token() else 'NÃO'}, "
            f"xet={'off' if os.environ.get('HF_HUB_DISABLE_XET') else 'on'}")


def _carrega_pipe():
    # Log no stderr (logs do servidor) + `_diag` (exibido na UI): sem isso não
    # dá para distinguir "download lento", "sem token" e "morto por falta de
    # memória" olhando um app que só mostra uma barra parada.
    global _diag
    t0 = time.monotonic()
    print(f"[ner_bert] carregando {_MODEL} ({_ambiente()})",
          file=sys.stderr, flush=True)
    from transformers import pipeline
    pipe = pipeline(
        "token-classification",
        model=_MODEL,
        aggregation_strategy="max",
    )
    segundos = time.monotonic() - t0
    _diag = f"IA de nomes pronta em {segundos:.1f}s ({_ambiente()})"
    print(f"[ner_bert] modelo pronto em {segundos:.1f}s",
          file=sys.stderr, flush=True)
    # Alguns tokenizers do HF não trazem `model_max_length` no config,
    # o que faz o pipeline usar o valor-sentinela (~1e30) e efetivamente
    # nunca truncar. Sem isso, um chunk com muitos tokens por char (PDF
    # sem espaços/quebras de linha) ultrapassa os 512 tokens do BERT e
    # quebra na soma dos position embeddings.
    pipe.tokenizer.model_max_length = 512
    return pipe


def iniciar_carga() -> None:
    """Começa a baixar/carregar o modelo em segundo plano, sem esperar.

    Chamado assim que o app abre: o download (centenas de MB) corre enquanto o
    usuário escolhe o arquivo, em vez de começar só quando a análise pede. Em
    ambiente de rede lenta (Streamlit Cloud) isso é a diferença entre a análise
    esperar do zero e encontrar o modelo já pronto. Idempotente."""
    global _thread
    with _lock:
        if _pipe is not None or _falha is not None or _thread is not None:
            return

        def _run():
            try:
                _resultado["pipe"] = _carrega_pipe()
            except BaseException as e:  # noqa: BLE001 — qualquer falha vira aviso
                _resultado["erro"] = e

        _thread = threading.Thread(target=_run, daemon=True,
                                   name="carrega-ner-bert")
        _thread.start()


def _get_pipe(timeout: float | None = None):
    """Devolve o pipeline, esperando no máximo `timeout` — ou levanta
    NerBertUnavailable.

    A carga roda numa thread DAEMON e é esperada com `join(timeout)`: não dá
    para matar uma thread em Python, mas daemon + não-join garante que um
    download pendurado não segura o app nem a saída do processo.

    Um timeout NÃO é memorizado como falha: a thread continua baixando, e a
    próxima análise (ou um F5) pode encontrar o modelo já pronto. Só erro de
    verdade (sem rede, memória, modelo inexistente) vira `_falha` definitiva —
    aí não adianta repetir a espera a cada página do PDF.
    """
    global _pipe, _falha, _diag
    if _pipe is not None:
        return _pipe
    if _falha is not None:
        raise NerBertUnavailable(_falha)

    iniciar_carga()
    t = _thread
    if t is not None:
        t.join(_TIMEOUT_S if timeout is None else timeout)

    if "erro" in _resultado:
        _falha = (f"não foi possível carregar o modelo de IA "
                  f"({_resultado['erro']} — {_ambiente()})")
        _diag = _falha
        raise NerBertUnavailable(_falha)
    if "pipe" not in _resultado:
        # Ainda baixando: não é falha definitiva. A thread segue trabalhando.
        msg = (f"o modelo de IA ainda está sendo baixado (esperamos "
               f"{int(_TIMEOUT_S)}s — {_ambiente()}). Ele continua vindo em "
               f"segundo plano: refaça a análise em alguns minutos para usá-lo")
        _diag = msg
        raise NerBertUnavailable(msg)

    _pipe = _resultado["pipe"]
    return _pipe


def diagnostico() -> str | None:
    """Como foi o carregamento do modelo (tempo, se havia token, se o xet
    estava ligado) — para a UI exibir. None se nunca se tentou carregar."""
    return _diag


def garantir_disponivel() -> str | None:
    """Aquece o modelo ANTES de varrer o documento. Devolve None se está tudo
    certo, ou a mensagem do problema se a IA não vai poder ser usada — assim
    quem chama decide o fallback (heurísticas) uma vez só, em vez de tropeçar
    na mesma falha a cada página."""
    try:
        _get_pipe()
        return None
    except NerBertUnavailable as e:
        return str(e)


def _chunks(text: str, max_chars: int = 1000):
    """Divide o texto em blocos <= max_chars com o offset de origem.

    O modelo tem limite de 512 tokens; blocos de ~1000 chars costumam ficar
    abaixo disso. Linhas maiores que max_chars (ex.: PDF extraído sem quebras)
    são cortadas à força, senão o chunk cresceria sem limite.
    """
    cur, base = "", 0
    for line in text.splitlines(keepends=True):
        while len(line) > max_chars:
            if cur:
                yield cur, base
                base += len(cur)
                cur = ""
            piece, line = line[:max_chars], line[max_chars:]
            yield piece, base
            base += len(piece)
        if cur and len(cur) + len(line) > max_chars:
            yield cur, base
            base += len(cur)
            cur = ""
        cur += line
    if cur:
        yield cur, base


def detect_person_names_bert(text: str):
    """Retorna [(inicio, fim, nome, score)] de PESSOA no texto.

    Descarta spans que intersectam um endereço de e-mail e nomes com menos de
    duas palavras (via _limpa_nome).
    """
    pipe = _get_pipe()
    email_spans = [(m.start(), m.end()) for m in _EMAIL_RE.finditer(text)]
    achados = []
    for chunk, base in _chunks(text):
        for e in pipe(chunk):
            if e["entity_group"] != "PESSOA":
                continue
            s, end = base + int(e["start"]), base + int(e["end"])
            if any(s < e_end and end > e_ini for e_ini, e_end in email_spans):
                continue  # dentro de um e-mail — tratado em separado
            nome = _limpa_nome(text[s:end])
            if not nome:
                continue
            off = text[s:end].find(nome.split()[0])
            ns = s + max(off, 0)
            achados.append((ns, ns + len(nome), nome, float(e["score"])))
    return achados
