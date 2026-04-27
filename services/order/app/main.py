import json
import os
import uuid
from datetime import datetime, timezone, timedelta

import pika
import psycopg
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from prometheus_client import Counter, generate_latest
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi.middleware.cors import CORSMiddleware


RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
ORDER_QUEUE = os.getenv("ORDER_QUEUE", "orders")
DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

app = FastAPI(title="Order Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
security = HTTPBearer()

# ---------------- Metrics ----------------
orders_created = Counter(
    "orders_created_total",
    "Total number of created orders"
)

# ---------------- HTTP REQUESTS ----------------
http_requests = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint"]
)

# ---------------- Models ----------------
class OrderRequest(BaseModel):
    user_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: str = Field(pattern="^(BUY|SELL)$")
    qty: float = Field(gt=0)



class RegisterRequest(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=6)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=6)

# ---------------- RabbitMQ ----------------
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

# ---------------- DB helpers ----------------
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


def ensure_users_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    conn.commit()




# ---------------- Auth helper ----------------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "user_id": payload.get("user_id"),
            "username": payload.get("username"),
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ---------------- Health ----------------
@app.get("/health")
def health():
    http_requests.labels(method="GET", endpoint="/health").inc()
    return {"status": "ok"}

# ---------------- Create Order ----------------
@app.post("/orders")
def create_order(req: OrderRequest):
    http_requests.labels(method="POST", endpoint="/orders").inc()
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

    # 1) DB write
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

    # 2) Publish to RabbitMQ
    try:
        publish_order(event)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to publish order: {e}")

    # 3) METRICS 🔥
    orders_created.inc()

    return {"order_id": order_id, "status": "ACCEPTED"}

# ---------------- Portfolio ----------------
@app.get("/portfolio/{user_id}")
def get_portfolio(user_id: str):
    http_requests.labels(method="GET", endpoint="/portfolio/{user_id}").inc()
    _require_db()

    with psycopg.connect(DATABASE_URL) as conn:
        row = conn.execute(
            "SELECT user_id, cash_balance, updated_at FROM portfolios WHERE user_id=%s",
            (user_id,),
        ).fetchone()

        if not row:
            return {"user_id": user_id, "cash_balance": None, "updated_at": None}

        return {
            "user_id": row[0],
            "cash_balance": row[1],
            "updated_at": str(row[2]),
        }

# ---------------- Positions ----------------
@app.get("/positions/{user_id}")
def get_positions(user_id: str):
    http_requests.labels(method="GET", endpoint="/positions/{user_id}").inc()
    _require_db()

    with psycopg.connect(DATABASE_URL) as conn:
        rows = conn.execute(
            "SELECT symbol, qty, updated_at FROM positions WHERE user_id=%s ORDER BY symbol",
            (user_id,),
        ).fetchall()

        return {
            "user_id": user_id,
            "positions": [
                {"symbol": r[0], "qty": r[1], "updated_at": str(r[2])}
                for r in rows
            ],
        }

# ---------------- Trades ----------------
@app.get("/trades/{user_id}")
def get_trades(user_id: str):
    http_requests.labels(method="GET", endpoint="/trades/{user_id}").inc()
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

# ---------------- Rejections ----------------
@app.get("/rejections/{user_id}")
def get_rejections(user_id: str):
    http_requests.labels(method="GET", endpoint="/rejections/{user_id}").inc()
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
                {
                    "order_id": r[0],
                    "symbol": r[1],
                    "side": r[2],
                    "qty": r[3],
                    "reason": r[4],
                    "ts": str(r[5]),
                }
                for r in rows
            ],
        }

# ---------------- Order Status ----------------
@app.get("/orders/{order_id}")
def get_order_status(order_id: str):
    http_requests.labels(method="GET", endpoint="/orders/{order_id}").inc()
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

# ---------------- Metrics Endpoint ----------------
@app.get("/metrics")
def metrics():
    http_requests.labels(method="GET", endpoint="/metrics").inc()
    return Response(generate_latest(), media_type="text/plain")


@app.post("/auth/register")
def register(req: RegisterRequest):
    _require_db()

    user_id = f"usr_{uuid.uuid4().hex}"
    password_hash = hash_password(req.password)

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            ensure_users_table(conn)

            conn.execute(
                """
                INSERT INTO users (user_id, username, password_hash)
                VALUES (%s, %s, %s)
                """,
                (user_id, req.username, password_hash),
            )
            conn.commit()

        return {
            "user_id": user_id,
            "username": req.username,
            "message": "User registered successfully"
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=400, detail="Username already exists")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {e}")


@app.post("/auth/login")
def login(req: LoginRequest):
    _require_db()

    with psycopg.connect(DATABASE_URL) as conn:
        ensure_users_table(conn)

        row = conn.execute(
            """
            SELECT user_id, username, password_hash
            FROM users
            WHERE username=%s
            """,
            (req.username,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        user_id, username, password_hash = row

        if not verify_password(req.password, password_hash):
            raise HTTPException(status_code=401, detail="Invalid username or password")

        token = create_access_token({
            "user_id": user_id,
            "username": username,
        })

        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user_id,
            "username": username
        }


@app.get("/users/me")
def users_me(current_user: dict = Depends(get_current_user)):
    return current_user