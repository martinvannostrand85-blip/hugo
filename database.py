"""
Project Hugo - Database Layer
SQLite database for storing subnet snapshots, price history, portfolio, and trades.
"""

import sqlite3
import os
from datetime import datetime
from config import DB_PATH


def get_connection():
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    # Latest snapshot of each subnet from the pools endpoint
    c.execute("""
        CREATE TABLE IF NOT EXISTS subnet_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            netuid INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            block_number INTEGER,
            name TEXT,
            symbol TEXT,
            price REAL,
            market_cap REAL,
            total_tao REAL,
            total_alpha REAL,
            alpha_in_pool REAL,
            alpha_staked REAL,
            rank INTEGER,
            startup_mode INTEGER,
            root_prop REAL,
            price_change_1h REAL,
            price_change_1d REAL,
            price_change_1w REAL,
            price_change_1m REAL,
            tao_volume_24h REAL,
            tao_buy_volume_24h REAL,
            tao_sell_volume_24h REAL,
            buys_24h INTEGER,
            sells_24h INTEGER,
            buyers_24h INTEGER,
            sellers_24h INTEGER,
            tao_flow REAL,
            fear_greed_index REAL,
            fear_greed_sentiment TEXT,
            collected_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_netuid_time
        ON subnet_snapshots(netuid, collected_at)
    """)

    # 7-day price history from the pools endpoint (4-hourly data points)
    c.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            netuid INTEGER NOT NULL,
            block_number INTEGER,
            timestamp TEXT NOT NULL,
            price REAL NOT NULL,
            collected_at TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_pricehistory_netuid_time
        ON price_history(netuid, timestamp)
    """)

    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pricehistory_unique
        ON price_history(netuid, block_number)
    """)

    # Paper trading portfolio
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            netuid INTEGER PRIMARY KEY,
            alpha_amount REAL NOT NULL,
            entry_price_tao REAL NOT NULL,
            entry_timestamp TEXT NOT NULL,
            peak_price_tao REAL NOT NULL,
            current_price_tao REAL
        )
    """)

    # Trade log
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            netuid INTEGER NOT NULL,
            subnet_name TEXT,
            direction TEXT NOT NULL,
            amount_tao REAL NOT NULL,
            alpha_amount REAL NOT NULL,
            price_tao REAL NOT NULL,
            slippage_est REAL,
            reason TEXT,
            portfolio_value_after REAL
        )
    """)

    # Portfolio value snapshots over time
    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_value_tao REAL NOT NULL,
            tao_balance REAL NOT NULL,
            num_positions INTEGER NOT NULL,
            notes TEXT
        )
    """)

    # Paper trading balance
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_balance (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            tao_balance REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Database initialised at {DB_PATH}")


def store_subnet_snapshot(data, tao_flows, collected_at):
    """Store a batch of subnet snapshots from the pools endpoint."""
    conn = get_connection()
    c = conn.cursor()

    # Build tao_flow lookup
    flow_map = {}
    if tao_flows:
        for f in tao_flows:
            flow_map[f["netuid"]] = f.get("tao_flow", 0)

    count = 0
    for s in data:
        netuid = s["netuid"]
        tao_flow = flow_map.get(netuid, 0)

        c.execute("""
            INSERT INTO subnet_snapshots (
                netuid, timestamp, block_number, name, symbol, price,
                market_cap, total_tao, total_alpha, alpha_in_pool,
                alpha_staked, rank, startup_mode, root_prop,
                price_change_1h, price_change_1d, price_change_1w, price_change_1m,
                tao_volume_24h, tao_buy_volume_24h, tao_sell_volume_24h,
                buys_24h, sells_24h, buyers_24h, sellers_24h,
                tao_flow, fear_greed_index, fear_greed_sentiment, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            netuid,
            s.get("timestamp"),
            s.get("block_number"),
            s.get("name"),
            s.get("symbol"),
            _float(s.get("price")),
            _float(s.get("market_cap")),
            _float(s.get("total_tao")),
            _float(s.get("total_alpha")),
            _float(s.get("alpha_in_pool")),
            _float(s.get("alpha_staked")),
            s.get("rank"),
            1 if s.get("startup_mode") else 0,
            _float(s.get("root_prop")),
            _float(s.get("price_change_1_hour")),
            _float(s.get("price_change_1_day")),
            _float(s.get("price_change_1_week")),
            _float(s.get("price_change_1_month")),
            _float(s.get("tao_volume_24_hr")),
            _float(s.get("tao_buy_volume_24_hr")),
            _float(s.get("tao_sell_volume_24_hr")),
            s.get("buys_24_hr"),
            s.get("sells_24_hr"),
            s.get("buyers_24_hr"),
            s.get("sellers_24_hr"),
            tao_flow,
            _float(s.get("fear_and_greed_index")),
            s.get("fear_and_greed_sentiment"),
            collected_at,
        ))
        count += 1

    conn.commit()
    conn.close()
    return count


def store_price_history(data, collected_at):
    """Store 7-day price history from subnet pool data. Upserts on netuid+block."""
    conn = get_connection()
    c = conn.cursor()

    count = 0
    for subnet in data:
        netuid = subnet["netuid"]
        prices = subnet.get("seven_day_prices", [])
        if not prices:
            continue

        for p in prices:
            try:
                c.execute("""
                    INSERT OR IGNORE INTO price_history
                    (netuid, block_number, timestamp, price, collected_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    netuid,
                    p.get("block_number"),
                    p.get("timestamp"),
                    _float(p.get("price")),
                    collected_at,
                ))
                count += c.rowcount
            except sqlite3.IntegrityError:
                pass

    conn.commit()
    conn.close()
    return count


def get_latest_snapshots():
    """Get the most recent snapshot for each subnet."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM subnet_snapshots
        WHERE id IN (
            SELECT MAX(id) FROM subnet_snapshots GROUP BY netuid
        )
        ORDER BY netuid
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_price_history(netuid, limit=168):
    """Get price history for a subnet, ordered by timestamp."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM price_history
        WHERE netuid = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (netuid, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _float(val):
    """Safely convert to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    init_db()
