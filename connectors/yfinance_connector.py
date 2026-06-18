"""Yahoo Finance chart API connector — live premarket and regular session prices.

Uses Yahoo Finance's v8 chart API directly rather than the yfinance library,
because the chart endpoint includes pre/post-market candles and is not
subject to the same rate limits as the info/fast_info endpoints.
"""
import logging
import sys
import time
import requests
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.time_utils import is_premarket_hours

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_CHART_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}


class YFinanceConnector:
    """Fetches live prices from Yahoo Finance chart API."""

    def get_premarket_gainers(self, tickers: Optional[List[str]] = None) -> List[Dict]:
        """
        Return price data for all tickers in the given list.
        Handles premarket and regular session automatically.
        """
        universe = tickers or self._default_universe()
        in_premarket = is_premarket_hours()
        session_label = "premarket" if in_premarket else "regular session"
        logger.info(f"Fetching prices for {len(universe)} tickers via Yahoo chart API ({session_label})...")

        gainers = []
        for ticker in universe:
            data = self._fetch_chart(ticker, in_premarket=in_premarket)
            if data is not None and data["gap_pct"] >= 0.1:
                gainers.append(data)
            time.sleep(0.2)

        gainers.sort(key=lambda x: x["gap_pct"], reverse=True)
        logger.info(f"Found {len(gainers)} tickers with positive gap")
        return gainers

    def _fetch_chart(self, ticker: str, in_premarket: bool = True) -> Optional[Dict]:
        """
        GET /v8/finance/chart/{ticker}?interval=1m&range=1d&prePost=true

        The correct prev_close field differs by session:
          - Premarket:        regularMarketPrice = yesterday's official close
          - Regular session:  regularMarketPrice updates live → use chartPreviousClose
        """
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            r = requests.get(
                url,
                headers=_CHART_HEADERS,
                params={"interval": "1m", "range": "1d", "prePost": "true", "includePrePost": "true"},
                timeout=10,
            )
            if r.status_code != 200:
                logger.debug(f"Chart API {r.status_code} for {ticker}")
                return None

            result = r.json()["chart"]["result"][0]
            meta = result["meta"]

            if in_premarket:
                prev_close = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
            else:
                prev_close = meta.get("chartPreviousClose") or meta.get("regularMarketPrice")

            if not prev_close or prev_close == 0:
                return None

            quotes = result.get("indicators", {}).get("quote", [{}])[0]
            closes = quotes.get("close", [])
            volumes = quotes.get("volume", [])

            current_price = next((c for c in reversed(closes) if c is not None), None)
            if not current_price:
                return None

            total_volume = sum(v for v in volumes if v is not None)
            gap_pct = (current_price - prev_close) / prev_close * 100

            return {
                "ticker": ticker,
                "price": round(current_price, 4),
                "prev_close": round(prev_close, 4),
                "gap_pct": round(gap_pct, 4),
                "volume": int(total_volume),
            }

        except Exception as e:
            logger.debug(f"Chart fetch error for {ticker}: {e}")
            return None

    def get_volume_data(self, tickers: List[str], lookback_days: int = 20) -> Dict:
        """
        Fetch historical daily volume for a list of tickers via Yahoo chart API.
        Returns {ticker: {"prev_vol": int, "avg_vol": int}} where:
          prev_vol = most recent completed session volume
          avg_vol  = mean of the prior lookback_days sessions (the baseline)
        """
        result = {}
        logger.info(f"Fetching {lookback_days}-day volume baseline for {len(tickers)} tickers...")
        for ticker in tickers:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                r = requests.get(
                    url,
                    headers=_CHART_HEADERS,
                    params={"interval": "1d", "range": "1mo"},
                    timeout=10,
                )
                if r.status_code != 200:
                    continue
                volumes = r.json()["chart"]["result"][0]["indicators"]["quote"][0].get("volume", [])
                volumes = [v for v in volumes if v is not None and v > 0]
                if len(volumes) >= 2:
                    prev_vol = volumes[-1]
                    baseline = volumes[:-1]
                    result[ticker] = {
                        "prev_vol": int(prev_vol),
                        "avg_vol": int(sum(baseline) / len(baseline)),
                    }
                time.sleep(0.15)
            except Exception as e:
                logger.debug(f"Volume data error for {ticker}: {e}")
        logger.info(f"Got volume baseline for {len(result)}/{len(tickers)} tickers")
        return result

    _SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

    # Emergency fallback — only used if Wikipedia is unreachable
    _FALLBACK_UNIVERSE = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM",
        "JNJ", "V", "WMT", "PG", "MA", "HD", "MCD", "DIS", "INTC", "MU",
        "AMD", "NFLX", "CRM", "ADBE", "CSCO", "PEP", "COST", "QCOM", "AVGO",
        "BA", "CAT", "LMT", "RTX", "GE", "TXN", "GILD", "ASML", "ORCL",
        "GS", "MS", "BAC", "WFC", "C", "UNH", "PFE", "ABBV", "LLY", "MRK",
        "XOM", "CVX", "SQ", "ROKU", "SNOW", "DDOG", "NET", "CRWD", "PLTR",
        "AI", "ARM", "IONQ", "SOFI", "SMCI", "RKLB", "MSTR", "COIN",
    ]

    _STATIC_SP500_PATH = Path(__file__).parent.parent / "data" / "sp500_tickers.txt"

    def _default_universe(self) -> List[str]:
        """
        Fetch S&P 500 tickers from Wikipedia.
        Falls back to the committed data/sp500_tickers.txt if Wikipedia is
        unreachable (common in CI/cloud environments), then to a small
        hardcoded emergency list as a last resort.
        """
        try:
            import pandas as pd
            from io import StringIO
            resp = requests.get(
                self._SP500_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; premarket-screener/1.0)"},
                timeout=10,
            )
            resp.raise_for_status()
            tables = pd.read_html(StringIO(resp.text), attrs={"id": "constituents"})
            tickers = tables[0]["Symbol"].tolist()
            # Wikipedia uses dots for share classes (e.g. BRK.B) — Yahoo uses dashes
            tickers = [t.replace(".", "-") for t in tickers]
            logger.info(f"Loaded {len(tickers)} tickers from S&P 500 Wikipedia list")
            self.sp500_tickers = set(tickers)
            return tickers
        except Exception as e:
            logger.warning(f"Wikipedia unavailable ({e}) — trying static fallback file")

        try:
            tickers = self._STATIC_SP500_PATH.read_text().splitlines()
            tickers = [t.strip() for t in tickers if t.strip()]
            logger.info(f"Loaded {len(tickers)} tickers from static fallback file")
            self.sp500_tickers = set(tickers)
            return tickers
        except Exception as e:
            logger.warning(f"Static fallback file unavailable ({e}) — using hardcoded emergency list")
            self.sp500_tickers = set(self._FALLBACK_UNIVERSE)
            return self._FALLBACK_UNIVERSE
