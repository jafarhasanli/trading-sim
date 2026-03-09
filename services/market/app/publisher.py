import os
import random
import time

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SYMBOL = os.getenv("MARKET_SYMBOL", "BTCUSDT")
START_PRICE = float(os.getenv("START_PRICE", "100.0"))
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", "2"))

client = redis.from_url(REDIS_URL, decode_responses=True)


def publisher_loop():
    price = START_PRICE
    print(f"[MARKET] Starting price publisher for {SYMBOL}", flush=True)

    while True:
        delta = random.uniform(-2.0, 2.0)
        price = max(1.0, round(price + delta, 2))

        # latest price
        client.set(f"price:{SYMBOL}", price)

        # history (Redis list)
        client.lpush(f"history:{SYMBOL}", price)

        # keep last 100 prices
        client.ltrim(f"history:{SYMBOL}", 0, 100)

        print(f"[MARKET] {SYMBOL}={price}", flush=True)

        time.sleep(SLEEP_SECONDS)