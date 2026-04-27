import http from "k6/http";
import { check, sleep } from "k6";

const symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"];

export const options = {
  vus: 10,
  duration: "30s",
};

export default function () {
  const payload = JSON.stringify({
    user_id: "load-user",
    symbol: symbols[Math.floor(Math.random() * symbols.length)],
    side: Math.random() > 0.5 ? "BUY" : "SELL",
    qty: 0.1
  });

  const params = {
    headers: {
      "Content-Type": "application/json",
    },
  };

  const res = http.post("http://localhost:8000/orders", payload, params);

  check(res, {
    "status is 200": (r) => r.status === 200,
  });

  sleep(1);
}