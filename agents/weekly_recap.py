"""Weekly market recap agent.

Every Friday at 11:59 AM SGT:
  1. Scrape the T. Rowe Price Global Markets Weekly Update
  2. Summarise with Claude (claude-haiku)
  3. Fetch live market data (indices, fixed income, currencies)
  4. Assemble an HTML email and send via Gmail SMTP
"""
import logging
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import anthropic
import pytz
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.market_data import fetch_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SOURCE_URL = (
    "https://www.troweprice.com/personal-investing/resources/insights/"
    "global-markets-weekly-update.html"
)

_SUMMARY_SYSTEM = """You are a concise financial analyst writing a Friday briefing email.
You will receive the raw text of T. Rowe Price's Global Markets Weekly Update.

Write a tight summary using HTML. Rules:
- Use <h3> for each regional section header (include a flag emoji)
- Use <ul><li> for bullet points (2–4 per section, no more)
- Bold (<b>) any percentage moves or rate decisions
- Sections: 🇺🇸 U.S. Markets · 🇪🇺 Europe · 🇯🇵 Japan · 🇨🇳 China · 🌐 Other Markets
- Total length: scannable in under 2 minutes
- Start directly with the first <h3> tag — no preamble"""

# ── HTML helpers ───────────────────────────────────────────────────────────

_GREEN = "#16a34a"
_RED   = "#dc2626"
_GRAY  = "#6b7280"
_TH    = "background:#1a3a5c;color:#fff;padding:6px 10px;text-align:right;white-space:nowrap;"
_TH_L  = "background:#1a3a5c;color:#fff;padding:6px 10px;text-align:left;"
_TD    = "padding:5px 10px;border-bottom:1px solid #e5e7eb;text-align:right;"
_TD_L  = "padding:5px 10px;border-bottom:1px solid #e5e7eb;text-align:left;"


def _pct(val: Optional[float], decimals: int = 2) -> str:
    if val is None:
        return '<span style="color:#9ca3af">—</span>'
    sign = "+" if val >= 0 else ""
    color = _GREEN if val > 0 else (_RED if val < 0 else _GRAY)
    return f'<span style="color:{color};font-weight:600">{sign}{val:.{decimals}f}%</span>'


def _bps(val: Optional[float]) -> str:
    if val is None:
        return '<span style="color:#9ca3af">—</span>'
    sign = "+" if val >= 0 else ""
    color = _GREEN if val < 0 else (_RED if val > 0 else _GRAY)  # lower yields = green
    return f'<span style="color:{color};font-weight:600">{sign}{val:.1f} bps</span>'


def _price(val: Optional[float]) -> str:
    if val is None:
        return "—"
    if val >= 10_000:
        return f"{val:,.0f}"
    if val >= 100:
        return f"{val:,.2f}"
    return f"{val:.4f}"


def _returns_table(rows: List[Tuple[str, Optional[Dict]]], title: str) -> str:
    header = (
        f'<h3 style="color:#1a3a5c;margin-top:24px">{title}</h3>'
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        f'<thead><tr>'
        f'<th style="{_TH_L}">Name</th>'
        f'<th style="{_TH}">Last</th>'
        f'<th style="{_TH}">1W %</th>'
        f'<th style="{_TH}">MTD %</th>'
        f'<th style="{_TH}">YTD %</th>'
        f'</tr></thead><tbody>'
    )
    body = ""
    for name, d in rows:
        if d:
            body += (
                f'<tr><td style="{_TD_L}">{name}</td>'
                f'<td style="{_TD}">{_price(d["last"])}</td>'
                f'<td style="{_TD}">{_pct(d["weekly"])}</td>'
                f'<td style="{_TD}">{_pct(d["mtd"])}</td>'
                f'<td style="{_TD}">{_pct(d["ytd"])}</td></tr>'
            )
        else:
            body += (
                f'<tr><td style="{_TD_L}">{name}</td>'
                + f'<td style="{_TD}" colspan="4" style="color:#9ca3af">data unavailable</td></tr>'
            )
    return header + body + "</tbody></table>"


def _yields_table(us_yields, sovereign, spreads, spread_10y_2y, lqd_hyg) -> str:
    html = '<h3 style="color:#1a3a5c;margin-top:24px">💵 Fixed Income</h3>'

    # US yields + sovereign rates
    html += (
        '<p style="font-weight:600;margin:12px 0 4px">Rates</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        f'<thead><tr>'
        f'<th style="{_TH_L}">Instrument</th>'
        f'<th style="{_TH}">Yield (%)</th>'
        f'<th style="{_TH}">1W Δ (bps)</th>'
        f'</tr></thead><tbody>'
    )
    for name, d in us_yields + sovereign:
        if d:
            html += (
                f'<tr><td style="{_TD_L}">{name}</td>'
                f'<td style="{_TD}">{d["value"]:.2f}%</td>'
                f'<td style="{_TD}">{_bps(d["weekly_bps"])}</td></tr>'
            )
        else:
            html += f'<tr><td style="{_TD_L}">{name}</td><td style="{_TD}" colspan="2">—</td></tr>'
    html += "</tbody></table>"

    # Derived metrics
    html += (
        '<p style="font-weight:600;margin:16px 0 4px">Derived Metrics</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:13px">'
        f'<thead><tr>'
        f'<th style="{_TH_L}">Metric</th>'
        f'<th style="{_TH}">Level</th>'
        f'<th style="{_TH}">1W Δ</th>'
        f'</tr></thead><tbody>'
    )
    if spread_10y_2y:
        html += (
            f'<tr><td style="{_TD_L}">10Y–2Y Spread</td>'
            f'<td style="{_TD}">{spread_10y_2y["value"]} bps</td>'
            f'<td style="{_TD}">{_bps(spread_10y_2y["weekly_bps"])}</td></tr>'
        )
    for name, d in spreads:
        if d:
            html += (
                f'<tr><td style="{_TD_L}">{name}</td>'
                f'<td style="{_TD}">{d["value"]:.0f} bps</td>'
                f'<td style="{_TD}">{_bps(d["weekly_bps"])}</td></tr>'
            )
    if lqd_hyg:
        html += (
            f'<tr><td style="{_TD_L}">LQD/HYG Ratio</td>'
            f'<td style="{_TD}">{lqd_hyg:.4f}</td>'
            f'<td style="{_TD}"><span style="color:#9ca3af">—</span></td></tr>'
        )
    html += "</tbody></table>"
    return html


# ── Core agent ─────────────────────────────────────────────────────────────

class WeeklyRecapAgent:

    def fetch_article(self) -> str:
        logger.info("Fetching T. Rowe Price weekly update...")
        r = requests.get(
            SOURCE_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; weekly-recap/1.0)"},
            timeout=15,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body
        lines = [l.strip() for l in main.get_text(separator="\n").splitlines() if l.strip()]
        return "\n".join(lines)

    def summarise(self, text: str) -> str:
        logger.info("Summarising with Claude...")
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        return msg.content[0].text

    def build_email(self, summary_html: str, data: Dict, date_str: str) -> str:
        indices_section   = _returns_table(data["indices"],    "📈 Market Indices")
        bond_etf_section  = _returns_table(data["bond_etfs"], "Bond ETFs")
        fi_section        = _yields_table(
            data["us_yields"], data["sovereign"], data["spreads"],
            data["spread_10y_2y"], data["lqd_hyg_ratio"],
        ) + bond_etf_section
        fx_section        = _returns_table(data["currencies"], "💱 Currencies")

        return f"""<html>
<body style="font-family:Arial,sans-serif;max-width:720px;margin:auto;color:#222;line-height:1.6;">

  <div style="background:#1a3a5c;color:#fff;padding:18px 24px;border-radius:6px 6px 0 0;">
    <h2 style="margin:0;font-size:20px;">🌍 Weekly Market Recap</h2>
    <p style="margin:4px 0 0;font-size:13px;opacity:0.8;">{date_str}</p>
  </div>

  <div style="padding:20px 24px;background:#f9fafb;border:1px solid #e5e7eb;border-top:none;">

    <h3 style="color:#1a3a5c;margin-top:0">📝 Weekly Summary</h3>
    <div style="background:#fff;padding:16px;border-radius:4px;border:1px solid #e5e7eb;">
      {summary_html}
    </div>

    {indices_section}
    {fi_section}
    {fx_section}

    <hr style="margin-top:32px;border:none;border-top:1px solid #e5e7eb;">
    <p style="font-size:11px;color:#9ca3af;">
      Market data via Yahoo Finance &amp; FRED. Summary sourced from
      <a href="{SOURCE_URL}" style="color:#9ca3af;">T. Rowe Price Global Markets Weekly Update</a>.<br>
      Delivered automatically every Friday at 11:59 AM SGT.
    </p>
  </div>

</body>
</html>"""

    def send_email(self, subject: str, html: str) -> None:
        sender    = os.getenv("GMAIL_USER")
        password  = os.getenv("GMAIL_APP_PASSWORD")
        recipient = os.getenv("RECIPIENT_EMAIL") or sender

        if not sender or not password:
            logger.error("GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping send")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = recipient
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Email sent → {recipient}")

    def run(self) -> None:
        sgt      = pytz.timezone("Asia/Singapore")
        date_str = datetime.now(sgt).strftime("%B %d, %Y")
        subject  = f"🌍 Weekly Market Recap — {date_str}"

        article_text = self.fetch_article()
        summary_html = self.summarise(article_text)

        fred_key = os.getenv("FRED_API_KEY", "")
        data     = fetch_all(fred_key)

        email_html = self.build_email(summary_html, data, date_str)
        self.send_email(subject, email_html)
        logger.info("Weekly recap complete.")


if __name__ == "__main__":
    WeeklyRecapAgent().run()
