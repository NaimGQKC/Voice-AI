"""Client-facing progress + cost report for the restaurant."""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

OUT = "/home/user/FuckEcosystemLockIn/restaurant_voice_agent_report.pdf"

INK = colors.HexColor("#1a1a19")
DIM = colors.HexColor("#6b6b68")
ACCENT = colors.HexColor("#1f3864")
OK = colors.HexColor("#1a7f4b")
RULE = colors.HexColor("#d8d7d3")
BAND = colors.HexColor("#f4f4f1")

ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                            fontSize=21, leading=25, textColor=INK, alignment=TA_LEFT,
                            spaceAfter=2),
    "sub": ParagraphStyle("s", parent=ss["Normal"], fontName="Helvetica",
                          fontSize=10.5, leading=14, textColor=DIM, spaceAfter=16),
    "h": ParagraphStyle("h", parent=ss["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, leading=16, textColor=ACCENT,
                        spaceBefore=12, spaceAfter=6),
    "h2": ParagraphStyle("h2", parent=ss["Heading3"], fontName="Helvetica-Bold",
                         fontSize=11, leading=14, textColor=INK,
                         spaceBefore=11, spaceAfter=4),
    "p": ParagraphStyle("p", parent=ss["Normal"], fontName="Helvetica",
                        fontSize=10, leading=14, textColor=INK, spaceAfter=6),
    "small": ParagraphStyle("sm", parent=ss["Normal"], fontName="Helvetica",
                            fontSize=8.5, leading=12, textColor=DIM, spaceAfter=5),
    "cell": ParagraphStyle("c", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9, leading=12.5, textColor=INK),
    "cellb": ParagraphStyle("cb", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=9, leading=12.5, textColor=INK),
    "cellsm": ParagraphStyle("cs", parent=ss["Normal"], fontName="Helvetica",
                             fontSize=8, leading=11, textColor=DIM),
}


def P(t, s="p"):
    return Paragraph(t, S[s])


def rule():
    return HRFlowable(width="100%", thickness=0.6, color=RULE,
                      spaceBefore=6, spaceAfter=10)


def table(rows, widths, header=True, align_right=(), band_last=False):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), BAND),
                  ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT)]
    for c in align_right:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    if band_last:
        style += [("BACKGROUND", (0, -1), (-1, -1), BAND),
                  ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT)]
    t.setStyle(TableStyle(style))
    return t


story = []
A = story.append

# ---------------------------------------------------------------- cover ----
A(P("the restaurant", "title"))
A(P("AI phone assistant &mdash; progress report and cost breakdown<br/>"
    "Prepared 29 July 2026", "sub"))
A(rule())

A(P("Summary", "h"))
A(P("The assistant answers the phone in French, understands English, and books "
    "real tables directly in your Libro account. It has been tested end to end "
    "and books correctly.", "p"))
A(P("Running it costs roughly <b>$20 CAD per month</b>, against the "
    "<b>$275 CAD per month</b> currently being paid to the existing provider "
    "&mdash; a saving of about <b>$3,000 CAD per year</b>.", "p"))

A(Spacer(1, 8))
A(table([
    [P("", "cellb"), P("Today", "cellb"), P("Proposed", "cellb")],
    [P("Monthly running cost", "cell"), P("~$275 CAD", "cell"),
     P("<b>~$20 CAD</b>", "cell")],
    [P("Calls that reach a booking", "cell"), P("15%", "cell"),
     P("to be measured", "cell")],
    [P("Calls handed to a human", "cell"), P("43%", "cell"),
     P("to be measured", "cell")],
    [P("Takeout callers", "cell"), P("transferred or told<br/>to use the website", "cell"),
     P("handled &mdash; see page 3", "cell")],
    [P("Cancellations by phone", "cell"), P("not possible", "cell"),
     P("automatic", "cell")],
    [P("Who owns the call data", "cell"), P("the provider", "cell"),
     P("<b>you do</b>", "cell")],
], [2.25 * inch, 1.9 * inch, 2.2 * inch], align_right=()))

A(Spacer(1, 6))
A(P("The 15% and 43% figures are measured from 84 real calls to your line "
    "between 30 June and 25 July 2026, taken from the current provider's own "
    "records.", "small"))

# ------------------------------------------------------------- progress ----
A(P("What is built and working", "h"))

A(table([
    [P("Capability", "cellb"), P("Status", "cellb")],
    [P("Answers in French, switches to English if the caller does", "cell"),
     P("Working", "cell")],
    [P("Checks live availability in Libro", "cell"), P("Working", "cell")],
    [P("Creates, changes and cancels real reservations", "cell"),
     P("Working &mdash; verified on a live booking", "cell")],
    [P("Reads the phone number back before booking; understands "
       "&ldquo;demain soir&rdquo;, &ldquo;sept heures&rdquo;, "
       "&ldquo;vendredi prochain&rdquo;", "cell"), P("Working", "cell")],
    [P("Offers alternatives when the requested time is full", "cell"),
     P("Working", "cell")],
    [P("Handles takeout callers (~1 call in 6)", "cell"),
     P("Working &mdash; method under review, page 3", "cell")],
    [P("Answers hours, address, parking, dietary; never quotes prices", "cell"),
     P("Working", "cell")],
    [P("Passes groups of 7+ to a person (Libro cannot hold those tables)", "cell"),
     P("Working", "cell")],
    [P("Dashboard showing every call, outcome and transcript", "cell"),
     P("Working", "cell")],
    [P("Answering a real phone number", "cell"),
     P("<b>Next step</b> &mdash; needs the accounts below", "cell")],
    [P("Live transfer to a staff member", "cell"),
     P("<b>Next</b> &mdash; details on page 3", "cell")],
], [4.0 * inch, 2.35 * inch]))

A(PageBreak())

# ----------------------------------------------------------------- costs ---
A(P("Costs", "title"))
A(P("All amounts in Canadian dollars. Converted from USD at 1.37, which should "
    "be confirmed against the rate on the day of payment.", "sub"))

A(P("One-time &mdash; development", "h"))
A(table([
    [P("Item", "cellb"), P("Detail", "cellb"), P("Cost", "cellb")],
    [P("AI development tooling", "cell"),
     P("Claude Max subscription, one month, including taxes &mdash; used to "
       "build the system", "cellsm"),
     P("$160", "cell")],
    [P("Development time", "cell"),
     P("Not charged", "cellsm"), P("$0", "cell")],
    [P("<b>One-time total</b>", "cellb"), P("", "cell"), P("<b>$160</b>", "cellb")],
], [1.9 * inch, 3.25 * inch, 1.2 * inch], align_right=(2,), band_last=True))

A(Spacer(1, 4))
A(P("A cost already incurred to build the system. It does not repeat. "
    "Development time is not being charged.", "small"))

A(P("Recurring &mdash; monthly running cost", "h"))
A(table([
    [P("Service", "cellb"), P("What it does", "cellb"), P("Monthly", "cellb")],
    [P("Server hosting", "cell"),
     P("Runs the assistant, always on, in Toronto (Fly.io)", "cellsm"),
     P("$9.80", "cell")],
    [P("Phone number", "cell"),
     P("Montreal 514 number ($1.15/mo) plus inbound minutes (Twilio)", "cellsm"),
     P("$2.90", "cell")],
    [P("Speech recognition", "cell"),
     P("Understanding the caller, French and English (Deepgram)", "cellsm"),
     P("$1.20", "cell")],
    [P("Voice", "cell"),
     P("The French voice the caller hears (Cartesia, billed via LiveKit)", "cellsm"),
     P("$1.80", "cell")],
    [P("Language model", "cell"),
     P("Understanding what the caller wants (Google Gemini)", "cellsm"),
     P("$1.50", "cell")],
    [P("Call records database", "cell"),
     P("Stored in Canada; free at this volume (Supabase)", "cellsm"),
     P("$0.00", "cell")],
    [P("Call handling", "cell"),
     P("Connects the phone line; free at this volume (LiveKit)", "cellsm"),
     P("$0.00", "cell")],
    [P("Subtotal", "cell"), P("", "cell"), P("$17.20", "cell")],
    [P("Contingency (15%)", "cell"),
     P("Rate changes, busier months, anything unforeseen", "cellsm"),
     P("$2.60", "cell")],
    [P("<b>Recurring total</b>", "cellb"), P("", "cell"),
     P("<b>~$20 / month</b>", "cellb")],
], [1.9 * inch, 3.25 * inch, 1.2 * inch], align_right=(2,), band_last=True))

A(Spacer(1, 4))
A(P("Based on your measured volume: 102 calls and 111 minutes per month. Most "
    "of the total is fixed cost, so it changes very little as calls increase.",
    "small"))

A(P("Comparison", "h"))
A(table([
    [P("", "cellb"), P("Per month", "cellb"), P("Per year", "cellb")],
    [P("Current provider", "cell"), P("~$275", "cell"), P("~$3,300", "cell")],
    [P("Proposed", "cell"), P("~$20", "cell"), P("~$240", "cell")],
    [P("<b>Saving</b>", "cellb"), P("<b>~$255</b>", "cellb"),
     P("<b>~$3,060</b>", "cellb")],
], [2.4 * inch, 1.85 * inch, 1.85 * inch], align_right=(1, 2), band_last=True))

A(Spacer(1, 6))
A(P("Every account is in the restaurant's name and each provider bills you "
    "directly. No markup on any figure above.", "small"))

A(PageBreak())

# ------------------------------------------------------------- next -------
A(P("What happens next", "title"))
A(P("Two things are needed from you, and one decision.", "sub"))

A(P("1. Accounts to open", "h"))
A(P("Each is opened in the restaurant's name, so you own it outright. Setup is "
    "about half a day in total; none need attention afterwards.", "p"))
A(table([
    [P("Account", "cellb"), P("Why", "cellb"), P("Card needed", "cellb")],
    [P("Hosting (Fly.io)", "cell"), P("Runs the assistant", "cellsm"),
     P("Yes", "cell")],
    [P("Phone (Twilio)", "cell"), P("The new 514 number", "cellsm"),
     P("Yes", "cell")],
    [P("Google AI", "cell"), P("Understanding callers", "cellsm"),
     P("Yes &mdash; free tier is not reliable enough for a live line", "cellsm")],
    [P("Deepgram", "cell"), P("Speech recognition and voice", "cellsm"),
     P("Already open", "cell")],
    [P("LiveKit", "cell"), P("Connects the phone line", "cellsm"),
     P("Already open", "cell")],
    [P("Supabase", "cell"), P("Call records, stored in Canada", "cellsm"),
     P("No", "cell")],
], [1.6 * inch, 2.2 * inch, 2.55 * inch]))

A(P("2. A new phone number, not your existing one", "h"))
A(P("The assistant answers a brand-new 514 number. Your existing line, "
    "514-555-0100, is untouched and rings in the restaurant exactly as it does "
    "today. Nothing changes for your current callers until you decide to point "
    "them at it.", "p"))

A(P("3. Escalation &mdash; confirming the details", "h"))
A(P("Agreed and clear: <b>when the assistant cannot handle something, it "
    "transfers the caller to a person</b> during opening hours. Outside hours it "
    "gives them the email address instead. That is what will be built.", "p"))
A(P("Two details to settle, because they change what gets built:", "p"))
A(table([
    [P("Question", "cellb"), P("Why it matters", "cellb")],
    [P("If the transfer rings out, what then?", "cell"),
     P("Take their name and number so you can call back, or simply let it ring? "
       "Either is fine &mdash; we just need to pick one.", "cellsm")],
    [P("Takeout &mdash; transfer, or take the details?", "cell"),
     P("About one call in six. Transferring rings the restaurant during "
       "service; taking the details lets someone call back when it suits.",
       "cellsm")],
], [2.3 * inch, 4.05 * inch]))

A(P("Timeline", "h"))
A(table([
    [P("Stage", "cellb"), P("What it involves", "cellb")],
    [P("Now", "cell"), P("Testing the conversation in every scenario", "cell")],
    [P("Next", "cell"),
     P("Open the accounts above; connect the phone number", "cell")],
    [P("Then", "cell"),
     P("You and I call the test number and listen to it work", "cell")],
    [P("Go live", "cell"),
     P("Start with overflow only &mdash; it answers when nobody else can", "cell")],
], [1.2 * inch, 5.15 * inch]))

A(Spacer(1, 10))
A(P("On the figures: running costs are provider list prices applied to your "
    "measured call volume, and none has appeared on a real invoice yet &mdash; "
    "treat the first month as the number to confirm. The $275 comparison comes "
    "from your current provider's invoice.", "small"))

SimpleDocTemplate(
    OUT, pagesize=LETTER,
    leftMargin=0.85 * inch, rightMargin=0.85 * inch,
    topMargin=0.7 * inch, bottomMargin=0.6 * inch,
    title="the restaurant — AI phone assistant",
    author="",
).build(story)
print("wrote", OUT)
