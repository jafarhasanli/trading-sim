import os
import threading

import redis
from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from app.publisher import publisher_loop

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

client = redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title="Market Data Service")


@app.on_event("startup")
def start_publisher():
    thread = threading.Thread(target=publisher_loop, daemon=True)
    thread.start()


@app.get("/price/{symbol}")
def get_price(symbol: str):
    value = client.get(f"price:{symbol}")

    return {
        "symbol": symbol,
        "price": float(value) if value else None
    }


@app.get("/history/{symbol}")
def get_history(symbol: str):
    data = client.lrange(f"history:{symbol}", 0, 20)

    return {
        "symbol": symbol,
        "history": [float(x) for x in data]
    }


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)