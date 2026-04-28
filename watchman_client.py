"""
Watchman Client - Queries the Watchman regime detector for trading guidance.
Falls back to normal behaviour if Watchman is unreachable.
"""

import requests

WATCHMAN_URL = "https://watchman-production-8d09.up.railway.app"
TIMEOUT = 10


def get_regime():
    """
    Get current market regime from Watchman.
    Returns dict with score, mode, and breakdown.
    Falls back to NORMAL if unreachable.
    """
    try:
        r = requests.get(f"{WATCHMAN_URL}/api/regime", timeout=TIMEOUT)
        if r.ok:
            data = r.json()
            print(f"[WATCHMAN] Regime: {data.get('score', '?')}/100 - {data.get('mode', '?')}")
            return data
    except Exception as e:
        print(f"[WATCHMAN] Unreachable ({e}), using defaults")

    return {"score": 50, "mode": "NORMAL", "breakdown": {}}


def get_recommendation(bot_name):
    """
    Get per-bot recommendation from Watchman.
    Returns max_positions and entry_multiplier.
    Falls back to standard settings if unreachable.
    """
    try:
        r = requests.get(f"{WATCHMAN_URL}/api/recommendation/{bot_name}", timeout=TIMEOUT)
        if r.ok:
            rec = r.json()
            print(f"[WATCHMAN] {bot_name} rec: max_pos={rec.get('max_positions')}, "
                  f"entry_mult={rec.get('entry_multiplier')}, mode={rec.get('mode')}")
            return rec
    except Exception as e:
        print(f"[WATCHMAN] Unreachable for {bot_name} ({e}), using defaults")

    return {"max_positions": 5, "entry_multiplier": 1.0, "mode": "NORMAL"}
