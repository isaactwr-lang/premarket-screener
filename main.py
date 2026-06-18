"""Main entry point for the Premarket Screener."""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

from agents.gappers_screener import GappersScreener
from utils.telegram_notifier import send_results, send_no_results
from utils.time_utils import get_us_date


def main():
    print("\n" + "=" * 80)
    print("PREMARKET SCREENER")
    print("=" * 80 + "\n")

    alpaca_api_key = os.getenv("ALPACA_API_KEY")
    alpaca_secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not (alpaca_api_key and alpaca_secret_key):
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env")
        sys.exit(1)

    screener = GappersScreener(
        alpaca_api_key=alpaca_api_key,
        alpaca_secret_key=alpaca_secret_key,
    )
    result = screener.run()
    screener.print_results()

    if result.get("status") == "success" and result.get("results"):
        send_results(result["results"], result["scan_date"], result["scan_time"])
    else:
        send_no_results(get_us_date().strftime("%b %d, %Y"))


if __name__ == "__main__":
    main()
