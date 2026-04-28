"""
Project Hugo - Configuration
Bittensor Best-of-All Trader
Joshua scoring + Adina filters + Watchman gate at 40+.
Higher entry bar at 0.65 with fewer, higher conviction positions.
"""

import os

# Taostats API
TAOSTATS_API_KEY = os.environ.get("TAOSTATS_API_KEY", "tao-7cdf8927-d0c8-4175-ac7a-de9be1e4cf92:40c87577")
TAOSTATS_BASE_URL = "https://api.taostats.io/api"

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Database
DB_PATH = os.environ.get("DB_PATH", "hugo.db")

# Timing
CYCLE_INTERVAL_MINUTES = 30

# Portfolio Settings
STARTING_BALANCE_TAO = 100.0
MAX_POSITIONS = 5
MAX_POSITION_PCT = 0.25
RESERVE_PCT = 0.10
MAX_TRADES_PER_CYCLE = 2
MAX_TRADES_PER_DAY = 6

# Adina's 13 hard pre-filters (from Siam)
FILTER_MAX_PRICE = 0.04
FILTER_MIN_POOL_TAO = 3000e9
FILTER_MAX_POOL_TAO = 150000e9
FILTER_MAX_GINI = 0.85
FILTER_MAX_MONTHLY_PUMP = 500
FILTER_ACCEL_SELL_DAY = -5
FILTER_ACCEL_SELL_WEEK = -5
FILTER_FLAT_MONTH = 3
FILTER_FLAT_WEEK = 3
FILTER_STRUCTURAL_DECLINE = -10
FILTER_DAY_CRASH = -20
FILTER_DEREG_RISK_RANK = 10

# Joshua's 7-signal scoring weights (must sum to 1.0)
SCORE_WEIGHT_TREND_7D = 0.15
SCORE_WEIGHT_EMA_TIMING = 0.15
SCORE_WEIGHT_EMISSION_STRENGTH = 0.20
SCORE_WEIGHT_POOL_VELOCITY = 0.15
SCORE_WEIGHT_BUYER_DIVERSITY = 0.15
SCORE_WEIGHT_RELATIVE_STRENGTH = 0.10
SCORE_WEIGHT_VOLUME_PROFILE = 0.10

# Entry/Exit - higher bar than Joshua (0.58) or Adina (0.55)
MIN_SCORE_ENTRY = 0.65
TRAILING_STOP_PCT = 0.22
MAX_SLIPPAGE_PCT = 0.03
MIN_HOLD_HOURS = 72
COOLDOWN_DAYS = 5

# Watchman gate - only trade when regime score >= 40
WATCHMAN_MIN_SCORE = 40

# Subnet blacklist - skip these netuids entirely
SUBNET_BLACKLIST = [111]

# Heat control
HEAT_CAUTION = 0.03
HEAT_DANGER = 0.15
MAX_POSITIONS_DANGER = 2

# Unit conversion
RAO_PER_TAO = 1e9
