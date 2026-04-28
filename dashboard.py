"""
Project Hugo - Dashboard Server
Flask app serving a live dashboard for the paper trading bot.
Run: python dashboard.py
View: http://localhost:5555
"""

import json
from datetime import datetime, timezone
from flask import Flask, jsonify, send_from_directory
from database import get_connection, get_latest_snapshots
from config import STARTING_BALANCE_TAO, RAO_PER_TAO

app = Flask(__name__, static_folder=".", static_url_path="")


@app.route("/")
def index():
    return send_from_directory(".", "dashboard.html")


@app.route("/api/portfolio")
def api_portfolio():
    conn = get_connection()
    c = conn.cursor()

    # Balance
    c.execute("SELECT tao_balance FROM paper_balance WHERE id = 1")
    row = c.fetchone()
    balance = row[0] if row else STARTING_BALANCE_TAO

    # Positions
    c.execute("SELECT * FROM portfolio ORDER BY netuid")
    positions = [dict(r) for r in c.fetchall()]

    # Get current prices from latest snapshots
    snapshots = get_latest_snapshots()
    snap_map = {s["netuid"]: s for s in snapshots}

    total_position_value = 0
    enriched_positions = []
    for pos in positions:
        netuid = pos["netuid"]
        snap = snap_map.get(netuid, {})
        current_price = snap.get("price", pos.get("current_price_tao", 0)) or 0
        entry_price = pos["entry_price_tao"] or 0
        alpha = pos["alpha_amount"]
        value = alpha * current_price
        total_position_value += value
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        enriched_positions.append({
            "netuid": netuid,
            "name": snap.get("name", "?"),
            "alpha_amount": round(alpha, 2),
            "entry_price": round(entry_price, 6),
            "current_price": round(current_price, 6),
            "peak_price": round(pos.get("peak_price_tao", 0), 6),
            "value_tao": round(value, 2),
            "pnl_pct": round(pnl_pct, 1),
            "entry_timestamp": pos.get("entry_timestamp", ""),
        })

    total_value = balance + total_position_value
    pnl_total = total_value - STARTING_BALANCE_TAO
    pnl_pct = (total_value / STARTING_BALANCE_TAO - 1) * 100

    # Vault balance (profit skim)
    try:
        c2 = conn.cursor()
        c2.execute("SELECT tao_balance FROM vault WHERE id = 1")
        vault_row = c2.fetchone()
        vault_balance = vault_row[0] if vault_row and vault_row[0] else 0
    except:
        vault_balance = 0

    conn.close()
    return jsonify({
        "balance": round(balance, 2),
        "total_value": round(total_value, 2),
        "starting_balance": STARTING_BALANCE_TAO,
        "pnl_tao": round(pnl_total, 2),
        "pnl_pct": round(pnl_pct, 2),
        "num_positions": len(positions),
        "positions": enriched_positions,
        "vault_balance": round(vault_balance, 2),
        "total_with_vault": round(total_value + vault_balance, 2),
        "portfolio_value": round(total_value, 2),
        "tao_balance": round(balance, 2),
    })


@app.route("/api/trades")
def api_trades():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT 50")
    trades = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(trades)


@app.route("/api/snapshots")
def api_snapshots():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT 500")
    snaps = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify(snaps)


@app.route("/api/screener")
def api_screener():
    from signals import run_screener
    scored = run_screener()
    return jsonify(scored[:20])


if __name__ == "__main__":
    print("[DASHBOARD] Starting at http://localhost:5555")
    app.run(host="0.0.0.0", port=5555, debug=False)
