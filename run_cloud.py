"""
Project Hugo - Cloud Runner
Runs both the trading loop and the dashboard server together.
Designed for Railway / Docker deployment.
"""

import threading
import time
import os
from datetime import datetime, timezone
from database import init_db
from collector import collect_cycle
from paper_trader import PaperTrader
from config import CYCLE_INTERVAL_MINUTES


def trading_loop():
    """Background thread: collect data and trade every cycle."""
    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            print(f"\n[HUGO] === Cycle #{cycle_count} at {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} ===")

            success = collect_cycle()
            if success:
                trader = PaperTrader()
                trader.run_cycle()
            else:
                print("[HUGO] Data collection failed, skipping trade cycle")

        except Exception as e:
            print(f"[HUGO] Error in cycle #{cycle_count}: {e}")
            import traceback
            traceback.print_exc()

        print(f"[HUGO] Next cycle in {CYCLE_INTERVAL_MINUTES} minutes")
        time.sleep(CYCLE_INTERVAL_MINUTES * 60)


def run_dashboard():
    """Main thread: run the Flask dashboard."""
    from dashboard import app
    port = int(os.environ.get("PORT", 5555))
    print(f"[DASHBOARD] Starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    print("=" * 60)
    print("  HUGO - Bittensor Best-of-All Trader")
    print("  Cloud Mode: Trading + Dashboard")
    print("=" * 60)

    # Initialise database
    init_db()

    # Start trading loop in background thread
    trade_thread = threading.Thread(target=trading_loop, daemon=True)
    trade_thread.start()

    # Run dashboard in main thread (this blocks)
    run_dashboard()
