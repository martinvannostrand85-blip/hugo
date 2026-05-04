"""
Project Hugo - Paper Trading Engine
Simulates trades using live data with realistic slippage estimation.
Includes Watchman gate: skips entries when regime score < 40.
"""

from datetime import datetime, timezone, timedelta
from watchman_client import get_recommendation
from database import get_connection, get_latest_snapshots
from signals import run_screener
from config import (
    STARTING_BALANCE_TAO, MAX_POSITIONS, MAX_POSITION_PCT,
    RESERVE_PCT, MAX_TRADES_PER_CYCLE, MAX_TRADES_PER_DAY,
    MIN_SCORE_ENTRY, MAX_SLIPPAGE_PCT, RAO_PER_TAO,
    WATCHMAN_MIN_SCORE, SUBNET_BLACKLIST
)

BOT_NAME = "HUGO"

# Adaptive trailing stop thresholds
TRAILING_STOP_DEFAULT  = 0.22   # 22% drop from peak
TRAILING_STOP_TIER_10  = 0.08   # 8% when up 10-20%
TRAILING_STOP_TIER_20  = 0.08   # 8% when up 20-30%
TRAILING_STOP_TIER_30  = 0.07   # 7% when up 30-40%
TRAILING_STOP_TIER_40  = 0.06   # 6% when up 40-50%
TRAILING_STOP_TIER_50  = 0.05   # 5% when up 50%+

# Take profit
TAKE_PROFIT_PCT = 0.35   # sell at 35% gain

# Heat control thresholds
HEAT_CAUTION_DD  = 0.03   # 3% drawdown from peak
HEAT_DANGER_DD   = 0.15   # 15% drawdown from peak
MAX_POS_DANGER   = 2

# Exit & entry tuning
SCORE_COLLAPSE_RATIO = 0.50  # exit when score < 50% of entry threshold
COOLDOWN_DAYS        = 5
MIN_HOLD_HOURS       = 72    # 72h hold for losing positions only
SELL_SLIPPAGE_EST    = 0.01  # 1% estimated sell slippage
SKIM_PCT             = 0.50  # bank 50% of realised profit on winners
SKIM_MIN_PROFIT      = 0.1   # minimum profit (TAO) to trigger skim


class PaperTrader:
    """Manages a simulated portfolio with realistic execution."""

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        """Create all required tables if they don't exist."""
        conn = get_connection()
        c = conn.cursor()

        # Vault migration: check for old 'balance' column and rename to 'tao_balance'
        try:
            c.execute("PRAGMA table_info(paper_balance)")
            columns = [row[1] for row in c.fetchall()]
            if 'balance' in columns and 'tao_balance' not in columns:
                c.execute("ALTER TABLE paper_balance RENAME COLUMN balance TO tao_balance")
                print(f"[{BOT_NAME}] Migrated paper_balance.balance -> tao_balance")
                conn.commit()
        except Exception:
            pass

        # Paper balance
        c.execute("SELECT tao_balance FROM paper_balance WHERE id = 1")
        if not c.fetchone():
            c.execute(
                "INSERT INTO paper_balance (id, tao_balance, updated_at) VALUES (1, ?, ?)",
                (STARTING_BALANCE_TAO, datetime.now(timezone.utc).isoformat())
            )
            print(f"[{BOT_NAME}] Initialised with {STARTING_BALANCE_TAO} TAO")

        # Vault
        c.execute("""
            CREATE TABLE IF NOT EXISTS vault (
                id INTEGER PRIMARY KEY DEFAULT 1,
                tao_balance REAL NOT NULL DEFAULT 0,
                updated_at TEXT
            )
        """)
        c.execute("SELECT tao_balance FROM vault WHERE id = 1")
        if not c.fetchone():
            c.execute("INSERT INTO vault (id, tao_balance, updated_at) VALUES (1, 0, ?)",
                      (datetime.now(timezone.utc).isoformat(),))

        # Cooldowns
        c.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                netuid INTEGER PRIMARY KEY,
                expires_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    # -- Balance helpers --

    def get_balance(self):
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT tao_balance FROM paper_balance WHERE id = 1")
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0

    def set_balance(self, amount):
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "UPDATE paper_balance SET tao_balance = ?, updated_at = ? WHERE id = 1",
            (amount, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()

    def get_positions(self):
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM portfolio ORDER BY netuid")
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_portfolio_value(self, snap_map=None):
        """Calculate total portfolio value in TAO."""
        balance = self.get_balance()
        positions = self.get_positions()
        position_value = 0
        for pos in positions:
            if snap_map and pos["netuid"] in snap_map:
                price = snap_map[pos["netuid"]]["price"]
            elif pos.get("current_price_tao"):
                price = pos["current_price_tao"]
            else:
                price = pos["entry_price_tao"]
            position_value += pos["alpha_amount"] * price
        return balance + position_value

    # -- Vault helpers --

    def _get_vault_balance(self):
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT tao_balance FROM vault WHERE id = 1")
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0.0

    def _add_to_vault(self, amount):
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT tao_balance FROM vault WHERE id = 1")
        row = c.fetchone()
        new_balance = (row[0] if row else 0) + amount
        if row:
            c.execute("UPDATE vault SET tao_balance = ?, updated_at = ? WHERE id = 1",
                      (new_balance, datetime.now(timezone.utc).isoformat()))
        else:
            c.execute("INSERT INTO vault (id, tao_balance, updated_at) VALUES (1, ?, ?)",
                      (new_balance, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

    # -- Cooldown helpers --

    def _set_cooldown(self, netuid, now):
        conn = get_connection()
        c = conn.cursor()
        cooldown_end = (now + timedelta(days=COOLDOWN_DAYS)).isoformat()
        c.execute("INSERT OR REPLACE INTO cooldowns (netuid, expires_at) VALUES (?, ?)",
                  (netuid, cooldown_end))
        conn.commit()
        conn.close()

    def _is_on_cooldown(self, netuid, now):
        conn = get_connection()
        c = conn.cursor()
        try:
            c.execute("SELECT expires_at FROM cooldowns WHERE netuid = ?", (netuid,))
            row = c.fetchone()
        except Exception:
            conn.close()
            return False
        conn.close()
        if not row:
            return False
        try:
            expires = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            return now < expires
        except Exception:
            return False

    # -- AMM slippage model --

    def estimate_slippage(self, amount_tao_rao, pool_tao_rao, pool_alpha_rao):
        """
        Estimate slippage for a buy using constant product formula.
        All inputs in RAO.
        Returns (alpha_received_rao, effective_price_tao, price_impact_pct).
        """
        if pool_tao_rao <= 0 or pool_alpha_rao <= 0:
            return 0, 0, 1.0
        k = pool_tao_rao * pool_alpha_rao
        new_alpha = k / (pool_tao_rao + amount_tao_rao)
        alpha_received = pool_alpha_rao - new_alpha
        if alpha_received <= 0:
            return 0, 0, 1.0
        spot_price = pool_tao_rao / pool_alpha_rao
        effective_price = amount_tao_rao / alpha_received
        price_impact = (effective_price - spot_price) / spot_price
        return alpha_received, effective_price / RAO_PER_TAO, price_impact

    # -- Price tracking --

    def _update_position_prices(self, snap_map):
        conn = get_connection()
        c = conn.cursor()
        positions = self.get_positions()
        for pos in positions:
            netuid = pos["netuid"]
            if netuid in snap_map:
                current = snap_map[netuid].get("price", 0)
                peak = max(pos["peak_price_tao"] or 0, current)
                c.execute("UPDATE portfolio SET current_price_tao = ?, peak_price_tao = ? WHERE netuid = ?",
                          (current, peak, netuid))
        conn.commit()
        conn.close()

    def _count_trades_today(self):
        conn = get_connection()
        c = conn.cursor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        c.execute("SELECT COUNT(*) FROM trades WHERE timestamp LIKE ?", (f"{today}%",))
        count = c.fetchone()[0]
        conn.close()
        return count

    def _get_peak_equity(self):
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT MAX(total_value_tao) FROM portfolio_snapshots")
        row = c.fetchone()
        conn.close()
        peak = row[0] if row and row[0] else STARTING_BALANCE_TAO
        return max(peak, STARTING_BALANCE_TAO)

    # -- Core trading cycle --

    def run_cycle(self):
        now = datetime.now(timezone.utc)
        print(f"\n{'='*60}")
        print(f"[{BOT_NAME}] Trading cycle at {now.isoformat()}")
        print(f"{'='*60}")

        # Get market data and signals
        snap_map = {s["netuid"]: s for s in get_latest_snapshots()}
        scored = run_screener()
        score_map = {s["netuid"]: s for s in scored} if scored else {}

        trades_today = self._count_trades_today()
        trades_this_cycle = 0

        self._update_position_prices(snap_map)

        balance = self.get_balance()
        positions = self.get_positions()
        portfolio_value = self.get_portfolio_value(snap_map)

        # -- Portfolio heat control --
        peak = self._get_peak_equity()
        if portfolio_value > peak:
            peak = portfolio_value
        dd = (peak - portfolio_value) / peak if peak > 0 else 0

        if dd >= HEAT_DANGER_DD:
            heat_level = "DANGER"
            effective_max = MAX_POS_DANGER
        elif dd >= HEAT_CAUTION_DD:
            heat_level = "CAUTION"
            effective_max = MAX_POSITIONS // 2
        else:
            heat_level = "NORMAL"
            effective_max = MAX_POSITIONS

        print(f"[{BOT_NAME}] Balance: {balance:.2f} TAO | Positions: {len(positions)} | Portfolio: {portfolio_value:.2f} TAO")
        print(f"[{BOT_NAME}] Heat: {heat_level} (dd {dd:.1%} from peak {peak:.2f}) | Max positions: {effective_max}")

        # -- Watchman market regime --
        watchman_rec = get_recommendation("hugo")
        watchman_max = watchman_rec.get("max_positions", effective_max)
        watchman_mult = watchman_rec.get("entry_multiplier", 1.0)
        watchman_mode = watchman_rec.get("mode", "NORMAL")
        watchman_score = watchman_rec.get("score", 50)
        effective_max = min(effective_max, watchman_max)
        print(f"[{BOT_NAME}] Watchman: {watchman_mode} (score {watchman_score}) | Max positions: {effective_max} | Entry mult: {watchman_mult:.1f}x")

        # -- Phase 1: Check exits --
        exits = []
        for pos in positions:
            netuid = pos["netuid"]
            entry_price = pos["entry_price_tao"]
            peak_price = pos["peak_price_tao"]
            # Use latest snap_map price for accurate calculations
            current_price = snap_map[netuid]["price"] if netuid in snap_map else (pos.get("current_price_tao") or entry_price)

            try:
                entry_dt = datetime.fromisoformat(pos["entry_timestamp"].replace("Z", "+00:00"))
                hours_held = (now - entry_dt).total_seconds() / 3600
            except Exception:
                hours_held = 999

            exit_reason = None

            # Calculate gain once, used by both take-profit and trailing stop
            gain_from_entry = (current_price - entry_price) / entry_price if entry_price > 0 else 0

            # Take profit: sell at 25% gain
            if gain_from_entry >= TAKE_PROFIT_PCT:
                exit_reason = f"take profit ({gain_from_entry:.1%} gain)"

            # Adaptive trailing stop (use peak gain to lock stop level)
            if not exit_reason and peak_price > 0 and entry_price > 0:
                drop_from_peak = (peak_price - current_price) / peak_price
                peak_gain = (peak_price - entry_price) / entry_price

                if peak_gain >= 0.50:
                    active_stop = TRAILING_STOP_TIER_50
                elif peak_gain >= 0.40:
                    active_stop = TRAILING_STOP_TIER_40
                elif peak_gain >= 0.30:
                    active_stop = TRAILING_STOP_TIER_30
                elif peak_gain >= 0.20:
                    active_stop = TRAILING_STOP_TIER_20
                elif peak_gain >= 0.10:
                    active_stop = TRAILING_STOP_TIER_10
                elif peak_gain > 0:
                    active_stop = TRAILING_STOP_DEFAULT
                elif hours_held >= MIN_HOLD_HOURS:
                    active_stop = TRAILING_STOP_DEFAULT
                else:
                    active_stop = None  # Still within hold period for losing position

                if active_stop is not None and drop_from_peak >= active_stop:
                    exit_reason = f"trailing stop ({drop_from_peak:.1%} from peak, stop {active_stop:.0%}, peak_gain {peak_gain:.1%}, gain {gain_from_entry:.1%}, held {hours_held:.0f}h)"

            # Failed pre-filters (only when screener data available)
            if not exit_reason and scored and netuid not in score_map:
                exit_reason = "failed pre-filters"

            # Score collapse (only when screener data available)
            if not exit_reason and scored and netuid in score_map:
                if score_map[netuid]["score"] < MIN_SCORE_ENTRY * SCORE_COLLAPSE_RATIO:
                    exit_reason = f"score collapsed ({score_map[netuid]['score']:.3f})"

            if exit_reason:
                exits.append((pos, exit_reason))

        # Execute exits
        for pos, reason in exits:
            if trades_today >= MAX_TRADES_PER_DAY or trades_this_cycle >= MAX_TRADES_PER_CYCLE:
                print(f"[{BOT_NAME}] Trade limit reached, deferring exit SN{pos['netuid']}")
                break
            self._execute_sell(pos, reason, snap_map)
            self._set_cooldown(pos["netuid"], now)
            trades_today += 1
            trades_this_cycle += 1

        # -- Heat-based position reduction --
        positions = self.get_positions()
        if len(positions) > effective_max:
            held_scores = [(pos, score_map.get(pos["netuid"], {}).get("score", 0)) for pos in positions]
            held_scores.sort(key=lambda x: x[1])
            to_cut = len(positions) - effective_max
            for pos, sc in held_scores[:to_cut]:
                if trades_today >= MAX_TRADES_PER_DAY or trades_this_cycle >= MAX_TRADES_PER_CYCLE:
                    break
                self._execute_sell(pos, f"heat reduce ({heat_level}, dd {dd:.0%}, score {sc:.3f})", snap_map)
                self._set_cooldown(pos["netuid"], now)
                trades_today += 1
                trades_this_cycle += 1

        # Refresh state after exits
        balance = self.get_balance()
        positions = self.get_positions()
        held_netuids = {p["netuid"] for p in positions}

        # -- Watchman gate: skip entries if score < WATCHMAN_MIN_SCORE --
        if watchman_score < WATCHMAN_MIN_SCORE:
            print(f"[{BOT_NAME}] Watchman gate CLOSED (score {watchman_score} < {WATCHMAN_MIN_SCORE}), skipping entries")
            self._snapshot_portfolio(snap_map)
            self._print_portfolio(snap_map, score_map)
            return

        # -- Phase 2: Check entries --
        if not scored:
            print(f"[{BOT_NAME}] No scored subnets, skipping entries")
            self._snapshot_portfolio(snap_map)
            self._print_portfolio(snap_map, score_map)
            return

        # Dynamic reserve scaling
        effective_reserve = RESERVE_PCT
        if portfolio_value > STARTING_BALANCE_TAO * 1.15:
            effective_reserve = max(RESERVE_PCT, 0.30)
        elif portfolio_value > STARTING_BALANCE_TAO * 1.05:
            effective_reserve = max(RESERVE_PCT, 0.20)
        available_balance = balance - (portfolio_value * effective_reserve)
        slots_available = effective_max - len(positions)

        # Entry threshold: use the stricter of watchman and heat multipliers
        heat_mult = 1.0
        if heat_level == "DANGER":
            heat_mult = 1.4
        elif heat_level == "CAUTION":
            heat_mult = 1.2
        entry_threshold = MIN_SCORE_ENTRY * max(watchman_mult, heat_mult)

        if slots_available <= 0:
            print(f"[{BOT_NAME}] All {effective_max} slots filled ({heat_level} mode)")
        elif available_balance <= 1:
            print(f"[{BOT_NAME}] Insufficient balance for new entries ({available_balance:.2f} TAO)")
        else:
            candidates = [s for s in scored
                          if s["netuid"] not in held_netuids
                          and s["netuid"] not in SUBNET_BLACKLIST
                          and s["score"] >= entry_threshold
                          and not self._is_on_cooldown(s["netuid"], now)]

            entries_to_make = min(
                slots_available,
                MAX_TRADES_PER_CYCLE - trades_this_cycle,
                MAX_TRADES_PER_DAY - trades_today,
                len(candidates)
            )

            if entries_to_make > 0:
                position_size = min(
                    available_balance / max(entries_to_make, 1),
                    portfolio_value * MAX_POSITION_PCT
                )
                for candidate in candidates[:entries_to_make]:
                    netuid = candidate["netuid"]
                    if netuid not in snap_map:
                        continue
                    self._execute_buy(netuid, position_size, snap_map[netuid], candidate)
                    trades_today += 1
                    trades_this_cycle += 1

        self._snapshot_portfolio(snap_map)
        self._print_portfolio(snap_map, score_map)

    # -- Trade execution --

    def _execute_buy(self, netuid, amount_tao, snapshot, signal):
        pool_tao = snapshot.get("total_tao", 0)
        pool_alpha = snapshot.get("alpha_in_pool", 0)
        spot_price = snapshot.get("price", 0)
        name = snapshot.get("name", "?")

        if spot_price <= 0 or pool_tao <= 0 or pool_alpha <= 0:
            print(f"[{BOT_NAME}] SKIP BUY SN{netuid} ({name}): no valid price/pool data")
            return

        amount_rao = amount_tao * RAO_PER_TAO
        alpha_received_rao, _, slippage = self.estimate_slippage(amount_rao, pool_tao, pool_alpha)

        if slippage > MAX_SLIPPAGE_PCT:
            print(f"[{BOT_NAME}] SKIP BUY SN{netuid} ({name}): slippage {slippage:.2%} > {MAX_SLIPPAGE_PCT:.0%}")
            return

        alpha_received = alpha_received_rao / RAO_PER_TAO
        effective_price = amount_tao / alpha_received if alpha_received > 0 else spot_price

        balance = self.get_balance()
        self.set_balance(balance - amount_tao)

        conn = get_connection()
        c = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        c.execute("""
            INSERT OR REPLACE INTO portfolio
            (netuid, alpha_amount, entry_price_tao, entry_timestamp, peak_price_tao, current_price_tao)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (netuid, alpha_received, effective_price, now, spot_price, spot_price))
        c.execute("""
            INSERT INTO trades (timestamp, netuid, subnet_name, direction, amount_tao,
                alpha_amount, price_tao, slippage_est, reason, portfolio_value_after)
            VALUES (?, ?, ?, 'BUY', ?, ?, ?, ?, ?, ?)
        """, (now, netuid, name, amount_tao, alpha_received,
              effective_price, slippage, f"score={signal['score']:.3f}", None))
        conn.commit()
        conn.close()

        print(f"[{BOT_NAME}] BUY  SN{netuid} ({name}): {amount_tao:.2f} TAO -> {alpha_received:.2f} alpha @ {effective_price:.6f} TAO/alpha (slip {slippage:.2%}, score {signal['score']:.3f})")

    def _execute_sell(self, position, reason, snap_map):
        netuid = position["netuid"]
        snap = snap_map.get(netuid)
        name = snap.get("name", "?") if snap else "?"
        price = snap.get("price", position["entry_price_tao"]) if snap else position["entry_price_tao"]

        alpha_amount = position["alpha_amount"]
        tao_received = alpha_amount * price * (1 - SELL_SLIPPAGE_EST)

        balance = self.get_balance()
        self.set_balance(balance + tao_received)

        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM portfolio WHERE netuid = ?", (netuid,))

        now = datetime.now(timezone.utc).isoformat()
        cost_basis = alpha_amount * position["entry_price_tao"]
        pnl = tao_received - cost_basis
        pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

        c.execute("""
            INSERT INTO trades (timestamp, netuid, subnet_name, direction, amount_tao,
                alpha_amount, price_tao, slippage_est, reason, portfolio_value_after)
            VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?)
        """, (now, netuid, name, tao_received, alpha_amount,
              price, SELL_SLIPPAGE_EST, f"{reason} | PnL: {pnl:+.2f} TAO ({pnl_pct:+.1f}%)", None))
        conn.commit()
        conn.close()

        print(f"[{BOT_NAME}] SELL SN{netuid} ({name}): {alpha_amount:.2f} alpha -> {tao_received:.2f} TAO | PnL: {pnl:+.2f} TAO ({pnl_pct:+.1f}%) | {reason}")

        # Per-trade profit skim
        if pnl > SKIM_MIN_PROFIT:
            skim_amount = pnl * SKIM_PCT
            current_balance = self.get_balance()
            skim_amount = min(skim_amount, current_balance)
            if skim_amount > 0:
                vault = self._get_vault_balance()
                self._add_to_vault(skim_amount)
                self.set_balance(current_balance - skim_amount)
                print(f"[{BOT_NAME}] SKIM: Banking {skim_amount:.2f} TAO (50% of {pnl:.2f} profit on SN{netuid}) | Vault: {vault + skim_amount:.2f} TAO")

    # -- Snapshots & display --

    def _snapshot_portfolio(self, snap_map):
        conn = get_connection()
        c = conn.cursor()
        value = self.get_portfolio_value(snap_map)
        balance = self.get_balance()
        positions = self.get_positions()
        now = datetime.now(timezone.utc).isoformat()
        c.execute("""
            INSERT INTO portfolio_snapshots (timestamp, total_value_tao, tao_balance, num_positions, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (now, value, balance, len(positions), None))
        conn.commit()
        conn.close()

    def _print_portfolio(self, snap_map, score_map):
        positions = self.get_positions()
        balance = self.get_balance()
        total = self.get_portfolio_value(snap_map)
        vault = self._get_vault_balance()

        print(f"\n{'='*70}")
        print(f"  PORTFOLIO STATUS")
        print(f"{'='*70}")
        print(f"  TAO Balance: {balance:.2f}")
        print(f"  Vault:       {vault:.2f} TAO (banked profit)")
        print(f"  Positions:   {len(positions)}")
        print(f"  Total Value: {total:.2f} TAO")
        print(f"  PnL:         {total - STARTING_BALANCE_TAO:+.2f} TAO ({(total/STARTING_BALANCE_TAO - 1)*100:+.1f}%)")
        print(f"{'-'*70}")

        if positions:
            print(f"{'SN':>4} {'Name':18} {'Alpha':>10} {'Entry':>9} {'Current':>9} {'PnL%':>7} {'Score':>6}")
            for pos in positions:
                netuid = pos["netuid"]
                name = snap_map.get(netuid, {}).get("name", "?")
                current = pos.get("current_price_tao") or pos["entry_price_tao"]
                entry = pos["entry_price_tao"]
                pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
                score = score_map.get(netuid, {}).get("score", 0)
                print(f"SN{netuid:>3} {name:18} {pos['alpha_amount']:>10.2f} {entry:>9.6f} {current:>9.6f} {pnl_pct:>+6.1f}% {score:>5.3f}")

        print(f"{'='*70}")


def run_paper_cycle():
    """Convenience function to run a single paper trading cycle."""
    trader = PaperTrader()
    trader.run_cycle()


if __name__ == "__main__":
    run_paper_cycle()
