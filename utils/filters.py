"""Utility functions for filtering and processing stock data."""
from typing import List, Dict, Optional
import logging
import time

import requests

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


_CHART_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _fetch_market_cap(ticker: str) -> Optional[int]:
    """Return market cap in dollars via Yahoo chart API, or None if unavailable."""
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            headers=_CHART_HEADERS,
            params={"interval": "1d", "range": "1d"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        mc = r.json()["chart"]["result"][0]["meta"].get("marketCap")
        return int(mc) if mc and mc > 0 else None
    except Exception:
        return None


def enrich_with_market_cap(stocks: List[Dict], sp500_tickers: Optional[set] = None) -> None:
    """
    Add 'market_cap' and 'cap_tier' to each stock dict in-place.

    S&P 500 members are classified as 'large' without an API call — S&P 500
    membership requires $18B+ market cap so the tier is unambiguous.
    For non-S&P-500 tickers the Yahoo chart API is tried; failures get 'unknown'.
    """
    for stock in stocks:
        ticker = stock.get("ticker", "")
        if sp500_tickers and ticker in sp500_tickers:
            stock["market_cap"] = 0  # exact value not needed
            stock["cap_tier"] = "large"
            continue
        mc = _fetch_market_cap(ticker)
        if mc:
            stock["market_cap"] = mc
            stock["cap_tier"] = _cap_tier_from_market_cap(mc)
        else:
            stock["market_cap"] = 0
            stock["cap_tier"] = "unknown"
            logger.debug(f"Market cap unavailable for {ticker}")


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


# For large/mega caps the gap itself is the signal — skip RVOL filter entirely
# For smaller caps, unusual volume confirms the move is real
_RVOL_BY_TIER = {
    "mega":    0.0,
    "large":   0.0,
    "mid":     1.5,
    "small":   3.0,
    "micro":   3.0,
    "unknown": 3.0,
}


def filter_gappers(stocks: List[Dict], gap_threshold: float = 5.0, top_n: int = 20,
                   sp500_tickers: Optional[set] = None) -> List[Dict]:
    """
    Filter stocks by gapper criteria, enrich with market cap tier, and sort:
      primary   — cap tier (mega → large → mid → small → micro → unknown)
      secondary — gap % descending within each tier

    RVOL threshold is tiered by market cap: large/mega caps use a lower bar
    since their average daily volume is so high that 3x is rarely reached.
    RVOL filter is skipped entirely when rvol is None (premarket proxy unavailable).
    """
    # First pass: gap filter only
    gap_passed = []
    for stock in stocks:
        try:
            if float(stock.get("gap_pct", 0)) >= gap_threshold:
                gap_passed.append(stock)
        except (ValueError, TypeError) as e:
            logger.warning(f"Error processing stock {stock.get('ticker', 'UNKNOWN')}: {e}")

    if not gap_passed:
        return []

    # Enrich with market cap before RVOL filter so we can apply tier-based thresholds
    logger.info(f"Fetching market cap for {len(gap_passed)} gappers...")
    enrich_with_market_cap(gap_passed, sp500_tickers=sp500_tickers)

    # Second pass: tier-aware RVOL filter
    filtered = []
    for stock in gap_passed:
        rvol = stock.get("rvol", None)
        if rvol is not None:
            tier = stock.get("cap_tier", "unknown")
            min_rvol = _RVOL_BY_TIER.get(tier, 3.0)
            if rvol < min_rvol:
                logger.debug(f"Filtered {stock['ticker']} ({tier}): RVOL {rvol:.1f}x < {min_rvol}x threshold")
                continue
        filtered.append(stock)

    if not filtered:
        return []

    # Stable sort: gap% descending first, then tier ascending
    filtered.sort(key=lambda x: float(x.get("gap_pct", 0)), reverse=True)
    filtered.sort(key=lambda x: _TIER_ORDER.get(x.get("cap_tier", "unknown"), len(_CAP_TIERS)))

    return filtered[:top_n]
