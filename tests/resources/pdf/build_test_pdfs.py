"""Builds the PDF workflow's test PDFs: six cases of about ten pages each, every word invented
(`AcmeSync`, `Jonas Weber`), printed from HTML with headless Chrome, and a scanned copy whose text is only pixels.
Writes html/ and out/ beside this file. Run from the project's root folder:
  .venv/bin/python tests/resources/pdf/build_test_pdfs.py
Each case and what a clean result keeps and drops is listed in CASES; out/ is git-ignored and built again here."""

import html
import io
import random
import subprocess
from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
HTML_DIR = HERE / "html"
OUT_DIR = HERE / "out"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# What each case tests: the noise a clean result drops, and the content it keeps
CASES = {
    "01-confluence-export": "a wiki page export: breadcrumb, page tree, author line, buttons, emoticons, status "
                            "lozenges, labels, an attachments list, comments and anchor links are noise; the sections, "
                            "the retry table, the rollout steps and the two external links are content",
    "02-report-broken-tables": "a quarterly report whose tables run over page breaks with their header repeated and "
                               "cells wrapped: each table is one table, every row once",
    "03-slides-export": "a deck printed to PDF, one slide a page: the same footer and logo on every slide are noise; "
                        "titles, bullets, a diagram and a chart are content",
    "04-confluence-scanned": "the wiki export of case 01 scanned: no text layer, a tilt and speckles; the text must be "
                             "read from the pixels",
    "05-manual-screenshots-code": "an operator manual: screenshots, code blocks, numbered steps and warning boxes are "
                                  "content; the running header and footer are noise",
    "06-notes-no-headings": "meeting notes typed as plain paragraphs with no heading at all: the only case where "
                            "chapters must be made, from the subjects the paragraphs change between",
}

STYLE = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 11pt; color: #172b4d; line-height: 1.45; }
h1 { font-size: 20pt; margin: 0 0 8pt; } h2 { font-size: 15pt; margin: 14pt 0 6pt; } h3 { font-size: 12pt; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; } th, td { border: 1px solid #c1c7d0; padding: 4pt 6pt;
vertical-align: top; text-align: left; } th { background: #f4f5f7; } thead { display: table-header-group; }
pre { background: #f4f5f7; border: 1px solid #dfe1e6; padding: 8pt; font-size: 9pt; white-space: pre-wrap; }
.page { page-break-after: always; } .page:last-child { page-break-after: auto; }
"""


def _paragraphs(sentences: list[str]) -> str:
    return "".join(f"<p>{html.escape(sentence)}</p>" for sentence in sentences)


def _table(header: list[str],
           rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(cell)}</th>" for cell in header)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)

    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _write(name: str,
           body: str,
           style: str = "") -> Path:
    path = HTML_DIR / f"{name}.html"
    path.write_text(f"<!doctype html><html><head><meta charset='utf-8'><style>{STYLE}{style}</style></head>"
                    f"<body>{body}</body></html>", encoding="utf-8")

    return path


# STEP 1: the wiki export, its chrome repeated on every printed page by fixed positions
CONFLUENCE_STYLE = """
.chrome-top { position: fixed; top: 0; left: 0; right: 0; height: 26pt; font-size: 8.5pt; color: #6b778c; }
.chrome-top img { height: 14pt; vertical-align: middle; margin-left: 4pt; }
.tree { position: fixed; top: 34pt; left: 0; width: 120pt; font-size: 8.5pt; color: #42526e; }
.content { margin-left: 135pt; margin-top: 30pt; }
.meta { color: #6b778c; font-size: 9pt; } .meta img { height: 16pt; vertical-align: middle; }
.lozenge { height: 13pt; vertical-align: middle; } .emo { height: 12pt; vertical-align: middle; }
.footer { position: fixed; bottom: 0; left: 0; right: 0; font-size: 8pt; color: #97a0af; text-align: center; }
"""
SECTIONS = [
    ("Overview", ["The AcmeSync Gateway forwards refund and payment calls from partner shops to the ledger service.",
                  "Every partner gets its own tenant, and every tenant has its own retry budget and rate limit.",
                  "This guide describes how a new partner is onboarded, from the first sandbox key to the live switch.",
                  "The gateway runs in two regions, Frankfurt and Dublin, and a partner is pinned to one of them."]),
    ("Architecture", ["A call enters through the edge proxy, which checks the partner's signature with SHA-256.",
                      "The router then picks the ledger shard from the tenant id and forwards the call over mutual TLS.",
                      "Responses are cached for 30 seconds only for read calls; refunds are never cached.",
                      "Each component writes its traces to the shared collector, kept for 14 days."]),
    ("Retry policy", ["A failed call is retried at most three times, with waits of 2, 4 and 8 seconds.",
                      "A call that times out after 30 seconds counts as failed and uses one retry.",
                      "The retry budget is per tenant: once a tenant spends 50 retries in one minute, new failures are "
                      "returned to the partner at once."]),
    ("Rollout steps", ["The rollout of a partner runs in five steps, each signed off by the partner manager."]),
    ("Monitoring", ["The dashboard shows each tenant's error rate, latency at the 95th percentile and retry count.",
                    "An alert fires when a tenant's error rate stays above 2 percent for five minutes.",
                    "On-call engineers acknowledge an alert within 15 minutes during business hours."]),
    ("Known issues", ["Refunds above 2,000 EUR can take longer than 30 seconds at the ledger and time out.",
                      "A fix that answers such refunds asynchronously is planned for release 2.15.0.",
                      "Until then, partners are asked to split refunds above 2,000 EUR into smaller ones."]),
    ("Glossary", ["Tenant: one partner's isolated configuration in the gateway.",
                  "Shard: one of the eight ledger databases a tenant's data lives in.",
                  "Sandbox key: an API key that reaches only the test ledger and expires after 14 days."]),
]
ROLLOUT = ["Create the tenant and issue a sandbox key.", "The partner runs the 40 test calls of the certification suite.",
           "Review the trace of every failed test call with the partner.",
           "Issue the live key and set the tenant's rate limit to 50 calls a second.",
           "Switch the partner's DNS entry and watch the dashboard for one hour."]


def build_confluence() -> Path:
    chrome = ("<div class='chrome-top'>Spaces / AcmeSync Platform / Partner onboarding / Gateway onboarding guide "
              "<img src='../pictures/btn-edit.png'><img src='../pictures/btn-like.png'><img src='../pictures/btn-share.png'>"
              "<img src='../pictures/btn-watch.png'></div>"
              "<div class='tree'><b>Page tree</b><br>Partner onboarding<br>&nbsp;Gateway onboarding guide<br>"
              "&nbsp;Sandbox FAQ<br>&nbsp;Certification suite<br>Release notes<br>Team calendar</div>"
              "<div class='footer'>Printed from AcmeSync Wiki · Gateway onboarding guide</div>")
    parts = [f"{chrome}<div class='content'><h1>Gateway onboarding guide</h1>"
             "<p class='meta'><img src='../pictures/avatar.png'> Created by Jonas Weber, last updated by Mara Keller "
             "<img class='emo' src='../pictures/icon-eye.png'> 214 views "
             "<img class='lozenge' src='../pictures/lozenge-approved.png'></p>"
             "<p><a href='#retry-policy'>Jump to retry policy</a> · <a href='#top'>Back to top</a></p>"]
    for index, (title, sentences) in enumerate(SECTIONS):
        body = _paragraphs(sentences)
        if title == "Retry policy":
            body += _table(["Attempt", "Wait before it", "Counts against budget"],
                           [["1", "none", "no"], ["2", "2 s", "yes"], ["3", "4 s", "yes"], ["4", "8 s", "yes"]])
        if title == "Rollout steps":
            body += "<ol>" + "".join(f"<li>{html.escape(step)}</li>" for step in ROLLOUT) + "</ol>"
        if title == "Architecture":
            body += "<p><img src='../pictures/diagram-image.png' style='width:70%'></p>"
            body += ("<p>The signature scheme follows <a href='https://datatracker.ietf.org/doc/html/rfc9421'>"
                     "HTTP Message Signatures</a>.</p>")
        if title == "Known issues":
            body += ("<p><img class='emo' src='../pictures/emoticon-warning.png'> Status of the fix: "
                     "<a href='https://jira.acmesync.example/browse/ASP-412'>ASP-412</a></p>")
        parts.append(f"<div class='page'><h2 id='{title.lower().replace(' ', '-')}'>{title}</h2>{body}"
                     "<p class='meta'><img class='emo' src='../pictures/emoticon-smile.png'> Like · 3 people like this</p>"
                     "</div>")
        if index == 2:
            parts.append("<div class='page'><h2>Retry examples</h2>" + _paragraphs([
                "A refund that fails at 10:00:00 is retried at 10:00:02, 10:00:06 and 10:00:14.",
                "If the third retry fails, the partner receives error 503 with the header Retry-After: 60.",
                "A read call that fails is retried the same way but its cached answer is served if one exists."]) + "</div>")
    parts.append("<div class='page'><h2>Attachments</h2><p>trace-refund-4471.png · load-test-report-v2.pdf · "
                 "gateway-2.14.0.log</p><p>Labels: onboarding, gateway, partners</p><h2>Comments</h2>"
                 "<p>Mara Keller: <img class='emo' src='../pictures/emoticon-tick.png'> Looks good to me.</p>"
                 "<p>Jonas Weber: Thanks, I will add the Dublin region next week.</p></div></div>")

    return _write("01-confluence-export", "".join(parts), CONFLUENCE_STYLE)


# STEP 2: a report whose long tables break over pages, the header repeated by the print
def build_report() -> Path:
    regions = ["Frankfurt", "Dublin"]
    tenants = [f"Tenant {index + 1:02d}" for index in range(60)]
    rng = random.Random(7)
    medians = [rng.randint(80, 240) for _ in tenants]
    latency = [[tenant, regions[index % 2], f"{medians[index]} ms", f"{medians[index] + rng.randint(40, 240)} ms",
                f"{rng.uniform(0.1, 3.2):.1f} %",
                "Retries above budget twice in March; asked to split large refunds" if index % 5 == 0 else "none"]
               for index, tenant in enumerate(tenants)]
    incidents = [[f"INC-{3100 + index}", f"2026-0{1 + index % 3}-{1 + index % 28:02d}",
                  ["ledger timeout", "certificate expiry", "rate limit misconfigured", "DNS switch delayed"][index % 4],
                  f"{rng.randint(4, 95)} min", f"Postmortem written; action owner {['Jonas', 'Mara', 'Lea'][index % 3]}"]
                 for index in range(80)]
    body = ("<h1>AcmeSync Gateway quarterly operations report, Q1</h1>"
            + _paragraphs(["This report covers January to March and compares every tenant's latency and error rate.",
                           "Two incidents had customer impact; both were caused by ledger timeouts on large refunds.",
                           "The tables below list every tenant and every incident of the quarter."])
            + "<h2>Latency and errors by tenant</h2>"
            + _table(["Tenant", "Region", "Median latency", "95th percentile", "Error rate", "Notes"], latency)
            + "<h2>Incidents</h2>"
            + _table(["Incident", "Date", "Cause", "Duration", "Follow-up"], incidents)
            + "".join(f"<h2>{month}</h2>" + _paragraphs([
                f"In {month} the gateway handled {calls} million calls, {share} percent of them refunds.",
                f"The busiest day was the {day}th, with a peak of {peak} calls a second in Frankfurt.",
                f"{changes} configuration changes were made, none of them rolled back."])
                + _table(["Week", "Calls (millions)", "Refunds", "Errors", "Retries"],
                         [[f"Week {week}", f"{calls / 4:.1f}", f"{rng.randint(20, 90)} k", f"{rng.randint(200, 900)}",
                           f"{rng.randint(1, 9)} k"] for week in range(1, 5)])
                for month, calls, share, day, peak, changes in (("January", 41, 12, 14, 920, 6),
                                                                ("February", 38, 11, 9, 870, 4),
                                                                ("March", 46, 14, 27, 1040, 9)))
            + "<h2>Conclusions</h2>"
            + _paragraphs(["Median latency stayed under 250 ms for every tenant.",
                           "Incidents caused by ledger timeouts fell from 9 in Q4 to 6 in Q1.",
                           "The asynchronous refund path planned for 2.15.0 is expected to remove the remaining ones."]))

    return _write("02-report-broken-tables", body)


# STEP 3: a deck printed to PDF, one slide a page, with a footer and a logo on every slide
SLIDES = [
    ("AcmeSync Gateway 2.15", ["Release review for partner managers", "Jonas Weber, Platform team"]),
    ("Agenda", ["What changes in 2.15", "Asynchronous refunds", "Rollout plan", "Questions"]),
    ("What changes", ["Refunds above 2,000 EUR answered asynchronously", "Retry budget raised to 60 per minute",
                      "Sandbox keys valid for 30 days instead of 14"]),
    ("Asynchronous refunds", ["The gateway answers 202 Accepted at once", "The partner polls the refund's status URL",
                              "A webhook reports the final state"], "diagram-image.png"),
    ("Refund latency before and after", ["Measured on the staging ledger with 5,000 refunds"], "chart-bars.png"),
    ("Rollout plan", ["Week 1: staging only", "Week 2: three pilot partners", "Week 3: every partner in Frankfurt",
                      "Week 4: Dublin"]),
    ("Risks", ["Partners that do not poll keep waiting for a synchronous answer",
               "Webhooks blocked by a partner's firewall"]),
    ("Mitigations", ["Old behaviour kept behind a flag until release 2.16", "A test webhook sent during onboarding"]),
    ("Next steps", [], "wordart-next-steps.png"),
    ("Questions", ["platform-team@acmesync.example is the contact for the rollout"]),
]
SLIDE_STYLE = """
@page { size: 297mm 167mm; margin: 0; }
.slide { width: 297mm; height: 166mm; box-sizing: border-box; padding: 14mm 18mm; position: relative; overflow: hidden;
page-break-after: always; } h1 { font-size: 26pt; color: #0052cc; } li { font-size: 15pt; margin: 4pt 0; }
.slide img.picture { position: absolute; right: 18mm; top: 40mm; height: 95mm; }
.slide .footer { position: absolute; bottom: 7mm; left: 18mm; right: 18mm; font-size: 9pt; color: #6b778c; }
.slide img.logo { position: absolute; top: 9mm; right: 14mm; height: 12mm; }
"""


def build_slides() -> Path:
    parts = []
    for number, (title, bullets, *picture) in enumerate(SLIDES, start=1):
        items = "<ul>" + "".join(f"<li>{html.escape(bullet)}</li>" for bullet in bullets) + "</ul>" if bullets else ""
        image = f"<img class='picture' src='../pictures/{picture[0]}'>" if picture else ""
        parts.append(f"<div class='slide'><img class='logo' src='../pictures/logo.png'><h1>{html.escape(title)}</h1>"
                     f"{items}{image}<div class='footer'>Confidential · AcmeSync · {number} / {len(SLIDES)}</div></div>")

    return _write("03-slides-export", "".join(parts), SLIDE_STYLE)


# STEP 4: an operator manual with screenshots, code and warnings, a running header and footer
MANUAL_STYLE = """
.header { position: fixed; top: -12mm; left: 0; right: 0; font-size: 8pt; color: #97a0af; }
.foot { position: fixed; bottom: -12mm; left: 0; right: 0; font-size: 8pt; color: #97a0af; text-align: right; }
.warn { border-left: 4pt solid #ff8b00; background: #fffae6; padding: 6pt 8pt; }
img.shot { width: 85%; border: 1px solid #dfe1e6; }
"""
MANUAL = [
    ("1 Installing the gateway CLI", ["The CLI talks to the gateway's admin API and needs Python 3.11 or later."],
     "pip install acmesync-gateway-cli==2.15.0\nacmesync --version", None),
    ("2 Signing in", ["The CLI reads the admin token from the environment, never from a file in the repository."],
     "export ACMESYNC_TOKEN=<your admin token>\nacmesync login --region frankfurt", "screenshot-settings.png"),
    ("3 Creating a tenant", ["A tenant's name must be unique and at most 32 characters long."],
     "acmesync tenant create --name partner-b --region dublin --rate-limit 50", None),
    ("4 Issuing keys", ["Sandbox keys expire after 14 days; live keys never expire but can be revoked."],
     "acmesync key issue --tenant partner-b --kind sandbox", "screenshot-code.png"),
    ("5 Reading the trace of a failed call", ["Every call has a trace id in the X-Trace-Id response header."],
     "acmesync trace show 7f3a9c1e --tenant partner-b", "screenshot-error-dialog.png"),
    ("6 Changing the retry budget", ["The budget is set per tenant and takes effect within one minute."],
     "acmesync tenant update partner-b --retry-budget 60", None),
    ("7 Switching a partner live", ["The switch changes the partner's DNS entry; it is reversed with --rollback."],
     "acmesync tenant go-live partner-b\nacmesync tenant go-live partner-b --rollback", None),
    ("8 Revoking a key", ["A revoked key stops working within 30 seconds in both regions."],
     "acmesync key revoke --tenant partner-b --key-id k_51", None),
    ("9 Exporting usage", ["Usage is exported as CSV with one row per tenant and day."],
     "acmesync usage export --from 2026-01-01 --to 2026-03-31 > usage-q1.csv", None),
    ("10 Troubleshooting", ["Error 401 means the token expired; sign in again.",
                            "Error 429 means the tenant's rate limit was reached."], None, "table-as-image.png"),
]


def build_manual() -> Path:
    parts = ["<div class='header'>AcmeSync Gateway · Operator manual · version 2.15</div>"
             "<div class='foot'>© AcmeSync · internal</div><h1>Operator manual</h1>"]
    for title, sentences, code, shot in MANUAL:
        block = f"<pre>{html.escape(code)}</pre>" if code else ""
        picture = f"<p><img class='shot' src='../pictures/{shot}'></p>" if shot else ""
        warning = ("<p class='warn'>Never paste the admin token into a ticket or a chat.</p>"
                   if "Signing" in title else "")
        parts.append(f"<div class='page'><h2>{html.escape(title)}</h2>{_paragraphs(sentences)}{block}{warning}{picture}"
                     "</div>")

    return _write("05-manual-screenshots-code", "".join(parts), MANUAL_STYLE)


# STEP 5: meeting notes typed as plain paragraphs, no heading anywhere
NOTES = [
    "Jonas opened the weekly sync and said the 2.15 release candidate passed the staging tests on Tuesday.",
    "Mara reported that two pilot partners have not yet built the polling of refund status URLs.",
    "The team agreed to keep the synchronous refund path behind a flag until release 2.16.",
    "Lea asked who tells the pilot partners about the webhook test; Mara will do it by Friday.",
    "On the Dublin region, the new ledger shard is ready but its backup job still runs only once a day.",
    "Jonas wants the backup every four hours before any partner is pinned to Dublin.",
    "The decision was to delay Dublin onboarding by one week until the backup runs every four hours.",
    "The rate limit dashboard shows Partner F at 48 calls a second, close to its limit of 50.",
    "Mara will ask Partner F whether they expect more traffic in April.",
    "Sandbox keys valid for 30 days were requested by three partners; nobody objected.",
    "The certification suite grows from 40 to 46 test calls to cover asynchronous refunds.",
    "Lea will update the onboarding guide with the six new test calls.",
    "Open question: does a partner need a new sandbox key after the suite changes? Nobody knew.",
    "The next sync is moved to Thursday because of the public holiday.",
]


def build_notes() -> Path:
    paragraphs = [f"<p>{html.escape(sentence)} {html.escape(NOTES[(index + 5) % len(NOTES)])}</p>"
                  for index, sentence in enumerate(NOTES)]
    pages = ["<div class='page'>" + "".join(paragraphs[start:start + 2]) + "</div>" for start in range(0, len(paragraphs), 2)]
    pages += ["<div class='page'><p>Attendees: Jonas, Mara, Lea. Minutes taken by Lea.</p>"
              "<p>Action items are tracked in the team's board; this page only keeps the discussion.</p></div>"] * 3

    return _write("06-notes-no-headings", "".join(pages), "body { font-size: 12pt; } p { margin-bottom: 30pt; }")


def print_pdf(html_path: Path) -> Path:
    """Chrome's own header and footer are off: they print this machine's file path."""

    pdf_path = OUT_DIR / f"{html_path.stem}.pdf"
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--allow-file-access-from-files", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=True, capture_output=True, timeout=120)

    return pdf_path


def write_scanned_copy(source_pdf: Path,
                       target_pdf: Path) -> None:
    """Each page one grey picture, tilted by about a degree, speckled and soft, with no text layer: an office scan."""

    source, target = pymupdf.open(source_pdf), pymupdf.open()
    for number, page in enumerate(source):
        pixmap = page.get_pixmap(dpi=150)
        grey = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples).convert("L")
        grey = grey.rotate(1.2, expand=False, fillcolor=255, resample=Image.BICUBIC)
        speckles, pixels = random.Random(number), grey.load()
        for _ in range(grey.width * grey.height // 400):
            pixels[speckles.randrange(grey.width), speckles.randrange(grey.height)] = speckles.choice((0, 90, 255))
        buffer = io.BytesIO()
        grey.filter(ImageFilter.GaussianBlur(0.6)).save(buffer, format="JPEG", quality=60)
        new_page = target.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=buffer.getvalue())
    target.save(target_pdf)


def main() -> None:
    HTML_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    built = [print_pdf(build()) for build in (build_confluence, build_report, build_slides, build_manual, build_notes)]
    write_scanned_copy(OUT_DIR / "01-confluence-export.pdf", OUT_DIR / "04-confluence-scanned.pdf")
    for pdf in sorted(OUT_DIR.glob("*.pdf")):
        print(f"{pdf.name}: {pymupdf.open(pdf).page_count} pages · {CASES[pdf.stem]}")
    del built


main()
