"""Market data fetcher for weekly recap email.

Pulls equity index / ETF / currency returns from yfinance and
yield / spread data from the FRED API (free, requires API key).
"""
import logging
import os
from datetime import date
from typing import Dict, List, Optional, Tuple

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# ── Instrument definitions ─────────────────────────────────────────────────

INDICES: List[Tuple[str, str]] = [
    ("S&P 500",        "^GSPC"),
    ("Nasdaq",         "^IXIC"),
    ("Dow Jones",      "^DJI"),
    ("Russell 2000",   "^RUT"),
    ("Euro STOXX 50",  "^STOXX50E"),
    ("FTSE 100",       "^FTSE"),
    ("Nikkei 225",     "^N225"),
    ("KOSPI",          "^KS11"),
    ("TSMC",           "TSM"),
    ("Hang Seng",      "^HSI"),
    ("CSI 300",        "000300.SS"),
    ("Straits Times",  "^STI"),
    ("ILF",            "ILF"),
    ("VIX",            "^VIX"),
    ("Copper",         "HG=F"),
    ("WTI Crude",      "CL=F"),
]

BOND_ETFS: List[Tuple[str, str]] = [
    ("TLT (20Y Treasury)", "TLT"),
    ("HYG (High Yield)",   "HYG"),
    ("LQD (IG Corp)",      "LQD"),
]

CURRENCIES: List[Tuple[str, str]] = [
    ("USD/SGD", "USDSGD=X"),
    ("EUR/USD", "EURUSD=X"),
    ("USD/JPY", "USDJPY=X"),
    ("BTC/USD", "BTC-USD"),
    ("ETH/USD", "ETH-USD"),
    ("XAU/USD", "GC=F"),
    ("XAG/USD", "SI=F"),
]

SIGNAL_RATIOS: List[Tuple[str, str, str]] = [
    ("SPY / VIX", "SPY",  "^VIX"),
    ("RSP / SPY", "RSP",  "SPY"),
    ("IWD / IWF", "IWD",  "IWF"),
]

# Daily FRED series (US yields + spreads)
FRED_DAILY: List[Tuple[str, str]] = [
    ("US 2Y",      "DGS2"),
    ("US 10Y",     "DGS10"),
    ("US 30Y",     "DGS30"),
    ("HY Spread",  "BAMLH0A0HYM2"),
    ("IG Spread",  "BAMLC0A0CM"),
]

# Monthly FRED series (sovereign yields — show level only, no weekly Δ)
FRED_MONTHLY: List[Tuple[str, str]] = [
    ("German Bund 10Y", "IRLTLT01DEM156N"),
    ("UK Gilt 10Y",     "IRLTLT01GBM156N"),
    ("Japan JGB 10Y",   "IRLTLT01JPM156N"),
]

# ── yfinance helpers ───────────────────────────────────────────────────────

def _returns(ticker: str) -> Optional[Dict]:
    """Return last price + weekly/MTD/YTD % for a Yahoo Finance ticker."""
    try:
        hist = yf.Ticker(ticker).history(period="ytd", auto_adjust=True)
        if hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None
        last = float(closes.iloc[-1])

        week_base = float(closes.iloc[max(0, len(closes) - 6)])
        weekly = (last / week_base - 1) * 100

        today = date.today()
        month_data = closes[closes.index.month == today.month]
        mtd_base = float(month_data.iloc[0]) if not month_data.empty else float(closes.iloc[0])
        mtd = (last / mtd_base - 1) * 100

        ytd = (last / float(closes.iloc[0]) - 1) * 100

        return {"last": last, "weekly": weekly, "mtd": mtd, "ytd": ytd}
    except Exception as e:
        logger.warning(f"yfinance [{ticker}]: {e}")
        return None

# ── Ratio helpers ─────────────────────────────────────────────────────────

def _ratio(t1: str, t2: str) -> Optional[Dict]:
    """Return current ratio of two tickers plus its weekly change."""
    try:
        h1 = yf.Ticker(t1).history(period="ytd", auto_adjust=True)["Close"].dropna()
        h2 = yf.Ticker(t2).history(period="ytd", auto_adjust=True)["Close"].dropna()
        if len(h1) < 2 or len(h2) < 2:
            return None
        ratio_now  = float(h1.iloc[-1])  / float(h2.iloc[-1])
        ratio_week = float(h1.iloc[max(0, len(h1) - 6)]) / float(h2.iloc[max(0, len(h2) - 6)])
        return {"ratio": round(ratio_now, 4), "weekly_change": round(ratio_now - ratio_week, 4)}
    except Exception as e:
        logger.warning(f"ratio [{t1}/{t2}]: {e}")
        return None

# ── FRED helpers ───────────────────────────────────────────────────────────

def _fred(series_id: str, api_key: str, monthly: bool = False) -> Optional[Dict]:
    """Fetch latest observation (and weekly Δ for daily series) from FRED."""
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 30,
            },
            timeout=10,
        )
        r.raise_for_status()
        obs = [
            (o["date"], float(o["value"]))
            for o in r.json()["observations"]
            if o["value"] != "."
        ]
        if not obs:
            return None
        latest = obs[0][1]
        if monthly:
            prev = obs[1][1] if len(obs) > 1 else None
            return {
                "value": round(latest, 2),
                "weekly_bps": round((latest - prev) * 100, 1) if prev is not None else None,
            }
        if len(obs) < 6:
            return {"value": round(latest, 2), "weekly_bps": None}
        week_ago = obs[min(5, len(obs) - 1)][1]
        return {
            "value": round(latest, 2),
            "weekly_bps": round((latest - week_ago) * 100, 1),
        }
    except Exception as e:
        logger.warning(f"FRED [{series_id}]: {e}")
        return None

# ── Main entry point ───────────────────────────────────────────────────────

def fetch_all(fred_api_key: str) -> Dict:
    logger.info("Fetching market data (yfinance + FRED)...")

    indices   = [(n, _returns(t))  for n, t in INDICES]
    bond_etfs = [(n, _returns(t))  for n, t in BOND_ETFS]
    currencies = [(n, _returns(t)) for n, t in CURRENCIES]

    yields_daily   = [(n, _fred(s, fred_api_key))              for n, s in FRED_DAILY]
    yields_monthly = [(n, _fred(s, fred_api_key, monthly=True)) for n, s in FRED_MONTHLY]

    # Separate US yields from spreads
    us_yields = [(n, d) for n, d in yields_daily if "Spread" not in n]
    spreads   = [(n, d) for n, d in yields_daily if "Spread" in n]

    # Derived: 10Y − 2Y
    d_2y  = next((d for n, d in us_yields if n == "US 2Y"),  None)
    d_10y = next((d for n, d in us_yields if n == "US 10Y"), None)
    if d_2y and d_10y:
        spread_10y_2y = {
            "value": round((d_10y["value"] - d_2y["value"]) * 100, 1),
            "weekly_bps": (
                round(d_10y["weekly_bps"] - d_2y["weekly_bps"], 1)
                if d_10y["weekly_bps"] is not None and d_2y["weekly_bps"] is not None
                else None
            ),
        }
    else:
        spread_10y_2y = None

    # Derived: LQD / HYG ratio + signal ratios
    lqd_hyg = _ratio("LQD", "HYG")
    signals  = [(name, _ratio(t1, t2)) for name, t1, t2 in SIGNAL_RATIOS]

    return {
        "indices":        indices,
        "bond_etfs":      bond_etfs,
        "currencies":     currencies,
        "us_yields":      us_yields,
        "sovereign":      yields_monthly,
        "spreads":        spreads,
        "spread_10y_2y":  spread_10y_2y,
        "lqd_hyg_ratio":  lqd_hyg,
        "signals":        signals,
    }
