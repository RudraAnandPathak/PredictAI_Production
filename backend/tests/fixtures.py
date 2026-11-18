"""Builds small but real files for every supported format using only the standard library (plus Pillow for the scanned-PDF case).
Nothing here is a mock: the same bytes a user would upload."""
import io
import zipfile
from xml.sax.saxutils import escape

XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _zip(files, mimetype_first=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if mimetype_first:
            z.writestr(zipfile.ZipInfo("mimetype"), mimetype_first)
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def xlsx(rows, sheets_extra=0, date_col=None, macro=False):
    """rows: list of lists of str/int/float/bool/None. date_col: index of a column holding Excel serial numbers with a date style."""
    strings, cells = [], []
    for r, row in enumerate(rows, start=1):
        out = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if value is None:
                continue
            if isinstance(value, bool):
                out.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
            elif isinstance(value, (int, float)):
                style = ' s="1"' if date_col == c and r > 1 else ""
                out.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
            else:
                if value not in strings:
                    strings.append(value)
                out.append(f'<c r="{ref}" t="s"><v>{strings.index(value)}</v></c>')
        cells.append(f'<row r="{r}">{"".join(out)}</row>')
    sheet = f'{XML_HEAD}<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(cells)}</sheetData></worksheet>'
    sst = f'{XML_HEAD}<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>"
    sheet_tags = '<sheet name="Data" sheetId="1" r:id="rId1"/>' + "".join(f'<sheet name="Extra{i}" sheetId="{i + 1}" r:id="rId1"/>' for i in range(1, sheets_extra + 1))
    workbook = (f'{XML_HEAD}<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheet_tags}</sheets></workbook>')
    rels = (f'{XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    styles = (f'{XML_HEAD}<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs count="2">'
              '<xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    files = {"[Content_Types].xml": f'{XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
             "xl/workbook.xml": workbook, "xl/_rels/workbook.xml.rels": rels, "xl/worksheets/sheet1.xml": sheet,
             "xl/sharedStrings.xml": sst, "xl/styles.xml": styles}
    if macro:
        files["xl/vbaProject.bin"] = b"x"
    return _zip(files)


def ods(rows):
    def cell(v):
        if v is None or v == "":
            return "<table:table-cell/>"
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<table:table-cell office:value-type="float" office:value="{v}"><text:p>{v}</text:p></table:table-cell>'
        return f'<table:table-cell office:value-type="string"><text:p>{escape(str(v))}</text:p></table:table-cell>'
    body = "".join("<table:table-row>" + "".join(cell(v) for v in row) + "</table:table-row>" for row in rows)
    content = (f'{XML_HEAD}<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
               f'<office:body><office:spreadsheet><table:table table:name="Sheet1">{body}</table:table></office:spreadsheet></office:body></office:document-content>')
    return _zip({"content.xml": content}, mimetype_first="application/vnd.oasis.opendocument.spreadsheet")


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(paragraphs, table=None):
    """paragraphs: list of (style or None, text)."""
    parts = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        parts.append(f"<w:p>{ppr}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>")
    if table:
        parts.append("<w:tbl>" + "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{escape(c)}</w:t></w:r></w:p></w:tc>" for c in row) + "</w:tr>" for row in table) + "</w:tbl>")
    document = f'{XML_HEAD}<w:document {W}><w:body>{"".join(parts)}</w:body></w:document>'
    styles = (f'{XML_HEAD}<w:styles {W}><w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
              '<w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>')
    return _zip({"[Content_Types].xml": f'{XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
                 "word/document.xml": document, "word/styles.xml": styles})


def rtf(text_paragraphs):
    body = "".join(rf"\pard {p}\par " for p in text_paragraphs)
    return ("{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times;}}{\\colortbl;\\red0\\green0\\blue0;}" + body + "}").encode("latin-1")


def pdf(page_texts):
    """A valid multi-page PDF with a real text layer (Helvetica). Pages given as '' are blank (no text, no image)."""
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    add("<< /Type /Catalog /Pages 2 0 R >>")
    add(None)  # pages placeholder
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for text in page_texts:
        lines = text.split("\n") if text else []
        stream = "BT /F1 12 Tf 14 TL 72 720 Td " + " ".join(f"({ln.replace(chr(92), chr(92) * 2).replace('(', chr(92) + '(').replace(')', chr(92) + ')')}) Tj T*" for ln in lines) + " ET"
        content = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        page_ids.append(add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>"))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {len(page_ids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def scanned_pdf(lines=("INVOICE 2024", "Total due: 450 dollars")):
    """An image-only PDF (no text layer) rendered with Pillow; None when Pillow is missing."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    image = Image.new("RGB", (1240, 700), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 56)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((80, 120 + i * 140), line, fill="black", font=font)
    buf = io.BytesIO()
    image.save(buf, format="PDF", resolution=150)
    return buf.getvalue()
