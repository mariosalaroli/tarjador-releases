"""Gera `teste_emails.pdf` (na raiz do projeto, gitignored) para avaliação
manual da classificação de e-mails: funcionais por DOMÍNIO público (gov.br,
leg.br, jus.br, mp.br, def.br, mil.br, tc.br), lista fixa (BB, Banco Mundial,
BID, FMI), caixas institucionais por parte local, display name de pessoa e de
órgão, e-mails pessoais, e repetições do MESMO e-mail em situações diferentes
(corpo, cabeçalho NOME <email>, assinatura) espalhadas pelas 3 páginas.

Comportamento esperado (flag "Tarjar e-mails funcionais" DESLIGADA, padrão):
os marcados como [MARCADO] vêm com o checkbox ligado; os [desmarcado], não.
Ligar a flag inverte só os funcionais.

Uso:  python scripts/gerar_pdf_teste_emails.py
"""
from pathlib import Path

import fitz  # PyMuPDF

LINHAS = [
    ("t", "Documento de teste — classificação de e-mails"),
    ("", ""),
    ("h", "Página 1 — Seção A: pessoais (devem vir MARCADOS)"),
    ("", "1. O requerente informou o e-mail joao.silva@gmail.com para contato."),
    ("", "2. Luana Martins <luana.martins@outlook.com> solicitou cópia dos autos."),
    ("", "3. Contato acadêmico: prof.ricardo@ufmg.edu.br (edu.br fica fora da"),
    ("", "   regra de domínio funcional — inclui instituições privadas)."),
    ("", ""),
    ("h", "Seção B: funcionais por domínio público (devem vir desmarcados)"),
    ("", "4. A servidora Ana Pereira (ana.pereira@fazenda.gov.br) juntou parecer."),
    ("", "5. Protocolo municipal: carlos.souza@prefeitura.sp.gov.br."),
    ("", "6. Ofício encaminhado a maria.santos@tjsp.jus.br (Judiciário)."),
    ("", "7. Resposta do gabinete parlamentar: pedro.lima@camara.leg.br."),
    ("", ""),
    ("h", "Seção C: institucionais por parte local (devem vir desmarcados)"),
    ("", "8. Dúvidas com gabinete@construtoraalfa.com.br (domínio privado,"),
    ("", "   caixa institucional)."),
    ("", "9. Mensagem automática de noreply@bancobeta.com.br — não responda."),
    ("p", ""),  # quebra de página
    ("h", "Página 2 — Seção D: demais domínios públicos (desmarcados)"),
    ("", "10. Promotoria: julia.costa@mpsp.mp.br (Ministério Público)."),
    ("", "11. Defensoria: rafael.alves@defensoria.rs.def.br."),
    ("", "12. Exército: sgt.oliveira@eb.mil.br."),
    ("", "13. Tribunal de Contas: auditoria@tce.sp.tc.br (domínio E parte"),
    ("", "    local institucionais ao mesmo tempo)."),
    ("", ""),
    ("h", "Seção E: lista fixa — bancos e organismos (desmarcados)"),
    ("", "14. Banco do Brasil: fernando.tal@bb.com.br (nominal, mas na lista)."),
    ("", "15. Banco Mundial: jsmith@worldbank.org, missão de supervisão."),
    ("", ""),
    ("h", "Seção F: repetição em situação diferente — cabeçalho de e-mail"),
    ("", "O e-mail do item 4 reaparece aqui como remetente, com display name"),
    ("", "de PESSOA (o domínio gov.br deve vencer e mantê-lo desmarcado):"),
    ("", ""),
    ("", "De: Ana Pereira <ana.pereira@fazenda.gov.br>"),
    ("", "Para: Fernando Tal <fernando.tal@bb.com.br>"),
    ("", "Assunto: Conciliação de repasses — exercício 2026"),
    ("", ""),
    ("", "Prezado Fernando, segue planilha consolidada conforme combinado."),
    ("p", ""),  # quebra de página
    ("h", "Página 3 — Seção G: repetições em outra situação"),
    ("", "O e-mail pessoal do item 1 reaparece aqui no corpo do texto:"),
    ("", "o interessado joao.silva@gmail.com foi notificado por mensagem"),
    ("", "eletrônica, nos termos do art. 26 da Lei 9.784/1999."),
    ("", ""),
    ("", "A caixa institucional do item 8 também se repete: cópia enviada a"),
    ("", "gabinete@construtoraalfa.com.br para ciência da contratada."),
    ("", ""),
    ("h", "Seção H: bloco de assinatura com e-mail pessoal"),
    ("", "Sem mais para o momento, subscrevo-me."),
    ("", ""),
    ("", "Atenciosamente,"),
    ("", ""),
    ("", ""),
    ("", "João da Silva"),
    ("", "Representante legal da requerente"),
    ("", "joao.silva@gmail.com"),
    ("", "(11) 98765-4321"),
]

MARGEM_X = 50
Y_INICIO = 60
ALTURA_LINHA = 18


def main() -> None:
    doc = fitz.open()
    page = doc.new_page()  # A4
    y = Y_INICIO
    for estilo, texto in LINHAS:
        if estilo == "p":
            page = doc.new_page()
            y = Y_INICIO
            continue
        if y > page.rect.height - 60:
            page = doc.new_page()
            y = Y_INICIO
        if estilo == "t":
            page.insert_text((MARGEM_X, y), texto, fontsize=16)
            y += ALTURA_LINHA
        elif estilo == "h":
            y += 6
            page.insert_text((MARGEM_X, y), texto, fontsize=12)
        else:
            page.insert_text((MARGEM_X, y), texto, fontsize=10)
        y += ALTURA_LINHA

    destino = Path(__file__).resolve().parent.parent / "teste_emails.pdf"
    doc.save(destino)
    doc.close()
    print(f"Gerado: {destino}")


if __name__ == "__main__":
    main()
