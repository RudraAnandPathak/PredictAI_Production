"""Regenerates the small PDF fixtures used by the arXiv tests (needs `reportlab`, which is NOT a PredictAI dependency; the generated files are committed so the tests do not need it).
Run:  python make_pdf_fixtures.py   (from this folder)."""
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


def page_frame(c, n, total):
    c.setFont("Helvetica", 8)
    c.drawString(60, 810, "Preprint under review - Journal of Examples")          # running header (repeats on every page)
    c.drawString(290, 30, str(n))                                                 # page number
    if n == 1:
        c.saveState(); c.translate(20, 400); c.rotate(90); c.setFont("Helvetica", 9)
        c.drawString(0, 0, "arXiv:2101.00001v2 [cs.LG] 3 Feb 2021"); c.restoreState()


def write(c, lines, x=60, y=780, size=10, lead=14):
    c.setFont("Helvetica", size)
    for ln in lines:
        if ln.startswith("##"):
            c.setFont("Helvetica-Bold", 12); c.drawString(x, y, ln[2:].strip()); c.setFont("Helvetica", size)
        else:
            c.drawString(x, y, ln)
        y -= lead
    return y


paper = canvas.Canvas("arxiv_paper.pdf", pagesize=A4)
page_frame(paper, 1, 3)
y = write(paper, ["A Study of Seat Allocation in Airline Networks", "", "##Abstract",
                  "We study how airlines allocate premium seats. A new method improves the fill rate by 12 percent.",
                  "", "##1 Introduction", "Airlines sell seats in several cabins. Demand for the premium cabin is volatile, and classi-",
                  "fication of booking classes matters for revenue.", "", "##2 Related Work",
                  "Earlier work modelled booking limits with dynamic programming.",
                  "", "##3 Methodology", "Let x be the booking limit. We minimise the loss", "L = sum_i (y_i - f(x_i))^2    (1)",
                  "E[R] = p * q - c * q^2    (2)"], y=770)
paper.showPage()
page_frame(paper, 2, 3)
y = write(paper, ["##4 Experiments", "We evaluate on 12 routes over 24 months.", "", "##5 Results", "Table 1 reports the fill rate.", ""], y=770)
t = Table([["Method", "Fill rate", "Revenue"], ["Baseline", "0.71", "1.00"], ["Ours", "0.83", "1.09"]], colWidths=[120, 80, 80])
t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
t.wrapOn(paper, 400, 200); t.drawOn(paper, 60, y - 70)
paper.showPage()
page_frame(paper, 3, 3)
write(paper, ["##6 Discussion", "The gain is largest on long-haul routes. We caution that this is observational.", "", "##7 Conclusion",
              "A simple allocation rule raised the fill rate on the tested routes.", "", "##References",
              "[1] Smith, J. Booking limits. Journal of Revenue, 2015.", "[2] Doe, A. Dynamic pricing. Operations, 2018."], y=770)
paper.showPage()
paper.save()

scanned = canvas.Canvas("scanned_paper.pdf", pagesize=A4)                            # pages with a drawn box and no text layer at all
for _ in range(2):
    scanned.rect(60, 500, 400, 200, stroke=1, fill=0)
    scanned.showPage()
scanned.save()

noheads = canvas.Canvas("no_headings.pdf", pagesize=A4)
write(noheads, ["This note has no section headings at all. It simply describes a result in plain prose.",
                "The method was applied to 40 airports and the median delay fell by 9 minutes in the sample."], y=770)
noheads.showPage(); noheads.save()
