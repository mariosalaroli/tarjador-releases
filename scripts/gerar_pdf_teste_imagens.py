"""Gera `teste_imagens.pdf`: banco de casos para a revisão de imagens/selos.

Cobre os três eixos que costumam quebrar:

1. IMAGENS — cinco visuais diferentes (assinatura manuscrita, carimbo redondo,
   logotipo, brasão, foto 3x4) na faixa que a heurística varre, mais dois
   controles NEGATIVOS que ela deve ignorar: um ícone pequeno demais e uma
   imagem grande no topo da página (cabeçalho, não assinatura). Servem para
   conferir que a detecção não vira uma rede de arrasto.

2. DADOS PESSOAIS — CPF (com e sem máscara), CNPJ, RG, e-mail pessoal e
   institucional, celular e fixo em máscaras variadas, nomes.

3. NÚMEROS QUE NÃO SÃO TELEFONE — NUP/processo, protocolo SEI, referência a
   norma, valores e datas. Todos já causaram falso positivo em algum momento;
   nenhum pode aparecer como Celular/Telefone.

Uso: python scripts/gerar_pdf_teste_imagens.py
"""
import fitz

SAIDA = "teste_imagens.pdf"


def _img_assinatura():
    """Rabisco manuscrito sobre linha — a assinatura escaneada clássica."""
    d = fitz.open()
    pg = d.new_page(width=220, height=70)
    pg.draw_line((15, 55), (205, 55), color=(0, 0, 0), width=1)
    pg.draw_bezier((25, 50), (60, 5), (95, 65), (130, 20),
                   color=(0.05, 0.05, 0.35), width=2.2)
    pg.draw_bezier((110, 30), (140, 60), (170, 10), (200, 45),
                   color=(0.05, 0.05, 0.35), width=2.2)
    return pg.get_pixmap(dpi=150)


def _img_carimbo():
    """Carimbo redondo com texto ao centro."""
    d = fitz.open()
    pg = d.new_page(width=150, height=150)
    pg.draw_circle((75, 75), 68, color=(0.7, 0.1, 0.1), width=3)
    pg.draw_circle((75, 75), 58, color=(0.7, 0.1, 0.1), width=1)
    pg.insert_text((36, 72), "CONFERE", fontsize=13, color=(0.7, 0.1, 0.1))
    pg.insert_text((42, 88), "COM O", fontsize=11, color=(0.7, 0.1, 0.1))
    pg.insert_text((37, 103), "ORIGINAL", fontsize=11, color=(0.7, 0.1, 0.1))
    return pg.get_pixmap(dpi=150)


def _img_logotipo():
    """Logotipo corporativo — blocos coloridos + nome."""
    d = fitz.open()
    pg = d.new_page(width=240, height=80)
    for i, cor in enumerate([(0.9, 0.3, 0.1), (0.1, 0.5, 0.8), (0.2, 0.7, 0.3)]):
        pg.draw_rect(fitz.Rect(12 + i * 26, 18, 32 + i * 26, 62),
                     color=cor, fill=cor)
    pg.insert_text((100, 52), "ACME S.A.", fontsize=20, color=(0.15, 0.15, 0.15))
    return pg.get_pixmap(dpi=150)


def _img_brasao():
    """Brasão/escudo — imagem institucional que talvez NÃO se queira tarjar."""
    d = fitz.open()
    pg = d.new_page(width=120, height=140)
    pg.draw_polyline([(60, 10), (110, 32), (110, 85), (60, 130), (10, 85),
                      (10, 32), (60, 10)],
                     color=(0.15, 0.25, 0.5), fill=(0.85, 0.88, 0.95), width=2)
    pg.draw_line((60, 25), (60, 115), color=(0.15, 0.25, 0.5), width=1.5)
    pg.draw_line((20, 70), (100, 70), color=(0.15, 0.25, 0.5), width=1.5)
    pg.insert_text((42, 78), "MG", fontsize=16, color=(0.15, 0.25, 0.5))
    return pg.get_pixmap(dpi=150)


def _img_foto():
    """Foto 3x4 — retrato esquemático (silhueta)."""
    d = fitz.open()
    pg = d.new_page(width=105, height=140)
    pg.draw_rect(pg.rect, color=(0.75, 0.8, 0.85), fill=(0.75, 0.8, 0.85))
    pg.draw_circle((52, 55), 24, color=(0.4, 0.45, 0.5), fill=(0.4, 0.45, 0.5))
    pg.draw_oval(fitz.Rect(15, 85, 90, 165),
                 color=(0.4, 0.45, 0.5), fill=(0.4, 0.45, 0.5))
    return pg.get_pixmap(dpi=150)


def _img_icone():
    """CONTROLE NEGATIVO: ícone minúsculo — abaixo do tamanho de selo."""
    d = fitz.open()
    pg = d.new_page(width=40, height=40)
    pg.draw_rect(pg.rect, color=(0.2, 0.6, 0.3), fill=(0.2, 0.6, 0.3))
    pg.insert_text((13, 26), "OK", fontsize=13, color=(1, 1, 1))
    return pg.get_pixmap(dpi=150)


def _img_cabecalho():
    """CONTROLE NEGATIVO: faixa de cabeçalho — grande, mas no TOPO da página."""
    d = fitz.open()
    pg = d.new_page(width=400, height=50)
    pg.draw_rect(pg.rect, color=(0.1, 0.2, 0.45), fill=(0.1, 0.2, 0.45))
    pg.insert_text((14, 32), "MINISTERIO DA FAZENDA", fontsize=17,
                   color=(1, 1, 1))
    return pg.get_pixmap(dpi=150)


def main():
    doc = fitz.open()

    # ---------------------------------------------------------------- página 1
    p = doc.new_page()  # A4: 595 x 842 pt
    # Cabeçalho no TOPO (não deve ser detectado como imagem de assinatura)
    p.insert_image(fitz.Rect(60, 40, 460, 90), pixmap=_img_cabecalho())

    y = 120
    for linha in [
        "RELATORIO DE TESTE — Tarjador",
        "",
        "Dados pessoais:",
        "  Nome: Joao da Silva Junior",
        "  Nome: MARIA APARECIDA DE SOUZA",
        "  CPF com mascara: 456.865.909-40",
        "  CPF sem mascara: 45686590940",
        "  CPF invalido (nao deve aparecer): 293.849.249-00",
        "  CNPJ: 11.222.333/0001-81",
        "  RG: 47.012.311-4",
        "  E-mail pessoal: joao.silva@gmail.com",
        "  E-mail institucional: gabinete@pge.ap.gov.br",
        "  Celular: (11) 98765-4321",
        "  Celular sem mascara: 11987654321",
        "  Fixo: (61) 3412-2842",
        "  Fixo 0800: 4002-8922",
        "",
        "Numeros que NAO sao telefone (nenhum pode virar Celular/Telefone):",
        "  Processo NUP: 17944.000464/2026-14",
        "  Processo NUP: 08620.008109/2024-85",
        "  Protocolo: PVL02.002287/2025-59",
        "  SEI n 0023634429",
        "  Codigo verificador 0023608094",
        "  Norma: MP 2.179-36/2001",
        "  Norma: Lei 3.225/2025 de 19/08/2025",
        "  Valor: R$ 50.000.000,00",
        "  Data: 14/04/2026 08:48:36",
    ]:
        p.insert_text((60, y), linha, fontsize=11)
        y += 18

    # Imagens na METADE INFERIOR (região que a heurística varre)
    p.insert_image(fitz.Rect(60, 640, 260, 705), pixmap=_img_assinatura())
    p.insert_text((60, 720), "^ assinatura manuscrita (imagem)", fontsize=8)

    p.insert_image(fitz.Rect(330, 620, 440, 730), pixmap=_img_carimbo())
    p.insert_text((330, 745), "^ carimbo redondo (imagem)", fontsize=8)

    # Ícone pequeno — NÃO deve ser detectado
    p.insert_image(fitz.Rect(500, 700, 530, 730), pixmap=_img_icone())
    p.insert_text((470, 745), "^ icone pequeno: NAO detectar", fontsize=8)

    # ---------------------------------------------------------------- página 2
    p2 = doc.new_page()
    y = 90
    for linha in [
        "PAGINA 2 — outras imagens e mais dados",
        "",
        "  Assinado por Carlos Eduardo Pereira, Diretor.",
        "  Contato: carlos.pereira@empresa.com.br / (21) 3333-4444",
        "  CPF: 529.982.247-25",
        "",
        "  As imagens abaixo devem virar itens separados (Imagem 3, 4, 5):",
        "  logotipo, brasao e foto 3x4 — o usuario decide uma a uma.",
    ]:
        p2.insert_text((60, y), linha, fontsize=11)
        y += 20

    p2.insert_image(fitz.Rect(60, 520, 300, 600), pixmap=_img_logotipo())
    p2.insert_text((60, 615), "^ logotipo (imagem)", fontsize=8)

    p2.insert_image(fitz.Rect(60, 650, 160, 770), pixmap=_img_brasao())
    p2.insert_text((60, 785), "^ brasao (imagem)", fontsize=8)

    p2.insert_image(fitz.Rect(330, 650, 425, 775), pixmap=_img_foto())
    p2.insert_text((330, 790), "^ foto 3x4 (imagem)", fontsize=8)

    # ---------------------------------------------------------------- página 3
    # Uma imagem sozinha, longe das outras: confere que a numeração segue a
    # ordem de leitura do documento inteiro (esta é a última, "Imagem 6").
    p3 = doc.new_page()
    y = 90
    for linha in [
        "PAGINA 3 — imagem isolada",
        "",
        "  Ultima assinatura do documento, em pagina propria.",
        "  Testemunha: Ana Paula Rodrigues — CPF 987.654.321-00",
        "  Telefone da testemunha: (31) 99911-2233",
    ]:
        p3.insert_text((60, y), linha, fontsize=11)
        y += 20

    p3.insert_image(fitz.Rect(200, 600, 400, 665), pixmap=_img_assinatura())
    p3.insert_text((200, 680), "^ assinatura manuscrita (imagem)", fontsize=8)

    doc.save(SAIDA)
    doc.close()
    print(f"Gerado: {SAIDA}")


if __name__ == "__main__":
    main()
