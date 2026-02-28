import json
import os
import time
import uuid
from datetime import datetime, timezone

import pika
import psycopg

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
ORDER_QUEUE = os.getenv("ORDER_QUEUE", "orders")
DATABASE_URL = os.getenv("DATABASE_URL")


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            order_id TEXT UNIQUE NOT NULL,
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty DOUBLE PRECISION NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            ts TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE IF NOT EXISTS portfolios (
            user_id TEXT PRIMARY KEY,
            cash_balance DOUBLE PRECISION NOT NULL DEFAULT 10000.0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS positions (
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            qty DOUBLE PRECISION NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS rejections (
            order_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            qty DOUBLE PRECISION NOT NULL,
            reason TEXT NOT NULL,
            ts TIMESTAMPTZ NOT NULL
        );

        -- Phase 1.3: order lifecycle tracking
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


def ensure_user_rows(conn: psycopg.Connection, user_id: str, symbol: str) -> None:
    conn.execute(
        """
        INSERT INTO portfolios (user_id)
        VALUES (%s)
        ON CONFLICT (user_id) DO NOTHING
        """,
        (user_id,),
    )
    conn.execute(
        """
        INSERT INTO positions (user_id, symbol)
        VALUES (%s, %s)
        ON CONFLICT (user_id, symbol) DO NOTHING
        """,
        (user_id, symbol),
    )


def get_cash_balance(conn: psycopg.Connection, user_id: str) -> float:
    row = conn.execute(
        "SELECT cash_balance FROM portfolios WHERE user_id=%s",
        (user_id,),
    ).fetchone()
    return float(row[0]) if row else 0.0


def get_position_qty(conn: psycopg.Connection, user_id: str, symbol: str) -> float:
    row = conn.execute(
        "SELECT qty FROM positions WHERE user_id=%s AND symbol=%s",
        (user_id, symbol),
    ).fetchone()
    return float(row[0]) if row else 0.0


def record_rejection(conn: psycopg.Connection, order_event: dict, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO rejections (order_id, user_id, symbol, side, qty, reason, ts)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (order_id) DO NOTHING
        """,
        (
            order_event["order_id"],
            order_event["user_id"],
            order_event["symbol"],
            order_event["side"],
            float(order_event["qty"]),
            reason,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def upsert_order_status_if_missing(conn: psycopg.Connection, order_event: dict) -> None:
    # If order-service didn't insert for some reason, execution will still create it.
    conn.execute(
        """
        INSERT INTO order_status (order_id, user_id, symbol, side, qty, status, reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (order_id) DO NOTHING
        """,
        (
            order_event["order_id"],
            order_event["user_id"],
            order_event["symbol"],
            order_event["side"],
            float(order_event["qty"]),
            "ACCEPTED",
            None,
        ),
    )


def set_order_status(conn: psycopg.Connection, order_id: str, status: str, reason: str | None) -> None:
    conn.execute(
        """
        UPDATE order_status
        SET status=%s, reason=%s, updated_at=NOW()
        WHERE order_id=%s
        """,
        (status, reason, order_id),
    )


def apply_portfolio_updates(conn: psycopg.Connection, order_event: dict, price: float) -> None:
    user_id = order_event["user_id"]
    symbol = order_event["symbol"]
    side = order_event["side"]
    qty = float(order_event["qty"])
    delta_cash = qty * price

    if side == "BUY":
        conn.execute(
            """
            UPDATE portfolios
            SET cash_balance = cash_balance - %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (delta_cash, user_id),
        )
        conn.execute(
            """
            UPDATE positions
            SET qty = qty + %s,
                updated_at = NOW()
            WHERE user_id = %s AND symbol = %s
            """,
            (qty, user_id, symbol),
        )
    else:  # SELL
        conn.execute(
            """
            UPDATE portfolios
            SET cash_balance = cash_balance + %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (delta_cash, user_id),
        )
        conn.execute(
            """
            UPDATE positions
            SET qty = qty - %s,
                updated_at = NOW()
            WHERE user_id = %s AND symbol = %s
            """,
            (qty, user_id, symbol),
        )


def process_order(conn: psycopg.Connection, order_event: dict) -> None:
    price = 100.0  # Phase 2-də Redis market price olacaq

    user_id = order_event["user_id"]
    symbol = order_event["symbol"]
    side = order_event["side"]
    qty = float(order_event["qty"])
    order_id = order_event["order_id"]

    trade = {
        "trade_id": f"trd_{uuid.uuid4().hex}",
        "order_id": order_id,
        "user_id": user_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    try:
        with conn.transaction():
            ensure_user_rows(conn, user_id, symbol)

            # Make sure order_status row exists
            upsert_order_status_if_missing(conn, order_event)

            # -------- Risk checks --------
            if side == "BUY":
                cash = get_cash_balance(conn, user_id)
                cost = qty * price
                if cash < cost:
                    reason = f"INSUFFICIENT_CASH cash={cash} cost={cost}"
                    record_rejection(conn, order_event, reason)
                    set_order_status(conn, order_id, "REJECTED", reason)
                    print(f"[EXECUTION] REJECTED order_id={order_id} reason=INSUFFICIENT_CASH")
                    return

            if side == "SELL":
                pos = get_position_qty(conn, user_id, symbol)
                if pos < qty:
                    reason = f"INSUFFICIENT_POSITION pos={pos} sell_qty={qty}"
                    record_rejection(conn, order_event, reason)
                    set_order_status(conn, order_id, "REJECTED", reason)
                    print(f"[EXECUTION] REJECTED order_id={order_id} reason=INSUFFICIENT_POSITION")
                    return

            # -------- Execute trade --------
            conn.execute(
                """
                INSERT INTO trades (trade_id, order_id, user_id, symbol, side, qty, price, ts)
                VALUES (%(trade_id)s, %(order_id)s, %(user_id)s, %(symbol)s, %(side)s, %(qty)s, %(price)s, %(ts)s)
                """,
                trade,
            )

            apply_portfolio_updates(conn, order_event, price)
            set_order_status(conn, order_id, "FILLED", None)

        print(f"[EXECUTION] FILLED order_id={order_id}")

    except psycopg.errors.UniqueViolation:
        # duplicate order -> already processed
        print(f"[EXECUTION] Duplicate order detected (idempotent). order_id={order_id}")
    except Exception as e:
        print(f"[EXECUTION] ERROR processing order_id={order_id}: {e}")
        raise


def main():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    print("[EXECUTION] Connecting to DB...")
    while True:
        try:
            with psycopg.connect(DATABASE_URL) as conn:
                ensure_schema(conn)
            break
        except Exception as e:
            print(f"[EXECUTION] Postgres not ready yet: {e}. Retrying in 2s...")
            time.sleep(2)

    print("[EXECUTION] Connecting to RabbitMQ...")
    params = pika.URLParameters(RABBITMQ_URL)

    while True:
        try:
            connection = pika.BlockingConnection(params)
            break
        except Exception as e:
            print(f"[EXECUTION] RabbitMQ not ready yet: {e}. Retrying in 2s...")
            time.sleep(2)

    channel = connection.channel()
    channel.queue_declare(queue=ORDER_QUEUE, durable=True)
    channel.basic_qos(prefetch_count=10)

    def on_message(ch, method, properties, body):
        event = json.loads(body.decode("utf-8"))
        order_id = event.get("order_id")
        print(f"[EXECUTION] Received OrderPlaced: order_id={order_id}")

        with psycopg.connect(DATABASE_URL) as conn:
            process_order(conn, event)

        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=ORDER_QUEUE, on_message_callback=on_message)
    print("[EXECUTION] Waiting for messages...")
    channel.start_consuming()


if __name__ == "__main__":
    main()