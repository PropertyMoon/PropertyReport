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
from reportlab.graphics.shapes import Drawing, String, Line, Rect
from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart


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
    metrics = report.metrics if isinstance(getattr(report, "metrics", None), dict) else {}
    last_sale = metrics.get("last_sale_price", "Not on record")

    data = [
        ["Report Date", today],
        ["Market",      state["label"]],
        ["Last Sale",   last_sale],
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
    "property snapshot": ("🏠", ),
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


# ─── Section Visuals (charts, tables, panels injected after H2 headers) ──────

def _format_price_compact(p) -> str:
    if p is None:
        return "—"
    try:
        n = float(str(p).replace("$", "").replace(",", "").strip())
        if n >= 1_000_000:
            return f"${n/1_000_000:.2f}M".rstrip("0").rstrip(".")
        if n >= 1_000:
            return f"${n/1_000:.0f}K"
        return f"${n:,.0f}"
    except (ValueError, TypeError):
        return str(p)[:14]


def _format_amenity(e) -> str | None:
    if not isinstance(e, dict):
        return None
    name = (e.get("name") or "").strip()
    dist = e.get("distance_km")
    if not name:
        return None
    if isinstance(dist, (int, float)):
        return f"{name}  <font color='#94a3b8'>· {dist:.1f}km</font>"
    return name


def build_amenities_panel(report, styles: dict) -> list:
    suburb = report.suburb if isinstance(getattr(report, "suburb", None), dict) else {}
    freeway = _format_amenity(suburb.get("nearest_freeway") or {})
    gps = [a for a in (_format_amenity(g) for g in (suburb.get("nearby_gps") or [])[:2]) if a]
    hospitals = [a for a in (_format_amenity(h) for h in (suburb.get("nearby_hospitals") or [])[:3]) if a]

    if not freeway and not gps and not hospitals:
        return []

    label_style = ParagraphStyle("amenity_lbl", fontSize=7, fontName="Helvetica-Bold",
                                 textColor=MID_GREY, leading=10, alignment=TA_CENTER)
    body_style  = ParagraphStyle("amenity_body", fontSize=9, fontName="Helvetica",
                                 textColor=TEXT_DARK, leading=13, alignment=TA_CENTER)

    cells = [
        [Paragraph("NEAREST FREEWAY", label_style),
         Paragraph("NEARBY GPs",      label_style),
         Paragraph("HOSPITALS",       label_style)],
        [Paragraph(freeway or "—",       body_style),
         Paragraph("<br/>".join(gps) or "—",       body_style),
         Paragraph("<br/>".join(hospitals) or "—", body_style)],
    ]
    t = Table(cells, colWidths=[60*mm, 60*mm, 60*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  LIGHT_GREY),
        ("BACKGROUND",    (0,1), (-1,1),  WHITE),
        ("BOX",           (0,0), (-1,-1), 0.5, BORDER_GREY),
        ("INNERGRID",     (0,0), (-1,-1), 0.5, BORDER_GREY),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ]))
    return [t, Spacer(1, 4*mm)]


def build_school_chart(report, styles: dict) -> list:
    schools = report.schools if isinstance(getattr(report, "schools", None), dict) else {}
    entries = []
    for tier, key in (("Pri", "primary_schools"), ("Sec", "secondary_schools")):
        for s in (schools.get(key) or [])[:4]:
            if not isinstance(s, dict):
                continue
            icsea = s.get("icsea")
            if isinstance(icsea, bool) or not isinstance(icsea, (int, float)):
                continue
            if not (800 <= float(icsea) <= 1300):
                continue
            name = (s.get("name") or "").strip()
            if not name:
                continue
            entries.append((f"{name[:28]} ({tier})", int(icsea)))

    if not entries:
        return []

    entries = entries[:6]
    names  = [e[0] for e in entries]
    scores = [e[1] for e in entries]

    bar_h    = 14
    chart_h  = max(50, len(scores) * bar_h + 18)
    drawing  = Drawing(160*mm, chart_h + 16)

    chart = HorizontalBarChart()
    chart.x = 38*mm
    chart.y = 14
    chart.width  = 100*mm
    chart.height = chart_h - 18
    chart.data   = [scores]
    chart.categoryAxis.categoryNames    = names
    chart.categoryAxis.labels.fontSize  = 7
    chart.categoryAxis.labels.fillColor = TEXT_DARK
    chart.valueAxis.valueMin  = 850
    chart.valueAxis.valueMax  = 1200
    chart.valueAxis.valueStep = 50
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.labels.fillColor = MID_GREY
    chart.bars[0].fillColor   = GOLD
    chart.bars.strokeColor    = None
    chart.barSpacing = 2
    drawing.add(chart)

    # National-average reference line at 1000
    ref_x = chart.x + (1000 - 850) / (1200 - 850) * chart.width
    drawing.add(Line(ref_x, chart.y, ref_x, chart.y + chart.height,
                     strokeColor=colors.HexColor("#475569"),
                     strokeDashArray=[2, 2], strokeWidth=0.7))
    drawing.add(String(ref_x + 3, chart.y + chart.height + 2,
                       "Nat. avg (1000)",
                       fontSize=6, fillColor=colors.HexColor("#475569")))

    caption = ParagraphStyle("cap", fontSize=8, fontName="Helvetica-Oblique",
                             textColor=MID_GREY, spaceAfter=1*mm)
    return [
        Paragraph("ICSEA — Index of Community Socio-Educational Advantage (higher = stronger profile)", caption),
        drawing,
        Spacer(1, 3*mm),
    ]


def build_comparables_table(report, styles: dict) -> list:
    market = report.property_market if isinstance(getattr(report, "property_market", None), dict) else {}
    raw_sales = market.get("comparable_sales") or market.get("recent_sales") or []
    sales = [s for s in raw_sales if isinstance(s, dict)][:2]
    if not sales:
        return []

    rows = [["Address", "Sold", "Price", "Beds · Baths · Land"]]
    for s in sales:
        addr  = (s.get("address") or "").strip()[:40] or "—"
        date  = str(s.get("sale_date") or s.get("date") or "—")[:14]
        price = _format_price_compact(s.get("sale_price") or s.get("price"))
        beds  = s.get("bedrooms");  baths = s.get("bathrooms");  land = s.get("land_sqm")
        meta  = []
        if isinstance(beds,  (int, float)) and not isinstance(beds,  bool): meta.append(f"{int(beds)}br")
        if isinstance(baths, (int, float)) and not isinstance(baths, bool): meta.append(f"{int(baths)}ba")
        if isinstance(land,  (int, float)) and not isinstance(land,  bool): meta.append(f"{int(land)}m²")
        rows.append([addr, date, price, " · ".join(meta) or "—"])

    t = Table(rows, colWidths=[72*mm, 28*mm, 26*mm, 54*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0),  NAVY),
        ("TEXTCOLOR",     (0,0), (-1,0),  WHITE),
        ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 8.5),
        ("BACKGROUND",    (0,1), (-1,-1), LIGHT_GREY),
        ("ALIGN",         (0,0), (0,-1),  "LEFT"),
        ("ALIGN",         (1,0), (-1,-1), "LEFT"),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("GRID",          (0,0), (-1,-1), 0.4, BORDER_GREY),
    ]))
    caption = ParagraphStyle("cap", fontSize=8, fontName="Helvetica-Oblique",
                             textColor=MID_GREY, spaceAfter=2*mm)
    return [
        Paragraph("Recent comparable sales — same suburb, similar property", caption),
        t,
        Spacer(1, 4*mm),
    ]


def build_crime_chart(report, styles: dict) -> list:
    suburb = report.suburb if isinstance(getattr(report, "suburb", None), dict) else {}
    pct     = suburb.get("crime_safety_percentile")
    violent = suburb.get("crime_violent_vs_state_avg_pct")
    prop    = suburb.get("crime_property_vs_state_avg_pct")

    have_pct = isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100
    deltas = []
    for label, val in (("Violent crime", violent), ("Property crime", prop)):
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            deltas.append((label, int(val)))
    if not have_pct and not deltas:
        return []

    items = []

    if have_pct:
        pct_val = float(pct)
        if   pct_val < 30: fill, zone = RED,    "Higher-crime area"
        elif pct_val < 60: fill, zone = ORANGE, "Average safety"
        else:              fill, zone = GREEN,  "Safer than most"

        bar_full_w = 140*mm
        drawing = Drawing(170*mm, 18*mm)
        drawing.add(String(0, 13*mm,
                           f"Safety percentile — {zone}",
                           fontSize=9, fontName="Helvetica-Bold", fillColor=NAVY))
        drawing.add(Rect(0, 5*mm, bar_full_w, 5*mm,
                         fillColor=LIGHT_GREY, strokeColor=BORDER_GREY, strokeWidth=0.4))
        drawing.add(Rect(0, 5*mm, bar_full_w * pct_val / 100, 5*mm,
                         fillColor=fill, strokeColor=None))
        for tick in (25, 50, 75):
            x = bar_full_w * tick / 100
            drawing.add(Line(x, 4*mm, x, 5*mm, strokeColor=MID_GREY, strokeWidth=0.4))
            drawing.add(String(x, 1.5*mm, str(tick),
                               fontSize=6, fillColor=MID_GREY, textAnchor="middle"))
        drawing.add(String(bar_full_w + 4, 5.5*mm, f"{int(pct_val)}/100",
                           fontSize=10, fontName="Helvetica-Bold", fillColor=NAVY))
        items.append(drawing)
        items.append(Spacer(1, 1*mm))

    if deltas:
        rows = []
        for label, val in deltas:
            sign  = "+" if val > 0 else ""
            color = "#c0392b" if val > 5 else "#059669" if val < -5 else "#475569"
            rows.append([
                Paragraph(label, ParagraphStyle("cd_l", fontSize=9, fontName="Helvetica",
                                                textColor=TEXT_DARK)),
                Paragraph(f'<font color="{color}"><b>{sign}{val}%</b> vs state average</font>',
                          ParagraphStyle("cd_v", fontSize=9, fontName="Helvetica",
                                         textColor=TEXT_MID)),
            ])
        dt = Table(rows, colWidths=[55*mm, 105*mm])
        dt.setStyle(TableStyle([
            ("TOPPADDING",    (0,0), (-1,-1), 3),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
            ("LEFTPADDING",   (0,0), (-1,-1), 0),
            ("RIGHTPADDING",  (0,0), (-1,-1), 0),
            ("LINEBELOW",     (0,0), (-1,-2), 0.3, BORDER_GREY),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]))
        items.append(dt)

    items.append(Spacer(1, 4*mm))
    return items


def build_property_snapshot(report, styles: dict) -> list:
    intel = report.property_intel if isinstance(getattr(report, "property_intel", None), dict) else {}
    if not intel:
        return []

    def _num(key, suffix=""):
        v = intel.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        n = float(v)
        return f"{int(n)}{suffix}" if n == int(n) else f"{n:.1f}{suffix}"

    def _str(key):
        v = intel.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    def _potential(key):
        raw = intel.get(key)
        if isinstance(raw, dict):
            rating = raw.get("rating") or raw.get("level") or raw.get("score")
            reason = raw.get("reason") or raw.get("description") or raw.get("note")
            if rating:
                return f"<b>{rating}</b> — {reason}" if reason else f"<b>{rating}</b>"
        elif isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    land    = _num("land_sqm", " m²")
    dwell   = _str("dwelling_type")
    front   = _num("frontage_m", " m")
    beds    = _num("bedrooms")
    baths   = _num("bathrooms")
    parking = _num("parking")
    year    = _num("year_built")
    zone    = " — ".join(filter(None, [_str("zoning_code"), _str("zoning_description")]))
    corner  = "Yes" if intel.get("corner_block") is True else None
    street  = _num("street_position_quality", " / 10")

    config = " · ".join(filter(None, [
        f"{beds}br"   if beds    else None,
        f"{baths}ba"  if baths   else None,
        f"{parking}c" if parking else None,
    ])) or None

    rows: list = []
    def _add(label, value):
        if value: rows.append([label, value])

    _add("Land Size",      land)
    _add("Dwelling Type",  dwell)
    _add("Frontage",       front)
    _add("Configuration",  config)
    _add("Year Built",     year)
    _add("Corner Block",   corner)
    _add("Zoning",         zone or None)
    _add("Subdivision Potential",       _potential("subdivision_potential"))
    _add("Development Feasibility",     _potential("development_feasibility"))
    _add("Renovation Potential",        _potential("renovation_potential"))
    _add("Knockdown / Rebuild",         _potential("knockdown_rebuild_viability"))
    _add("Street Position",             street)

    if not rows:
        return []

    body_style = ParagraphStyle("psnap_body", fontSize=9, fontName="Helvetica",
                                textColor=TEXT_MID, leading=13)
    rendered = [[r[0], Paragraph(r[1], body_style)] for r in rows]

    t = Table(rendered, colWidths=[58*mm, 122*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (0,-1),  LIGHT_BLUE),
        ("FONTNAME",      (0,0), (0,-1),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 9),
        ("TEXTCOLOR",     (0,0), (0,-1),  NAVY),
        ("TOPPADDING",    (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("GRID",          (0,0), (-1,-1), 0.4, BORDER_GREY),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    caption = ParagraphStyle("cap", fontSize=8, fontName="Helvetica-Oblique",
                             textColor=MID_GREY, spaceAfter=2*mm)
    return [
        Paragraph("Subject-property data — confirm via Section 32 / contract of sale", caption),
        t,
        Spacer(1, 4*mm),
    ]


def build_growth_chart(report, styles: dict) -> list:
    suburb = report.suburb if isinstance(getattr(report, "suburb", None), dict) else {}
    history = suburb.get("price_history_5yr") or []
    points = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        yr    = entry.get("year") or entry.get("date")
        price = entry.get("median_house_price") or entry.get("median_price") or entry.get("price")
        if yr is None or price is None:
            continue
        try:
            pr = float(str(price).replace("$", "").replace(",", "").strip())
            yi = int(str(yr)[:4])
        except (ValueError, TypeError):
            continue
        if pr <= 0 or yi < 2000 or yi > 2100:
            continue
        points.append((yi, pr))
    points = sorted(set(points))[-6:]
    if len(points) < 2:
        return []

    years  = [str(p[0]) for p in points]
    prices = [p[1] for p in points]
    pmin, pmax = min(prices), max(prices)
    delta = pmax - pmin if pmax > pmin else max(pmax * 0.05, 1.0)

    drawing = Drawing(170*mm, 64*mm)
    chart = VerticalBarChart()
    chart.x = 22*mm
    chart.y = 12*mm
    chart.width  = 140*mm
    chart.height = 38*mm
    chart.data   = [prices]
    chart.categoryAxis.categoryNames    = years
    chart.categoryAxis.labels.fontSize  = 8
    chart.categoryAxis.labels.fillColor = TEXT_DARK
    chart.valueAxis.valueMin = max(0, pmin - delta * 0.25)
    chart.valueAxis.valueMax = pmax + delta * 0.15
    chart.valueAxis.labels.fontSize  = 7
    chart.valueAxis.labels.fillColor = MID_GREY
    chart.valueAxis.labelTextFormat = lambda v: (
        f"${v/1_000_000:.1f}M" if v >= 1_000_000 else f"${v/1000:.0f}K"
    )
    chart.bars[0].fillColor = GOLD
    chart.bars.strokeColor  = None
    chart.barSpacing = 4
    drawing.add(chart)
    drawing.add(String(22*mm, 56*mm, "Median House Price — recent annual history",
                       fontSize=9, fontName="Helvetica-Bold", fillColor=NAVY))

    pct = ((prices[-1] - prices[0]) / prices[0] * 100) if prices[0] else 0
    direction_color = "#059669" if pct >= 0 else "#c0392b"
    sign = "+" if pct >= 0 else ""
    drawing.add(String(22*mm, 50*mm,
                       f"{years[0]} → {years[-1]}: {sign}{pct:.1f}% total",
                       fontSize=7.5, fillColor=colors.HexColor(direction_color)))

    return [drawing, Spacer(1, 3*mm)]


def build_score_breakdown(report, styles: dict) -> list:
    scores = report.scores if isinstance(getattr(report, "scores", None), dict) else {}
    factors = [
        ("Growth Potential",   "growth_potential"),
        ("Rental Demand",      "rental_demand"),
        ("Infrastructure",     "infrastructure"),
        ("Safety",             "safety"),
        ("Family Suitability", "family_suitability"),
    ]
    bars = [(label, float(scores[key])) for label, key in factors
            if isinstance(scores.get(key), (int, float)) and not isinstance(scores.get(key), bool)]
    overall = scores.get("overall")
    have_overall = isinstance(overall, (int, float)) and not isinstance(overall, bool)
    if not bars and not have_overall:
        return []

    items: list = []

    if have_overall:
        hero_style = ParagraphStyle("score_hero", fontSize=11, fontName="Helvetica",
                                    textColor=MID_GREY, alignment=TA_CENTER, leading=14)
        items.append(Paragraph(
            f'<font size="36" color="#1e293b"><b>{float(overall):.1f}</b></font>'
            f'<font size="16" color="#94a3b8"> / 10</font>',
            ParagraphStyle("score_value", fontSize=36, fontName="Helvetica-Bold",
                           textColor=NAVY, alignment=TA_CENTER, leading=42)
        ))
        items.append(Paragraph("Overall PropertyReport Score — weighted across 5 factors", hero_style))
        items.append(Spacer(1, 4*mm))

    if bars:
        bar_max_w = 90 * mm
        rows = []
        for label, val in bars:
            if   val >= 7: fill = GREEN
            elif val >= 5: fill = ORANGE
            else:          fill = RED
            d = Drawing(bar_max_w + 16*mm, 12)
            d.add(Rect(0, 4, bar_max_w, 6,
                       fillColor=LIGHT_GREY, strokeColor=BORDER_GREY, strokeWidth=0.3))
            d.add(Rect(0, 4, bar_max_w * (val / 10.0), 6,
                       fillColor=fill, strokeColor=None))
            d.add(String(bar_max_w + 4, 3.5,
                         f"{val:.1f}",
                         fontSize=9, fontName="Helvetica-Bold", fillColor=NAVY))
            rows.append([
                Paragraph(label, ParagraphStyle("sb_lbl", fontSize=9, fontName="Helvetica",
                                                textColor=TEXT_DARK)),
                d,
            ])
        t = Table(rows, colWidths=[55*mm, bar_max_w + 18*mm])
        t.setStyle(TableStyle([
            ("TOPPADDING",    (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 0),
            ("RIGHTPADDING",  (0,0), (-1,-1), 0),
            ("LINEBELOW",     (0,0), (-1,-2), 0.3, BORDER_GREY),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]))
        items.append(t)
        items.append(Spacer(1, 4*mm))

    return items


def _section_visual(heading: str, report, styles: dict) -> list:
    """Dispatch to the right visual builder for a given H2 heading."""
    lower = heading.lower()
    if "property snapshot" in lower:
        return build_property_snapshot(report, styles)
    if "suburb profile" in lower:
        return build_amenities_panel(report, styles)
    if "school" in lower:
        return build_school_chart(report, styles)
    if "market analysis" in lower or "property market" in lower:
        return build_growth_chart(report, styles) + build_comparables_table(report, styles)
    if "risk" in lower:
        return build_crime_chart(report, styles)
    if "verdict" in lower:
        return build_score_breakdown(report, styles)
    return []


def parse_report_to_flowables(report, styles: dict) -> list:
    summary = report.summary if hasattr(report, "summary") else str(report)
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
            flowables.extend(_section_visual(heading_text, report, styles))

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
    story.extend(parse_report_to_flowables(report, styles))
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

    rd = data["research_data"]
    report = PropertyReport(
        address=data["address"],
        suburb=rd.get("suburb", {}),
        schools=rd.get("schools", {}),
        government_projects=rd.get("government_projects", {}),
        transport=rd.get("transport", {}),
        property_market=rd.get("property_market", {}),
        risk_overlays=rd.get("risk_overlays", {}),
        summary=data["summary"],
        property_intel=rd.get("property_intel", {}),
    )
    report.scores  = data.get("scores",  {})
    report.metrics = data.get("metrics", {})
    generate_pdf(report, "test_report.pdf")
