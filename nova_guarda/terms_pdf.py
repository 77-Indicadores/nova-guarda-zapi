from functools import lru_cache

from fpdf import FPDF

from nova_guarda.schedule_pdf import _latin1
from nova_guarda.terms_text import FOOTER, SECTIONS, SUBTITLE, TITLE


TERMS_FILE_NAME = "Termo de Aceite - Guarda Nova.pdf"


class _TermsPDF(FPDF):
    def header(self) -> None:
        self.set_font("Helvetica", "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, _latin1("Guarda Nova - Termo de Uso e Consentimento para Comunicações Operacionais"))
        self.cell(0, 5, f"Página {self.page_no()}".encode("latin-1", "replace").decode("latin-1"), align="R")
        self.ln(9)
        self.set_text_color(0, 0, 0)


@lru_cache(maxsize=1)
def build_terms_pdf() -> bytes:
    """PDF do termo gerado pelo app a partir de terms_text.py: o mesmo arquivo
    vai pelo WhatsApp e fica público em /termo.pdf, sem depender de link externo."""
    pdf = _TermsPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_title(_latin1(TITLE))
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 14)
    pdf.multi_cell(0, 7, _latin1(TITLE), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 5, _latin1(SUBTITLE), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    for heading, paragraphs in SECTIONS:
        pdf.set_font("Helvetica", "B", 11)
        pdf.multi_cell(0, 6, _latin1(heading), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for paragraph in paragraphs:
            pdf.multi_cell(0, 5, _latin1(paragraph), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.5)
        pdf.ln(1.5)

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    for line in FOOTER:
        pdf.cell(0, 5, _latin1(line), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
