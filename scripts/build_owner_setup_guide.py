"""Owner-facing setup guide: the accounts the venue opens so the venue owns the billing.

    python scripts/build_owner_setup_guide.py

Audience is the restaurant owner, not a developer. Plain language, no jargon,
no shell commands. Every account is opened in the restaurant's name so the
restaurant owns it outright — that is the entire point of the document.

Facts here are kept in step with docs/DEPLOY.md, .env.example and
scripts/build_client_report.py. The report is the source of truth for dollars;
if a number moves there, move it here too.
"""

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle,
)

OUT = "/home/user/FuckEcosystemLockIn/owner_setup_guide.pdf"

INK = colors.HexColor("#1a1a19")
DIM = colors.HexColor("#6b6b68")
ACCENT = colors.HexColor("#1f3864")
WARN = colors.HexColor("#9a3412")
OK = colors.HexColor("#1a7f4b")
RULE = colors.HexColor("#d8d7d3")
BAND = colors.HexColor("#f4f4f1")
WARNBAND = colors.HexColor("#fdf3ec")

ss = getSampleStyleSheet()
S = {
    "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                            fontSize=21, leading=25, textColor=INK,
                            alignment=TA_LEFT, spaceAfter=2),
    "sub": ParagraphStyle("s", parent=ss["Normal"], fontName="Helvetica",
                          fontSize=10.5, leading=14.5, textColor=DIM,
                          spaceAfter=14),
    "h": ParagraphStyle("h", parent=ss["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, leading=16, textColor=ACCENT,
                        spaceBefore=12, spaceAfter=6),
    "acct": ParagraphStyle("ac", parent=ss["Heading2"], fontName="Helvetica-Bold",
                           fontSize=12.5, leading=15, textColor=INK,
                           spaceBefore=0, spaceAfter=1),
    "acctsub": ParagraphStyle("as", parent=ss["Normal"], fontName="Helvetica",
                              fontSize=9, leading=12, textColor=DIM,
                              spaceAfter=0),
    "num": ParagraphStyle("n", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=17, leading=20, textColor=ACCENT),
    "p": ParagraphStyle("p", parent=ss["Normal"], fontName="Helvetica",
                        fontSize=10, leading=14, textColor=INK, spaceAfter=6),
    "step": ParagraphStyle("st", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9.5, leading=13.5, textColor=INK),
    "stepn": ParagraphStyle("sn", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=9.5, leading=13.5, textColor=DIM),
    "small": ParagraphStyle("sm", parent=ss["Normal"], fontName="Helvetica",
                            fontSize=8.5, leading=12, textColor=DIM,
                            spaceAfter=5),
    "warn": ParagraphStyle("w", parent=ss["Normal"], fontName="Helvetica",
                           fontSize=9.5, leading=13, textColor=INK),
    "warnh": ParagraphStyle("wh", parent=ss["Normal"], fontName="Helvetica-Bold",
                            fontSize=8, leading=11, textColor=WARN,
                            spaceAfter=2),
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
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
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


def callout(label, body, tone="warn"):
    """A boxed note with a coloured bar down the left edge."""
    bar, bg = (WARN, WARNBAND) if tone == "warn" else (ACCENT, BAND)
    inner = [P(label, "warnh")] if label else []
    inner.append(Paragraph(body, ParagraphStyle(
        "cw", parent=S["warn"], textColor=INK)))
    t = Table([[inner]], colWidths=[6.9 * inch], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, bar),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def account(n, name, tagline, cost, card, steps, note=None, note_tone="warn"):
    """One numbered account section, kept on a single page where it fits."""
    head = Table(
        [[P(str(n), "num"),
          [P(name, "acct"), P(tagline, "acctsub")],
          [P(f"<b>{cost}</b>", "cell"), P(card, "cellsm")]]],
        colWidths=[0.34 * inch, 4.45 * inch, 2.11 * inch], hAlign="LEFT",
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (1, 0), "TOP"),
        ("VALIGN", (2, 0), (2, 0), "TOP"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    rows = [[P(f"{i}.", "stepn"), P(s, "step")]
            for i, s in enumerate(steps, 1)]
    body = Table(rows, colWidths=[0.28 * inch, 6.62 * inch], hAlign="LEFT")
    body.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 6),
        ("LEFTPADDING", (1, 0), (1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))

    block = [head, HRFlowable(width="100%", thickness=0.6, color=RULE,
                              spaceBefore=1, spaceAfter=7), body]
    if note:
        block += [Spacer(1, 7), callout(None, note, note_tone)]
    block.append(Spacer(1, 11))
    return KeepTogether(block)


story = []
A = story.append

# ================================================================== cover ===
A(P("the restaurant", "title"))
A(P("AI phone assistant &mdash; the accounts to open<br/>"
    "A step-by-step guide. Prepared 12 August 2026.", "sub"))
A(rule())

A(P("What this is", "h"))
A(P("The assistant needs six services to run. This guide walks you through "
    "opening each one, in order, with what to click on every screen.", "p"))
A(P("<b>You open all six in the restaurant&rsquo;s name, on the restaurant&rsquo;s "
    "card.</b> Each company bills you directly. Nothing passes through me, and "
    "there is no markup on any of it. If you ever want to stop, you close the "
    "accounts yourself &mdash; you do not need my permission, and you do not "
    "need to ask me for your data.", "p"))
A(P("Total running cost is about <b>$20 CAD per month</b>, against the "
    "<b>$275</b> you pay today.", "p"))

A(Spacer(1, 6))
A(callout("BEFORE YOU START",
          "Set aside about <b>one hour</b>. Have ready: the restaurant&rsquo;s "
          "<b>credit card</b>, the legal business name and address "
          "(<b>1234-5678 Quebec Inc., 123 Example Street</b>), and an email "
          "address you want the bills to go to. Use the <b>same email for all "
          "six</b> &mdash; it makes them far easier to keep track of.",
          tone="info"))

A(Spacer(1, 14))
A(P("The six accounts", "h"))
A(table([
    [P("#", "cellb"), P("Service", "cellb"), P("What it does", "cellb"),
     P("Cost / month", "cellb"), P("Card?", "cellb")],
    [P("1", "cell"), P("LiveKit", "cell"),
     P("Connects phone calls to the assistant", "cellsm"),
     P("Free", "cell"), P("No", "cell")],
    [P("2", "cell"), P("Fly.io", "cell"),
     P("The server the assistant runs on", "cellsm"),
     P("$9.80", "cell"), P("Yes", "cell")],
    [P("3", "cell"), P("Twilio", "cell"),
     P("Your new 514 phone number", "cellsm"),
     P("$2.90", "cell"), P("Yes", "cell")],
    [P("4", "cell"), P("Google AI", "cell"),
     P("Understands what the caller wants", "cellsm"),
     P("$1.50", "cell"), P("Yes", "cell")],
    [P("5", "cell"), P("Deepgram", "cell"),
     P("Hears and understands speech", "cellsm"),
     P("$1.20", "cell"), P("No", "cell")],
    [P("6", "cell"), P("Supabase", "cell"),
     P("Stores your call records, in Canada", "cellsm"),
     P("Free", "cell"), P("No", "cell")],
], [0.3 * inch, 1.1 * inch, 2.85 * inch, 1.05 * inch, 0.6 * inch],
    align_right=(3,)))

A(Spacer(1, 8))
A(P("Libro is not on this list. It stays exactly as it is &mdash; same account, "
    "same login, same bill. The assistant books into the Libro you already have.",
    "small"))

A(Spacer(1, 8))
A(callout("DO THEM IN THIS ORDER",
          "<b>LiveKit must be first and Twilio must come after it</b>, because "
          "Twilio needs an address that LiveKit gives you. The other four can be "
          "done in any order."))

A(PageBreak())

# =============================================================== accounts ===
A(P("Opening the accounts", "title"))
A(P("Each step below is one screen. Where it says <b>send me</b>, copy the "
    "value somewhere safe &mdash; the last page explains how to get them all to "
    "me at once.", "sub"))

A(account(
    1, "LiveKit", "Connects a phone call to the assistant. Do this one first.",
    "Free", "No card needed",
    [
        "Go to <b>cloud.livekit.io</b> and choose <b>Sign up</b>.",
        "Sign up with the restaurant email.",
        "When it asks for a project name, type <b>resto-voice</b> and create it.",
        "In the left sidebar, click <b>Settings</b>, then <b>API Keys</b>.",
        "Click <b>Create key</b>. Name it <b>resto-agent</b>.",
        "It shows you three values: a <b>URL</b> (starts with <font face='Courier'>wss://</font>), "
        "an <b>API Key</b>, and an <b>API Secret</b>. <b>Send me all three.</b>",
    ],
    "The secret is shown <b>once only</b>. Copy it before you close that box. "
    "If you lose it, no harm done &mdash; delete the key and make a new one."))

A(account(
    2, "Fly.io", "The server that runs the assistant, 24 hours a day, in Toronto.",
    "$9.80", "Card needed",
    [
        "Go to <b>fly.io</b> and choose <b>Sign up</b>.",
        "Sign up with the restaurant email.",
        "Open <b>Billing</b> and add the restaurant credit card. Fly will not "
        "run anything until a card is on file.",
        "Go to your organisation, then <b>Members</b>, then <b>Invite</b>.",
        "Invite <b>a.naim2004@gmail.com</b>. That lets me deploy and fix the "
        "assistant. <b>You can remove me with one click, at any time.</b>",
    ],
    "This is the one real bill. It is a computer that never sleeps, because a "
    "sleeping one means a phone that rings and rings. Roughly the price of a "
    "coffee a week."))

A(account(
    3, "Twilio", "The new 514 phone number the assistant answers.",
    "$2.90", "Card needed",
    [
        "Go to <b>twilio.com</b> and choose <b>Sign up</b>. Do LiveKit first.",
        "Sign up with the restaurant email and verify your mobile number.",
        "Add the restaurant credit card, then add about <b>$20</b> of credit.",
        "In the search bar type <b>Buy a number</b>. Choose <b>Canada</b>, then "
        "area code <b>514</b> (or <b>438</b>), and make sure <b>Voice</b> is "
        "ticked. Buy it &mdash; about $1.15 per month.",
        "<b>Write the new number down and send it to me.</b>",
        "Click <b>Console</b> at the top left to go back to the main page. Near "
        "the bottom you will see <b>Account SID</b> and <b>Auth Token</b> "
        "(click <b>Show</b> to reveal the token). <b>Send me both.</b>",
    ],
    "<b>Your existing number, 514-555-0100, is not touched.</b> It keeps ringing "
    "in the restaurant exactly as it does today. The assistant answers a "
    "brand-new number, so you can test it for as long as you like before "
    "telling a single customer about it."))

A(account(
    4, "Google AI", "The part that works out what the caller is asking for.",
    "$1.50", "Card needed",
    [
        "Go to <b>aistudio.google.com/apikey</b>.",
        "Sign in with the restaurant&rsquo;s Google account.",
        "Click <b>Create API key</b>, then <b>Create API key in new project</b>.",
        "Copy the key and <b>send it to me</b>.",
        "Click <b>Set up billing</b> on that same page and add the restaurant "
        "card. This step is not optional &mdash; see the note.",
    ],
    "<b>The free version must not be used for a real phone line.</b> It is "
    "capped, and when the cap is hit calls simply fail &mdash; on a busy Friday "
    "night, silently. Adding the card lifts the cap. Your actual usage is about "
    "<b>$1.50 a month</b>."))

A(account(
    5, "Deepgram", "The ears. Turns what the caller says into words.",
    "$1.20", "No card needed",
    [
        "Go to <b>console.deepgram.com/signup</b>.",
        "Sign up with the restaurant email.",
        "In the left sidebar click <b>API Keys</b>.",
        "Click <b>Create a New API Key</b>, name it <b>resto-agent</b>, leave the "
        "permission as <b>Member</b>, and create it.",
        "Copy the key and <b>send it to me</b>. It is shown once only.",
    ],
    "Deepgram gives <b>$200 of free credit</b> with no card. At your call volume "
    "that is several years. You will not pay anything here for a very long time.",
    note_tone="info"))

A(account(
    6, "Supabase", "Keeps a record of every call, so you can check what happened.",
    "Free", "No card needed",
    [
        "Go to <b>supabase.com/dashboard</b> and sign up with the restaurant email.",
        "Click <b>New project</b> and name it <b>resto-calls</b>.",
        "<b>For Region, choose &ldquo;Canada (Central)&rdquo; &mdash; ca-central-1.</b> "
        "Do not accept the default. See the note.",
        "Set a database password. Save it somewhere &mdash; a password manager, "
        "or written down at home.",
        "Wait about two minutes for it to finish setting up.",
        "Go to <b>Project Settings</b>, then <b>Database</b>, then "
        "<b>Connection string</b>, and choose the <b>URI</b> tab. "
        "<b>Send me that line.</b>",
    ],
    "<b>The region matters and cannot be changed later.</b> This database holds "
    "your guests&rsquo; names and phone numbers. Quebec&rsquo;s Law 25 governs "
    "that information leaving the province, so it stays in Montreal. If you pick "
    "the default US region by mistake, tell me &mdash; we delete it and start "
    "again, which takes five minutes."))

# ============================================================== handover ====
# No hard page break here: the accounts above end at an unpredictable height,
# and forcing a break left a nearly empty page. Each heading is instead glued
# to the table under it with KeepTogether, so nothing splits mid-table.
A(KeepTogether([
    P("Sending me the details", "title"),
    P("Once the six accounts exist, I need the values below to connect them "
      "together. This is a one-time job.", "sub"),
    table([
    [P("From", "cellb"), P("What to send", "cellb")],
    [P("LiveKit", "cell"), P("URL, API Key, API Secret", "cell")],
    [P("Fly.io", "cell"),
     P("Nothing &mdash; just invite a.naim2004@gmail.com as a member", "cell")],
    [P("Twilio", "cell"), P("Account SID, Auth Token, and the new 514 number", "cell")],
    [P("Google AI", "cell"), P("The API key", "cell")],
    [P("Deepgram", "cell"), P("The API key", "cell")],
        [P("Supabase", "cell"), P("The database connection string (URI)", "cell")],
    ], [1.5 * inch, 5.4 * inch]),
]))

A(Spacer(1, 9))
A(callout("HOW TO SEND THEM",
          "These values are like keys to the building. <b>Please do not send "
          "them by text message or ordinary email.</b> Best: hand them to me in "
          "person, or use a shared password manager. Otherwise tell me and I "
          "will send a one-time secure link. <b>You can delete and regenerate "
          "any of them at any time</b>, from the same screen you got it from "
          "&mdash; that is what owning the accounts means."))

A(KeepTogether([
    P("What you are paying, once it is live", "h"),
    table([
        [P("Service", "cellb"), P("Monthly", "cellb")],
    [P("Fly.io &mdash; the server", "cell"), P("$9.80", "cell")],
    [P("Twilio &mdash; phone number and minutes", "cell"), P("$2.90", "cell")],
    [P("Google AI &mdash; understanding callers", "cell"), P("$1.50", "cell")],
    [P("Deepgram &mdash; speech recognition", "cell"), P("$1.20", "cell")],
    [P("The voice the caller hears &mdash; billed through LiveKit", "cell"),
     P("$1.80", "cell")],
    [P("LiveKit call handling, and Supabase call records", "cell"),
     P("Free at your volume", "cell")],
    [P("Allowance for busier months (15%)", "cell"), P("$2.60", "cell")],
        [P("<b>Total</b>", "cellb"), P("<b>~$20 / month</b>", "cellb")],
    ], [5.4 * inch, 1.5 * inch], align_right=(1,), band_last=True),
    Spacer(1, 5),
    P("Your measured volume: 102 calls and 111 minutes a month, from 84 calls "
      "to your line between 30 June and 25 July 2026. Mostly fixed cost, so it "
      "barely moves as calls rise.", "small"),
]))

A(P("What you never have to do", "h"))
A(P("No server to look after. No software to update. No backups. Nothing to "
    "install on your computer or your phone. Once these six accounts exist, "
    "<b>Libro stays the only system you actually manage</b> &mdash; exactly "
    "what you asked for.", "p"))

A(P("If something looks wrong", "h"))
A(P("Each of these companies emails you directly if a payment fails, so you "
    "hear about problems before I do. If a bill looks strange, or a screen "
    "does not match this guide, send me a photo rather than guessing &mdash; "
    "these screens change often.", "p"))

A(Spacer(1, 10))
A(P("Alejandro Monge &mdash; a.naim2004@gmail.com", "small"))

SimpleDocTemplate(
    OUT, pagesize=LETTER,
    leftMargin=0.8 * inch, rightMargin=0.8 * inch,
    topMargin=0.68 * inch, bottomMargin=0.5 * inch,
    title="the restaurant — accounts to open",
    author="Alejandro Monge",
).build(story)

print("wrote", OUT)
