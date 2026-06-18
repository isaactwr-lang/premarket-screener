"""Gappers Screening Agent

Scans premarket gappers and identifies news catalysts driving the moves.
Filters by: gap > 5%, RVOL >= 3x 20-day ADV (regular session only), top 20
Retrieves news catalysts from Benzinga (Alpaca News API as fallback).
"""
import json
import logging
import time
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from connectors.web_fetcher import WebFetcher
from connectors.yfinance_connector import YFinanceConnector
from connectors.alpaca_connector import AlpacaConnector
from utils.filters import filter_gappers, enrich_with_rvol
from utils.time_utils import get_us_date, get_us_datetime, is_premarket_hours

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class GappersScreener:
    """Screens for premarket gappers and retrieves news catalysts."""

    def __init__(self, alpaca_api_key: str, alpaca_secret_key: str):
        self.fetcher = WebFetcher()
        self.yf_connector = YFinanceConnector()

        self.alpaca_connector = AlpacaConnector(alpaca_api_key, alpaca_secret_key, paper=True)
        if self.alpaca_connector.test_connection():
            self.use_alpaca = True
        else:
            logger.warning("Alpaca connection failed — screening default universe only.")
            self.use_alpaca = False

        self.results = []

    # ------------------------------------------------------------------
    # Core workflow
    # ------------------------------------------------------------------

    def run(self, output_dir: str = "output") -> Dict:
        logger.info("Starting Gappers Screener...")

        logger.info("Step 1: Fetching premarket gappers via Yahoo Finance chart API...")
        all_gainers = self.get_premarket_gappers()

        if not all_gainers:
            logger.warning("No gappers found")
            return {"error": "Failed to fetch data"}

        in_premarket = is_premarket_hours()

        # Drop $0 prices (data artefacts from warrants/illiquid names) and pre-filter by gap
        # to avoid running expensive volume lookups on stocks that won't pass anyway
        gap_candidates = [s for s in all_gainers if s.get("price", 0) > 0 and s.get("gap_pct", 0) >= 5.0]
        logger.info(f"{len(gap_candidates)} candidates with gap >= 5% (from {len(all_gainers)} total)")

        if in_premarket:
            logger.info("Step 2: Premarket — enriching with previous session RVOL proxy, filtering by gap > 5% and prev-session RVOL >= 3x...")
        else:
            logger.info("Step 2: Regular session — enriching with live RVOL, filtering by gap > 5% and RVOL >= 3x 20-day ADV...")
        enrich_with_rvol(gap_candidates, self.yf_connector, in_premarket=in_premarket)
        sp500 = getattr(self.yf_connector, "sp500_tickers", None)
        filtered_gappers = filter_gappers(gap_candidates, sp500_tickers=sp500)

        if not filtered_gappers:
            logger.info("No stocks met the filter criteria")
            return {"status": "No gappers found matching criteria"}

        logger.info(f"Found {len(filtered_gappers)} gappers matching criteria")

        logger.info("Step 3: Fetching news catalysts from Benzinga...")
        for stock in filtered_gappers:
            ticker = stock["ticker"]
            logger.info(f"  Fetching catalyst for {ticker}...")
            stock["catalyst"] = self.get_news_catalyst(ticker)
            time.sleep(1.5)

        self.results = filtered_gappers

        logger.info("Step 4: Saving results to JSON...")
        output_file = self._save_results(output_dir)
        logger.info(f"Gappers screening complete. Results saved to {output_file}")

        return {
            "status": "success",
            "count": len(filtered_gappers),
            "output_file": output_file,
            "results": filtered_gappers,
            "scan_date": get_us_date().strftime("%b %d, %Y"),
            "scan_time": get_us_datetime().strftime("%H:%M"),
        }

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def get_premarket_gappers(self) -> List[Dict]:
        """
        Fetch live prices via Yahoo Finance chart API.
        Expands the ticker universe using Alpaca's screener endpoint.
        """
        extra_tickers = []
        if self.use_alpaca:
            logger.info("Fetching extra tickers from Alpaca screener...")
            raw = self.alpaca_connector._screener_tickers()
            # Strip warrants (W), rights (R), units (U) — these are derivative instruments
            # added by the screener that produce misleading gap % readings
            extra_tickers = [t for t in raw if not self._is_derivative_ticker(t)]
            skipped = len(raw) - len(extra_tickers)
            if skipped:
                logger.info(f"Filtered {skipped} derivative tickers (warrants/rights/units) from screener")

        universe = self.yf_connector._default_universe()
        if extra_tickers:
            universe = list(dict.fromkeys(universe + extra_tickers))

        logger.info(f"Screening {len(universe)} tickers for premarket gaps...")
        gappers = self.yf_connector.get_premarket_gainers(universe)
        logger.info(f"Found {len(gappers)} potential gappers")
        return gappers

    def get_news_catalyst(self, ticker: str) -> str:
        """
        Fetch latest news headline for a ticker.
        Tries Benzinga first; falls back to Alpaca News API if blocked.
        """
        headline = self._scrape_benzinga(ticker)
        if headline:
            return headline

        if self.use_alpaca:
            logger.info(f"  Benzinga blocked for {ticker}, trying Alpaca news...")
            headline = self.alpaca_connector.get_news(ticker)
            if headline:
                return headline

        logger.warning(f"No news found for {ticker}")
        return "No news available"

    # ------------------------------------------------------------------
    # Benzinga scraper
    # ------------------------------------------------------------------

    @staticmethod
    def _is_derivative_ticker(ticker: str) -> bool:
        """Return True if the ticker looks like a warrant, right, or unit — not common stock."""
        import re
        # Warrants typically end in W or WS, rights in R, units in U
        # Guard single-letter tickers (e.g. "W" = Wayfair) with length check
        return bool(len(ticker) >= 4 and re.search(r'(W|WS|WW|WWW|R)$', ticker))

    _JUNK_PHRASES = [
        "stock score locked", "want to see it", "sign up", "subscribe",
        "log in", "login", "register", "already a member", "benzinga pro",
        "upgrade", "free trial", "how do i buy", "how to buy",
        "heads up!", "heads up :",
        "who are ", "competitors?", "what is ", "how does ",
    ]

    def _scrape_benzinga(self, ticker: str) -> Optional[str]:
        html = self.fetcher.fetch_html(f"https://www.benzinga.com/quote/{ticker.upper()}")
        if not html:
            return None

        soup = self.fetcher.parse_html(html)
        selectors = [
            ("a", {"data-testid": "news-headline"}),
            ("h3", {"class": re.compile(r"headline", re.I)}),
            ("h2", {"class": re.compile(r"headline", re.I)}),
            ("a", {"class": re.compile(r"title|headline|story", re.I)}),
            ("h3", {}),
        ]

        for tag, attrs in selectors:
            for el in soup.find_all(tag, attrs):
                text = el.get_text(separator=" ").strip()
                if 20 < len(text) < 300 and not self._is_junk(text):
                    return self._clean_headline(text)

        return None

    def _is_junk(self, text: str) -> bool:
        lower = text.lower()
        return any(phrase in lower for phrase in self._JUNK_PHRASES)

    @staticmethod
    def _clean_headline(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        headline = sentences[0].strip()
        if not headline.endswith((".", "!", "?")):
            headline += "."
        return headline

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _save_results(self, output_dir: str) -> str:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        today = get_us_date().strftime("%Y-%m-%d")
        output_file = output_path / f"gappers_{today}.json"

        data = {
            "scan_date": today,
            "scan_time": get_us_datetime().strftime("%H:%M:%S"),
            "total_gappers": len(self.results),
            "criteria": {
                "min_gap_pct": 5.0,
                "min_rvol": "3.0x 20-day ADV (prev session proxy during premarket, live during regular session)",
                "top_n": 20,
            },
            "gappers": self.results,
        }

        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)

        return str(output_file)

    def print_results(self):
        print("\n" + "=" * 80)
        print("PREMARKET GAPPERS SCREENING RESULTS")
        print("=" * 80 + "\n")

        current_tier = None
        for stock in self.results:
            tier = stock.get("cap_tier", "unknown")
            if tier != current_tier:
                current_tier = tier
                label = "UNKNOWN CAP (data unavailable)" if tier == "unknown" else f"{tier.upper()} CAP"
                print(f"  [{label}]")
            rvol = stock.get("rvol")
            rvol_basis = stock.get("rvol_basis", "live")
            if rvol is not None:
                label = "prevRVOL" if rvol_basis == "prev_session" else "RVOL"
                rvol_str = f" {label}:{rvol:.1f}x"
            else:
                rvol_str = ""
            print(f"  * {stock['ticker']} ${stock['price']:.2f} +{stock['gap_pct']:.2f}%{rvol_str} - {stock['catalyst']}")

        print("\n" + "=" * 80 + "\n")
