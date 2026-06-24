"""Weekly market recap agent.

Scrapes the T. Rowe Price Global Markets Weekly Update every Friday,
summarises it with Claude, and emails the result.
"""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import anthropic
import pytz
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SOURCE_URL = (
    "https://www.troweprice.com/personal-investing/resources/insights/"
    "global-markets-weekly-update.html"
)

_SYSTEM_PROMPT = """You are a concise financial analyst writing a weekly briefing email.
You will receive the raw text of T. Rowe Price's Global Markets Weekly Update.

Summarise it in clear, plain English under these five sections. Use HTML formatting:
• <h3> for each section header
• <ul><li> bullet points (2–4 per section)
• Bold (<b>) any percentage figures or rate decisions

Sections to cover:
1. 🇺🇸 U.S. Markets — major index moves, Fed decisions, key macro data (jobs, CPI, retail sales)
2. 🇪🇺 Europe — notable index moves, ECB/BoE actions, key data releases
3. 🇯🇵 Japan — Nikkei/TOPIX, Bank of Japan policy, trade or inflation data
4. 🇨🇳 China — equity performance, PBOC actions, property or industrial data
5. 🌐 Other Markets — any other notable central bank moves or macro events

Keep the total length scannable — this is a Friday briefing, not a research report.
Do not add any text before the first <h3> tag."""


class WeeklyRecapAgent:

    def fetch_page(self) -> str:
        logger.info("Fetching T. Rowe Price weekly update...")
        r = requests.get(
            SOURCE_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; weekly-recap/1.0)"},
            timeout=15,
        )
        r.raise_for_status()
        return r.text

    def extract_text(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body
        lines = [l.strip() for l in main.get_text(separator="\n").splitlines() if l.strip()]
        return "\n".join(lines)

    def summarize(self, text: str) -> str:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        logger.info("Summarising with Claude...")
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
        )
        return response.content[0].text

    def send_email(self, subject: str, body_html: str) -> None:
        sender = os.getenv("GMAIL_USER")
        password = os.getenv("GMAIL_APP_PASSWORD")
        recipient = os.getenv("RECIPIENT_EMAIL") or sender

        if not sender or not password:
            logger.error("GMAIL_USER or GMAIL_APP_PASSWORD not configured — skipping email")
            return

        full_html = f"""<html>
<body style="font-family:Arial,sans-serif;max-width:680px;margin:auto;color:#222;line-height:1.6;">
  <h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:8px;">
    🌍 Weekly Market Recap
  </h2>
  {body_html}
  <hr style="margin-top:32px;border:none;border-top:1px solid #ddd;">
  <p style="font-size:11px;color:#999;">
    Source: <a href="{SOURCE_URL}" style="color:#999;">T. Rowe Price Global Markets Weekly Update</a><br>
    Delivered automatically every Friday at 11:59 AM SGT.
  </p>
</body>
</html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient
        msg.attach(MIMEText(full_html, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())

        logger.info(f"Email sent → {recipient}")

    def run(self) -> None:
        sgt = pytz.timezone("Asia/Singapore")
        date_str = datetime.now(sgt).strftime("%B %d, %Y")
        subject = f"🌍 Weekly Market Recap — {date_str}"

        html = self.fetch_page()
        text = self.extract_text(html)
        summary_html = self.summarize(text)
        self.send_email(subject, summary_html)
        logger.info("Weekly recap complete.")


if __name__ == "__main__":
    WeeklyRecapAgent().run()
