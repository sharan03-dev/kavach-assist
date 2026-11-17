"""
A tiny document model so the same report content can be written to PDF
(reportlab - the file that is submitted) and to DOCX (python-docx - editable).

Inline text "parts":  "plain"  |  (text, style)  with style letters
    b = bold, i = italic, c = code, y = yellow highlight
or  ("link", text, url).
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

NAVY = "1B2033"
ZEBRA = "F4F6FA"
CODE_BG = "F3F4F6"


class Doc:
    def __init__(self, footer: str = ""):
        self.blocks: list[tuple] = []
        self.footer = footer

    def h(self, text, level=1):
        self.blocks.append(("h", text, level))

    def p(self, *parts, align=None, size=None, italic=False):
        self.blocks.append(("p", list(parts), {"align": align, "size": size, "italic": italic}))

    def bullets(self, items):
        """items: str | tuple (one styled part) | list (several parts)"""
        self.blocks.append(("bullets", [it if isinstance(it, list) else [it] for it in items]))

    def code(self, text, size=7.5):
        self.blocks.append(("code", text.rstrip("\n").expandtabs(4), size))

    def table(self, headers, rows, widths=None, size=8.5, first_col_bold=False):
        self.blocks.append(("table", [str(h) for h in headers],
                            [[str(c) for c in r] for r in rows], widths, size, first_col_bold))

    def image(self, path, width_cm=17.0, caption=None):
        self.blocks.append(("image", str(path), width_cm, caption))

    def page_break(self):
        self.blocks.append(("pagebreak",))


def _norm(part):
    if isinstance(part, str):
        return ("text", part, "")
    if len(part) == 3 and part[0] == "link":
        return part
    return ("text", part[0], part[1])


# =============================================================================
# PDF (reportlab)
# =============================================================================
def render_pdf(doc: Doc, path: Path, title: str = "", author: str = ""):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    from report.termshot import find_font

    # ---- fonts (DejaVu has the arrows/x signs; fall back to built-ins) -------
    reg = find_font(["DejaVuSans.ttf"])
    if reg:
        pdfmetrics.registerFont(TTFont("Body", reg))
        pdfmetrics.registerFont(TTFont("Body-B", find_font(["DejaVuSans-Bold.ttf"]) or reg))
        pdfmetrics.registerFont(TTFont("Body-I", find_font(["DejaVuSans-Oblique.ttf"]) or reg))
        pdfmetrics.registerFont(TTFont("Body-BI", find_font(["DejaVuSans-BoldOblique.ttf",
                                                             "DejaVuSans-Bold.ttf"]) or reg))
        mono = find_font(["DejaVuSansMono.ttf"])
        pdfmetrics.registerFont(TTFont("Mono", mono or reg))
        from reportlab.lib.fonts import addMapping
        addMapping("Body", 0, 0, "Body")
        addMapping("Body", 1, 0, "Body-B")
        addMapping("Body", 0, 1, "Body-I")
        addMapping("Body", 1, 1, "Body-BI")
        BODY, BOLD, MONO = "Body", "Body-B", "Mono"
        clean = str
    else:
        BODY, BOLD, MONO = "Helvetica", "Helvetica-Bold", "Courier"

        def clean(s):
            return s.encode("latin-1", "replace").decode("latin-1")

    navy = colors.HexColor("#" + NAVY)
    st = {
        "body": ParagraphStyle("body", fontName=BODY, fontSize=10, leading=13.5, spaceAfter=5),
        "h1": ParagraphStyle("h1", fontName=BOLD, fontSize=15, leading=19, textColor=navy,
                             spaceBefore=10, spaceAfter=7),
        "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=12.5, leading=16, textColor=navy,
                             spaceBefore=9, spaceAfter=5),
        "h3": ParagraphStyle("h3", fontName=BOLD, fontSize=11, leading=14, textColor=navy,
                             spaceBefore=6, spaceAfter=4),
        "bullet": ParagraphStyle("bullet", fontName=BODY, fontSize=10, leading=13.5,
                                 leftIndent=14, bulletIndent=3, spaceAfter=3),
        "cap": ParagraphStyle("cap", fontName=BODY, fontSize=8.5, leading=11,
                              textColor=colors.HexColor("#555555"), alignment=TA_CENTER,
                              spaceBefore=2, spaceAfter=8),
    }

    def markup(parts, base_italic=False):
        out = []
        for part in parts:
            kind, text, style = _norm(part) if not (isinstance(part, tuple) and part[0] == "link") \
                else part
            if kind == "link":
                out.append(f'<a href="{escape(style)}" color="#1155CC"><u>{escape(clean(text))}</u></a>')
                continue
            t = escape(clean(text)).replace("\n", "<br/>")
            if "c" in style:
                t = f'<font name="{MONO}" size="8.5">{t}</font>'
            if "b" in style:
                t = f"<b>{t}</b>"
            if "i" in style or base_italic:
                t = f"<i>{t}</i>"
            if "y" in style:
                t = f'<span backColor="#FFF176">{t}</span>'
            out.append(t)
        return "".join(out)

    frame_w = A4[0] - 4 * cm
    frame_h = A4[1] - 4.2 * cm
    story = []

    def wrap_code(text, size):
        cols = max(40, int(frame_w / (size * 0.602)) - 2)
        lines = []
        for ln in clean(text).splitlines():
            while len(ln) > cols:
                lines.append(ln[:cols])
                ln = "    " + ln[cols:]
            lines.append(ln)
        return "\n".join(lines)

    for b in doc.blocks:
        kind = b[0]
        if kind == "h":
            p = Paragraph(markup([b[1]]), st[f"h{b[2]}"])
            p._bookmark = (b[1], b[2])
            story.append(p)
        elif kind == "p":
            opts = b[2]
            s = st["body"]
            if opts.get("align") == "center" or opts.get("size"):
                s = ParagraphStyle("pp", parent=s,
                                   alignment=TA_CENTER if opts.get("align") == "center" else TA_LEFT,
                                   fontSize=opts.get("size") or s.fontSize,
                                   leading=(opts.get("size") or s.fontSize) * 1.3)
            story.append(Paragraph(markup(b[1], opts.get("italic")), s))
        elif kind == "bullets":
            for item in b[1]:
                story.append(Paragraph(markup(item), st["bullet"], bulletText="\u2022"))
            story.append(Spacer(1, 3))
        elif kind == "code":
            size = b[2]
            s = ParagraphStyle("code", fontName=MONO, fontSize=size, leading=size * 1.22,
                               backColor=colors.HexColor("#" + CODE_BG), borderPadding=(4, 4, 4, 4),
                               spaceBefore=4, spaceAfter=9)
            story.append(Preformatted(wrap_code(b[1], size), s))
        elif kind == "table":
            _, headers, rows, widths, size, fcb = b
            cs = ParagraphStyle("cell", fontName=BODY, fontSize=size, leading=size * 1.22)
            hs = ParagraphStyle("hcell", parent=cs, fontName=BOLD, textColor=colors.white)
            bs = ParagraphStyle("bcell", parent=cs, fontName=BOLD)
            data = [[Paragraph(escape(clean(h)), hs) for h in headers]]
            for r in rows:
                data.append([Paragraph(escape(clean(c)).replace("\n", "<br/>"),
                                       bs if (fcb and i == 0) else cs) for i, c in enumerate(r)])
            if widths:
                tot = sum(widths)
                cw = [frame_w * w / tot for w in widths] if tot * cm > frame_w else [w * cm for w in widths]
            else:
                lens = [max([len(headers[i])] + [len(r[i]) for r in rows]) for i in range(len(headers))]
                lens = [min(max(L, 4), 60) for L in lens]
                tot = sum(lens)
                cw = [frame_w * L / tot for L in lens]
            t = Table(data, colWidths=cw, repeatRows=1, hAlign="CENTER")
            style = [("BACKGROUND", (0, 0), (-1, 0), navy),
                     ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8BCC8")),
                     ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                     ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
            for i in range(2, len(data), 2):
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#" + ZEBRA)))
            t.setStyle(TableStyle(style))
            story.append(t)
            story.append(Spacer(1, 8))
        elif kind == "image":
            _, ipath, wcm, cap = b
            from PIL import Image as PILImage
            with PILImage.open(ipath) as im:
                iw, ih = im.size
            w = min(wcm * cm, frame_w)
            h = w * ih / iw
            if h > frame_h - 1.5 * cm:
                h = frame_h - 1.5 * cm
                w = h * iw / ih
            flow = [Image(ipath, width=w, height=h)]
            if cap:
                flow.append(Paragraph(markup([cap]), st["cap"]))
            story.append(KeepTogether(flow))
        elif kind == "pagebreak":
            story.append(PageBreak())

    footer = clean(doc.footer)

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont(BODY, 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(2 * cm, 1.2 * cm, footer)
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {d.page}")
        canvas.restoreState()

    class Template(SimpleDocTemplate):
        def afterFlowable(self, flowable):
            bm = getattr(flowable, "_bookmark", None)
            if bm:
                key = f"bm{id(flowable)}"
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(clean(bm[0]), key, level=bm[1] - 1, closed=bm[1] > 1)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = Template(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                   topMargin=2 * cm, bottomMargin=2.2 * cm, title=title, author=author)
    pdf.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return path


# =============================================================================
# DOCX (python-docx)
# =============================================================================
def render_docx(doc: Doc, path: Path):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    def shd(fill):
        e = OxmlElement("w:shd")
        e.set(qn("w:val"), "clear")
        e.set(qn("w:color"), "auto")
        e.set(qn("w:fill"), fill)
        return e

    ppr_after = ("w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
                 "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
                 "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
                 "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
                 "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr",
                 "w:sectPr", "w:pPrChange")

    def shade_cell(cell, fill):
        cell._tc.get_or_add_tcPr().insert_element_before(
            shd(fill), "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
            "w:hideMark")

    def add_link(para, url, text):
        r_id = para.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        c = OxmlElement("w:color")
        c.set(qn("w:val"), "1155CC")
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(c)
        rpr.append(u)
        run.append(rpr)
        t = OxmlElement("w:t")
        t.text = text
        t.set(qn("xml:space"), "preserve")
        run.append(t)
        link.append(run)
        para._p.append(link)

    d = Document()
    sec = d.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    zoom = d.settings.element.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")
    normal = d.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(5)
    for lvl, size in ((1, 16), (2, 13), (3, 11.5)):
        s = d.styles[f"Heading {lvl}"]
        s.font.name = "Calibri"
        s.font.size = Pt(size)
        s.font.color.rgb = RGBColor.from_string(NAVY)
    if doc.footer:
        fp = sec.footer.paragraphs[0]
        fr = fp.add_run(doc.footer)
        fr.font.size = Pt(8)

    def add_parts(para, parts, italic=False, size=None):
        for part in parts:
            n = part if (isinstance(part, tuple) and part[0] == "link") else _norm(part)
            if n[0] == "link":
                add_link(para, n[2], n[1])
                continue
            _, text, style = n
            r = para.add_run(text)
            r.bold = "b" in style
            r.italic = "i" in style or italic
            if "c" in style:
                r.font.name = "Consolas"
                r.font.size = Pt(9)
            elif size:
                r.font.size = Pt(size)
            if "y" in style:
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW

    for b in doc.blocks:
        kind = b[0]
        if kind == "h":
            d.add_heading(b[1], level=b[2])
        elif kind == "p":
            para = d.add_paragraph()
            add_parts(para, b[1], b[2].get("italic"), b[2].get("size"))
            if b[2].get("align") == "center":
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif kind == "bullets":
            for item in b[1]:
                add_parts(d.add_paragraph(style="List Bullet"), item)
        elif kind == "code":
            para = d.add_paragraph()
            pf = para.paragraph_format
            pf.space_before, pf.space_after, pf.line_spacing = Pt(2), Pt(8), 1.0
            r = para.add_run(b[1])
            r.font.name = "Consolas"
            r.font.size = Pt(b[2])
            para._p.get_or_add_pPr().insert_element_before(shd(CODE_BG), *ppr_after)
        elif kind == "table":
            _, headers, rows, widths, size, fcb = b
            t = d.add_table(rows=1, cols=len(headers))
            t.style = "Table Grid"
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            for i, h in enumerate(headers):
                cell = t.rows[0].cells[i]
                cell.text = ""
                r = cell.paragraphs[0].add_run(h)
                r.bold = True
                r.font.size = Pt(size)
                r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                shade_cell(cell, NAVY)
            for k, row in enumerate(rows):
                cells = t.add_row().cells
                for i, v in enumerate(row):
                    cells[i].text = ""
                    r = cells[i].paragraphs[0].add_run(v)
                    r.font.size = Pt(size)
                    r.bold = fcb and i == 0
                    if k % 2:
                        shade_cell(cells[i], ZEBRA)
            if widths:
                tot = sum(widths)
                scale = 17.0 / tot if tot > 17.0 else 1.0
                t.autofit = False
                for row in t.rows:
                    for i, w in enumerate(widths):
                        row.cells[i].width = Cm(w * scale)
            d.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == "image":
            _, ipath, wcm, cap = b
            d.add_picture(ipath, width=Cm(min(wcm, 17.0)))
            d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            if cap:
                cp = d.add_paragraph()
                add_parts(cp, [cap], italic=True, size=8.5)
                cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif kind == "pagebreak":
            d.add_page_break()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(path)
    return path
