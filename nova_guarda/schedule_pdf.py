from typing import Any

from fpdf import FPDF

from nova_guarda.timezone import br_now


COLUMNS = (("Data", 30), ("Dia", 34), ("Horário", 40), ("Cliente", 86))


def _latin1(value: Any) -> str:
    # As fontes padrão do PDF só cobrem Latin-1 (acentos do português passam).
    return str(value or "").encode("latin-1", "replace").decode("latin-1")


def build_schedule_pdf(schedule_data: dict[str, Any]) -> bytes:
    """PDF da escala de trabalho enviada ao cooperado: só os dias desta escala
    (booking), com data, horário e cliente. Gerado aqui, e não pelo PDF mensal
    da 77Gestão, porque aquele lista o mês inteiro do cooperado e misturaria
    escalas já confirmadas com a que está sendo enviada."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(_latin1(f"Escala de trabalho - {schedule_data.get('schedule_period', '')}"))
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Nova Guarda", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6, "Escala de trabalho do cooperado", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    title = f"Escala de {schedule_data.get('client_name') or 'cooperado(a)'} - {schedule_data.get('schedule_period', '')}"
    pdf.multi_cell(0, 7, _latin1(title), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    days = int(schedule_data.get("schedule_days") or 0)
    span = (
        f"em {schedule_data.get('schedule_first')}"
        if schedule_data.get("schedule_first") == schedule_data.get("schedule_last")
        else f"de {schedule_data.get('schedule_first')} a {schedule_data.get('schedule_last')}"
    )
    pdf.cell(0, 6, _latin1(f"Dias de trabalho: {days} ({span})"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    def header_row() -> None:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(31, 41, 55)
        pdf.set_text_color(255, 255, 255)
        for label, width in COLUMNS:
            pdf.cell(width, 8, _latin1(label), border=1, fill=True)
        pdf.ln(8)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)

    header_row()
    for index, shift in enumerate(schedule_data.get("schedule_shifts") or []):
        if pdf.get_y() > 265:
            pdf.add_page()
            header_row()
        pdf.set_fill_color(243, 244, 246)
        values = (shift.get("date"), shift.get("weekday"), shift.get("time"), shift.get("customer") or "-")
        for (_, width), value in zip(COLUMNS, values):
            text = _latin1(value)
            while text and pdf.get_string_width(text) > width - 3:
                text = text[:-1]
            pdf.cell(width, 8, text, border=1, fill=index % 2 == 1)
        pdf.ln(8)

    pdf.ln(5)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(110, 110, 110)
    footer = f"Escala {schedule_data.get('booking_id') or '-'} - documento gerado em {br_now():%d/%m/%Y %H:%M}."
    pdf.cell(0, 5, _latin1(footer), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
