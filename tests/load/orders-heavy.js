import http from "k6/http";
import { check } from "k6";

const symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"];


export const options = {
  vus: 50,
  duration: "60s",
};

export default function () {
  const payload = JSON.stringify({
    user_id: "stress-user",
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
}