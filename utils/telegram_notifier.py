"""Telegram notification utility for gapper results."""
import os
import logging
import requests
from typing import List, Dict

logger = logging.getLogger(__name__)


def send_results(results: List[Dict], scan_date: str, scan_time: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.info("Telegram credentials not set — skipping notification")
        return False

    message = _format_message(results, scan_date, scan_time)
    for chunk in _split(message):
        if not _send(token, chat_id, chunk):
            return False
    return True


def send_no_results(scan_date: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    text = f"🔕 <b>Premarket Gappers — {scan_date}</b>\n\nNo stocks passed filters today (gap &gt;5%, RVOL &gt;3x)."
    return _send(token, chat_id, text)


def _format_message(results: List[Dict], scan_date: str, scan_time: str) -> str:
    lines = [
        f"🔔 <b>Premarket Gappers — {scan_date}</b>",
        f"<i>US Eastern: {scan_time} ET  |  {len(results)} names</i>",
    ]

    current_tier = None
    for stock in results:
        tier = stock.get("cap_tier", "unknown")
        if tier != current_tier:
            current_tier = tier
            label = "UNKNOWN CAP" if tier == "unknown" else f"{tier.upper()} CAP"
            lines.append(f"\n<b>[{label}]</b>")

        ticker = stock["ticker"]
        price = stock["price"]
        gap = stock["gap_pct"]
        rvol = stock.get("rvol")
        rvol_basis = stock.get("rvol_basis", "live")
        catalyst = stock.get("catalyst", "No news available")

        rvol_label = "prevRVOL" if rvol_basis == "prev_session" else "RVOL"
        rvol_str = f"  {rvol_label}: {rvol:.1f}x" if rvol is not None else ""

        if len(catalyst) > 120:
            catalyst = catalyst[:117] + "..."

        lines.append(f"• <b>{ticker}</b>  ${price:.2f}  <b>+{gap:.1f}%</b>{rvol_str}")
        lines.append(f"  <i>{catalyst}</i>")

    return "\n".join(lines)


def _split(text: str, max_len: int = 4096) -> List[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def _send(token: str, chat_id: str, text: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code == 200:
            return True
        logger.warning(f"Telegram {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Telegram send error: {e}")
        return False
