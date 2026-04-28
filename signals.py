"""
Project Hugo - Signal Engine
Adina's 13 hard pre-filters + Joshua's 7-signal scoring engine.
The combination is: Adina filters -> Joshua scoring.
Best-of-all approach: strictest filters with deepest scoring.
"""

import numpy as np
from datetime import datetime, timezone
from database import get_connection, get_latest_snapshots, get_price_history
from config import (
    FILTER_MAX_PRICE, FILTER_MIN_POOL_TAO, FILTER_MAX_POOL_TAO,
    FILTER_MAX_GINI, FILTER_MAX_MONTHLY_PUMP,
    FILTER_ACCEL_SELL_DAY, FILTER_ACCEL_SELL_WEEK,
    FILTER_FLAT_MONTH, FILTER_FLAT_WEEK,
    FILTER_STRUCTURAL_DECLINE, FILTER_DAY_CRASH,
    FILTER_DEREG_RISK_RANK,
    SCORE_WEIGHT_TREND_7D, SCORE_WEIGHT_EMA_TIMING,
    SCORE_WEIGHT_EMISSION_STRENGTH, SCORE_WEIGHT_POOL_VELOCITY,
    SCORE_WEIGHT_BUYER_DIVERSITY, SCORE_WEIGHT_RELATIVE_STRENGTH,
    SCORE_WEIGHT_VOLUME_PROFILE, MIN_SCORE_ENTRY,
    RAO_PER_TAO
)


def run_screener():
    """Run Hugo's screening pipeline: Adina filters + Joshua scoring."""
    snapshots = get_latest_snapshots()
    if not snapshots:
        print("[HUGO] No snapshot data available")
        return []

    # Calculate market-wide stats for relative strength
    market_stats = calculate_market_stats(snapshots)

    filtered = apply_prefilters(snapshots)
    print(f"[HUGO] Pre-filters: {len(snapshots)} -> {len(filtered)} subnets")

    if not filtered:
        return []

    scored = []
    for subnet in filtered:
        score, breakdown = score_subnet(subnet, market_stats)
        if score is not None:
            scored.append({
                "netuid": subnet["netuid"],
                "name": subnet.get("name", "?"),
                "price": subnet["price"],
                "pool_tao": subnet["total_tao"] / RAO_PER_TAO if subnet["total_tao"] else 0,
                "price_change_1w": subnet.get("price_change_1w", 0),
                "price_change_1d": subnet.get("price_change_1d", 0),
                "volume_24h_tao": subnet.get("tao_volume_24h", 0) / RAO_PER_TAO if subnet.get("tao_volume_24h") else 0,
                "tao_flow": subnet.get("tao_flow", 0),
                "score": score,
                "breakdown": breakdown,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    print(f"[HUGO] Scored {len(scored)} subnets")
    return scored


def calculate_market_stats(snapshots):
    """Calculate market-wide averages for relative strength scoring."""
    weekly_changes = []
    daily_changes = []
    for s in snapshots:
        if s["netuid"] == 0:
            continue
        w = s.get("price_change_1w", 0) or 0
        d = s.get("price_change_1d", 0) or 0
        if w != 0:
            weekly_changes.append(w)
        if d != 0:
            daily_changes.append(d)

    return {
        "avg_weekly": np.mean(weekly_changes) if weekly_changes else 0,
        "median_weekly": np.median(weekly_changes) if weekly_changes else 0,
        "avg_daily": np.mean(daily_changes) if daily_changes else 0,
        "std_weekly": np.std(weekly_changes) if weekly_changes else 1,
    }


def apply_prefilters(snapshots):
    """Apply Siam's 13 hard pre-filters (Adina's filter set)."""
    passed = []
    reasons = {}

    for s in snapshots:
        netuid = s["netuid"]
        price = s.get("price")
        total_tao = s.get("total_tao")
        startup = s.get("startup_mode")
        rank = s.get("rank")
        pct_1d = s.get("price_change_1d", 0) or 0
        pct_1w = s.get("price_change_1w", 0) or 0
        pct_1m = s.get("price_change_1m", 0) or 0

        # 0. Root subnet
        if netuid == 0:
            reasons[netuid] = "root subnet"
            continue

        # 1. Price cap
        if not price or price <= 0:
            reasons[netuid] = "no price"
            continue
        if price >= FILTER_MAX_PRICE:
            reasons[netuid] = "price cap"
            continue

        # 2. Startup mode
        if startup:
            reasons[netuid] = "startup mode"
            continue

        # 3. Min pool depth
        if total_tao is None:
            reasons[netuid] = "no pool data"
            continue
        if total_tao < FILTER_MIN_POOL_TAO:
            reasons[netuid] = "pool too thin"
            continue

        # 4. Max pool depth
        if total_tao > FILTER_MAX_POOL_TAO:
            reasons[netuid] = "pool too deep"
            continue

        # 5. Gini concentration (whale risk)
        gini = s.get("gini")
        if gini is not None and gini > FILTER_MAX_GINI:
            reasons[netuid] = f"gini {gini:.2f}"
            continue

        # 6. Monthly pump (manipulation)
        if pct_1m > FILTER_MAX_MONTHLY_PUMP:
            reasons[netuid] = f"monthly pump {pct_1m:.0f}%"
            continue

        # 7. All-zero guard (bad API data)
        if pct_1m == 0 and pct_1w == 0 and pct_1d == 0:
            reasons[netuid] = "all-zero data"
            continue

        # 8. Accelerating sell-off
        if pct_1d < FILTER_ACCEL_SELL_DAY and pct_1w < FILTER_ACCEL_SELL_WEEK:
            reasons[netuid] = f"accel sell (d:{pct_1d:.1f}% w:{pct_1w:.1f}%)"
            continue

        # 9. Flat momentum
        if abs(pct_1m) < FILTER_FLAT_MONTH and abs(pct_1w) < FILTER_FLAT_WEEK:
            reasons[netuid] = "flat momentum"
            continue

        # 10. Dual downtrend
        if pct_1m < 0 and pct_1w < 0:
            reasons[netuid] = f"dual downtrend (m:{pct_1m:.1f}% w:{pct_1w:.1f}%)"
            continue

        # 11. Structural decline
        if pct_1m < FILTER_STRUCTURAL_DECLINE:
            reasons[netuid] = f"structural decline {pct_1m:.1f}%"
            continue

        # 12. Day crash
        if pct_1d < FILTER_DAY_CRASH:
            reasons[netuid] = f"day crash {pct_1d:.1f}%"
            continue

        # 13. Deregistration risk
        total_subnets = len(snapshots)
        if rank is not None and rank >= (total_subnets - FILTER_DEREG_RISK_RANK):
            reasons[netuid] = "dereg risk"
            continue

        # Deprecated check
        name = s.get("name", "")
        if name and "deprecated" in name.lower():
            reasons[netuid] = "deprecated"
            continue

        passed.append(s)

    if reasons:
        reject_counts = {}
        for r in reasons.values():
            key = r.split("(")[0].strip()
            reject_counts[key] = reject_counts.get(key, 0) + 1
        print("[HUGO] Filter rejections:")
        for reason, count in sorted(reject_counts.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")

    return passed


def score_subnet(subnet, market_stats):
    """
    Joshua's 7-signal scoring engine.
    Scores each subnet on a 0-1 scale using advanced signals.
    """
    netuid = subnet["netuid"]
    prices = get_price_history(netuid, limit=42)

    if len(prices) < 5:
        return None, None

    price_series = [p["price"] for p in reversed(prices)]
    breakdown = {}

    # 1. Trend (7-day linear regression slope)
    x = np.arange(len(price_series))
    y = np.array(price_series)
    if y[0] > 0:
        y_norm = (y - y[0]) / y[0] * 100
        slope = np.polyfit(x, y_norm, 1)[0]
        trend_score = 0.5 + np.tanh(slope / 5) * 0.5
    else:
        trend_score = 0.5
    breakdown["trend_7d"] = float(np.clip(trend_score, 0, 1))

    # 2. EMA Crossover Timing (fresh crosses score higher)
    # If price just crossed above EMA = high score
    # If price has been above EMA for days = lower score (late entry)
    ema_period = min(72, len(price_series))
    multiplier = 2 / (ema_period + 1)
    ema_values = [price_series[0]]
    ema = price_series[0]
    for p in price_series[1:]:
        ema = (p - ema) * multiplier + ema
        ema_values.append(ema)

    current = price_series[-1]
    current_ema = ema_values[-1]

    if current_ema > 0:
        ema_dist = (current - current_ema) / current_ema
        # Count how many recent periods price has been above EMA
        above_count = sum(1 for i in range(-min(10, len(price_series)), 0)
                         if price_series[i] > ema_values[i])
        # Fresh cross (1-3 periods above) = best score
        # Long above (8-10 periods) = diminishing score
        freshness = 1.0 - (above_count / 10) * 0.5
        if ema_dist > 0:
            timing_score = min(0.5 + ema_dist * 5 * freshness, 1.0)
        else:
            timing_score = max(0.5 + ema_dist * 5, 0.0)
    else:
        timing_score = 0.5
    breakdown["ema_timing"] = float(np.clip(timing_score, 0, 1))

    # 3. Emission-Adjusted Strength
    # Rising despite miner sell pressure = genuine demand
    # If price is rising AND sell volume is high, demand must be absorbing emissions
    buy_vol = subnet.get("tao_buy_volume_24h") or 0
    sell_vol = subnet.get("tao_sell_volume_24h") or 0
    pct_1d = subnet.get("price_change_1d", 0) or 0
    pct_1w = subnet.get("price_change_1w", 0) or 0

    if sell_vol > 0 and buy_vol > 0:
        # Absorption ratio: how much of the sell pressure is being absorbed
        absorption = buy_vol / sell_vol
        # If price is rising despite selling, that's the key signal
        if pct_1d > 0 and absorption > 1.0:
            emission_score = 0.6 + np.tanh((absorption - 1.0) * 2) * 0.4
        elif pct_1d > 0:
            emission_score = 0.5 + np.tanh(pct_1d / 5) * 0.3
        else:
            emission_score = 0.3 + absorption * 0.2
    else:
        emission_score = 0.5
    breakdown["emission_strength"] = float(np.clip(emission_score, 0, 1))

    # 4. Pool Velocity (how fast is the pool growing)
    tao_flow = subnet.get("tao_flow") or 0
    total_tao = subnet.get("total_tao") or 0
    if total_tao > 0:
        # Positive flow as % of pool = growth rate
        velocity = tao_flow / total_tao
        velocity_score = 0.5 + np.tanh(velocity * 8) * 0.5
    else:
        velocity_score = 0.5
    breakdown["pool_velocity"] = float(np.clip(velocity_score, 0, 1))

    # 5. Buyer Diversity (organic demand vs whale pumps)
    buyers = subnet.get("buyers_24h", 0) or 0
    sellers = subnet.get("sellers_24h", 0) or 0
    buys_count = subnet.get("buys_24h", 0) or 0

    if buyers > 0:
        # More unique buyers = more organic
        # Also: ratio of unique buyers to total buys (lower = fewer whales doing many buys)
        buyer_ratio = buyers / max(sellers, 1)
        buys_per_buyer = buys_count / buyers if buyers > 0 else 10
        # Ideal: many unique buyers, each making few trades (organic)
        # Bad: few buyers making many trades (whale manipulation)
        organic = buyer_ratio * min(1.0, 3.0 / max(buys_per_buyer, 0.1))
        diversity_score = 0.5 + np.tanh((organic - 0.5) * 2) * 0.5
    else:
        diversity_score = 0.3
    breakdown["buyer_diversity"] = float(np.clip(diversity_score, 0, 1))

    # 6. Relative Strength (outperforming the market)
    avg_weekly = market_stats.get("avg_weekly", 0)
    std_weekly = market_stats.get("std_weekly", 1)
    if std_weekly > 0:
        # Z-score: how many std devs above average
        z = (pct_1w - avg_weekly) / std_weekly
        rs_score = 0.5 + np.tanh(z / 2) * 0.5
    else:
        rs_score = 0.5
    breakdown["relative_strength"] = float(np.clip(rs_score, 0, 1))

    # 7. Volume Profile (recent volume acceleration)
    # If buys in last few hours are higher than the 24h average rate
    buys_24h = subnet.get("buys_24h", 0) or 0
    sells_24h = subnet.get("sells_24h", 0) or 0
    pct_1h = subnet.get("price_change_1h", 0) or 0

    if buys_24h > 0:
        # Use hourly price action as proxy for recent volume
        if pct_1h > 0 and pct_1d > 0:
            # Hourly positive AND daily positive = active momentum
            vol_profile = 0.7 + min(pct_1h / 3, 0.3)
        elif pct_1h > 0:
            vol_profile = 0.55
        else:
            vol_profile = 0.4
    else:
        vol_profile = 0.3
    breakdown["volume_profile"] = float(np.clip(vol_profile, 0, 1))

    # Weighted total
    total = (
        breakdown["trend_7d"] * SCORE_WEIGHT_TREND_7D +
        breakdown["ema_timing"] * SCORE_WEIGHT_EMA_TIMING +
        breakdown["emission_strength"] * SCORE_WEIGHT_EMISSION_STRENGTH +
        breakdown["pool_velocity"] * SCORE_WEIGHT_POOL_VELOCITY +
        breakdown["buyer_diversity"] * SCORE_WEIGHT_BUYER_DIVERSITY +
        breakdown["relative_strength"] * SCORE_WEIGHT_RELATIVE_STRENGTH +
        breakdown["volume_profile"] * SCORE_WEIGHT_VOLUME_PROFILE
    )

    breakdown["total"] = total
    return total, breakdown


def print_screener_results(scored, top_n=15):
    """Pretty print results."""
    print(f"\n{'='*110}")
    print(f"  HUGO SCREENER - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*110}")
    print(f"{'SN':>4} {'Name':20} {'Score':>6} {'Price':>10} {'Pool':>8} {'Trnd':>5} {'EMA-T':>5} {'Emis':>5} {'PVel':>5} {'BDiv':>5} {'RelS':>5} {'VPrf':>5} {'7d%':>7}")
    print(f"{'-'*110}")

    for s in scored[:top_n]:
        b = s["breakdown"]
        marker = " *" if s["score"] >= MIN_SCORE_ENTRY else "  "
        print(
            f"SN{s['netuid']:>3} {s['name']:20} {s['score']:>5.3f}{marker}"
            f" {s['price']:>9.6f}"
            f" {s['pool_tao']:>7,.0f}T"
            f" {b.get('trend_7d', 0):>5.2f}"
            f" {b.get('ema_timing', 0):>5.2f}"
            f" {b.get('emission_strength', 0):>5.2f}"
            f" {b.get('pool_velocity', 0):>5.2f}"
            f" {b.get('buyer_diversity', 0):>5.2f}"
            f" {b.get('relative_strength', 0):>5.2f}"
            f" {b.get('volume_profile', 0):>5.2f}"
            f" {s.get('price_change_1w', 0):>+6.1f}%"
        )

    above = [s for s in scored if s["score"] >= MIN_SCORE_ENTRY]
    print(f"\n  {len(above)} subnets above entry threshold ({MIN_SCORE_ENTRY})")


if __name__ == "__main__":
    scored = run_screener()
    if scored:
        print_screener_results(scored)
