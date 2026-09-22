"""Invoice generator for the restaurant work.

    python invoices/build_invoice.py            # rebuilds every invoice in INVOICES

Add a new dict to INVOICES and re-run. Numbering is INV-<year>-<seq>.

`SENDER` and `CLIENT` below are the two parties. Keep the contact address in
step with the owner setup guide (scripts/build_owner_setup_guide.py) — the
client sees both documents, and two different addresses invites a bounced reply.
"""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

HERE = "/home/user/FuckEcosystemLockIn/invoices"

# --------------------------------------------------------------- who ------
# A supplier name is not optional: without one the client cannot use this as a
# supporting document for the expense. It does NOT have to be a personal name —
# but a trade name only belongs here if it is registered. In Quebec a sole
# proprietor operating under their own given name + surname is exempt from
# registering with the Registraire des entreprises; any other business name is
# not. So "Alejandro Monge" is always safe, and "The Compass Labs" is correct
# only if that entity is actually registered or incorporated.
SENDER = {
    "name": "Alejandro Monge",
    "lines": ["Montreal, QC", "a.naim2004@gmail.com"],
}

CLIENT = {
    "name": "1234-5678 Quebec Inc.",
    "lines": ["123 Example Street",
              "Montreal, QC  H0H 0H0",
              "owner@example.com"],
}

# Quebec's small-supplier threshold is $30,000 of taxable revenue over four
# consecutive quarters. Below it you are not registered and MUST NOT charge
# GST/QST. If that changes, set TAX to {"GST (5%)": 0.05, "QST (9.975%)": 0.09975}
# and put your registration numbers in TAX_NUMBERS — an invoice that charges
# these taxes without showing the numbers is not valid.
TAX: dict[str, float] = {}
TAX_NUMBERS: list[str] = []

INVOICES = [
    {
        "number": "INV-2026-001",
        "date": "31 July 2026",
        "status": "PAID",
        "paid_note": "Paid in full. No amount is outstanding &mdash; this document "
                     "is your receipt.",
        "subject": "AI phone assistant &mdash; development costs",
        "items": [
            ("AI development tooling",
             "Claude Max subscription, one month, including taxes. Used to build "
             "the bilingual phone assistant: reservation logic, the Libro "
             "integration, and the call-record dashboard.",
             160.00),
            ("Development time",
             "Design, implementation, testing and documentation. "
             "<b>Not charged.</b>",
             0.00),
        ],
        "footer": "The recurring running costs of the assistant (hosting, phone "
                  "number, speech and language services) are billed to you "
                  "directly by each provider, in your own name. They do not "
                  "appear on this invoice and carry no markup.",
    },
]

# ------------------------------------------------------------- styling ----
INK = colors.HexColor("#1a1a19")
DIM = colors.HexColor("#6b6b68")
ACCENT = colors.HexColor("#1f3864")
PAID = colors.HexColor("#1a7f4b")
RULE = colors.HexColor("#d8d7d3")
BAND = colors.HexColor("#f4f4f1")

ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                            fontSize=26, leading=29, textColor=INK,
                            alignment=TA_LEFT, spaceAfter=0),
    "meta": ParagraphStyle("m", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9.5, leading=14, textColor=DIM,
                           alignment=TA_RIGHT),
    "metab": ParagraphStyle("mb", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=9.5, leading=14, textColor=INK,
                            alignment=TA_RIGHT),
    "lbl": ParagraphStyle("l", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=7.5, leading=11, textColor=DIM, spaceAfter=3),
    "party": ParagraphStyle("pa", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=10.5, leading=14, textColor=INK),
    "partyl": ParagraphStyle("pl", parent=ss["Normal"], fontName="Helvetica",
                             fontSize=9.5, leading=13, textColor=INK),
    "subj": ParagraphStyle("sj", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=10.5, leading=14, textColor=DIM,
                           spaceAfter=14),
    "cellb": ParagraphStyle("cb", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=8, leading=11, textColor=INK),
    "cell": ParagraphStyle("c", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=9.5, leading=13, textColor=INK),
    "desc": ParagraphStyle("d", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=8.5, leading=12, textColor=DIM),
    "amt": ParagraphStyle("a", parent=ss["Normal"], fontName="Helvetica",
                          fontSize=9.5, leading=13, textColor=INK,
                          alignment=TA_RIGHT),
    "totl": ParagraphStyle("tl", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9.5, leading=14, textColor=INK,
                           alignment=TA_RIGHT),
    "totb": ParagraphStyle("tb", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=12, leading=17, textColor=INK,
                           alignment=TA_RIGHT),
    "small": ParagraphStyle("sm", parent=ss["Normal"], fontName="Helvetica",
                            fontSize=8, leading=11.5, textColor=DIM),
    "paid": ParagraphStyle("pd", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=11, leading=15, textColor=PAID),
}


def P(t, s="desc"):
    return Paragraph(t, S[s])


def money(v: float) -> str:
    return f"${v:,.2f}"


def build(inv: dict) -> str:
    out = f"{HERE}/{inv['number']}.pdf"
    doc = SimpleDocTemplate(
        out, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title=f"Invoice {inv['number']}", author=SENDER["name"],
    )
    st: list = []
    A = st.append

    # -- header: title left, invoice meta right ----------------------------
    meta = [P("<b>Invoice</b> " + inv["number"], "metab"),
            P(inv["date"], "meta")]
    if inv["status"] == "PAID":
        meta.append(P("PAID", "paid"))
    head = Table(
        [[[P("INVOICE", "title")], meta]],
        colWidths=[3.6 * inch, 3.4 * inch], hAlign="LEFT",
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    A(head)
    A(Spacer(1, 6))
    A(HRFlowable(width="100%", thickness=1.2, color=ACCENT,
                 spaceBefore=4, spaceAfter=16))

    # -- from / bill to ----------------------------------------------------
    def party(label, who):
        block = [P(label, "lbl"), P(who["name"], "party")]
        block += [P(x, "partyl") for x in who["lines"]]
        return block

    parties = Table(
        [[party("FROM", SENDER), party("BILL TO", CLIENT)]],
        colWidths=[3.4 * inch, 3.6 * inch], hAlign="LEFT",
    )
    parties.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    A(parties)
    A(Spacer(1, 22))

    A(P(inv["subject"], "subj"))

    # -- line items --------------------------------------------------------
    rows = [[P("DESCRIPTION", "cellb"), P("AMOUNT (CAD)", "cellb")]]
    for name, detail, amount in inv["items"]:
        rows.append([[P(name, "cell"), P(detail, "desc")],
                     P(money(amount), "amt")])
    t = Table(rows, colWidths=[5.35 * inch, 1.65 * inch], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, ACCENT),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
    ]))
    A(t)
    A(Spacer(1, 10))

    # -- totals ------------------------------------------------------------
    subtotal = sum(a for _, _, a in inv["items"])
    lines = [[P("Subtotal", "totl"), P(money(subtotal), "totl")]]
    total = subtotal
    for label, rate in TAX.items():
        amt = round(subtotal * rate, 2)
        total += amt
        lines.append([P(label, "totl"), P(money(amt), "totl")])
    if not TAX:
        lines.append([P("GST / QST", "totl"), P("Not applicable", "totl")])
    lines.append([P("<b>Total</b>", "totb"),
                  P(f"<b>{money(total)} CAD</b>", "totb")])

    tot = Table(lines, colWidths=[1.75 * inch, 1.5 * inch], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEABOVE", (0, -1), (-1, -1), 0.9, ACCENT),
        ("TOPPADDING", (0, -1), (-1, -1), 7),
    ]))
    A(tot)
    A(Spacer(1, 18))

    if inv["status"] == "PAID":
        A(P(inv["paid_note"], "small"))
    A(Spacer(1, 4))
    if not TAX:
        A(P("No GST or QST has been charged: the supplier is not registered for "
            "sales tax (Quebec small-supplier threshold).", "small"))
    for n in TAX_NUMBERS:
        A(P(n, "small"))
    A(Spacer(1, 8))
    A(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=8))
    A(P(inv["footer"], "small"))

    doc.build(st)
    return out


if __name__ == "__main__":
    for inv in INVOICES:
        print("wrote", build(inv))
