"use client";

import { useEffect, useMemo, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

const ORDER_API = process.env.NEXT_PUBLIC_ORDER_API || "http://localhost:8000";
const MARKET_API = process.env.NEXT_PUBLIC_MARKET_API || "http://localhost:9000";

const DEFAULT_SYMBOLS = [
  "BTCUSDT",
  "ETHUSDT",
  "BNBUSDT",
  "SOLUSDT",
  "ADAUSDT",
  "XRPUSDT",
];

type User = {
  user_id: string;
  username: string;
};

type Portfolio = {
  user_id: string;
  cash_balance: number | null;
  updated_at: string | null;
};

type Position = {
  symbol: string;
  qty: number;
  updated_at: string;
};

type Trade = {
  trade_id: string;
  order_id: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  ts: string;
};

type Rejection = {
  order_id: string;
  symbol: string;
  side: string;
  qty: number;
  reason: string;
  ts: string;
};

type OrderStatus = {
  order_id: string;
  user_id: string;
  symbol: string;
  side: string;
  qty: number;
  status: string;
  reason: string | null;
  updated_at: string;
};

export default function Home() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("jafar");
  const [password, setPassword] = useState("123456");

  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);

  const [symbols, setSymbols] = useState<string[]>(DEFAULT_SYMBOLS);
  const [selectedSymbol, setSelectedSymbol] = useState("BTCUSDT");
  const [prices, setPrices] = useState<Record<string, number | null>>({});
  const [history, setHistory] = useState<{ index: number; price: number }[]>([]);

  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [qty, setQty] = useState("0.1");
  const [lastOrder, setLastOrder] = useState<OrderStatus | null>(null);

  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [rejections, setRejections] = useState<Rejection[]>([]);

  const [message, setMessage] = useState("");
  const [marketStatus, setMarketStatus] = useState<"online" | "offline">("offline");
  const [orderStatus, setOrderStatus] = useState<"online" | "offline">("offline");

  const selectedPrice = prices[selectedSymbol];

  const chartData = useMemo(() => {
    return history.length > 0
      ? history
      : Array.from({ length: 20 }, (_, index) => ({
          index,
          price: 100,
        }));
  }, [history]);

  useEffect(() => {
    const savedToken = localStorage.getItem("token");
    const savedUser = localStorage.getItem("user");

    if (savedToken && savedUser) {
      setToken(savedToken);
      setUser(JSON.parse(savedUser));
    }
  }, []);

  useEffect(() => {
    loadSymbols();
    loadPrices(DEFAULT_SYMBOLS);
    loadHistory(selectedSymbol);

    const interval = setInterval(() => {
      loadSymbols();
      loadPrices(symbols.length ? symbols : DEFAULT_SYMBOLS);
      loadHistory(selectedSymbol);
    }, 2500);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSymbol]);

  useEffect(() => {
    if (!user) return;

    loadAccountData(user.user_id);

    const interval = setInterval(() => {
      loadAccountData(user.user_id);
    }, 3000);

    return () => clearInterval(interval);
  }, [user]);

  async function safeJson(res: Response) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }

  async function register() {
    setMessage("");

    try {
      const res = await fetch(`${ORDER_API}/auth/register`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ username, password }),
      });

      const data = await safeJson(res);

      if (!res.ok) {
        setMessage(getErrorMessage(data, "Registration failed"));
        return;
      }

      setMessage("User registered successfully. Now login.");
      setMode("login");
      setOrderStatus("online");
    } catch {
      setOrderStatus("offline");
      setMessage("Order Service is not reachable. Check port-forward on 8000.");
    }
  }

  async function login() {
    setMessage("");

    try {
      const res = await fetch(`${ORDER_API}/auth/login`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ username, password }),
      });

      const data = await safeJson(res);

      if (!res.ok) {
        setMessage(getErrorMessage(data, "Login failed"));
        return;
      }

      const loggedUser = {
        user_id: data.user_id,
        username: data.username,
      };

      localStorage.setItem("token", data.access_token);
      localStorage.setItem("user", JSON.stringify(loggedUser));

      setToken(data.access_token);
      setUser(loggedUser);
      setMessage("Login successful");
      setOrderStatus("online");
    } catch {
      setOrderStatus("offline");
      setMessage("Order Service is not reachable. Check port-forward on 8000.");
    }
  }

  function logout() {
    localStorage.removeItem("token");
    localStorage.removeItem("user");

    setToken(null);
    setUser(null);
    setLastOrder(null);
    setPortfolio(null);
    setPositions([]);
    setTrades([]);
    setRejections([]);
    setMessage("");
  }

  async function loadSymbols() {
    try {
      const res = await fetch(`${MARKET_API}/symbols`, {
        cache: "no-store",
      });

      if (!res.ok) throw new Error("symbols failed");

      const data = await res.json();
      const apiSymbols =
        Array.isArray(data.symbols) && data.symbols.length > 0
          ? data.symbols
          : DEFAULT_SYMBOLS;

      setSymbols(apiSymbols);
      setMarketStatus("online");

      if (!apiSymbols.includes(selectedSymbol)) {
        setSelectedSymbol(apiSymbols[0]);
      }
    } catch {
      setSymbols(DEFAULT_SYMBOLS);
      setMarketStatus("offline");
    }
  }

  async function loadPrices(activeSymbols: string[]) {
    const nextPrices: Record<string, number | null> = {};

    await Promise.all(
      activeSymbols.map(async (symbol) => {
        try {
          const res = await fetch(`${MARKET_API}/price/${symbol}`, {
            cache: "no-store",
          });

          if (!res.ok) throw new Error("price failed");

          const data = await res.json();
          nextPrices[symbol] =
            typeof data.price === "number" ? data.price : null;

          setMarketStatus("online");
        } catch {
          nextPrices[symbol] = prices[symbol] ?? null;
          setMarketStatus("offline");
        }
      })
    );

    setPrices((prev) => ({
      ...prev,
      ...nextPrices,
    }));
  }

  async function loadHistory(symbol: string) {
    try {
      const res = await fetch(`${MARKET_API}/history/${symbol}`, {
        cache: "no-store",
      });

      if (!res.ok) throw new Error("history failed");

      const data = await res.json();

      const rawHistory = Array.isArray(data.history) ? data.history : [];

      const formatted = rawHistory
        .slice()
        .reverse()
        .map((price: number, index: number) => ({
          index,
          price: Number(price),
        }))
        .filter((p: { price: number }) => Number.isFinite(p.price));

      setHistory(formatted);
      setMarketStatus("online");
    } catch {
      setMarketStatus("offline");
    }
  }

  async function createOrder() {
    if (!user) return;

    setMessage("");

    try {
      const res = await fetch(`${ORDER_API}/orders`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          user_id: user.user_id,
          symbol: selectedSymbol,
          side,
          qty: Number(qty),
        }),
      });

      const data = await safeJson(res);

      if (!res.ok) {
        setMessage(getErrorMessage(data, "Order failed"));
        return;
      }

      setMessage(`Order created: ${data.order_id}`);
      setOrderStatus("online");

      setTimeout(() => {
        loadOrderStatus(data.order_id);
        loadAccountData(user.user_id);
      }, 1200);

      setTimeout(() => {
        loadOrderStatus(data.order_id);
        loadAccountData(user.user_id);
      }, 2500);
    } catch {
      setOrderStatus("offline");
      setMessage("Order Service is not reachable. Check port-forward on 8000.");
    }
  }

  async function loadOrderStatus(orderId: string) {
    try {
      const res = await fetch(`${ORDER_API}/orders/${orderId}`, {
        cache: "no-store",
      });

      const data = await safeJson(res);

      if (res.ok && data) {
        setLastOrder(data);
        setOrderStatus("online");
      }
    } catch {
      setOrderStatus("offline");
    }
  }

  async function loadAccountData(userId: string) {
    try {
      const [portfolioRes, positionsRes, tradesRes, rejectionsRes] =
        await Promise.all([
          fetch(`${ORDER_API}/portfolio/${userId}`, { cache: "no-store" }),
          fetch(`${ORDER_API}/positions/${userId}`, { cache: "no-store" }),
          fetch(`${ORDER_API}/trades/${userId}`, { cache: "no-store" }),
          fetch(`${ORDER_API}/rejections/${userId}`, { cache: "no-store" }),
        ]);

      const portfolioData = await safeJson(portfolioRes);
      const positionsData = await safeJson(positionsRes);
      const tradesData = await safeJson(tradesRes);
      const rejectionsData = await safeJson(rejectionsRes);

      setPortfolio(portfolioData);
      setPositions(positionsData?.positions || []);
      setTrades(tradesData?.trades || []);
      setRejections(rejectionsData?.rejections || []);
      setOrderStatus("online");
    } catch {
      setOrderStatus("offline");
    }
  }

  if (!user || !token) {
    return (
      <main className="min-h-screen bg-slate-950 text-white flex items-center justify-center p-6">
        <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
          <h1 className="text-2xl font-bold mb-2">Trading Simulation</h1>
          <p className="text-slate-400 mb-6">
            Scalable distributed trading platform demo
          </p>

          <div className="grid grid-cols-2 gap-2 mb-6">
            <button
              onClick={() => setMode("login")}
              className={`rounded-xl py-2 ${
                mode === "login" ? "bg-blue-600" : "bg-slate-800"
              }`}
            >
              Login
            </button>

            <button
              onClick={() => setMode("register")}
              className={`rounded-xl py-2 ${
                mode === "register" ? "bg-blue-600" : "bg-slate-800"
              }`}
            >
              Register
            </button>
          </div>

          <div className="space-y-4">
            <input
              className="w-full rounded-xl bg-slate-800 border border-slate-700 px-4 py-3 outline-none"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />

            <input
              className="w-full rounded-xl bg-slate-800 border border-slate-700 px-4 py-3 outline-none"
              placeholder="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />

            <button
              onClick={mode === "login" ? login : register}
              className="btn w-full"
            >
              {mode === "login" ? "Login" : "Register"}
            </button>

            {message && (
              <div className="rounded-xl bg-slate-800 border border-slate-700 p-3 text-sm text-yellow-300">
                {message}
              </div>
            )}
          </div>

          <div className="mt-6 text-xs text-slate-500 space-y-1">
            <p>Order API: {ORDER_API}</p>
            <p>Market API: {MARKET_API}</p>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-slate-950 text-white p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <header className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">Trading Simulation Dashboard</h1>
            <p className="text-slate-400">
              FastAPI • RabbitMQ • Redis • PostgreSQL • Kubernetes
            </p>
          </div>

          <div className="flex flex-col md:flex-row gap-3">
            <StatusCard label="Order Service" status={orderStatus} />
            <StatusCard label="Market Service" status={marketStatus} />

            <div className="bg-slate-900 border border-slate-800 rounded-2xl px-4 py-3">
              <p className="text-sm text-slate-400">Logged in as</p>
              <div className="flex items-center gap-4">
                <p className="font-semibold">{user.username}</p>
                <button
                  onClick={logout}
                  className="text-sm bg-red-600 hover:bg-red-700 px-3 py-1 rounded-lg"
                >
                  Logout
                </button>
              </div>
            </div>
          </div>
        </header>

        {message && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 text-yellow-300">
            {message}
          </div>
        )}

        <section className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-4">
          {symbols.map((symbol) => (
            <button
              key={symbol}
              type="button"
              onClick={() => setSelectedSymbol(symbol)}
              className={`text-left bg-slate-900 border rounded-2xl p-4 transition ${
                selectedSymbol === symbol
                  ? "border-blue-500 ring-1 ring-blue-500"
                  : "border-slate-800 hover:border-slate-600"
              }`}
            >
              <p className="text-slate-400 text-sm">{symbol}</p>
              <p className="text-2xl font-bold">
                {prices[symbol] != null ? prices[symbol]?.toFixed(2) : "-"}
              </p>
              <p className="text-xs text-slate-500 mt-1">
                {selectedSymbol === symbol ? "Selected" : "Click to view"}
              </p>
            </button>
          ))}
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-5">
            <div className="flex items-center justify-between gap-4 mb-4">
              <div>
                <h2 className="text-xl font-bold">
                  Price History — {selectedSymbol}
                </h2>
                <p className="text-sm text-slate-400">
                  Current price:{" "}
                  {selectedPrice != null ? selectedPrice.toFixed(2) : "-"}
                </p>
              </div>

              <select
                className="rounded-xl bg-slate-800 border border-slate-700 px-4 py-2"
                value={selectedSymbol}
                onChange={(e) => setSelectedSymbol(e.target.value)}
              >
                {symbols.map((symbol) => (
                  <option key={symbol} value={symbol}>
                    {symbol}
                  </option>
                ))}
              </select>
            </div>

            <div className="w-full h-[320px] min-h-[320px]">
              <LineChart width={760} height={300} data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="index" />
                <YAxis domain={["auto", "auto"]} />
                <Tooltip />
                <Line
                  type="monotone"
                  dataKey="price"
                  stroke="#60a5fa"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </div>

            {marketStatus === "offline" && (
              <p className="mt-3 text-sm text-red-300">
                Market API is not reachable. Check: kubectl port-forward
                svc/market-service 9000:9000 -n trading-sim
              </p>
            )}
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
            <h2 className="text-xl font-bold mb-4">Create Order</h2>

            <div className="space-y-4">
              <select
                className="w-full rounded-xl bg-slate-800 border border-slate-700 px-4 py-3"
                value={selectedSymbol}
                onChange={(e) => setSelectedSymbol(e.target.value)}
              >
                {symbols.map((symbol) => (
                  <option key={symbol} value={symbol}>
                    {symbol}
                  </option>
                ))}
              </select>

              <div className="grid grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => setSide("BUY")}
                  className={`rounded-xl py-3 font-semibold ${
                    side === "BUY" ? "bg-green-600" : "bg-slate-800"
                  }`}
                >
                  BUY
                </button>

                <button
                  type="button"
                  onClick={() => setSide("SELL")}
                  className={`rounded-xl py-3 font-semibold ${
                    side === "SELL" ? "bg-red-600" : "bg-slate-800"
                  }`}
                >
                  SELL
                </button>
              </div>

              <input
                className="w-full rounded-xl bg-slate-800 border border-slate-700 px-4 py-3"
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                placeholder="Quantity"
                type="number"
                step="0.01"
                min="0.01"
              />

              <button
                type="button"
                onClick={createOrder}
                className="btn w-full"
              >
                Submit Order
              </button>
            </div>

            {lastOrder && (
              <div className="mt-5 bg-slate-800 rounded-xl p-4">
                <p className="text-sm text-slate-400">Last Order</p>
                <p className="font-mono text-xs break-all">
                  {lastOrder.order_id}
                </p>

                <p className="mt-2">
                  Status:{" "}
                  <span
                    className={
                      lastOrder.status === "FILLED"
                        ? "text-green-400"
                        : lastOrder.status === "REJECTED"
                        ? "text-red-400"
                        : "text-yellow-400"
                    }
                  >
                    {lastOrder.status}
                  </span>
                </p>

                <p className="text-sm text-slate-400 mt-1">
                  {lastOrder.side} {lastOrder.qty} {lastOrder.symbol}
                </p>

                {lastOrder.reason && (
                  <p className="text-sm text-red-300 mt-1">
                    {lastOrder.reason}
                  </p>
                )}
              </div>
            )}
          </div>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
            <h2 className="text-xl font-bold mb-4">Portfolio</h2>
            <p className="text-slate-400">Cash Balance</p>
            <p className="text-3xl font-bold">
              {portfolio?.cash_balance != null
                ? portfolio.cash_balance.toFixed(2)
                : "-"}
            </p>
            <p className="text-xs text-slate-500 mt-2">
              User ID: {user.user_id}
            </p>
          </div>

          <div className="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-5">
            <h2 className="text-xl font-bold mb-4">Positions</h2>
            <Table
              headers={["Symbol", "Qty", "Updated"]}
              rows={positions.map((p) => [
                p.symbol,
                String(p.qty),
                formatDate(p.updated_at),
              ])}
            />
          </div>
        </section>

        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
            <h2 className="text-xl font-bold mb-4">Latest Trades</h2>
            <Table
              headers={["Symbol", "Side", "Qty", "Price", "Time"]}
              rows={trades.slice(0, 10).map((t) => [
                t.symbol,
                t.side,
                String(t.qty),
                Number(t.price).toFixed(2),
                formatDate(t.ts),
              ])}
            />
          </div>

          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
            <h2 className="text-xl font-bold mb-4">Rejections</h2>
            <Table
              headers={["Symbol", "Side", "Qty", "Reason"]}
              rows={rejections.slice(0, 10).map((r) => [
                r.symbol,
                r.side,
                String(r.qty),
                r.reason,
              ])}
            />
          </div>
        </section>
      </div>
    </main>
  );
}

function StatusCard({
  label,
  status,
}: {
  label: string;
  status: "online" | "offline";
}) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl px-4 py-3">
      <p className="text-sm text-slate-400">{label}</p>
      <p
        className={`font-semibold ${
          status === "online" ? "text-green-400" : "text-red-400"
        }`}
      >
        {status === "online" ? "Online" : "Offline"}
      </p>
    </div>
  );
}

function Table({
  headers,
  rows,
}: {
  headers: string[];
  rows: string[][];
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-slate-400 border-b border-slate-800">
            {headers.map((h) => (
              <th key={h} className="py-2 pr-4">
                {h}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td className="py-4 text-slate-500" colSpan={headers.length}>
                No data
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr key={i} className="border-b border-slate-800">
                {row.map((cell, j) => (
                  <td key={j} className="py-3 pr-4">
                    {cell}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

function formatDate(value: string) {
  if (!value) return "-";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return value;

  return date.toLocaleString();
}

function getErrorMessage(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;

  const obj = data as { detail?: unknown };

  if (typeof obj.detail === "string") return obj.detail;

  if (Array.isArray(obj.detail)) {
    return obj.detail
      .map((err) => {
        if (err && typeof err === "object" && "msg" in err) {
          return String((err as { msg: unknown }).msg);
        }
        return JSON.stringify(err);
      })
      .join(", ");
  }

  if (obj.detail && typeof obj.detail === "object") {
    return JSON.stringify(obj.detail);
  }

  return fallback;
}