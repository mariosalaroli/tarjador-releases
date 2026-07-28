"""Gera `teste_sanitizacao.pdf`: formulário oficial preenchido, do tipo que
circula de verdade — e que carrega, sem ninguém notar, tudo o que a limpeza
estrutural do Tarjador precisa remover.

Por que um FORMULÁRIO: é assim que JavaScript chega a um PDF no mundo real.
Nada de script "malicioso" plantado à mão — quem gera esses arquivos (Adobe
Acrobat/LiveCycle, geradores de formulário de órgão público) embute scripts de
validação e máscara como parte normal do fluxo. O documento vira público, o
script continua lá, e junto com ele vão os valores digitados nos campos, os
metadados de quem preencheu e o anexo que alguém esqueceu.

O que o arquivo carrega — e o resumo do passo 3 deve acusar:

- **JavaScript** nos três lugares em que ele de fato aparece nesses PDFs:
  1. name tree `/Names /JavaScript` — o script "de documento" (máscara de CPF),
     que o gerador de formulário embute para rodar na abertura;
  2. `/OpenAction` — o clássico "lembre-se de imprimir e assinar";
  3. ação de campo de formulário — validação disparada ao editar/clicar.
- **Valores de formulário preenchidos** — nome, CPF, e-mail e telefone ficam no
  AcroForm mesmo que o texto pareça "chapado" na página.
- **Metadados** — autor, ferramenta, título: identificam quem preencheu.
- **Anexo embutido** — a certidão que o servidor anexou e ninguém removeu.
- **Texto invisível** (render mode 3) — a observação interna que foi "apagada"
  deixando a fonte transparente, e continua extraível.

Uso: python scripts/gerar_pdf_teste_sanitizacao.py
"""
import fitz

SAIDA = "teste_sanitizacao.pdf"

# Script de documento: máscara/validação de CPF. Exatamente o tipo de rotina que
# um gerador de formulário embute na name tree /JavaScript.
JS_DOCUMENTO = r"""
function validaCPF(campo) {
  var v = campo.value.replace(/[^0-9]/g, "");
  if (v.length != 11) { app.alert("CPF invalido: informe 11 digitos."); return false; }
  campo.value = v.substr(0,3) + "." + v.substr(3,3) + "." + v.substr(6,3) + "-" + v.substr(9,2);
  return true;
}
"""

# Ação de abertura: o lembrete que todo formulário de orgao publico tem.
JS_ABERTURA = (
    'app.alert("Preencha todos os campos obrigatorios, imprima e assine.");'
)

# Validação de campo: dispara ao editar o CPF.
JS_CAMPO = 'validaCPF(event.target);'


def _cabecalho(page):
    """Faixa institucional — só para o arquivo ter cara de documento real."""
    page.draw_rect(fitz.Rect(50, 40, 545, 88),
                   color=(0.10, 0.20, 0.45), fill=(0.10, 0.20, 0.45))
    page.insert_text((64, 62), "MINISTERIO DA GESTAO E DA INOVACAO",
                     fontsize=12, color=(1, 1, 1))
    page.insert_text((64, 78), "Requerimento de Beneficio — Formulario 2.1",
                     fontsize=9, color=(0.85, 0.88, 0.95))


def main():
    doc = fitz.open()
    page = doc.new_page()
    _cabecalho(page)

    # ------------------------------------------------- corpo do requerimento
    campos = [
        ("Nome completo:", "Joao da Silva Junior", "nome"),
        ("CPF:", "456.865.909-40", "cpf"),
        ("RG:", "47.012.311-4", "rg"),
        ("E-mail:", "joao.silva@gmail.com", "email"),
        ("Telefone:", "(11) 98765-4321", "telefone"),
        ("Processo:", "17944.000464/2026-14", "processo"),
    ]

    y = 130
    for rotulo, valor, nome_campo in campos:
        page.insert_text((64, y + 14), rotulo, fontsize=10)
        rect = fitz.Rect(190, y, 420, y + 20)
        w = fitz.Widget()
        w.rect = rect
        w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        w.field_name = nome_campo
        w.field_value = valor          # valor PREENCHIDO: fica no AcroForm
        w.text_fontsize = 10
        w.border_color = (0.6, 0.6, 0.6)
        w.border_width = 0.6
        if nome_campo == "cpf":
            # JavaScript 3: validação de campo (roda ao editar o CPF)
            w.script_change = JS_CAMPO
        page.add_widget(w)
        y += 34

    y += 10
    for linha in [
        "Declaro, sob as penas da lei, que as informacoes acima sao verdadeiras.",
        "",
        "Solicito a concessao do beneficio conforme a Lei 14.701/2023, tendo",
        "anexado a certidao exigida no art. 5o do referido diploma.",
        "",
        "Brasilia, 14 de julho de 2026.",
    ]:
        page.insert_text((64, y), linha, fontsize=10)
        y += 16

    # Linha de assinatura
    page.draw_line((64, 470), (300, 470), color=(0, 0, 0), width=0.8)
    page.insert_text((64, 484), "Joao da Silva Junior — Requerente", fontsize=9)

    # --------------------------------------------------------- texto INVISÍVEL
    # A "observacao interna" que alguem tentou apagar deixando a fonte
    # transparente (render mode 3). Continua no conteudo, copiavel e extraivel.
    page.insert_text(
        (64, 520),
        "OBS INTERNA (nao publicar): requerente ja teve beneficio negado — "
        "CPF 529.982.247-25, contato (61) 3412-2842",
        fontsize=9, render_mode=3,
    )

    # ------------------------------------------------------------- metadados
    doc.set_metadata({
        "title": "Requerimento de Beneficio — Joao da Silva Junior",
        "author": "Maria Aparecida de Souza (Matricula 123456)",
        "subject": "Processo 17944.000464/2026-14",
        "keywords": "requerimento, beneficio, cpf, dados pessoais",
        "creator": "Adobe LiveCycle Designer ES 11.0",
        "producer": "Adobe Acrobat Pro DC 23.006",
    })

    # ------------------------------------------------------------------ anexo
    # A certidao que o servidor anexou ao PDF e ninguem lembrou de tirar antes
    # de publicar. Vai junto com o arquivo, invisivel na tela.
    doc.embfile_add(
        "certidao_negativa.txt",
        b"CERTIDAO NEGATIVA\nRequerente: Joao da Silva Junior\n"
        b"CPF: 456.865.909-40\nConjuge: Ana Paula Rodrigues, CPF 987.654.321-00\n"
        b"Renda familiar declarada: R$ 3.450,00\n",
        filename="certidao_negativa.txt",
        desc="Certidao anexada pelo requerente",
    )

    # Anexo como ANOTAÇÃO (o "clipe" que aparece na página). É o caminho que
    # disparava o bug do PyMuPDF 1.25.5 no scrub() — fica aqui de propósito,
    # como teste de regressão do fix (ver redactor.py, remoção manual antes do
    # scrub).
    page.add_file_annot(
        fitz.Point(470, 300),
        b"Comprovante de residencia: Rua das Flores 123, Belo Horizonte/MG\n"
        b"Titular: Joao da Silva Junior, CPF 456.865.909-40\n",
        filename="comprovante_residencia.txt",
        desc="Comprovante anexado na pagina",
    )

    catalogo = doc.pdf_catalog()

    # ------------------------------- JavaScript 1: name tree /Names /JavaScript
    # A API do PyMuPDF nao expoe a name tree; monta-se o objeto e liga-se ao
    # catalogo — que e exatamente a estrutura gerada por um editor de formulario.
    xref_js = doc.get_new_xref()
    doc.update_object(xref_js, "<< /S /JavaScript /JS (%s) >>"
                      % _escapa(JS_DOCUMENTO))
    xref_names = doc.get_new_xref()
    doc.update_object(
        xref_names, "<< /Names [ (ValidaCPF) %d 0 R ] >>" % xref_js)
    # Chave ANINHADA (Names/JavaScript), não `Names`: escrever o dicionário
    # inteiro apagaria o /EmbeddedFiles que o anexo acabou de criar ali dentro.
    doc.xref_set_key(catalogo, "Names/JavaScript", "%d 0 R" % xref_names)

    # ----------------------------------------------- JavaScript 2: /OpenAction
    xref_open = doc.get_new_xref()
    doc.update_object(xref_open, "<< /S /JavaScript /JS (%s) >>"
                      % _escapa(JS_ABERTURA))
    doc.xref_set_key(catalogo, "OpenAction", "%d 0 R" % xref_open)

    doc.save(SAIDA)
    doc.close()
    _conferir()


def _escapa(js: str) -> str:
    """Escapa a string literal do PDF (parenteses e barra invertida)."""
    return (js.replace("\\", r"\\")
              .replace("(", r"\(")
              .replace(")", r"\)"))


def _conferir():
    """Relê o arquivo: a sujeira toda tem que estar lá antes de o app limpar."""
    from tarjador.core.redactor import _has_javascript, _count_attachments

    doc = fitz.open(SAIDA)
    invisivel = "OBS INTERNA" in doc[0].get_text()
    campos = [w.field_value for w in doc[0].widgets()]
    print(f"Gerado: {SAIDA}\n")
    print("O que o arquivo carrega (tudo deve ser removido pelo app):")
    print(f"  JavaScript ...............: {_has_javascript(doc)}")
    print(f"  Anexos embutidos .........: {_count_attachments(doc)}")
    print(f"  Metadados (autor) ........: {doc.metadata.get('author')!r}")
    print(f"  Texto invisivel ..........: {invisivel}")
    print(f"  Valores de formulario ....: {campos}")
    doc.close()


if __name__ == "__main__":
    main()
