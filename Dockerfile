# Hugging Face Space (SDK: docker) rodando o app Streamlit.
# O Streamlit deixou de ser SDK nativo do HF Spaces (deprecado em 2025-04-30),
# então rodamos via Docker. O proxy do Space aponta para app_port=8501
# (definido no YAML do README.md), a porta padrão do Streamlit.
FROM python:3.12-slim

# tesseract: OCR dos selos de assinatura (opcional na sidebar, mas o pacote
# precisa existir). build-essential por precaução, caso alguma dep compile.
# git: exigido pelo Dev Mode do HF Space (ele roda `git config` no build da
# imagem; sem git no PATH o build falha com exit 127).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-por \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces roda o container como uid 1000; criar o usuário evita problemas de
# permissão de escrita (cache de modelos, ~/.streamlit).
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    # cache do HF Hub/transformers num diretório gravável pelo uid 1000
    HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # 2 threads = usa os 2 vCPUs do cpu-basic na inferência do BERT; ~2x mais
    # rápido na análise. Antes ficava travado em 1 thread desde o incidente de
    # 2026-07-11 (websocket caindo no meio da análise, erro "Unexpected ASGI
    # message ... after 'websocket.close'"), MAS aquele fix subiu junto com
    # enableWebsocketCompression=false. Teste isolado em 2026-07-13 (doc de 400
    # páginas / 300 ocorrências, BERT ligado) provou que quem segurava a
    # conexão era a compressão desligada, não a trava de thread: com 2 threads
    # a análise terminou inteira, mais rápida e sem queda. NÃO mexer no
    # enableWebsocketCompression=false do CMD — esse é o load-bearing.
    OMP_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    # Download do modelo BERT pelo caminho HTTP tradicional (sem o backend
    # "xet"). É a configuração com que o Space carrega o modelo em ~4s hoje —
    # fixada AQUI, no arquivo que só o HF usa, para que a produção não dependa
    # do padrão da biblioteca nem de experimento feito para o Streamlit Cloud
    # (que sofre com a rede até o Hub e está sendo investigado à parte). É o
    # "config por ambiente" na prática: mesmo código, comportamento pinado onde
    # importa.
    HF_HUB_DISABLE_XET=1

WORKDIR /home/user/app

# requirements.txt já traz torch CPU (--extra-index-url embutido), transformers,
# spaCy + o wheel do pt_core_news_sm, presidio, pymupdf etc.
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user --upgrade pip \
    && pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user . .

EXPOSE 8501

# enableCORS/XSRF=false: o Space serve o app dentro de um iframe; sem isso o
# upload e o websocket podem falhar no embed. gatherUsageStats=false corta a
# telemetria do Streamlit.
# enableWebsocketCompression=false: o proxy do HF já comprime; a compressão
# dupla é causa conhecida de queda de websocket atrás de proxy.
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false", \
     "--server.enableWebsocketCompression=false", \
     "--browser.gatherUsageStats=false"]
