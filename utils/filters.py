"""Utility functions for filtering and processing stock data."""
from typing import List, Dict, Optional
import logging
import time

import yfinance as yf

logger = logging.getLogger(__name__)

_CAP_TIERS = [
    ("mega",  200_000_000_000),
    ("large",  10_000_000_000),
    ("mid",     2_000_000_000),
    ("small",     300_000_000),
    ("micro",               0),
]

# unknown sorts after micro (index 5)
_TIER_ORDER = {tier: i for i, (tier, _) in enumerate(_CAP_TIERS)}
_TIER_ORDER["unknown"] = len(_CAP_TIERS)


def _cap_tier_from_market_cap(market_cap: int) -> str:
    for tier, threshold in _CAP_TIERS:
        if market_cap >= threshold:
            return tier
    return "micro"


def _fetch_market_cap(ticker: str, retries: int = 2, delay: float = 2.0) -> Optional[int]:
    """Return market cap in dollars, or None if unavailable after retries."""
    for attempt in range(retries):
        try:
            mc = yf.Ticker(ticker).fast_info.market_cap
            if mc and mc > 0:
                return int(mc)
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def enrich_with_market_cap(stocks: List[Dict]) -> None:
    """
    Add 'market_cap' and 'cap_tier' to each stock dict in-place.
    If yfinance is unavailable (rate-limited), tier is set to 'unknown'
    rather than incorrectly defaulting to 'micro'.
    """
    consecutive_failures = 0
    for stock in stocks:
        ticker = stock.get("ticker", "")

        if consecutive_failures >= 3:
            stock["market_cap"] = 0
            stock["cap_tier"] = "unknown"
            continue

        mc = _fetch_market_cap(ticker)
        if mc:
            stock["market_cap"] = mc
            stock["cap_tier"] = _cap_tier_from_market_cap(mc)
            consecutive_failures = 0
        else:
            stock["market_cap"] = 0
            stock["cap_tier"] = "unknown"
            consecutive_failures += 1
            logger.debug(f"Market cap unavailable for {ticker} (rate-limited?)")


def enrich_with_rvol(stocks: List[Dict], yf_connector, in_premarket: bool = True) -> None:
    """
    Add 'rvol' and 'rvol_basis' to each stock dict in-place.

    Premarket:        rvol = yesterday's full session volume / 20-day ADV
                      (proxy — live premarket volume is unavailable on free feeds)
    Regular session:  rvol = today's live volume / 20-day ADV

    Uses Yahoo Finance daily chart API for the historical baseline.
    Stocks where baseline data is unavailable get rvol=None (RVOL filter skipped for them).
    """
    tickers = [s["ticker"] for s in stocks if s.get("ticker")]
    if not tickers:
        return

    vol_data = yf_connector.get_volume_data(tickers, lookback_days=20)

    for stock in stocks:
        ticker = stock.get("ticker", "")
        vd = vol_data.get(ticker)

        if not vd or vd["avg_vol"] == 0:
            stock["rvol"] = None
            continue

        if in_premarket:
            stock["rvol"] = round(vd["prev_vol"] / vd["avg_vol"], 2)
            stock["rvol_basis"] = "prev_session"
        else:
            stock["rvol"] = round(stock.get("volume", 0) / vd["avg_vol"], 2)
            stock["rvol_basis"] = "live"


def filter_gappers(stocks: List[Dict], gap_threshold: float = 5.0,
                   min_rvol: float = 3.0, top_n: int = 20) -> List[Dict]:
    """
    Filter stocks by gapper criteria, enrich with market cap tier, and sort:
      primary   — cap tier (mega → large → mid → small → micro → unknown)
      secondary — gap % descending within each tier

    RVOL filter only applies when a stock has an 'rvol' field set (regular session).
    During premarket the field is absent and the filter is skipped automatically.
    """
    filtered = []

    for stock in stocks:
        try:
            gap_pct = float(stock.get("gap_pct", 0))
            rvol = stock.get("rvol", None)

            if gap_pct < gap_threshold:
                continue

            # Only apply RVOL filter when we have a measured value
            if rvol is not None and rvol < min_rvol:
                continue

            filtered.append(stock)
        except (ValueError, TypeError) as e:
            logger.warning(f"Error processing stock {stock.get('ticker', 'UNKNOWN')}: {e}")
            continue

    if not filtered:
        return []

    logger.info(f"Fetching market cap for {len(filtered)} gappers...")
    enrich_with_market_cap(filtered)

    # Stable sort: gap% descending first, then tier ascending
    filtered.sort(key=lambda x: float(x.get("gap_pct", 0)), reverse=True)
    filtered.sort(key=lambda x: _TIER_ORDER.get(x.get("cap_tier", "unknown"), len(_CAP_TIERS)))

    return filtered[:top_n]
