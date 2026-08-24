"""Renderiza SuiteReport como PNG usando PIL.

INCORPORADO DO LogNebula (`exporter.py`) em 2026-08-14. Antes o NebulaTIR
chamava o `LogNebula.exe` como subprocesso e copiava 20 MB de executável para
dentro de **cada** pasta de teste. Agora o código roda no mesmo processo do
lançador, dentro do venv do TIR — que passa a ter Pillow como dependência.
"""
import os
import sys
from PIL import Image, ImageDraw, ImageFont

from nebula_parser import SuiteReport, TestCase


# ==== Constantes de layout (ajustar aqui) ====
PADDING = 10                    # padding externo da imagem
COL_WIDTHS = {
    "name":        180,
    "description": 220,
    "status":      180,
    "details":     480,
}
ROW_PAD_Y = 10                  # padding vertical dentro de cada linha
CELL_PAD_X = 10                 # padding horizontal dentro de cada célula
TITLE_PAD_Y = 14                # padding vertical do título
HEADER_PAD_Y = 12               # padding vertical do header

# Fonte
FONT_SIZE_TITLE = 20
FONT_SIZE_HEADER = 14
FONT_SIZE_CELL = 13

# Cores (tema dark)
COLOR_BG = (30, 30, 30)
COLOR_TITLE_SUCCESS = (46, 160, 67)      # verde
COLOR_TITLE_FAIL = (220, 53, 69)         # vermelho
COLOR_TITLE_TEXT = (255, 255, 255)
COLOR_HEADER_BG = (55, 55, 60)
COLOR_HEADER_TEXT = (240, 240, 240)
COLOR_ROW_A = (40, 40, 44)
COLOR_ROW_B = (48, 48, 52)
COLOR_TEXT = (230, 230, 230)
COLOR_STATUS_PASS = (46, 160, 67)
COLOR_STATUS_FAIL = (220, 53, 69)
COLOR_GRID = (70, 70, 75)


def _resource_path(rel: str) -> str:
    """Encontra assets tanto rodando .py quanto empacotado no PyInstaller."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(_resource_path(os.path.join("assets", "fonts", name)), size)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Quebra texto em linhas respeitando max_width (pixels)."""
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if font.getlength(candidate) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                # palavra sozinha maior que largura → força
                while font.getlength(word) > max_width and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and font.getlength(word[:cut]) > max_width:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                current = word
        if current:
            lines.append(current)
    return lines or [""]


def _text_height(font: ImageFont.FreeTypeFont) -> int:
    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1]


def export_png(report: SuiteReport, out_dir: str, out_name: str | None = None) -> str:
    """Gera PNG e devolve caminho completo.

    out_name: nome do arquivo (sem extensão). Se None, usa report.display_name.
    """
    font_title = _load_font(FONT_SIZE_TITLE, bold=True)
    font_header = _load_font(FONT_SIZE_HEADER, bold=True)
    font_cell = _load_font(FONT_SIZE_CELL)
    font_cell_bold = _load_font(FONT_SIZE_CELL, bold=True)

    # Título
    title_text = report.title
    title_bg = COLOR_TITLE_SUCCESS if report.success else COLOR_TITLE_FAIL

    table_width = sum(COL_WIDTHS.values())
    col_order = ["name", "description", "status", "details"]
    col_labels = {
        "name": "Caso de teste",
        "description": "Descrição",
        "status": "Status da Execução",
        "details": "Detalhes",
    }

    line_h_cell = _text_height(font_cell)
    line_h_header = _text_height(font_header)
    line_h_title = _text_height(font_title)

    # Altura do título
    title_h = line_h_title + TITLE_PAD_Y * 2

    # Altura do header
    header_h = line_h_header + HEADER_PAD_Y * 2

    # Calcula altura de cada linha (multi-line em detalhes/descrição)
    row_heights = []
    wrapped_rows = []
    for case in report.cases:
        cells_wrapped = {
            "name":        _wrap_text(case.name, font_cell_bold, COL_WIDTHS["name"] - CELL_PAD_X * 2),
            "description": _wrap_text(case.description, font_cell, COL_WIDTHS["description"] - CELL_PAD_X * 2),
            "status":      _wrap_text(case.status_label, font_cell_bold, COL_WIDTHS["status"] - CELL_PAD_X * 2),
            "details":     _wrap_text(case.details, font_cell, COL_WIDTHS["details"] - CELL_PAD_X * 2),
        }
        max_lines = max(len(v) for v in cells_wrapped.values())
        row_h = max_lines * (line_h_cell + 4) + ROW_PAD_Y * 2
        row_heights.append(row_h)
        wrapped_rows.append(cells_wrapped)

    total_rows_h = sum(row_heights)

    img_w = table_width + PADDING * 2
    img_h = title_h + header_h + total_rows_h + PADDING * 2

    img = Image.new("RGB", (img_w, img_h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # Título (barra colorida)
    y = PADDING
    draw.rectangle(
        [PADDING, y, PADDING + table_width, y + title_h],
        fill=title_bg,
    )
    title_w = font_title.getlength(title_text)
    draw.text(
        (PADDING + (table_width - title_w) / 2, y + TITLE_PAD_Y - 2),
        title_text, font=font_title, fill=COLOR_TITLE_TEXT,
    )
    y += title_h

    # Header
    draw.rectangle(
        [PADDING, y, PADDING + table_width, y + header_h],
        fill=COLOR_HEADER_BG,
    )
    x = PADDING
    for key in col_order:
        w = COL_WIDTHS[key]
        draw.text(
            (x + CELL_PAD_X, y + HEADER_PAD_Y - 2),
            col_labels[key], font=font_header, fill=COLOR_HEADER_TEXT,
        )
        x += w
    y += header_h

    # Linhas
    for i, (case, cells_wrapped, row_h) in enumerate(zip(report.cases, wrapped_rows, row_heights)):
        row_bg = COLOR_ROW_A if i % 2 == 0 else COLOR_ROW_B
        draw.rectangle(
            [PADDING, y, PADDING + table_width, y + row_h],
            fill=row_bg,
        )
        x = PADDING
        for key in col_order:
            w = COL_WIDTHS[key]
            lines = cells_wrapped[key]
            if key == "status":
                color = COLOR_STATUS_PASS if case.passed else COLOR_STATUS_FAIL
                font_use = font_cell_bold
            elif key == "name":
                color = COLOR_TEXT
                font_use = font_cell_bold
            else:
                color = COLOR_TEXT
                font_use = font_cell
            for li, line in enumerate(lines):
                draw.text(
                    (x + CELL_PAD_X, y + ROW_PAD_Y + li * (line_h_cell + 4)),
                    line, font=font_use, fill=color,
                )
            x += w
        # grid vertical
        gx = PADDING
        for key in col_order[:-1]:
            gx += COL_WIDTHS[key]
            draw.line([(gx, y), (gx, y + row_h)], fill=COLOR_GRID, width=1)
        # grid horizontal
        draw.line(
            [(PADDING, y + row_h), (PADDING + table_width, y + row_h)],
            fill=COLOR_GRID, width=1,
        )
        y += row_h

    base = out_name if out_name else report.display_name
    out_path = os.path.join(out_dir, f"{base}.png")
    img.save(out_path, "PNG")
    return out_path
