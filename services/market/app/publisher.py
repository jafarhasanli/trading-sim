import os
import random
import time

import redis
from prometheus_client import Gauge


REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
MARKET_SYMBOLS = os.getenv("MARKET_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,ADAUSDT,XRPUSDT")
START_PRICE = float(os.getenv("START_PRICE", "100.0"))
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", "2"))

SYMBOLS = [s.strip() for s in MARKET_SYMBOLS.split(",") if s.strip()]

client = redis.from_url(REDIS_URL, decode_responses=True)

current_price = Gauge(
    "market_price",
    "Current market price",
    ["symbol"]
)


def publisher_loop():
    prices = {symbol: START_PRICE for symbol in SYMBOLS}

    # Grafana dashboardda problem olmasın deyə metric-ləri başlanğıcda set edirik
    for symbol in SYMBOLS:
        current_price.labels(symbol=symbol).set(START_PRICE)

    print(f"[MARKET] Starting price publisher for {SYMBOLS}", flush=True)

    while True:
        for symbol in SYMBOLS:
            delta = random.uniform(-2.0, 2.0)
            prices[symbol] = max(1.0, round(prices[symbol] + delta, 2))

            try:
                # latest price
                client.set(f"price:{symbol}", prices[symbol])

                # history (Redis list)
                client.lpush(f"history:{symbol}", prices[symbol])

                # keep last 100 prices
                client.ltrim(f"history:{symbol}", 0, 100)

            except Exception as e:
                print(f"[MARKET] Redis write failed for {symbol}: {e}", flush=True)

            # Prometheus metric update
            # Redis temporarily unavailable olsa belə Grafana qrafiki dayanmasın
            current_price.labels(symbol=symbol).set(prices[symbol])

            print(f"[MARKET] {symbol}={prices[symbol]}", flush=True)

        time.sleep(SLEEP_SECONDS)