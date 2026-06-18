"""Alpaca connector for market data retrieval.

Two separate base URLs:
- data.alpaca.markets  → market data, screener, snapshots, bars
- paper-api / api.alpaca.markets → account, orders (trading)
"""
import logging
from typing import List, Dict, Optional
import requests

logger = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets"
PAPER_TRADE_URL = "https://paper-api.alpaca.markets"
LIVE_TRADE_URL = "https://api.alpaca.markets"


class AlpacaConnector:
    """Handles market data retrieval from Alpaca Markets API."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.trade_url = PAPER_TRADE_URL if paper else LIVE_TRADE_URL

        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
        }
        self.timeout = 15
        logger.info(f"Alpaca connector ready ({'paper' if paper else 'live'})")

    def test_connection(self) -> bool:
        """Verify credentials against the account endpoint."""
        try:
            url = f"{self.trade_url}/v2/account"
            r = requests.get(url, headers=self.headers, timeout=self.timeout)
            if r.status_code == 200:
                acct = r.json()
                logger.info(f"Connected — account {acct.get('account_number', 'N/A')}")
                return True
            logger.error(f"Auth failed: {r.status_code} {r.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    # ------------------------------------------------------------------
    # Primary screener
    # ------------------------------------------------------------------

    def get_market_gainers(self) -> List[Dict]:
        """
        Return top gainers using snapshot-based gap calculation.
        Augments the curated universe with any tickers from the screener endpoint
        so new movers outside the hardcoded list are captured.
        """
        # Pull screener tickers to supplement the hardcoded universe
        extra_tickers = self._screener_tickers()
        return self._snapshot_gainers(extra_tickers)

    def _screener_tickers(self) -> List[str]:
        """
        Pull ticker symbols from the screener endpoint to augment the snapshot universe.
        We only use the symbols here — prices/volumes come from snapshots for consistency.
        """
        try:
            url = f"{DATA_URL}/v1beta1/screener/stocks/movers"
            r = requests.get(url, headers=self.headers, params={"top": 50}, timeout=self.timeout)
            if r.status_code != 200:
                return []
            data = r.json()
            symbols = [item.get("symbol", "") for item in data.get("gainers", []) if item.get("symbol")]
            logger.info(f"Screener added {len(symbols)} extra tickers to universe")
            return symbols
        except Exception as e:
            logger.debug(f"Screener tickers error: {e}")
            return []

    # ------------------------------------------------------------------
    # Snapshot fallback
    # ------------------------------------------------------------------

    def _snapshot_gainers(self, extra_tickers: Optional[List[str]] = None) -> List[Dict]:
        """
        Fetch multi-symbol snapshots and calculate gap vs previous close.
        Uses the latest trade price so premarket moves are captured.
        """
        universe = [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM",
            "JNJ", "V", "WMT", "PG", "MA", "HD", "MCD", "DIS", "INTC",
            "AMD", "NFLX", "CRM", "ADBE", "CSCO", "PEP", "COST", "QCOM",
            "BA", "CAT", "LMT", "RTX", "GE", "TXN", "GILD", "ASML",
            "SQ", "ROKU", "DASH", "SPOT", "SNOW", "DDOG", "NET", "CRWD",
            "PINS", "RBLX", "COIN", "HOOD", "GME", "NIO", "BABA", "UPST",
            "AI", "PLTR", "ARM", "IONQ", "SOFI", "SMCI", "RKLB", "MSTR",
        ]

        if extra_tickers:
            combined = list(dict.fromkeys(universe + extra_tickers))  # deduplicate, preserve order
        else:
            combined = universe

        logger.info(f"Screening {len(combined)} symbols via snapshots...")
        snapshots = self._get_snapshots(combined)
        gainers = []

        for ticker, snap in snapshots.items():
            try:
                prev_close = float(snap.get("prevDailyBar", {}).get("c", 0))
                if prev_close == 0:
                    continue

                # Latest trade captures premarket activity
                current = (
                    float(snap.get("latestTrade", {}).get("p", 0))
                    or float(snap.get("dailyBar", {}).get("c", 0))
                )
                if current == 0:
                    continue

                gap_pct = (current - prev_close) / prev_close * 100
                volume = int(snap.get("dailyBar", {}).get("v", 0))

                if gap_pct >= 0.1:
                    gainers.append({
                        "ticker": ticker,
                        "price": current,
                        "gap_pct": gap_pct,
                        "volume": volume,
                        "prev_close": prev_close,
                    })
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"Snapshot error for {ticker}: {e}")

        gainers.sort(key=lambda x: x["gap_pct"], reverse=True)
        logger.info(f"Snapshot fallback found {len(gainers)} gainers")
        return gainers

    def _get_snapshots(self, symbols: List[str]) -> Dict:
        """GET /v2/stocks/snapshots — batch fetch for multiple tickers."""
        try:
            url = f"{DATA_URL}/v2/stocks/snapshots"
            r = requests.get(
                url,
                headers=self.headers,
                params={"symbols": ",".join(symbols), "feed": "iex"},
                timeout=30,
            )
            if r.status_code != 200:
                logger.warning(f"Snapshots: {r.status_code} — {r.text[:200]}")
                return {}
            return r.json()
        except Exception as e:
            logger.warning(f"Snapshots error: {e}")
            return {}

    def get_news(self, ticker: str, limit: int = 3) -> Optional[str]:
        """
        Fetch the most recent news headline for a ticker via Alpaca News API.
        Returns the headline of the latest article, or None if unavailable.
        """
        try:
            url = f"{DATA_URL}/v1beta1/news"
            r = requests.get(
                url,
                headers=self.headers,
                params={"symbols": ticker, "limit": limit, "sort": "desc"},
                timeout=self.timeout,
            )
            if r.status_code != 200:
                logger.debug(f"News API {r.status_code} for {ticker}")
                return None

            articles = r.json().get("news", [])
            if not articles:
                return None

            headline = articles[0].get("headline", "").strip()
            return headline if headline else None

        except Exception as e:
            logger.debug(f"News API error for {ticker}: {e}")
            return None

    def get_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 5) -> Optional[List[Dict]]:
        """GET /v2/stocks/{symbol}/bars — OHLCV bars."""
        try:
            url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
            r = requests.get(
                url,
                headers=self.headers,
                params={"timeframe": timeframe, "limit": limit, "adjustment": "raw", "feed": "iex"},
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json().get("bars", [])
        except requests.RequestException as e:
            logger.debug(f"Bars error for {symbol}: {e}")
            return None

