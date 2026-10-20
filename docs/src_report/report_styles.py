#!/usr/bin/env python3
"""
report_styles.py

Enterprise-grade OpenXML styling engine for python-docx.
Enforces strict university thesis rubrics matching sample-reference.docx:
- Times New Roman typography throughout
- 12 pt body text, 1.5 line spacing, fully justified
- Left margin 1.5 in (3.81 cm) for physical binding, 1.0 in top/bottom/right
- Multi-section layout with dedicated A4 Landscape section for wide comparison matrices
- Roman numeral preliminary pagination and Arabic numeral body pagination
- High-contrast accessible palettes, styled tables, algorithms, and callout boxes
- Native Figure embedding with captions below and follow-up explanatory paragraphs
"""

import os
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_SECTION, WD_ORIENT
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

# --- Black and White / Grayscale Academic Palette matching sample-reference.docx ---
COLOR_NAVY = RGBColor(0, 0, 0)          # #000000 Primary Header (Black)
COLOR_SLATE = RGBColor(40, 40, 40)      # #282828 Secondary Subheading (Dark Gray)
COLOR_BODY = RGBColor(0, 0, 0)          # #000000 Body Text (Black)
COLOR_BLUE = RGBColor(0, 0, 0)          # #000000 Accents (Black)
COLOR_CRIMSON = RGBColor(0, 0, 0)       # #000000 Warnings (Black)
COLOR_GREEN = RGBColor(0, 0, 0)         # #000000 Success (Black)
COLOR_MUTED = RGBColor(80, 80, 80)      # #505050 Captions/Footnotes (Muted Gray)

HEX_NAVY = "000000"
HEX_HEADER_BG = "F2F2F2"                # Neutral gray header shading matching sample-reference
HEX_LIGHT_ROW = "F9F9F9"
HEX_ALT_ROW = "FFFFFF"
HEX_BORDER = "000000"                   # Standard black 0.5pt borders matching sample-reference
HEX_CALLOUT_BG = "F8F8F8"
HEX_CALLOUT_BORDER = "000000"
HEX_ALGO_BG = "FFFFFF"
HEX_ALGO_BORDER = "000000"

FONT_MAIN = "Times New Roman"
FONT_CODE = "Consolas"

FIGURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figures")
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def set_cell_shading(cell, color_hex):
    """Apply background color to a table cell."""
    tcPr = cell._tc.get_or_add_tcPr()
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}"/>')
    tcPr.append(shd)


def set_cell_margins(cell, top=140, bottom=140, left=180, right=180):
    """Set inner padding for table cells (in twips, 20 twips = 1 pt)."""
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)


def set_table_borders(table, color="000000", sz="4", val="single"):
    """Set clean borders across an entire table matching sample-reference.docx."""
    tblPr = table._tbl.tblPr
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:bottom w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:left w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:right w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:insideH w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:insideV w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(borders)


def set_table_horizontal_only_borders(table, color="000000", sz="4", val="single"):
    """Set horizontal-only borders (top, bottom, insideH, no vertical lines)."""
    tblPr = table._tbl.tblPr
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:bottom w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:left w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'<w:insideH w:val="{val}" w:sz="{sz}" w:space="0" w:color="{color}"/>'
        f'<w:insideV w:val="none"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(borders)


def init_document():
    """Create and configure base document according to university A4 specifications."""
    doc = docx.Document()
    
    # Configure Section 0 (Preliminary Pages: Roman numerals)
    sec0 = doc.sections[0]
    sec0.page_width = Inches(8.27)
    sec0.page_height = Inches(11.69)
    sec0.left_margin = Inches(1.5)     # 3.81 cm for binding gutter
    sec0.right_margin = Inches(1.0)    # 2.54 cm
    sec0.top_margin = Inches(1.0)      # 2.54 cm
    sec0.bottom_margin = Inches(1.0)   # 2.54 cm
    
    # Set lowerRoman page numbering for preliminary section
    sectPr0 = sec0._sectPr
    for p in sectPr0.findall('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pgNumType'):
        sectPr0.remove(p)
    sectPr0.append(parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="lowerRoman"/>'))
    
    # Cover page has no visible page number
    sec0.different_first_page_header_footer = True
    first_footer = sec0.first_page_footer
    first_footer.paragraphs[0].text = ""

    # Configure footer for preliminary section (centered lowerRoman numeral)
    footer0 = sec0.footer
    f_p0 = footer0.paragraphs[0]
    f_p0.text = ""
    f_p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
    f_p0._p.append(parse_xml(r'<w:fldSimple %s w:instr="PAGE"/>' % nsdecls('w')))
    
    return doc


def add_body_section(doc):
    """Add Section 1 for main body chapters with decimal Arabic page numbering starting at 1."""
    sec1 = doc.add_section(WD_SECTION.NEW_PAGE)
    sec1.orientation = WD_ORIENT.PORTRAIT
    sec1.page_width = Inches(8.27)
    sec1.page_height = Inches(11.69)
    sec1.left_margin = Inches(1.5)
    sec1.right_margin = Inches(1.0)
    sec1.top_margin = Inches(1.0)
    sec1.bottom_margin = Inches(1.0)
    sec1.different_first_page_header_footer = False
    sec1.header.is_linked_to_previous = False
    sec1.footer.is_linked_to_previous = False
    
    # Header for body chapters - cleared (no running header watermark)
    sec1.header.paragraphs[0].text = ""
    
    # Set Arabic page numbering starting from 1 (clear inherited lowerRoman)
    sectPr1 = sec1._sectPr
    for p in sectPr1.findall('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pgNumType'):
        sectPr1.remove(p)
    sectPr1.append(parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="decimal" w:start="1"/>'))
    
    # Configure footer for body section (centered decimal Arabic numeral)
    footer1 = sec1.footer
    fp1 = footer1.paragraphs[0]
    fp1.text = ""
    fp1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp1._p.append(parse_xml(r'<w:fldSimple %s w:instr="PAGE"/>' % nsdecls('w')))
    
    return sec1


def add_landscape_section(doc):
    """Add an A4 Landscape section (11.69 in x 8.27 in) for wide comparison tables matching Section 3."""
    sec_land = doc.add_section(WD_SECTION.NEW_PAGE)
    sec_land.orientation = WD_ORIENT.LANDSCAPE
    sec_land.page_width = Inches(11.69)
    sec_land.page_height = Inches(8.27)
    sec_land.left_margin = Inches(1.0)
    sec_land.right_margin = Inches(1.0)
    sec_land.top_margin = Inches(1.0)
    sec_land.bottom_margin = Inches(1.0)
    sec_land.different_first_page_header_footer = False
    sec_land.header.is_linked_to_previous = False
    sec_land.header.paragraphs[0].text = ""
    sec_land.footer.is_linked_to_previous = True

    # Ensure continuous decimal numbering without restarting at 1
    sectPr = sec_land._sectPr
    for p in sectPr.findall('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pgNumType'):
        sectPr.remove(p)
    sectPr.append(parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="decimal"/>'))

    # Set orient in w:pgSz
    pgSz = sectPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pgSz')
    if pgSz is not None:
        pgSz.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}orient', 'landscape')

    return sec_land


def add_portrait_section(doc):
    """Return to standard A4 Portrait section with 1.5 in left binding margin."""
    sec_port = doc.add_section(WD_SECTION.NEW_PAGE)
    sec_port.orientation = WD_ORIENT.PORTRAIT
    sec_port.page_width = Inches(8.27)
    sec_port.page_height = Inches(11.69)
    sec_port.left_margin = Inches(1.5)
    sec_port.right_margin = Inches(1.0)
    sec_port.top_margin = Inches(1.0)
    sec_port.bottom_margin = Inches(1.0)
    sec_port.different_first_page_header_footer = False
    sec_port.header.is_linked_to_previous = False
    sec_port.header.paragraphs[0].text = ""
    sec_port.footer.is_linked_to_previous = True

    # Ensure continuous decimal numbering without restarting at 1
    sectPr = sec_port._sectPr
    for p in sectPr.findall('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pgNumType'):
        sectPr.remove(p)
    sectPr.append(parse_xml(f'<w:pgNumType {nsdecls("w")} w:fmt="decimal"/>'))

    pgSz = sectPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pgSz')
    if pgSz is not None:
        pgSz.set('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}orient', 'portrait')

    return sec_port


def add_chapter_header(doc, chapter_num, chapter_title):
    """Add standard university chapter header centered on a fresh page."""
    p_num = doc.add_paragraph()
    p_num.paragraph_format.space_before = Pt(18)
    p_num.paragraph_format.space_after = Pt(4)
    p_num.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_num.paragraph_format.keep_with_next = True
    r_num = p_num.add_run(f"CHAPTER {chapter_num}")
    r_num.font.name = FONT_MAIN
    r_num.font.size = Pt(16)
    r_num.font.bold = True
    r_num.font.color.rgb = COLOR_NAVY
    
    p_title = doc.add_paragraph()
    p_title.paragraph_format.space_before = Pt(0)
    p_title.paragraph_format.space_after = Pt(14)
    p_title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.keep_with_next = True
    r_title = p_title.add_run(chapter_title.upper())
    r_title.font.name = FONT_MAIN
    r_title.font.size = Pt(17)
    r_title.font.bold = True
    r_title.font.color.rgb = COLOR_NAVY
    
    pPr = p_title._p.get_or_add_pPr()
    pBdr = parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="12" w:space="6" w:color="{HEX_NAVY}"/></w:pBdr>')
    pPr.append(pBdr)


def add_appendix_header(doc, appendix_letter, appendix_title):
    """Add formal Appendix header matching sample-reference.docx."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after = Pt(12)
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.keep_with_next = True
    r = p.add_run(f"APPENDIX {appendix_letter}: {appendix_title.upper()}")
    r.font.name = FONT_MAIN
    r.font.size = Pt(15)
    r.font.bold = True
    r.font.color.rgb = COLOR_NAVY
    
    pPr = p._p.get_or_add_pPr()
    pBdr = parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="12" w:space="5" w:color="{HEX_NAVY}"/></w:pBdr>')
    pPr.append(pBdr)


def add_section_header(doc, sec_num, sec_title):
    """Add 13pt Bold ALL CAPS section header."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(11)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.keep_with_next = True
    r = p.add_run(f"{sec_num}  {sec_title.upper()}")
    r.font.name = FONT_MAIN
    r.font.size = Pt(13)
    r.font.bold = True
    r.font.color.rgb = COLOR_NAVY
    return p


def add_subsection_header(doc, subsec_num, subsec_title):
    """Add 11.5pt Bold Title Case subsection header."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.keep_with_next = True
    r = p.add_run(f"{subsec_num}  {subsec_title}")
    r.font.name = FONT_MAIN
    r.font.size = Pt(11.5)
    r.font.bold = True
    r.font.color.rgb = COLOR_SLATE
    return p


def add_paragraph(doc, text, bold_prefix=None, space_after=3):
    """Add standard justified 12pt Times New Roman paragraph with 1.30 line spacing."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.30
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    
    if bold_prefix:
        rb = p.add_run(bold_prefix)
        rb.font.name = FONT_MAIN
        rb.font.size = Pt(12)
        rb.font.bold = True
        rb.font.color.rgb = COLOR_NAVY
    
    r = p.add_run(text)
    r.font.name = FONT_MAIN
    r.font.size = Pt(12)
    r.font.color.rgb = COLOR_BODY
    return p


def add_bullet(doc, text, bold_prefix=None):
    """Add bullet point with 1.15 line spacing."""
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.15
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    
    if bold_prefix:
        rb = p.add_run(bold_prefix)
        rb.font.name = FONT_MAIN
        rb.font.size = Pt(12)
        rb.font.bold = True
        rb.font.color.rgb = COLOR_NAVY
        
    r = p.add_run(text)
    r.font.name = FONT_MAIN
    r.font.size = Pt(12)
    r.font.color.rgb = COLOR_BODY
    return p


def add_callout(doc, text, title=None, alert_type="NOTE"):
    """Render a formal academic callout box with neutral gray background and black accent border."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    
    cell = tbl.rows[0].cells[0]
    cell.width = Inches(5.77)
    set_cell_shading(cell, HEX_CALLOUT_BG)
    set_cell_margins(cell, top=90, bottom=90, left=160, right=160)
    
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:left w:val="single" w:sz="18" w:space="0" w:color="{HEX_CALLOUT_BORDER}"/>'
        f'<w:top w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'<w:bottom w:val="none"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(tcBorders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.15
    
    prefix = f"[{alert_type.upper()}] "
    if title:
        prefix += f"{title}: "
    rb = p.add_run(prefix)
    rb.font.name = FONT_MAIN
    rb.font.size = Pt(10.5)
    rb.font.bold = True
    rb.font.color.rgb = COLOR_NAVY
    
    r = p.add_run(text)
    r.font.name = FONT_MAIN
    r.font.size = Pt(10.5)
    r.font.italic = True
    r.font.color.rgb = COLOR_BODY
    
    doc.add_paragraph().paragraph_format.space_after = Pt(3)


def add_boxed_equation(doc, eq_text, eq_number_str):
    """Add a formally formatted boxed equation table matching sample-reference.docx (0.5pt outer border, centered math, right-aligned number)."""
    tbl = doc.add_table(rows=1, cols=2)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = False
    
    c_eq = tbl.rows[0].cells[0]
    c_num = tbl.rows[0].cells[1]
    
    c_eq.width = Inches(5.15)
    c_num.width = Inches(0.62)
    
    set_cell_margins(c_eq, top=60, bottom=60, left=80, right=80)
    set_cell_margins(c_num, top=60, bottom=60, left=40, right=60)
    set_cell_shading(c_eq, "FFFFFF")
    set_cell_shading(c_num, "FFFFFF")
    
    p_eq = c_eq.paragraphs[0]
    p_eq.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_eq.paragraph_format.space_before = Pt(1.5)
    p_eq.paragraph_format.space_after = Pt(1.5)
    p_eq.paragraph_format.line_spacing = 1.05
    r_eq = p_eq.add_run(eq_text)
    r_eq.font.name = FONT_MAIN
    r_eq.font.size = Pt(9.5)
    r_eq.font.italic = True
    r_eq.font.bold = True
    r_eq.font.color.rgb = RGBColor(0, 0, 0)
    
    p_num = c_num.paragraphs[0]
    p_num.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_num.paragraph_format.space_before = Pt(1.5)
    p_num.paragraph_format.space_after = Pt(1.5)
    r_num = p_num.add_run(f"({eq_number_str})")
    r_num.font.name = FONT_MAIN
    r_num.font.size = Pt(9.5)
    r_num.font.bold = True
    r_num.font.color.rgb = RGBColor(0, 0, 0)
    
    # Outer 0.5pt border, inside borders none
    tblPr = tbl._tbl.tblPr
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'<w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'<w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'<w:insideH w:val="none"/>'
        f'<w:insideV w:val="none"/>'
        f'</w:tblBorders>'
    )
    tblPr.append(borders)

    # Ensure equation row cannot split across pages
    trPr = tbl.rows[0]._tr.get_or_add_trPr()
    trPr.append(parse_xml(r'<w:cantSplit %s/>' % nsdecls('w')))
    
    # Spacing paragraph after equation box
    p_sp = doc.add_paragraph()
    p_sp.paragraph_format.space_before = Pt(0)
    p_sp.paragraph_format.space_after = Pt(2)


def add_equation(doc, eq_text, eq_number_str):
    """Direct alias to add_boxed_equation ensuring all equations are boxed matching sample-reference.docx."""
    return add_boxed_equation(doc, eq_text, eq_number_str)


def add_table(doc, table_num, table_caption, headers, rows_data, col_widths=None, is_landscape=False, col_alignments=None):
    """Render a formal academic table with caption, B&W styled header (#F2F2F2), and alternating rows."""
    p_cap = doc.add_paragraph()
    p_cap.paragraph_format.space_before = Pt(8)
    p_cap.paragraph_format.space_after = Pt(2.5)
    p_cap.paragraph_format.keep_with_next = True
    p_cap.alignment = WD_ALIGN_PARAGRAPH.LEFT
    
    r_num = p_cap.add_run(f"Table {table_num}: ")
    r_num.font.name = FONT_MAIN
    r_num.font.size = Pt(10)
    r_num.font.bold = True
    r_num.font.color.rgb = COLOR_NAVY
    
    r_cap = p_cap.add_run(table_caption)
    r_cap.font.name = FONT_MAIN
    r_cap.font.size = Pt(10)
    r_cap.font.italic = True
    r_cap.font.color.rgb = COLOR_BODY
    
    num_rows = len(rows_data) + 1
    num_cols = len(headers)
    tbl = doc.add_table(rows=num_rows, cols=num_cols)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    set_table_borders(tbl, color="000000", sz="4", val="single")
    
    # Header row
    hdr_cells = tbl.rows[0].cells
    for i, h_text in enumerate(headers):
        hdr_cells[i].text = ""
        p = hdr_cells[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(2.5)
        p.paragraph_format.space_after = Pt(2.5)
        r = p.add_run(h_text)
        r.font.name = FONT_MAIN
        r.font.size = Pt(9.0)
        r.font.bold = True
        r.font.color.rgb = RGBColor(0, 0, 0)
        set_cell_shading(hdr_cells[i], HEX_HEADER_BG)
        set_cell_margins(hdr_cells[i], top=80, bottom=80, left=80, right=80)
        if col_widths and i < len(col_widths):
            hdr_cells[i].width = Inches(col_widths[i])
            
    # Data rows
    for r_idx, row_values in enumerate(rows_data):
        row_cells = tbl.rows[r_idx + 1].cells
        shading = HEX_ALT_ROW if r_idx % 2 == 0 else HEX_LIGHT_ROW
        for c_idx, val in enumerate(row_values):
            row_cells[c_idx].text = ""
            p = row_cells[c_idx].paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.08
            val_str = str(val)
            
            # Explicit column alignment if specified
            if col_alignments and c_idx < len(col_alignments):
                align_spec = col_alignments[c_idx].upper()
                if align_spec == 'LEFT':
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                elif align_spec == 'RIGHT':
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif align_spec == 'JUSTIFY':
                    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                else:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                val_clean = val_str.strip()
                if val_clean.startswith("Chapter") or val_clean.startswith("App.") or "Chapter" in val_clean:
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                elif len(val_str) <= 10 or val_str.replace('.', '', 1).replace('%', '').replace('-', '').replace('+', '').isdigit():
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                else:
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                
            r = p.add_run(val_str)
            r.font.name = FONT_MAIN
            r.font.size = Pt(8.5)
            r.font.color.rgb = COLOR_BODY
            set_cell_shading(row_cells[c_idx], shading)
            set_cell_margins(row_cells[c_idx], top=55, bottom=55, left=75, right=75)
            if col_widths and c_idx < len(col_widths):
                row_cells[c_idx].width = Inches(col_widths[c_idx])
                
    doc.add_paragraph().paragraph_format.space_after = Pt(3)


def add_algorithm_block(doc, algo_num, algo_title, input_text, output_text, steps):
    """Render formal boxed algorithmic pseudocode block matching Table 13 in sample-reference.docx."""
    tbl = doc.add_table(rows=1, cols=1)
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    cell = tbl.rows[0].cells[0]
    cell.width = Inches(5.77)
    set_cell_shading(cell, "FFFFFF")
    set_cell_margins(cell, top=100, bottom=100, left=130, right=130)
    
    # 0.5pt solid border matching sample-reference
    tcPr = cell._tc.get_or_add_tcPr()
    tcBorders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'<w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'<w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(tcBorders)

    # 1. Header line
    p0 = cell.paragraphs[0]
    p0.paragraph_format.space_before = Pt(2)
    p0.paragraph_format.space_after = Pt(2.5)
    r_hdr = p0.add_run(f"Algorithm {algo_num}: {algo_title}")
    r_hdr.font.name = FONT_MAIN
    r_hdr.font.size = Pt(10.0)
    r_hdr.font.bold = True
    r_hdr.font.color.rgb = COLOR_NAVY

    # 2. Input line
    p_in = cell.add_paragraph()
    p_in.paragraph_format.space_before = Pt(1)
    p_in.paragraph_format.space_after = Pt(1)
    r_in_lbl = p_in.add_run("Input : ")
    r_in_lbl.font.name = FONT_MAIN
    r_in_lbl.font.size = Pt(9.0)
    r_in_lbl.font.bold = True
    r_in_txt = p_in.add_run(input_text)
    r_in_txt.font.name = FONT_MAIN
    r_in_txt.font.size = Pt(9.0)

    # 3. Output line with bottom divider
    p_out = cell.add_paragraph()
    p_out.paragraph_format.space_before = Pt(1)
    p_out.paragraph_format.space_after = Pt(4)
    r_out_lbl = p_out.add_run("Output: ")
    r_out_lbl.font.name = FONT_MAIN
    r_out_lbl.font.size = Pt(9.0)
    r_out_lbl.font.bold = True
    r_out_txt = p_out.add_run(output_text)
    r_out_txt.font.name = FONT_MAIN
    r_out_txt.font.size = Pt(9.0)

    # Horizontal divider rule beneath Output
    pPr = p_out._p.get_or_add_pPr()
    pPr.append(parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="4" w:space="3" w:color="000000"/></w:pBdr>'))

    # 4. Steps
    for step in steps:
        p_step = cell.add_paragraph()
        p_step.paragraph_format.space_before = Pt(0.5)
        p_step.paragraph_format.space_after = Pt(0.5)
        p_step.paragraph_format.line_spacing = 1.02
        p_step.alignment = WD_ALIGN_PARAGRAPH.LEFT
        r_step = p_step.add_run(step)
        r_step.font.name = FONT_CODE
        r_step.font.size = Pt(8.0)
        r_step.font.color.rgb = COLOR_BODY

    doc.add_paragraph().paragraph_format.space_after = Pt(3)


def add_figure(doc, fig_num, fig_title, image_filename, width_in=4.8, explanation_text=None):
    """Embed publication-grade figure with centered layout, single formal centered caption below, and follow-up explanatory paragraph."""
    img_path = os.path.join(FIGURES_DIR, image_filename)
    if not os.path.exists(img_path):
        img_path = os.path.join(ASSETS_DIR, image_filename)
    
    # 1. Image paragraph
    p_img = doc.add_paragraph()
    p_img.paragraph_format.space_before = Pt(6)
    p_img.paragraph_format.space_after = Pt(2)
    p_img.paragraph_format.keep_with_next = True
    p_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if os.path.exists(img_path):
        p_img.add_run().add_picture(img_path, width=Inches(width_in))
    else:
        r_miss = p_img.add_run(f"[FIGURE {fig_num} PLACEHOLDER: {image_filename}]")
        r_miss.font.color.rgb = COLOR_CRIMSON

    # 2. Single Figure Caption Paragraph BELOW the figure, centered
    p_cap = doc.add_paragraph()
    p_cap.paragraph_format.space_before = Pt(3)
    p_cap.paragraph_format.space_after = Pt(3)
    p_cap.paragraph_format.keep_with_next = True
    p_cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    r_num = p_cap.add_run(f"Figure {fig_num}: ")
    r_num.font.name = FONT_MAIN
    r_num.font.size = Pt(10)
    r_num.font.bold = True
    r_num.font.color.rgb = COLOR_NAVY
    
    r_title = p_cap.add_run(fig_title)
    r_title.font.name = FONT_MAIN
    r_title.font.size = Pt(10)
    r_title.font.italic = True
    r_title.font.color.rgb = COLOR_BODY

    # 3. Follow-up explanatory paragraph matching sample-reference.docx
    if explanation_text:
        add_paragraph(doc, explanation_text, space_after=3)
    else:
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

