import json
import os
import uuid
from datetime import datetime, timezone

import pika
import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
ORDER_QUEUE = os.getenv("ORDER_QUEUE", "orders")
DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI(title="Order Service")


class OrderRequest(BaseModel):
    user_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: str = Field(pattern="^(BUY|SELL)$")
    qty: float = Field(gt=0)


def publish_order(event: dict) -> None:
    params = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=ORDER_QUEUE, durable=True)

    body = json.dumps(event).encode("utf-8")
    channel.basic_publish(
        exchange="",
        routing_key=ORDER_QUEUE,
        body=body,
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
        ),
    )
    connection.close()


def _require_db():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set for order-service")


def ensure_order_status_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_status (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty DOUBLE PRECISION NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    conn.commit()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders")
def create_order(req: OrderRequest):
    _require_db()

    order_id = f"ord_{uuid.uuid4().hex}"
    event = {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "order_id": order_id,
        "user_id": req.user_id,
        "symbol": req.symbol,
        "side": req.side,
        "qty": req.qty,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # 1) Write ACCEPTED to DB first (so we can query status immediately)
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            ensure_order_status_table(conn)
            conn.execute(
                """
                INSERT INTO order_status (order_id, user_id, symbol, side, qty, status, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id) DO NOTHING
                """,
                (order_id, req.user_id, req.symbol, req.side, float(req.qty), "ACCEPTED", None),
            )
            conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write order_status: {e}")

    # 2) Publish event to queue
    try:
        publish_order(event)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish order: {e}")

    return {"order_id": order_id, "status": "ACCEPTED"}


# -------- Phase 1.2 read endpoints (unchanged) --------

@app.get("/portfolio/{user_id}")
def get_portfolio(user_id: str):
    _require_db()
    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            "SELECT user_id, cash_balance, updated_at FROM portfolios WHERE user_id=%s",
            (user_id,),
        ).fetchone()

        if not row:
            return {"user_id": user_id, "cash_balance": None, "updated_at": None}

        return {"user_id": row[0], "cash_balance": row[1], "updated_at": str(row[2])}


@app.get("/positions/{user_id}")
def get_positions(user_id: str):
    _require_db()
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT symbol, qty, updated_at FROM positions WHERE user_id=%s ORDER BY symbol",
            (user_id,),
        ).fetchall()

        return {
            "user_id": user_id,
            "positions": [{"symbol": r[0], "qty": r[1], "updated_at": str(r[2])} for r in rows],
        }


@app.get("/trades/{user_id}")
def get_trades(user_id: str):
    _require_db()
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            """
            SELECT trade_id, order_id, symbol, side, qty, price, ts
            FROM trades
            WHERE user_id=%s
            ORDER BY ts DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()

        return {
            "user_id": user_id,
            "trades": [
                {
                    "trade_id": r[0],
                    "order_id": r[1],
                    "symbol": r[2],
                    "side": r[3],
                    "qty": r[4],
                    "price": r[5],
                    "ts": str(r[6]),
                }
                for r in rows
            ],
        }


@app.get("/rejections/{user_id}")
def get_rejections(user_id: str):
    _require_db()
    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            """
            SELECT order_id, symbol, side, qty, reason, ts
            FROM rejections
            WHERE user_id=%s
            ORDER BY ts DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()

        return {
            "user_id": user_id,
            "rejections": [
                {"order_id": r[0], "symbol": r[1], "side": r[2], "qty": r[3], "reason": r[4], "ts": str(r[5])}
                for r in rows
            ],
        }


# ---------------- Phase 1.3: order status endpoint ----------------

@app.get("/orders/{order_id}")
def get_order_status(order_id: str):
    _require_db()
    with psycopg.connect(DATABASE_URL) as conn:
        ensure_order_status_table(conn)
        row = conn.execute(
            """
            SELECT order_id, user_id, symbol, side, qty, status, reason, updated_at
            FROM order_status
            WHERE order_id=%s
            """,
            (order_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Order not found")

        return {
            "order_id": row[0],
            "user_id": row[1],
            "symbol": row[2],
            "side": row[3],
            "qty": row[4],
            "status": row[5],
            "reason": row[6],
            "updated_at": str(row[7]),
        }