"""
PDF Report Generator
Converts a PropertyReport into a professional branded PDF
"""

import os
import re
import tempfile
import urllib.request
import urllib.parse
from datetime import datetime


# ─── State Detection ──────────────────────────────────────────────────────────

_STATE_SOURCES = {
    "VIC": {
        "label":   "Melbourne, Victoria, Australia",
        "planning": "planning.vic.gov.au",
        "crime":    "crimestats.vic.gov.au",
        "flood":    "vicfloodmap.com.au",
    },
    "NSW": {
        "label":   "New South Wales, Australia",
        "planning": "planning.nsw.gov.au",
        "crime":    "bocsar.nsw.gov.au",
        "flood":    "floodplanning.nsw.gov.au",
    },
    "QLD": {
        "label":   "Queensland, Australia",
        "planning": "dsdilgp.qld.gov.au",
        "crime":    "police.qld.gov.au/maps-and-statistics",
        "flood":    "floodcheck.qld.gov.au",
    },
    "SA": {
        "label":   "South Australia, Australia",
        "planning": "plan.sa.gov.au",
        "crime":    "police.sa.gov.au/services-and-stats",
        "flood":    "environment.sa.gov.au/flood",
    },
    "WA": {
        "label":   "Western Australia, Australia",
        "planning": "planning.wa.gov.au",
        "crime":    "police.wa.gov.au/crime-statistics",
        "flood":    "planning.wa.gov.au/flood",
    },
    "TAS": {
        "label":   "Tasmania, Australia",
        "planning": "listmap.tas.gov.au",
        "crime":    "justice.tas.gov.au/crime-statistics",
        "flood":    "dpipwe.tas.gov.au/flood",
    },
    "ACT": {
        "label":   "Australian Capital Territory, Australia",
        "planning": "actmapi.act.gov.au",
        "crime":    "police.act.gov.au/crime-statistics",
        "flood":    "esa.act.gov.au/flood",
    },
    "NT": {
        "label":   "Northern Territory, Australia",
        "planning": "planning.nt.gov.au",
        "crime":    "pfes.nt.gov.au/crime-statistics",
        "flood":    "nt.gov.au/emergency/flood",
    },
}

_DEFAULT_SOURCES = {
    "label":    "Australia",
    "planning": "planning.gov.au",
    "crime":    "aic.gov.au",
    "flood":    "ga.gov.au/flood",
}

_STATE_RE = re.compile(
    r'\b(VIC|NSW|QLD|SA|WA|TAS|ACT|NT)\b', re.IGNORECASE
)


def detect_state(address: str) -> dict:
    m = _STATE_RE.search(address)
    if m:
        return _STATE_SOURCES.get(m.group(1).upper(), _DEFAULT_SOURCES)
    return _DEFAULT_SOURCES


def state_data_sources(address: str) -> str:
    s = detect_state(address)
    return f"realestate.com.au · myschool.edu.au · {s['planning']} · {s['crime']}"
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether, Image
)
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate
from reportlab.pdfgen import canvas as rl_canvas


# ─── Brand Colors ─────────────────────────────────────────────────────────────

NAVY       = colors.HexColor("#1e293b")
NAVY_DARK  = colors.HexColor("#0f172a")
TEAL       = colors.HexColor("#334155")
GOLD       = colors.HexColor("#10b981")
GOLD_LIGHT = colors.HexColor("#6ee7b7")
LIGHT_BLUE = colors.HexColor("#d1fae5")
MID_GREY   = colors.HexColor("#94a3b8")
LIGHT_GREY = colors.HexColor("#f1f5f9")
BORDER_GREY= colors.HexColor("#e2e8f0")
WHITE      = colors.white
RED        = colors.HexColor("#c0392b")
GREEN      = colors.HexColor("#059669")
ORANGE     = colors.HexColor("#d97706")
TEXT_DARK  = colors.HexColor("#0f172a")
TEXT_MID   = colors.HexColor("#475569")


# ─── Custom Page Template ──────────────────────────────────────────────────────

class PropertyReportTemplate(BaseDocTemplate):
    def __init__(self, filename, address, **kwargs):
        self.address = address
        super().__init__(filename, **kwargs)
        frame = Frame(
            15*mm, 22*mm,
            self.width, self.height,
            leftPadding=0, rightPadding=0,
            topPadding=5*mm, bottomPadding=5*mm
        )
        self.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=self._draw_page)])

    def _draw_page(self, canv, doc):
        canv.saveState()
        w, h = A4

        # Header bar
        canv.setFillColor(NAVY_DARK)
        canv.rect(0, h - 16*mm, w, 16*mm, fill=1, stroke=0)

        # Gold accent stripe
        canv.setFillColor(GOLD)
        canv.rect(0, h - 17.5*mm, w, 1.5*mm, fill=1, stroke=0)

        canv.setFillColor(GOLD)
        canv.setFont("Helvetica-Bold", 12)
        canv.drawString(15*mm, h - 11*mm, "PropertyReport")

        canv.setFillColor(WHITE)
        canv.setFont("Helvetica", 7.5)
        canv.drawRightString(w - 15*mm, h - 11*mm, "AI Property Intelligence Report")

        # Footer
        canv.setFillColor(LIGHT_GREY)
        canv.rect(0, 0, w, 16*mm, fill=1, stroke=0)
        canv.setFillColor(GOLD)
        canv.rect(0, 15.5*mm, w, 0.5*mm, fill=1, stroke=0)

        canv.setFillColor(MID_GREY)
        canv.setFont("Helvetica", 7)
        addr = self.address[:70] + "..." if len(self.address) > 70 else self.address
        canv.drawString(15*mm, 9*mm, addr)
        canv.drawCentredString(w / 2, 9*mm, datetime.now().strftime("%d %B %Y"))
        canv.drawRightString(w - 15*mm, 9*mm, f"Page {doc.page}")

        canv.setFont("Helvetica-Oblique", 5.5)
        canv.setFillColor(colors.HexColor("#aaaaaa"))
        canv.drawCentredString(w / 2, 4*mm,
            "For informational purposes only. Not financial advice. Always conduct independent due diligence.")

        canv.restoreState()


# ─── Styles ───────────────────────────────────────────────────────────────────

def get_styles():
    styles = {
        "hero_eyebrow": ParagraphStyle("hero_eyebrow", fontSize=8, fontName="Helvetica-Bold",
            textColor=GOLD_LIGHT, spaceAfter=3*mm, leading=10),
        "hero_title": ParagraphStyle("hero_title", fontSize=26, fontName="Helvetica-Bold",
            textColor=WHITE, spaceAfter=3*mm, leading=32),
        "hero_address": ParagraphStyle("hero_address", fontSize=13, fontName="Helvetica",
            textColor=GOLD_LIGHT, spaceAfter=2*mm, leading=18),
        "hero_meta": ParagraphStyle("hero_meta", fontSize=8, fontName="Helvetica",
            textColor=colors.HexColor("#aabbcc"), leading=12),
        "report_title": ParagraphStyle("report_title", fontSize=20, fontName="Helvetica-Bold",
            textColor=NAVY, spaceAfter=4*mm, leading=26),
        "address": ParagraphStyle("address", fontSize=12, fontName="Helvetica",
            textColor=MID_GREY, spaceAfter=5*mm),
        "section_heading": ParagraphStyle("section_heading", fontSize=11, fontName="Helvetica-Bold",
            textColor=WHITE, spaceBefore=3*mm, spaceAfter=2*mm, leftIndent=3*mm),
        "subheading": ParagraphStyle("subheading", fontSize=10, fontName="Helvetica-Bold",
            textColor=NAVY, spaceBefore=3*mm, spaceAfter=1*mm),
        "body": ParagraphStyle("body", fontSize=9, fontName="Helvetica",
            textColor=TEXT_MID, leading=14, spaceAfter=2*mm),
        "bullet": ParagraphStyle("bullet", fontSize=9, fontName="Helvetica",
            textColor=TEXT_MID, leading=13, leftIndent=6*mm, spaceAfter=1.5*mm),
        "scorecard_label": ParagraphStyle("scorecard_label", fontSize=7.5, fontName="Helvetica-Bold",
            textColor=MID_GREY, leading=10, alignment=TA_CENTER),
        "scorecard_value": ParagraphStyle("scorecard_value", fontSize=11, fontName="Helvetica-Bold",
            textColor=NAVY, leading=14, alignment=TA_CENTER),
        "scorecard_sub": ParagraphStyle("scorecard_sub", fontSize=7, fontName="Helvetica",
            textColor=MID_GREY, leading=10, alignment=TA_CENTER),
        "footer_note": ParagraphStyle("footer_note", fontSize=7, fontName="Helvetica-Oblique",
            textColor=MID_GREY, leading=10),
    }
    return styles


# ─── Section Header ───────────────────────────────────────────────────────────

_section_count = 0

def section_header(title: str, emoji: str, styles: dict):
    global _section_count
    bg = TEAL if _section_count % 2 == 1 else NAVY
    _section_count += 1

    data = [[Paragraph(f"{emoji}  {title}", styles["section_heading"])]]
    t = Table(data, colWidths=[180*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), bg),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("ROWBORDERPADDING", (0,0), (-1,-1), 0),
    ]))
    return [t, Spacer(1, 3*mm)]


# ─── Scorecard ────────────────────────────────────────────────────────────────

def build_scorecard(report, styles: dict) -> list:
    m = report.metrics if isinstance(getattr(report, "metrics", None), dict) else {}

    cells = [
        ("MEDIAN PRICE",  m.get("median_price",   "N/A"), "house"),
        ("RENTAL YIELD",  m.get("rental_yield",   "N/A"), "estimate"),
        ("SCHOOLS",       m.get("school_quality", "N/A"), "quality"),
        ("FLOOD RISK",    m.get("flood_risk",     "N/A"), "overlay"),
        ("TRAIN TO CBD",  m.get("cbd_train_mins", "N/A"), "nearest"),
        ("MARKET",        m.get("market_outlook", "N/A"), "outlook"),
    ]

    col_w = 30*mm

    def make_cell(label, value, sub):
        inner = Table(
            [[Paragraph(label, styles["scorecard_label"])],
             [Paragraph(value, styles["scorecard_value"])],
             [Paragraph(sub,   styles["scorecard_sub"])]],
            colWidths=[col_w]
        )
        inner.setStyle(TableStyle([
            ("ALIGN",         (0,0), (-1,-1), "CENTER"),
            ("TOPPADDING",    (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ]))
        return inner

    row = [[make_cell(label, val, sub) for label, val, sub in cells]]
    t = Table(row, colWidths=[col_w]*6)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), LIGHT_GREY),
        ("BOX",           (0,0), (-1,-1), 0.5, BORDER_GREY),
        ("LINEBEFORE",    (1,0), (-1,-1), 0.5, BORDER_GREY),
        ("ALIGN",         (0,0), (-1,-1), "CENTER"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))

    return [
        Paragraph("KEY METRICS AT A GLANCE", ParagraphStyle("sm", fontSize=7.5,
            fontName="Helvetica-Bold", textColor=MID_GREY, spaceAfter=2*mm)),
        t,
        Spacer(1, 5*mm),
    ]


# ─── Street View Image ────────────────────────────────────────────────────────

def fetch_street_view(address: str, width: int = 560, height: int = 260) -> str | None:
    """Download a Google Street View image for the address. Returns temp file path or None."""
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key or api_key == "your-google-maps-api-key-here":
        return None
    try:
        params = urllib.parse.urlencode({
            "size":               f"{width}x{height}",
            "location":           address,
            "key":                api_key,
            "return_error_codes": "true",
            "source":             "outdoor",
        })
        url  = f"https://maps.googleapis.com/maps/api/streetview?{params}"
        path = os.path.join(tempfile.gettempdir(), f"sv_{abs(hash(address))}.jpg")
        urllib.request.urlretrieve(url, path)
        # Google returns a grey placeholder for unknown addresses with status 200;
        # check file size — real images are >5 KB
        if os.path.getsize(path) < 5_000:
            return None
        return path
    except Exception as e:
        print(f"⚠️  Street View fetch failed: {e}")
        return None


# ─── Cover Page ───────────────────────────────────────────────────────────────

def build_cover_page(report, styles: dict) -> list:
    address  = report.address
    today    = datetime.now().strftime("%d %B %Y")
    state    = detect_state(address)
    items    = []

    # ── Hero block (navy background via table) ──
    hero_content = [
        [Paragraph("AI PROPERTY INTELLIGENCE REPORT", styles["hero_eyebrow"])],
        [Paragraph("Property<br/>Research Report", styles["hero_title"])],
        [Spacer(1, 2*mm)],
        [HRFlowable(width="100%", thickness=1, color=GOLD, spaceAfter=3*mm)],
        [Paragraph(address, styles["hero_address"])],
        [Paragraph(f"Generated {today}  ·  {state['label']}  ·  PropertyReport", styles["hero_meta"])],
    ]
    hero_table = Table([[row] for row in hero_content], colWidths=[180*mm])
    hero_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), NAVY_DARK),
        ("LEFTPADDING",   (0,0), (-1,-1), 10*mm),
        ("RIGHTPADDING",  (0,0), (-1,-1), 10*mm),
        ("TOPPADDING",    (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("TOPPADDING",    (0,0), (0,0),   8*mm),
        ("BOTTOMPADDING", (0,5), (0,5),   8*mm),
    ]))
    items.append(Spacer(1, 5*mm))
    items.append(hero_table)
    items.append(Spacer(1, 4*mm))

    # ── Street View photo ──
    img_path = fetch_street_view(address)
    if img_path:
        img = Image(img_path, width=180*mm, height=84*mm)
        img.hAlign = "LEFT"
        # Wrap in a table to add a thin border
        img_table = Table([[img]], colWidths=[180*mm])
        img_table.setStyle(TableStyle([
            ("BOX",           (0,0), (-1,-1), 0.5, BORDER_GREY),
            ("TOPPADDING",    (0,0), (-1,-1), 0),
            ("BOTTOMPADDING", (0,0), (-1,-1), 0),
            ("LEFTPADDING",   (0,0), (-1,-1), 0),
            ("RIGHTPADDING",  (0,0), (-1,-1), 0),
        ]))
        items.append(img_table)
        items.append(Spacer(1, 4*mm))

    # ── Scorecard ──
    items.extend(build_scorecard(report, styles))

    # ── Info table ──
    data = [
        ["Report Date", today],
        ["Market",      state["label"]],
        ["Prepared by", "PropertyReport AI Research Platform"],
    ]
    t = Table(data, colWidths=[42*mm, 138*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (0,-1), LIGHT_BLUE),
        ("FONTNAME",    (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,-1), 8.5),
        ("TEXTCOLOR",   (0,0), (0,-1), NAVY),
        ("TEXTCOLOR",   (1,0), (1,-1), TEXT_MID),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("GRID",        (0,0), (-1,-1), 0.5, BORDER_GREY),
        ("VALIGN",      (0,0), (-1,-1), "TOP"),
    ]))
    items.append(t)
    items.append(Spacer(1, 6*mm))

    # ── Disclaimer ──
    items.append(Paragraph(
        "This report was generated by artificial intelligence using publicly available Australian data sources. "
        "It is a research aid only and does not constitute financial, legal, or investment advice. "
        "Always engage a licensed property professional before making purchasing decisions.",
        styles["footer_note"]
    ))
    items.append(PageBreak())
    return items


# ─── Parse Report Text ────────────────────────────────────────────────────────

SECTION_MAP = {
    "executive summary": ("📋", ),
    "suburb profile":    ("🏘️", ),
    "liveability":       ("🏘️", ),
    "school":            ("🏫", ),
    "infrastructure":    ("🏗️", ),
    "government":        ("🏛️", ),
    "transport":         ("🚆", ),
    "property market":   ("📈", ),
    "market analysis":   ("📈", ),
    "risk":              ("⚠️", ),
    "investment":        ("💡", ),
    "verdict":           ("💡", ),
    "recommendation":    ("💡", ),
}

def _emoji_for(heading: str) -> str:
    lower = heading.lower()
    for key, (em,) in SECTION_MAP.items():
        if key in lower:
            return em
    return "📌"


def parse_report_to_flowables(summary: str, styles: dict) -> list:
    flowables = []
    lines = summary.split("\n")
    current_bullets = []

    def flush_bullets():
        nonlocal current_bullets
        for b in current_bullets:
            # Detect sentiment for bullet color
            lower_b = b.lower()
            if any(w in lower_b for w in ("no ", "low ", "minimal", "excellent", "strong", "good", "well")):
                dot_color = "#1e8449"
            elif any(w in lower_b for w in ("risk", "concern", "flood", "high", "poor", "limited", "lack")):
                dot_color = "#c0392b"
            else:
                dot_color = "#c9a84c"
            clean = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", b)
            flowables.append(Paragraph(
                f'<font color="{dot_color}">■</font>  {clean}',
                styles["bullet"]
            ))
        current_bullets = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_bullets()
            continue

        if stripped.startswith("## ") or stripped.startswith("# "):
            flush_bullets()
            heading_text = stripped.lstrip("#").strip()
            flowables.extend(section_header(heading_text, _emoji_for(heading_text), styles))

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
            clean = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", stripped)
            flowables.append(Paragraph(clean, styles["body"]))

    flush_bullets()
    return flowables


# ─── Main PDF Builder ─────────────────────────────────────────────────────────

def generate_pdf(report, output_path: str = "property_report.pdf") -> str:
    global _section_count
    _section_count = 0  # reset alternating colors per report

    styles = get_styles()

    doc = PropertyReportTemplate(
        output_path,
        address=report.address,
        pagesize=A4,
        leftMargin=15*mm,
        rightMargin=15*mm,
        topMargin=20*mm,
        bottomMargin=20*mm,
    )

    story = []
    story.extend(build_cover_page(report, styles))
    story.extend(parse_report_to_flowables(report.summary, styles))
    doc.build(story)
    print(f"✅ PDF generated: {output_path}")
    return output_path


# ─── CLI Test ─────────────────────────────────────────────────────────────────

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
