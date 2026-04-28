"""
Project Hugo - Data Collector
Pulls subnet data from Taostats API and stores in local database.
Designed to stay within free tier: 5 calls/min, 10k calls/month.
"""

import requests
import time
from datetime import datetime, timezone
from config import TAOSTATS_API_KEY, TAOSTATS_BASE_URL, RAO_PER_TAO
from database import init_db, store_subnet_snapshot, store_price_history


class TaostatsClient:
    """Minimal Taostats API client."""

    def __init__(self):
        self.base_url = TAOSTATS_BASE_URL
        self.headers = {"Authorization": TAOSTATS_API_KEY}
        self.calls_this_minute = 0
        self.minute_start = time.time()

    def _rate_limit(self):
        """Enforce 5 calls per minute limit."""
        now = time.time()
        if now - self.minute_start >= 60:
            self.calls_this_minute = 0
            self.minute_start = now

        if self.calls_this_minute >= 4:  # leave 1 spare
            wait = 60 - (now - self.minute_start)
            if wait > 0:
                print(f"[API] Rate limit: waiting {wait:.0f}s")
                time.sleep(wait)
            self.calls_this_minute = 0
            self.minute_start = time.time()

        self.calls_this_minute += 1

    def _get(self, endpoint, params=None):
        """Make a GET request to the API."""
        self._rate_limit()
        url = f"{self.base_url}{endpoint}"
        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            else:
                print(f"[API] Error {r.status_code} on {endpoint}: {r.text[:200]}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"[API] Request failed for {endpoint}: {e}")
            return None

    def get_all_subnet_pools(self):
        """Fetch all subnet pool data. Paginates to get all 129 subnets."""
        all_data = []
        page = 1
        while True:
            result = self._get("/dtao/pool/latest/v1", {
                "limit": 200,
                "page": page,
            })
            if not result or "data" not in result:
                break

            all_data.extend(result["data"])

            pagination = result.get("pagination", {})
            if pagination.get("next_page") is None:
                break
            page = pagination["next_page"]

        return all_data

    def get_tao_flow(self):
        """Fetch TAO flow data for all subnets (single call, returns all)."""
        result = self._get("/dtao/tao_flow/v1")
        if result and "data" in result:
            return result["data"]
        return []


def collect_cycle():
    """Run one data collection cycle. Uses 2 API calls."""
    client = TaostatsClient()
    now = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*60}")
    print(f"[COLLECT] Starting cycle at {now}")
    print(f"{'='*60}")

    # Call 1: Get all subnet pools (includes 7-day price history)
    print("[COLLECT] Fetching subnet pools...")
    pools = client.get_all_subnet_pools()
    if not pools:
        print("[COLLECT] ERROR: No pool data returned")
        return False

    print(f"[COLLECT] Got {len(pools)} subnets")

    # Call 2: Get TAO flow
    print("[COLLECT] Fetching TAO flow...")
    flows = client.get_tao_flow()
    print(f"[COLLECT] Got {len(flows)} flow entries")

    # Store snapshots
    snap_count = store_subnet_snapshot(pools, flows, now)
    print(f"[COLLECT] Stored {snap_count} subnet snapshots")

    # Store price history (from seven_day_prices in pool data)
    price_count = store_price_history(pools, now)
    print(f"[COLLECT] Stored {price_count} new price history points")

    # Quick summary
    _print_summary(pools)

    return True


def _print_summary(pools):
    """Print a quick summary of the data collected."""
    active = [p for p in pools if not p.get("startup_mode")]
    startup = [p for p in pools if p.get("startup_mode")]

    # Find interesting subnets
    by_volume = sorted(active, key=lambda x: float(x.get("tao_volume_24_hr") or 0), reverse=True)
    by_price_change = sorted(active, key=lambda x: float(x.get("price_change_1_week") or 0), reverse=True)

    print(f"\n[SUMMARY]")
    print(f"  Active subnets: {len(active)}")
    print(f"  Startup mode: {len(startup)}")

    if by_volume[:3]:
        print(f"  Top 3 by 24h volume:")
        for s in by_volume[:3]:
            vol_tao = float(s.get("tao_volume_24_hr") or 0) / RAO_PER_TAO
            print(f"    SN{s['netuid']} {s.get('name', '?'):20s} vol: {vol_tao:,.0f} TAO")

    if by_price_change[:3]:
        print(f"  Top 3 by 7d price change:")
        for s in by_price_change[:3]:
            pct = float(s.get("price_change_1_week") or 0)
            print(f"    SN{s['netuid']} {s.get('name', '?'):20s} 7d: {pct:+.1f}%")

    worst = sorted(active, key=lambda x: float(x.get("price_change_1_week") or 0))[:3]
    if worst:
        print(f"  Bottom 3 by 7d price change:")
        for s in worst:
            pct = float(s.get("price_change_1_week") or 0)
            print(f"    SN{s['netuid']} {s.get('name', '?'):20s} 7d: {pct:+.1f}%")


if __name__ == "__main__":
    init_db()
    collect_cycle()
