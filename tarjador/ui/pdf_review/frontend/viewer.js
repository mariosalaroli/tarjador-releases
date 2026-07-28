/* Visualizador de revisão do Tarjador — componente customizado do Streamlit.
 *
 * Toda a interação (scroll contínuo, zoom Ctrl+rolagem, toggle de tarja por
 * clique) acontece AQUI, no navegador — o servidor não participa. O Python
 * manda uma vez a URL do PDF (servida da RAM pelo media_file_manager) e a
 * geometria das caixas; o estado de marcação volta debounced via
 * setComponentValue.
 *
 * Protocolo de sincronização (evita eco infinito, é IDEMPOTENTE):
 *  - `py_ver` (args): só muda quando a ALTERAÇÃO NASCEU NO PYTHON (clique na
 *    tabela, marcar-tudo). Quando muda, adotamos o `marcar` dos args por
 *    inteiro. Reruns com py_ver igual são eco do nosso próprio envio e o
 *    `marcar` deles é ignorado (o local pode estar mais novo que o eco).
 *  - `seq` (valor): incrementa a cada envio nosso; o Python deduplica por ele
 *    e NÃO bumpa py_ver ao aplicar (senão viraria eco).
 *  - `marcar` (valor): a cada envio manda o SNAPSHOT COMPLETO de S.marcar,
 *    não um diff dos gids que mudaram desde o último envio. Com documentos
 *    grandes (~300 entidades) o rerun do fragment é caro o bastante para um
 *    lote de cliques dobrar o anterior antes do Python terminar de
 *    processá-lo — o Streamlit descarta/supera o rerun em andamento, e um
 *    diff (só os gids QUE MUDARAM naquele envio) some para sempre: o clique
 *    parecia ter "pegado" na tela, mas o servidor nunca soube dele, e ele
 *    reaparecia desmarcado assim que QUALQUER outra coisa bumpasse py_ver.
 *    Mandar o estado inteiro a cada vez resolve isso — se um envio se perde,
 *    o próximo já carrega tudo acumulado (nada fica órfão).
 *
 * Coordenadas: as caixas chegam em pontos PDF com origem no topo-esquerdo
 * (referencial do PyMuPDF). O viewport do pdf.js em scale=1 tem 1px = 1pt e a
 * mesma origem, então as caixas são posicionadas em PORCENTAGEM da página —
 * zoom muda só o tamanho do wrapper e as caixas acompanham de graça.
 */

import * as pdfjsLib from "./pdfjs/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc =
  new URL("./pdfjs/pdf.worker.min.mjs", import.meta.url).href;

const SEP = "\x1f"; // separador tipo/texto dentro do gid (ver _gid no app.py)
const scroller = document.getElementById("scroller");
const statusEl = document.getElementById("status");
const statusText = document.getElementById("status-text");
const pageInput = document.getElementById("pageinput");
const pageTotal = document.getElementById("pagetotal");
const zoomLabel = document.getElementById("zoomlabel");

const S = {
  url: null, pdf: null, nPages: 0,
  wPt: [], hPt: [],            // dimensões (pontos) por página, 0-based
  scale: 0, fitScale: 1,
  wrappers: [], canvases: [], renderedScale: [], renderTasks: [],
  visible: new Set(),          // páginas dentro da margem de renderização
  boxesJson: "",               // p/ detectar mudança de geometria nos args
  gidEls: new Map(),           // gid -> [{el, page}] na ordem (página, y, x)
  detPages: [],
  marcar: {}, pyVer: null, navKey: "", seq: 0,
  manual: [],                  // tarjas do rolo: [[página, x0, y0, x1, y1]]
  manualAdopted: false,        // args.manual só é adotado 1x (restore de F5)
  lineBoxes: [],               // caixas de linha de texto por página (rolo)
  textLayers: [],              // div .textLayer por página renderizada
  mode: "hand",                // "hand" (arrastar) | "texto" | "rolo"
  curPage: 0, io: null, laidOut: false,
  pendingArgs: null, sendTimer: 0, rerenderTimer: 0,
};

/* ------------------------------------------------- protocolo do Streamlit */
function send(type, data) {
  window.parent.postMessage(Object.assign({ isStreamlitMessage: true, type }, data), "*");
}
send("streamlit:componentReady", { apiVersion: 1 });

window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || d.type !== "streamlit:render") return;
  onRender(d.args || {}, d.theme || null);
});

function scheduleSend() {
  clearTimeout(S.sendTimer);
  S.sendTimer = setTimeout(() => {
    S.seq += 1;
    // Snapshot completo de S.marcar (não um diff) — ver docstring do topo do
    // arquivo sobre por que isso é essencial para não perder toggles sob
    // cliques rápidos em documentos grandes. Tarjas manuais: lista completa
    // também — o componente é o único dono delas.
    send("streamlit:setComponentValue", {
      value: { seq: S.seq, marcar: S.marcar, manual: S.manual },
      dataType: "json",
    });
  }, 250); // agrupa cliques rápidos num envio só
}

/* ------------------------------------------------------------ ciclo args */
function onRender(args, theme) {
  if (theme) applyTheme(theme);
  if (args.height) send("streamlit:setFrameHeight", { height: args.height });

  if (!S.url) {
    S.url = args.pdf_url;
    S.pendingArgs = args;
    loadDocument(args);
  } else if (args.pdf_url && args.pdf_url !== S.url) {
    // Documento trocou (novo upload/análise): recomeça do zero.
    window.location.reload();
  } else if (S.esperandoBytes && args.pdf_b64) {
    // Chegou o PDF pelos args (plano B pedido em loadDocument).
    S.esperandoBytes = false;
    S.pendingArgs = args;
    loadDocument(args);
  } else if (S.laidOut) {
    applyArgs(args);
  } else {
    S.pendingArgs = args; // chegou rerun antes do layout terminar
  }
}

function applyTheme(t) {
  document.body.classList.toggle("dark", t.base === "dark");
  if (t.primaryColor)
    document.documentElement.style.setProperty("--accent", t.primaryColor);
}

function applyArgs(args) {
  const bj = JSON.stringify(args.boxes || {});
  if (bj !== S.boxesJson) {
    S.boxesJson = bj;
    buildOverlays(args.boxes || {});
  }
  if (!S.manualAdopted) {
    // Restaura tarjas manuais após F5/remontagem — fora isso o dono da lista
    // é o componente (o Python só ecoa o que recebeu, por isso adota-se 1x).
    S.manualAdopted = true;
    if (Array.isArray(args.manual) && args.manual.length) {
      S.manual = args.manual.map((r) => r.slice());
      renderManualBoxes();
    }
  }
  if (args.py_ver !== S.pyVer) {
    // Mudança nascida no Python (tabela/marcar-tudo): adota por inteiro.
    S.pyVer = args.py_ver;
    S.marcar = Object.assign({}, args.marcar || {});
    refreshAllBoxes();
  }
  const navKey = args.nav ? args.nav[0] + "|" + args.nav[1] : "";
  if (navKey !== S.navKey) {
    S.navKey = navKey;
    if (args.nav) navigateTo(args.nav[0], args.nav[1]);
    else clearNavRing();
  }
}

/* -------------------------------------------------------- carga e layout */
function _b64ParaBytes(b64) {
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return arr.buffer;
}

/* Busca o PDF pela URL /media/… do Streamlit. Devolve o ArrayBuffer, ou
 * lança se a resposta não for um PDF — o que acontece no Streamlit Cloud,
 * onde a camada de autenticação/CDN devolve uma página HTML no lugar do
 * arquivo (o HF Space não tem esse intermediário e serve direto).
 * Requisição ÚNICA de propósito: por conta própria o pdf.js usaria range
 * requests + streaming, e atrás de um proxy que não os trate direito ele
 * recebe lixo e morre com "Invalid PDF structure", sem dizer o que veio. */
async function _buscaPdfPorUrl(url) {
  const resp = await fetch(url, { credentials: "same-origin" });
  if (!resp.ok) {
    throw new Error(`o servidor respondeu ${resp.status} ao buscar o PDF`);
  }
  const buf = await resp.arrayBuffer();
  const assinatura = new TextDecoder().decode(new Uint8Array(buf, 0, 5));
  if (!assinatura.startsWith("%PDF")) {
    const tipo = resp.headers.get("content-type") || "desconhecido";
    throw new Error(`a resposta não é um PDF (content-type: ${tipo}, `
                    + `${buf.byteLength} bytes)`);
  }
  return buf;
}

async function loadDocument(args) {
  try {
    let buf;
    if (args.pdf_b64) {
      // O Python mandou os bytes (plano B, ver abaixo).
      buf = _b64ParaBytes(args.pdf_b64);
    } else {
      try {
        buf = await _buscaPdfPorUrl(args.pdf_url);
      } catch (err) {
        // Plano B: pede ao Python que mande o PDF pelos args do componente,
        // pela conexão que já está autenticada. Custo (o binário trafega)
        // pago SÓ onde a URL não funciona — onde ela funciona, nada muda.
        console.warn("PDF por URL falhou, pedindo bytes ao Python:", err);
        S.esperandoBytes = true;
        statusText.textContent = "Carregando documento...";
        S.seq += 1;
        send("streamlit:setComponentValue", {
          value: { seq: S.seq, need_bytes: true,
                   marcar: S.marcar, manual: S.manual },
          dataType: "json",
        });
        return;
      }
    }
    const task = pdfjsLib.getDocument({ data: buf });
    S.pdf = await task.promise;
    S.nPages = S.pdf.numPages;
    pageTotal.textContent = "/ " + S.nPages;
    pageInput.max = S.nPages;
    for (let i = 1; i <= S.nPages; i++) {
      const vp = (await S.pdf.getPage(i)).getViewport({ scale: 1 });
      S.wPt.push(vp.width);
      S.hPt.push(vp.height);
    }
    whenWideEnough(() => layout());
  } catch (err) {
    statusText.textContent = "Falha ao carregar o PDF: " + (err && err.message);
    statusEl.querySelector(".spinner").style.display = "none";
  }
}

// O iframe pode nascer com largura 0 (mount do Streamlit); calcular o fit
// nesse instante daria escala lixo — espera a largura real aparecer.
function whenWideEnough(fn) {
  if (scroller.clientWidth > 40) { fn(); return; }
  const ro = new ResizeObserver(() => {
    if (scroller.clientWidth > 40) { ro.disconnect(); fn(); }
  });
  ro.observe(scroller);
}

// Reajusta o zoom de "encaixar na largura" sempre que o container mudar de
// tamanho — não só na 1ª vez. Alimenta tanto o resize da janela quanto o
// arrasto do splitter (setupSplitter, abaixo): a coluna do PDF muda de
// largura via CSS no documento PAI, o iframe (width:100% por padrão do
// Streamlit) acompanha, e este observer nota a mudança de `scroller` por
// dentro. Mesma lógica do fullscreenchange: só força o fit se o usuário
// estava nele (zoom manual não é mexido por um resize).
let resizeRerenderTimer = 0;
new ResizeObserver(() => {
  if (!S.laidOut) return;
  const estavaNoFit = Math.abs(S.scale - S.fitScale) < 0.02;
  S.fitScale = computeFit();
  if (estavaNoFit) {
    setScaleKeepingView(S.fitScale);
    clearTimeout(resizeRerenderTimer);
    resizeRerenderTimer = setTimeout(() => {
      for (const i of S.visible) renderPage(i);
    }, 120);
  }
  updateZoomLabel();
}).observe(scroller);

// Troca a escala preservando O QUE o usuário estava vendo: guarda a página
// no topo da área visível e a fração dentro dela, reescala e recoloca o
// scroll no mesmo ponto do CONTEÚDO. Sem isso, redimensionar as páginas
// (fullscreen, splitter, resize) deixa o scrollTop antigo em pixels brutos
// apontando pra outra página — era o "fullscreen não abre na página que eu
// estava vendo".
function setScaleKeepingView(next) {
  const cy = scroller.scrollTop + 8;
  const pi = pageAtContentY(cy);
  const wrap = S.wrappers[pi];
  const frac = wrap ? (cy - wrap.offsetTop) / wrap.offsetHeight : 0;
  S.scale = next;
  applyPageSizes();
  if (wrap) scroller.scrollTop = wrap.offsetTop + frac * wrap.offsetHeight - 8;
}

function computeFit() {
  const maxW = Math.max(...S.wPt);
  return Math.max(0.1, (scroller.clientWidth - 28) / maxW);
}

function layout() {
  S.fitScale = computeFit();
  S.scale = S.fitScale;
  for (let i = 0; i < S.nPages; i++) {
    const w = document.createElement("div");
    w.className = "page-wrap";
    w.dataset.page = i;
    const num = document.createElement("div");
    num.className = "page-num";
    num.textContent = i + 1;
    w.appendChild(num);
    const ov = document.createElement("div");
    ov.className = "overlay";
    w.appendChild(ov);
    scroller.appendChild(w);
    S.wrappers.push(w);
    S.canvases.push(null);
    S.renderedScale.push(-1);
    S.renderTasks.push(null);
  }
  applyPageSizes();
  S.io = new IntersectionObserver(onIntersect,
    { root: scroller, rootMargin: "900px 0px" });
  S.wrappers.forEach((w) => S.io.observe(w));
  S.laidOut = true;
  statusEl.classList.add("hidden");
  if (S.pendingArgs) { applyArgs(S.pendingArgs); S.pendingArgs = null; }
  updateZoomLabel();
}

function applyPageSizes() {
  for (let i = 0; i < S.nPages; i++) {
    S.wrappers[i].style.width = S.wPt[i] * S.scale + "px";
    S.wrappers[i].style.height = S.hPt[i] * S.scale + "px";
    // O TextLayer do pdf.js posiciona os spans via calc(var(--scale-factor));
    // atualizar aqui mantém a camada de texto alinhada durante o zoom, antes
    // do re-render nítido chegar.
    S.wrappers[i].style.setProperty("--scale-factor", S.scale);
  }
}

/* ---------------------------------------------------------- renderização */
function onIntersect(entries) {
  for (const en of entries) {
    const i = +en.target.dataset.page;
    if (en.isIntersecting) {
      S.visible.add(i);
      renderPage(i);
    } else {
      S.visible.delete(i);
      derenderPage(i);
    }
  }
  updateCurrentPage();
}

async function renderPage(i) {
  if (!S.pdf || S.renderedScale[i] === S.scale) return;
  if (S.renderTasks[i]) { S.renderTasks[i].cancel(); S.renderTasks[i] = null; }
  const scaleAtStart = S.scale;
  try {
    const page = await S.pdf.getPage(i + 1);
    if (S.scale !== scaleAtStart) return; // zoom mudou enquanto esperava
    const vp = page.getViewport({ scale: scaleAtStart });
    // DPR limitado: em zoom alto o canvas explodiria (memória/tempo).
    const dpr = Math.min(window.devicePixelRatio || 1,
      Math.sqrt(16e6 / (vp.width * vp.height)) || 1);
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(vp.width * dpr);
    canvas.height = Math.floor(vp.height * dpr);
    const ctx = canvas.getContext("2d");
    const task = page.render({
      canvasContext: ctx,
      viewport: vp,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
      // ENABLE_FORMS: desenha a aparência de widgets de formulário no canvas —
      // é o que faz o selo gov.br (widget de assinatura) aparecer quando NÃO
      // está marcado. Marcado, o overlay preto opaco cobre por cima (HTML
      // fica acima do canvas — o problema antigo de apagar widget sumiu).
      annotationMode: pdfjsLib.AnnotationMode.ENABLE_FORMS,
    });
    S.renderTasks[i] = task;
    await task.promise;
    S.renderTasks[i] = null;
    if (S.scale !== scaleAtStart) return; // ficou obsoleto durante o render
    if (S.canvases[i]) S.canvases[i].remove();
    // Canvas atrás do overlay (overlay é o último filho, fica por cima).
    S.wrappers[i].insertBefore(canvas, S.wrappers[i].querySelector(".overlay"));
    S.canvases[i] = canvas;
    S.renderedScale[i] = scaleAtStart;
    // Camada de texto (seleção no modo "texto"): entre o canvas e o overlay.
    if (S.textLayers[i]) { S.textLayers[i].remove(); S.textLayers[i] = null; }
    const td = document.createElement("div");
    td.className = "textLayer";
    S.wrappers[i].insertBefore(td, S.wrappers[i].querySelector(".overlay"));
    S.textLayers[i] = td;
    await new pdfjsLib.TextLayer({
      textContentSource: page.streamTextContent(),
      container: td,
      viewport: vp,
    }).render();
  } catch (err) {
    if (err && err.name === "RenderingCancelledException") return;
    console.error("render page", i + 1, err);
  }
}

function derenderPage(i) {
  if (S.renderTasks[i]) { S.renderTasks[i].cancel(); S.renderTasks[i] = null; }
  if (S.canvases[i]) { S.canvases[i].remove(); S.canvases[i] = null; }
  if (S.textLayers[i]) { S.textLayers[i].remove(); S.textLayers[i] = null; }
  S.renderedScale[i] = -1;
}

/* ------------------------------------------------------------------ zoom */
scroller.addEventListener("wheel", (e) => {
  if (!e.ctrlKey) return; // rolagem normal segue nativa
  e.preventDefault();
  zoomAt(Math.exp(-e.deltaY * 0.0018), e.clientX, e.clientY);
}, { passive: false });

function zoomAt(factor, clientX, clientY) {
  const next = Math.min(Math.max(S.scale * factor, S.fitScale * 0.25), S.fitScale * 6);
  if (next === S.scale) return;
  // Âncora na PÁGINA sob o cursor, não no conteúdo do scroller: as páginas
  // são centralizadas (margin auto) e o vão lateral NÃO escala junto — a
  // conta ingênua com scrollLeft*r fazia o zoom derivar pra esquerda.
  const sr = scroller.getBoundingClientRect();
  const cy = scroller.scrollTop + (clientY - sr.top);
  const pi = pageAtContentY(cy);
  const wrap = S.wrappers[pi];
  const fx = (scroller.scrollLeft + (clientX - sr.left) - wrap.offsetLeft)
             / wrap.offsetWidth;
  const fy = (cy - wrap.offsetTop) / wrap.offsetHeight;
  S.scale = next;
  applyPageSizes(); // os canvases esticam via CSS na hora (feedback imediato)
  // offsetLeft/offsetTop relidos DEPOIS do resize (o autocentro mudou).
  scroller.scrollLeft = wrap.offsetLeft + fx * wrap.offsetWidth
                        - (clientX - sr.left);
  scroller.scrollTop = wrap.offsetTop + fy * wrap.offsetHeight
                       - (clientY - sr.top);
  updateZoomLabel();
  clearTimeout(S.rerenderTimer); // re-render nítido só quando o gesto parar
  S.rerenderTimer = setTimeout(() => {
    for (const i of S.visible) renderPage(i);
  }, 170);
}

function updateZoomLabel() {
  zoomLabel.textContent = Math.round((S.scale / S.fitScale) * 100) + "%";
}

document.getElementById("btn-zoomin").onclick = () => zoomCenter(1.25);
document.getElementById("btn-zoomout").onclick = () => zoomCenter(1 / 1.25);
document.getElementById("btn-fit").onclick = () => {
  S.fitScale = computeFit();
  zoomCenter(S.fitScale / S.scale);
};
function zoomCenter(f) {
  const rect = scroller.getBoundingClientRect();
  zoomAt(f, rect.left + rect.width / 2, rect.top + rect.height / 2);
}

/* Maximizar: fullscreen NATIVO do iframe do componente (o allow do Streamlit
 * inclui `fullscreen`) — instantâneo, sem rerun, Esc sai, e zoom/scroll/
 * tarjas sobrevivem. Ao entrar/sair, se o usuário estava no enquadramento
 * "ajustar à largura", re-ajusta para a largura nova; um zoom manual é
 * respeitado. */
const btnMax = document.getElementById("btn-max");
btnMax.onclick = () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen().catch((err) => {
    console.error("fullscreen negado", err);
  });
};
document.addEventListener("fullscreenchange", () => {
  const full = !!document.fullscreenElement;
  btnMax.textContent = full ? "🗗" : "⛶";
  btnMax.title = full ? "Restaurar tamanho (Esc)"
                      : "Maximizar o visualizador (tela cheia — Esc para sair)";
  const estavaNoFit = Math.abs(S.scale - S.fitScale) < 0.02;
  // A largura nova só existe depois do reflow do fullscreen.
  requestAnimationFrame(() => {
    S.fitScale = computeFit();
    if (estavaNoFit && S.laidOut) {
      setScaleKeepingView(S.fitScale);
      for (const i of S.visible) renderPage(i);
    }
    updateZoomLabel();
  });
});

/* ------------------------------------------------------- navegação/página */
function pageAtContentY(cy) {
  let cur = 0;
  for (let i = 0; i < S.nPages; i++) {
    if (S.wrappers[i].offsetTop <= cy) cur = i; else break;
  }
  return cur;
}

function pageAtViewport() {
  return pageAtContentY(scroller.scrollTop + scroller.clientHeight * 0.35);
}

let scrollTick = false;
scroller.addEventListener("scroll", () => {
  if (scrollTick) return;
  scrollTick = true;
  requestAnimationFrame(() => { scrollTick = false; updateCurrentPage(); });
});

function updateCurrentPage() {
  if (!S.laidOut) return;
  S.curPage = pageAtViewport();
  if (document.activeElement !== pageInput)
    pageInput.value = S.curPage + 1;
}

function jumpToPage(i, smooth) {
  i = Math.min(Math.max(i, 0), S.nPages - 1);
  scroller.scrollTo({ top: S.wrappers[i].offsetTop - 8,
                      behavior: smooth ? "smooth" : "auto" });
}

pageInput.addEventListener("change", () => {
  const n = parseInt(pageInput.value, 10);
  if (!isNaN(n)) jumpToPage(n - 1, false);
  pageInput.blur();
});

document.getElementById("btn-prevdet").onclick = () => {
  const atras = S.detPages.filter((p) => p < S.curPage);
  const alvo = atras.length ? atras[atras.length - 1]
                            : S.detPages[S.detPages.length - 1];
  if (alvo !== undefined) jumpToPage(alvo, false);
};
document.getElementById("btn-nextdet").onclick = () => {
  const adiante = S.detPages.filter((p) => p > S.curPage);
  const alvo = adiante.length ? adiante[0] : S.detPages[0];
  if (alvo !== undefined) jumpToPage(alvo, false);
};

/* ----------------------------------------------------- overlays de tarja */
function buildOverlays(boxes) {
  S.gidEls = new Map();
  S.detPages = Object.keys(boxes).map(Number).sort((a, b) => a - b);
  const prevdet = document.getElementById("btn-prevdet");
  const nextdet = document.getElementById("btn-nextdet");
  prevdet.disabled = nextdet.disabled = S.detPages.length === 0;
  for (let i = 0; i < S.nPages; i++) {
    const ov = S.wrappers[i].querySelector(".overlay");
    ov.textContent = "";
    const lista = boxes[i] || boxes[String(i)];
    if (!lista) continue;
    // Ordena por (y, x) dentro da página — junto com a iteração de páginas
    // em ordem, define a MESMA ordem de ocorrências que o Python usa para o
    // ciclo de navegação (nav_occ % n).
    const orden = lista.slice().sort((a, b) => (a[2] - b[2]) || (a[1] - b[1]));
    for (const [gid, x0, y0, x1, y1, seal, mask] of orden) {
      const el = document.createElement("div");
      el.className = "box" + (seal ? " seal" : "") + (mask ? " mask" : "");
      el.style.left = (x0 / S.wPt[i]) * 100 + "%";
      el.style.top = (y0 / S.hPt[i]) * 100 + "%";
      el.style.width = ((x1 - x0) / S.wPt[i]) * 100 + "%";
      el.style.height = ((y1 - y0) / S.hPt[i]) * 100 + "%";
      const [tipo, texto] = gid.split(SEP);
      if (mask) {
        // CPF descaracterizado: a prévia é o próprio texto mascarado
        // (***.456.789-**), não uma tarja preta. Fonte proporcional à
        // altura da caixa E ao zoom: --scale-factor vive no .page-wrap e o
        // applyPageSizes o atualiza a cada mudança de escala.
        el.textContent = mask;
        el.style.fontSize =
          "calc(var(--scale-factor) * " + ((y1 - y0) * 0.52).toFixed(2) + "px)";
        el.title = tipo + ": " + texto + "\nSerá descaracterizado para " +
          mask + "\nClique para tratar/não tratar";
      } else {
        el.title = tipo + ": " + texto + "\nClique para tarjar/não tarjar";
      }
      el.addEventListener("click", (e) => { e.stopPropagation(); toggle(gid); });
      ov.appendChild(el);
      if (!S.gidEls.has(gid)) S.gidEls.set(gid, []);
      S.gidEls.get(gid).push({ el, page: i });
    }
  }
  refreshAllBoxes();
  renderManualBoxes(); // o wipe acima levou junto as tarjas manuais
}

function boxState(gid) {
  return S.marcar[gid] !== undefined ? !!S.marcar[gid] : true;
}

function refreshGid(gid) {
  const on = boxState(gid);
  for (const { el } of S.gidEls.get(gid) || []) {
    el.classList.toggle("on", on);
    el.classList.toggle("off", !on);
  }
}

function refreshAllBoxes() {
  for (const gid of S.gidEls.keys()) refreshGid(gid);
}

function toggle(gid) {
  S.marcar[gid] = !boxState(gid);
  refreshGid(gid); // feedback instantâneo em TODAS as caixas da entidade
  scheduleSend();  // tabela/contador sincronizam no próximo envio debounced
}

/* Anel teal na entidade focada pela tabela + scroll até a ocorrência. */
function clearNavRing() {
  scroller.querySelectorAll(".box.nav").forEach((el) =>
    el.classList.remove("nav", "pulse"));
}

function navigateTo(gid, tick) {
  clearNavRing();
  const els = S.gidEls.get(gid);
  if (!els || !els.length) return;
  els.forEach(({ el }) => el.classList.add("nav"));
  const alvo = els[((tick % els.length) + els.length) % els.length];
  const wrap = S.wrappers[alvo.page];
  const y = wrap.offsetTop + alvo.el.offsetTop - scroller.clientHeight * 0.3;
  scroller.scrollTo({ top: Math.max(0, y), behavior: "smooth" });
  alvo.el.classList.add("pulse");
  setTimeout(() => alvo.el.classList.remove("pulse"), 1300);
}

/* ------------------------------------------------------- modos e ferramentas
 * "texto" (padrão): arrastar sobre TEXTO = seleção nativa (camada de texto
 *   do pdf.js); arrastar de uma área em branco (ou de uma caixa) = mão, move
 *   o documento; clique parado numa caixa = toggle.
 * "rolo": pinta tarjas manuais que grudam nas linhas de texto; sobre região
 *         sem texto (documento escaneado), vira retângulo livre.
 */
const MODE_BTNS = {
  texto: document.getElementById("btn-texto"),
  rolo: document.getElementById("btn-rolo"),
};
for (const [m, btn] of Object.entries(MODE_BTNS)) btn.onclick = () => setMode(m);

function setMode(m) {
  S.mode = m;
  document.getElementById("root").className = "mode-" + m;
  for (const [k, btn] of Object.entries(MODE_BTNS))
    btn.classList.toggle("active", k === m);
}
setMode("texto");

/* Mão implícita: no modo texto, arrastar a partir de qualquer ponto que NÃO
 * seja texto (área em branco da folha, vão entre páginas, ou até uma caixa)
 * move o documento. A decisão é no mousedown: em cima de um span da camada
 * de texto, deixa a seleção nativa acontecer. Um arrasto NÃO pode disparar o
 * clique de toggle da caixa onde o mouse soltou — o clique é engolido na
 * fase de captura quando houve movimento. */
let pan = null;
let panMoved = false;
scroller.addEventListener("mousedown", (e) => {
  if (S.mode !== "texto" || e.button !== 0) return;
  if (e.target.tagName === "SPAN" && e.target.closest(".textLayer")) return;
  pan = { x: e.clientX, y: e.clientY,
          sl: scroller.scrollLeft, st: scroller.scrollTop };
  panMoved = false;
});
window.addEventListener("mousemove", (e) => {
  if (pan) {
    const dx = e.clientX - pan.x, dy = e.clientY - pan.y;
    if (!panMoved && Math.abs(dx) + Math.abs(dy) > 5) {
      panMoved = true;
      document.body.classList.add("panning");
    }
    if (panMoved) {
      scroller.scrollLeft = pan.sl - dx;
      scroller.scrollTop = pan.st - dy;
      e.preventDefault();
    }
  }
  if (stroke) strokeMove(e);
});
window.addEventListener("mouseup", () => {
  if (pan) { pan = null; document.body.classList.remove("panning"); }
  if (stroke) strokeEnd();
});
scroller.addEventListener("click", (e) => {
  if (panMoved) { e.stopPropagation(); e.preventDefault(); panMoved = false; }
}, true);

/* --------------------------------------------------- rolo de tarja manual */
const BRUSH_PT = 6; // meia-altura do "rolo" em pontos PDF

async function getLineBoxes(i) {
  // Caixas de linha de texto da página (pontos PDF, origem topo-esquerdo),
  // calculadas do getTextContent — é nelas que o rolo "gruda".
  if (S.lineBoxes[i]) return S.lineBoxes[i];
  const page = await S.pdf.getPage(i + 1);
  const tc = await page.getTextContent();
  const H = S.hPt[i];
  const items = [];
  for (const it of tc.items) {
    if (!it.str || !it.str.trim() || !it.width) continue;
    const t = it.transform; // origem bottom-left, y pra cima
    const h = Math.hypot(t[2], t[3]) || Math.hypot(t[0], t[1]) || 8;
    items.push({ x0: t[4], x1: t[4] + it.width,
                 y0: H - t[5] - h * 0.82, y1: H - t[5] + h * 0.22 });
  }
  items.sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0);
  const lines = [];
  for (const it of items) {
    const last = lines[lines.length - 1];
    // mesma linha = sobreposição vertical majoritária com a anterior
    if (last && it.y0 < last.y1 - (last.y1 - last.y0) * 0.4) {
      last.x0 = Math.min(last.x0, it.x0);
      last.x1 = Math.max(last.x1, it.x1);
      last.y0 = Math.min(last.y0, it.y0);
      last.y1 = Math.max(last.y1, it.y1);
    } else {
      lines.push({ ...it });
    }
  }
  S.lineBoxes[i] = lines;
  return lines;
}

/* A seleção do rolo vai da ÂNCORA (onde o botão foi apertado) até a posição
 * ATUAL do cursor — não é o rastro acumulado por onde o mouse passou. É o que
 * permite VOLTAR ATRÁS sem soltar o botão: arrastar de volta encolhe a
 * seleção, e recuar por cima de uma linha já pintada a desfaz. Mesma mecânica
 * de uma seleção de texto (ou do marca-texto de qualquer leitor de PDF). */
let stroke = null; // {page, a: âncora, b: atual, previews: Map, freeEl}

scroller.addEventListener("mousedown", (e) => {
  if (S.mode !== "rolo" || e.button !== 0) return;
  if (e.target.classList && e.target.classList.contains("manual")) return;
  const wrap = e.target.closest && e.target.closest(".page-wrap");
  if (!wrap) return;
  e.preventDefault(); // sem seleção de texto no meio da pintura
  const pi = +wrap.dataset.page;
  stroke = { page: pi, a: null, b: null, previews: new Map(), freeEl: null };
  getLineBoxes(pi).then((l) => {
    if (stroke && stroke.page === pi) {
      stroke.lines = l;
      strokeRedraw();  // as linhas chegaram: desenha o que já foi arrastado
    }
  });
  strokeMove(e);
});

function _pontoNaPagina(e) {
  const wrap = S.wrappers[stroke.page];
  const wr = wrap.getBoundingClientRect();
  return {
    x: Math.min(Math.max((e.clientX - wr.left) / S.scale, 0), S.wPt[stroke.page]),
    y: Math.min(Math.max((e.clientY - wr.top) / S.scale, 0), S.hPt[stroke.page]),
  };
}

function strokeMove(e) {
  const p = _pontoNaPagina(e);
  if (!stroke.a) stroke.a = p;  // âncora: fixa até soltar o botão
  stroke.b = p;                 // atual: muda a cada movimento, pode recuar
  strokeRedraw();
}

function strokeRedraw() {
  if (!stroke || !stroke.lines) return;  // linhas ainda carregando
  const wrap = S.wrappers[stroke.page];
  const ov = wrap.querySelector(".overlay");
  const segs = strokeSegments();

  for (const [li, seg] of segs) {
    let el = stroke.previews.get(li);
    if (!el) {
      el = document.createElement("div");
      el.className = "box on manual preview";
      ov.appendChild(el);
      stroke.previews.set(li, el);
    }
    positionBox(el, stroke.page, seg);
  }
  // Linhas que saíram da seleção (o cursor recuou): somem na hora.
  for (const [li, el] of stroke.previews) {
    if (!segs.has(li)) { el.remove(); stroke.previews.delete(li); }
  }

  // Sem linha de texto envolvida → retângulo livre da âncora até o cursor
  // (documento escaneado, área em branco). Também encolhe ao voltar.
  const bb = segs.size ? null : strokeBBox();
  if (bb && (bb[2] - bb[0] > 3 || bb[3] - bb[1] > 3)) {
    if (!stroke.freeEl) {
      stroke.freeEl = document.createElement("div");
      stroke.freeEl.className = "box on manual preview";
      ov.appendChild(stroke.freeEl);
    }
    positionBox(stroke.freeEl, stroke.page, bb);
  } else if (stroke.freeEl) {
    stroke.freeEl.remove();
    stroke.freeEl = null;
  }
}

function _linhaEm(y) {
  /* Índice da linha de texto na altura `y`. `dentro` diz se `y` cai de fato
   * sobre a linha (e não no vão entre duas) — o que decide se o traço é uma
   * seleção de texto ou um retângulo livre. Fora das linhas, devolve a mais
   * próxima: assim arrastar para além da última linha ainda a seleciona. */
  const lines = stroke.lines || [];
  let melhor = -1, dist = Infinity;
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const cy = (ln.y0 + ln.y1) / 2;
    const tol = (ln.y1 - ln.y0) / 2 + BRUSH_PT * 0.7;
    const d = Math.abs(y - cy);
    if (d <= tol) return { i, dentro: true };
    if (d < dist) { dist = d; melhor = i; }
  }
  return { i: melhor, dentro: false };
}

function strokeSegments() {
  /* Segmentos por linha, da âncora até o cursor — semântica de seleção de
   * texto: a linha inicial é coberta da âncora até a sua ponta, as do meio
   * inteiras, e a final até onde o cursor está agora. Recuar reduz a linha
   * atual e apaga as que ficaram para trás. */
  const segs = new Map();
  if (!stroke.a || !stroke.b || !stroke.lines || !stroke.lines.length) return segs;

  const ancora = _linhaEm(stroke.a.y);
  // O traço só é seleção de TEXTO se começou sobre uma linha; começando no
  // branco, é retângulo livre (e aí `segs` fica vazio de propósito).
  if (ancora.i < 0 || !ancora.dentro) return segs;
  const atual = _linhaEm(stroke.b.y);
  if (atual.i < 0) return segs;

  const desce = atual.i >= ancora.i;
  const de = Math.min(ancora.i, atual.i);
  const ate = Math.max(ancora.i, atual.i);

  for (let li = de; li <= ate; li++) {
    const ln = stroke.lines[li];
    let x0, x1;
    if (li === ancora.i && li === atual.i) {          // uma linha só
      x0 = Math.min(stroke.a.x, stroke.b.x);
      x1 = Math.max(stroke.a.x, stroke.b.x);
    } else if (li === ancora.i) {                     // linha da âncora
      x0 = desce ? stroke.a.x : ln.x0;
      x1 = desce ? ln.x1 : stroke.a.x;
    } else if (li === atual.i) {                      // linha do cursor
      x0 = desce ? ln.x0 : stroke.b.x;
      x1 = desce ? stroke.b.x : ln.x1;
    } else {                                          // linhas do meio
      x0 = ln.x0;
      x1 = ln.x1;
    }
    x0 = Math.max(ln.x0 - 1.5, x0 - BRUSH_PT);
    x1 = Math.min(ln.x1 + 1.5, x1 + BRUSH_PT);
    if (x1 - x0 > 2) segs.set(li, [x0, ln.y0 - 1.5, x1, ln.y1 + 1.5]);
  }
  return segs;
}

function strokeBBox() {
  // Retângulo da âncora até o cursor (não o envelope do rastro): encolhe
  // quando o cursor volta, como o resto do rolo.
  if (!stroke.a || !stroke.b) return [0, 0, 0, 0];
  const { a, b } = stroke;
  return [Math.min(a.x, b.x) - BRUSH_PT, Math.min(a.y, b.y) - BRUSH_PT,
          Math.max(a.x, b.x) + BRUSH_PT, Math.max(a.y, b.y) + BRUSH_PT];
}

function strokeEnd() {
  const st = stroke;
  if (!st) return;
  st.previews.forEach((el) => el.remove());
  if (st.freeEl) st.freeEl.remove();

  // O que vale é o estado FINAL da seleção (âncora → onde o cursor parou),
  // já com as idas e voltas resolvidas. `stroke` continua setado aqui porque
  // strokeSegments/strokeBBox leem dele.
  const rects = [];
  for (const [, seg] of strokeSegments()) rects.push(seg);
  if (!rects.length && st.a && st.b) {
    const bb = strokeBBox();
    if (bb[2] - bb[0] > 4 && bb[3] - bb[1] > 4) rects.push(bb);
  }
  stroke = null;

  if (!rects.length) return;
  for (const r of rects) S.manual.push([st.page, r[0], r[1], r[2], r[3]]);
  renderManualBoxes();
  scheduleSend();
}

function positionBox(el, page, r) {
  el.style.left = (r[0] / S.wPt[page]) * 100 + "%";
  el.style.top = (r[1] / S.hPt[page]) * 100 + "%";
  el.style.width = ((r[2] - r[0]) / S.wPt[page]) * 100 + "%";
  el.style.height = ((r[3] - r[1]) / S.hPt[page]) * 100 + "%";
}

function renderManualBoxes() {
  scroller.querySelectorAll(".box.manual:not(.preview)").forEach((el) => el.remove());
  S.manual.forEach((r, mi) => {
    const ov = S.wrappers[r[0]] && S.wrappers[r[0]].querySelector(".overlay");
    if (!ov) return;
    const el = document.createElement("div");
    el.className = "box on manual";
    el.title = "Tarja manual — clique para remover";
    positionBox(el, r[0], [r[1], r[2], r[3], r[4]]);
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      S.manual.splice(mi, 1);
      renderManualBoxes();
      scheduleSend();
    });
    ov.appendChild(el);
  });
}

/* --------------------------------------------- splitter tabela ↔ visualizador
 * O componente vive num iframe; o divisor arrastável precisa alcançar o DOM
 * do documento PAI (a coluna da tabela e a coluna deste iframe são ambas
 * filhas do st.columns lá fora). Mesma origem — o componente é servido pelo
 * próprio Streamlit — então `window.parent.document` é acessível direto,
 * sem CORS. Falha em silêncio: se a estrutura interna do Streamlit mudar e a
 * busca não achar as âncoras, o layout fixo de sempre continua valendo, só
 * sem o arrasto.
 *
 * Âncoras: `st-key-painel_revisao` (app.py, em volta do painel esquerdo) e
 * `st-key-pdfframe` (em volta deste componente) — de cada uma sobe até o
 * ancestral `[data-testid="stColumn"]`, que é o que de fato precisa mudar de
 * largura. O ratio escolhido persiste em localStorage (por navegador, não
 * por usuário — não há motivo pra ida ao servidor por isto).
 *
 * O divisor NÃO entra como filho da linha (`row.insertBefore`) — essa linha
 * é a árvore que o React do Streamlit possui e reconcilia a cada rerun; um
 * nó estranho enfiado no meio dos filhos dela sobrevive ao primeiro render,
 * mas o próximo diff do React encontra uma lista de filhos que não bate com
 * o que ele espera e quebra o flex (foi o que empilhou tabela e PDF em duas
 * linhas ao vivo). Em vez disso o divisor é filho direto do <body> —
 * completamente fora da árvore que o React enxerga — com `position:fixed` e
 * a posição resincronizada a cada frame a partir do retângulo real da
 * coluna esquerda. Custo de um `getBoundingClientRect` por frame é
 * desprezível para uma barra fina.
 */
const SPLIT_RATIO_KEY = "tj_split_ratio";
const SPLIT_MIN_PX = 240;
const SPLIT_HANDLE_PX = 20;

function setupSplitter(tentativas) {
  try {
    const doc = window.parent.document;
    // Se um iframe ANTERIOR deste componente já tinha montado um divisor e
    // foi destruído/recriado pelo Streamlit (remount), o nó dele sobrevive
    // no documento pai (quem inseriu foi o outro iframe, não este) mas os
    // listeners (mousedown/mousemove/reposition) morreram junto com aquele
    // contexto — um zumbi visível e destravado. Em vez de desistir quando
    // já existe um `#tj-splitter`, remove o antigo e monta um novo — quem
    // rodar por último sempre deixa uma instância funcional.
    const zumbi = doc.getElementById("tj-splitter");
    if (zumbi) zumbi.remove();
    const zumbiStyle = doc.getElementById("tj-splitter-style");
    if (zumbiStyle) zumbiStyle.remove();
    const leftAnchor = doc.querySelector('[class*="st-key-painel_revisao"]');
    const rightAnchor = doc.querySelector('[class*="st-key-pdfframe"]');
    const leftCol = leftAnchor && leftAnchor.closest('[data-testid="stColumn"]');
    const rightCol = rightAnchor && rightAnchor.closest('[data-testid="stColumn"]');
    const row = leftCol && rightCol && leftCol.parentElement;
    if (!leftCol || !rightCol || !row || row !== rightCol.parentElement) {
      if ((tentativas || 0) < 20) setTimeout(() => setupSplitter((tentativas || 0) + 1), 300);
      return;
    }
    // Flex item por padrão tem min-width:auto (= largura mínima do próprio
    // conteúdo) — sem zerar isso, o navegador recusa encolher a coluna além
    // do que o conteúdo interno "pede", e o arrasto trava antes do limite
    // que a gente definiu (SPLIT_MIN_PX). Só nestas duas colunas, não em
    // todo `stColumn` do app (outras seções contam com o comportamento
    // padrão).
    leftCol.style.minWidth = "0";
    rightCol.style.minWidth = "0";

    const style = doc.createElement("style");
    style.id = "tj-splitter-style";
    style.textContent = `
      #tj-splitter{ position:fixed; width:${SPLIT_HANDLE_PX}px; cursor:col-resize;
        display:flex; align-items:center; justify-content:center;
        color:#9a9a9a; user-select:none; font-size:22px; font-weight:bold;
        z-index:1000; }
      #tj-splitter:hover, #tj-splitter.tj-dragging{ color:#2ab6a6; }
    `;
    doc.head.appendChild(style);

    const divider = doc.createElement("div");
    divider.id = "tj-splitter";
    divider.title = "Arraste para redimensionar";
    divider.textContent = "⇔";
    doc.body.appendChild(divider);

    // Rolar com o mouse EM CIMA do divisor: por estar fora da árvore da
    // página (filho do <body>, não de `row` — ver comentário acima), a
    // cadeia de ancestrais que o navegador usa pra decidir "o que rola" não
    // inclui o container de scroll de verdade, e a rolagem simplesmente não
    // acontece nessa faixa de 20px. Acha esse ancestral UMA VEZ a partir de
    // `leftCol` (que está no lugar certo da árvore) e repassa o delta na mão.
    function findScrollParent(el) {
      let node = el.parentElement;
      while (node && node !== doc.body) {
        const cs = doc.defaultView.getComputedStyle(node);
        if (/(auto|scroll)/.test(cs.overflowY) && node.scrollHeight > node.clientHeight) {
          return node;
        }
        node = node.parentElement;
      }
      return doc.scrollingElement || doc.documentElement;
    }
    const scrollParent = findScrollParent(leftCol);
    divider.addEventListener("wheel", (e) => {
      scrollParent.scrollTop += e.deltaY;
      e.preventDefault();
    }, { passive: false });

    const applyRatio = (ratio) => {
      leftCol.style.flex = `0 0 ${(ratio * 100).toFixed(2)}%`;
      rightCol.style.flex = "1 1 0%";
    };

    // Sem ratio salvo (1ª visita), NENHUM flex é tocado — as colunas ficam
    // exatamente como o st.columns([5, 8]) as definiu, igual antes desta
    // feature existir.
    const saved = parseFloat(localStorage.getItem(SPLIT_RATIO_KEY));
    if (saved && saved > 0.1 && saved < 0.9) applyRatio(saved);

    // Teto da tabela de seleção: fundo do iframe do PDF − topo da tabela.
    // O height inline que o Streamlit põe no container fixo NÃO fica
    // necessariamente no mesmo nó da classe st-key (nem 1-3 níveis acima:
    // testado e a busca rasa não achava o nó real — o clamp não fazia
    // nada, daí o fundo da tabela continuar passando do iframe). Por isso a
    // busca varre a subárvore inteira, e não basta ter "height: Npx" —
    // exige overflow-y de rolagem (auto/overlay/scroll) junto, que é a
    // marca registrada do container que o Streamlit realmente limita
    // (evita casar com algum widget interno que por acaso tenha um height
    // em px no style inline sem ser rolável). Só encolhe: o valor do
    // Python (guardado em data-tj-h0 na 1ª visita ao nó; reruns recriam o
    // nó e zeram o cache junto) é o teto — crescer além dele abriria vão
    // DENTRO do container quando a lista é curta.
    function achaContainerRolavel(raiz) {
      const candidatos = [raiz, ...raiz.querySelectorAll("*")];
      for (const el of candidatos) {
        if (!/px/.test(el.style.height)) continue;
        const overflowY = doc.defaultView.getComputedStyle(el).overflowY;
        if (/auto|overlay|scroll/.test(overflowY)) return el;
      }
      return null;
    }
    function clampTabela(moldura) {
      const tabela = doc.querySelector('[class*="st-key-tabela_selecao"]');
      if (!tabela || !moldura) return;
      const hEl = achaContainerRolavel(tabela);
      if (!hEl) return; // lista curta: sem altura fixa, nada a clampar
      if (!hEl.dataset.tjH0) hEl.dataset.tjH0 = parseFloat(hEl.style.height);
      const rp = moldura.getBoundingClientRect();
      const alvo = Math.min(
        parseFloat(hEl.dataset.tjH0),
        Math.max(200, Math.round(rp.bottom - hEl.getBoundingClientRect().top)));
      if (Math.abs(parseFloat(hEl.style.height) - alvo) > 1) {
        hEl.style.height = alvo + "px";
      }
    }

    function reposition() {
      if (!leftCol.isConnected || !rightCol.isConnected) return; // desmontou
      const rl = leftCol.getBoundingClientRect();
      const rr = rightCol.getBoundingClientRect();
      clampTabela(rightAnchor.isConnected
        ? rightAnchor
        : doc.querySelector('[class*="st-key-pdfframe"]'));
      // Encostado na coluna do PDF (não centrado no meio do vão) — centrado,
      // a metade esquerda do divisor invadia a tabela bem em cima da barra
      // de rolagem dela (coluna Páginas), competindo pelo clique.
      divider.style.left = (rr.left - SPLIT_HANDLE_PX) + "px";
      divider.style.top = rl.top + "px";
      divider.style.height = rl.height + "px";
      requestAnimationFrame(reposition);
    }
    requestAnimationFrame(reposition);

    let dragging = false, startX = 0, startLeftW = 0, rowW = 0;
    divider.addEventListener("mousedown", (e) => {
      dragging = true;
      divider.classList.add("tj-dragging");
      startX = e.clientX;
      startLeftW = leftCol.getBoundingClientRect().width;
      rowW = row.getBoundingClientRect().width;
      doc.body.style.userSelect = "none";
      e.preventDefault();
    });
    doc.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      let newLeftW = startLeftW + (e.clientX - startX);
      newLeftW = Math.max(SPLIT_MIN_PX,
        Math.min(newLeftW, rowW - SPLIT_HANDLE_PX - SPLIT_MIN_PX));
      const ratio = newLeftW / rowW;
      applyRatio(ratio);
      localStorage.setItem(SPLIT_RATIO_KEY, ratio);
    });
    doc.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      divider.classList.remove("tj-dragging");
      doc.body.style.userSelect = "";
    });
  } catch (err) {
    console.warn("tj-splitter indisponível:", err);
  }
}
setupSplitter();
