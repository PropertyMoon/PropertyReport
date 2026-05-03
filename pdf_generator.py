"""
PDF Report Generator
Converts a PropertyReport into a professional branded PDF
Uses reportlab for PDF generation
"""

import re
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate
from reportlab.pdfgen import canvas as rl_canvas


# ─── Brand Colors ─────────────────────────────────────────────────────────────

NAVY       = colors.HexColor("#1a3c5e")
GOLD       = colors.HexColor("#c9a84c")
LIGHT_BLUE = colors.HexColor("#e8f0f8")
MID_GREY   = colors.HexColor("#666666")
LIGHT_GREY = colors.HexColor("#f5f7fa")
WHITE      = colors.white
RED        = colors.HexColor("#d9534f")
GREEN      = colors.HexColor("#2e7d32")


# ─── Custom Page Template ──────────────────────────────────────────────────────

class PropertyReportTemplate(BaseDocTemplate):
    """Custom doc template with header/footer on every page."""

    def __init__(self, filename, address, **kwargs):
        self.address = address
        super().__init__(filename, **kwargs)

        frame = Frame(
            15*mm, 20*mm,             # x, y (bottom-left of content area)
            self.width, self.height,   # width, height
            leftPadding=0, rightPadding=0,
            topPadding=5*mm, bottomPadding=5*mm
        )
        template = PageTemplate(
            id="main",
            frames=[frame],
            onPage=self._draw_page
        )
        self.addPageTemplates([template])

    def _draw_page(self, canv, doc):
        canv.saveState()
        w, h = A4

        # ── Header bar ──
        canv.setFillColor(NAVY)
        canv.rect(0, h - 18*mm, w, 18*mm, fill=1, stroke=0)

        canv.setFillColor(GOLD)
        canv.setFont("Helvetica-Bold", 13)
        canv.drawString(15*mm, h - 12*mm, "PropertyIQ")

        canv.setFillColor(WHITE)
        canv.setFont("Helvetica", 8)
        canv.drawRightString(w - 15*mm, h - 12*mm, "Australia's AI Property Research Platform")

        # ── Gold accent line under header ──
        canv.setStrokeColor(GOLD)
        canv.setLineWidth(1.5)
        canv.line(0, h - 18*mm, w, h - 18*mm)

        # ── Footer ──
        canv.setFillColor(LIGHT_GREY)
        canv.rect(0, 0, w, 14*mm, fill=1, stroke=0)

        canv.setFillColor(MID_GREY)
        canv.setFont("Helvetica", 7)
        canv.drawString(15*mm, 5*mm, f"Property: {self.address}")
        canv.drawCentredString(w / 2, 5*mm, f"Generated {datetime.now().strftime('%d %B %Y')}")
        canv.drawRightString(w - 15*mm, 5*mm, f"Page {doc.page}")

        # ── Disclaimer line ──
        canv.setFont("Helvetica-Oblique", 6)
        canv.setFillColor(colors.HexColor("#aaaaaa"))
        canv.drawCentredString(
            w / 2, 10*mm,
            "For informational purposes only. Not financial advice. Always conduct independent due diligence."
        )

        canv.restoreState()


# ─── Styles ───────────────────────────────────────────────────────────────────

def get_styles():
    base = getSampleStyleSheet()

    styles = {
        "report_title": ParagraphStyle(
            "report_title",
            fontSize=22,
            fontName="Helvetica-Bold",
            textColor=NAVY,
            spaceAfter=4*mm,
            leading=28,
        ),
        "address": ParagraphStyle(
            "address",
            fontSize=13,
            fontName="Helvetica",
            textColor=MID_GREY,
            spaceAfter=6*mm,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            fontSize=13,
            fontName="Helvetica-Bold",
            textColor=WHITE,
            spaceBefore=4*mm,
            spaceAfter=3*mm,
            leftIndent=4*mm,
        ),
        "subheading": ParagraphStyle(
            "subheading",
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=NAVY,
            spaceBefore=3*mm,
            spaceAfter=1*mm,
        ),
        "body": ParagraphStyle(
            "body",
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#333333"),
            leading=14,
            spaceAfter=2*mm,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontSize=9,
            fontName="Helvetica",
            textColor=colors.HexColor("#333333"),
            leading=13,
            leftIndent=8*mm,
            bulletIndent=3*mm,
            spaceAfter=1*mm,
        ),
        "tag_good": ParagraphStyle(
            "tag_good",
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=GREEN,
        ),
        "tag_warn": ParagraphStyle(
            "tag_warn",
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#e65100"),
        ),
        "tag_bad": ParagraphStyle(
            "tag_bad",
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=RED,
        ),
        "footer_note": ParagraphStyle(
            "footer_note",
            fontSize=7,
            fontName="Helvetica-Oblique",
            textColor=MID_GREY,
            leading=10,
        ),
    }
    return styles


# ─── Section Header Helper ────────────────────────────────────────────────────

def section_header(title: str, emoji: str, styles: dict):
    """Renders a coloured section header bar."""
    items = []
    # Coloured background table
    data = [[Paragraph(f"{emoji}  {title}", styles["section_heading"])]]
    t = Table(data, colWidths=[180*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("ROWPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    items.append(t)
    items.append(Spacer(1, 3*mm))
    return items


# ─── Parse Report Text ────────────────────────────────────────────────────────

def parse_report_to_flowables(summary: str, styles: dict) -> list:
    """
    Converts the Claude-generated markdown-ish report text
    into reportlab flowables with proper section headers.
    """
    flowables = []
    lines = summary.split("\n")

    SECTION_MAP = {
        "Executive Summary":            ("📋", True),
        "Suburb Profile":               ("🏘️", True),
        "School Catchments":            ("🏫", True),
        "Infrastructure":               ("🏗️", True),
        "Government":                   ("🏛️", True),
        "Transport":                    ("🚆", True),
        "Property Market":              ("📈", True),
        "Risk Assessment":              ("⚠️", True),
        "Investment Verdict":           ("💡", True),
        "Recommendation":               ("💡", True),
    }

    current_bullets = []

    def flush_bullets():
        nonlocal current_bullets
        for b in current_bullets:
            flowables.append(Paragraph(f"• {b}", styles["bullet"]))
        current_bullets = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_bullets()
            continue

        # H1 / H2 headings
        if stripped.startswith("## ") or stripped.startswith("# "):
            flush_bullets()
            heading_text = stripped.lstrip("#").strip()
            # Find matching section
            emoji = "📌"
            for key, (em, _) in SECTION_MAP.items():
                if key.lower() in heading_text.lower():
                    emoji = em
                    break
            flowables.extend(section_header(heading_text, emoji, styles))

        elif stripped.startswith("### "):
            flush_bullets()
            flowables.append(Paragraph(stripped[4:], styles["subheading"]))

        elif stripped.startswith("- ") or stripped.startswith("• "):
            current_bullets.append(stripped[2:])

        elif stripped.startswith("**") and stripped.endswith("**"):
            flush_bullets()
            flowables.append(Paragraph(stripped.strip("*"), styles["subheading"]))

        else:
            flush_bullets()
            # Clean up any remaining markdown bold
            clean = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", stripped)
            flowables.append(Paragraph(clean, styles["body"]))

    flush_bullets()
    return flowables


# ─── Cover Page ───────────────────────────────────────────────────────────────

def build_cover_page(address: str, styles: dict) -> list:
    items = []
    items.append(Spacer(1, 20*mm))

    # Title
    items.append(Paragraph("Property Intelligence Report", styles["report_title"]))
    items.append(HRFlowable(width="100%", thickness=2, color=GOLD, spaceAfter=4*mm))
    items.append(Paragraph(address, styles["address"]))
    items.append(Spacer(1, 8*mm))

    # Info box
    today = datetime.now().strftime("%d %B %Y")
    data = [
        ["Report Date", today],
        ["Market",      "Melbourne, Victoria, Australia"],
        ["Data Sources","realestate.com.au · myschool.edu.au · planning.vic.gov.au · crimestats.vic.gov.au"],
        ["Prepared by", "PropertyIQ AI Research Platform"],
    ]
    t = Table(data, colWidths=[45*mm, 135*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (0, -1), LIGHT_BLUE),
        ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("TEXTCOLOR",    (0, 0), (0, -1), NAVY),
        ("TEXTCOLOR",    (1, 0), (1, -1), colors.HexColor("#333333")),
        ("ROWPADDING",   (0, 0), (-1, -1), 6),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
    ]))
    items.append(t)
    items.append(Spacer(1, 10*mm))

    # Disclaimer
    items.append(Paragraph(
        "This report was generated by artificial intelligence using publicly available Australian data sources. "
        "It is intended as a research aid only and does not constitute financial, legal, or investment advice. "
        "Always engage a licensed property professional before making purchasing decisions.",
        styles["footer_note"]
    ))

    items.append(PageBreak())
    return items


# ─── Main PDF Builder ─────────────────────────────────────────────────────────

def generate_pdf(report, output_path: str = "property_report.pdf") -> str:
    """
    Generate a PDF from a PropertyReport object.

    Args:
        report: PropertyReport dataclass instance
        output_path: Where to save the PDF

    Returns:
        Path to the generated PDF
    """
    styles = get_styles()
    w, h = A4

    doc = PropertyReportTemplate(
        output_path,
        address=report.address,
        pagesize=A4,
        leftMargin=15*mm,
        rightMargin=15*mm,
        topMargin=22*mm,
        bottomMargin=18*mm,
    )

    story = []

    # Cover page
    story.extend(build_cover_page(report.address, styles))

    # Full report content (parsed from Claude's text)
    story.extend(parse_report_to_flowables(report.summary, styles))

    # Build PDF
    doc.build(story)
    print(f"✅ PDF generated: {output_path}")
    return output_path


# ─── CLI Test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, sys
    sys.path.insert(0, ".")
    from orchestrator import PropertyReport

    with open("report_output.json") as f:
        data = json.load(f)

    report = PropertyReport(
        address=data["address"],
        suburb=data["research_data"]["suburb"],
        schools=data["research_data"]["schools"],
        government_projects=data["research_data"]["government_projects"],
        transport=data["research_data"]["transport"],
        property_market=data["research_data"]["property_market"],
        risk_overlays=data["research_data"]["risk_overlays"],
        summary=data["summary"]
    )

    generate_pdf(report, "test_report.pdf")
