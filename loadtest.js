import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

// Custom metrics
const orderCreated    = new Counter("orders_created");
const orderFetched    = new Counter("orders_fetched");
const errorRate       = new Rate("error_rate");
const createLatency   = new Trend("create_order_latency", true);
const fetchLatency    = new Trend("fetch_order_latency",  true);

// Test configuration
export const options = {
  stages: [
    { duration: "30s", target: 20 },
    { duration: "60s", target: 20 },
    { duration: "15s", target: 0  },
  ],
  thresholds: {
    http_req_duration: ["p(95)<500"],
    error_rate: ["rate<0.01"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";

// Helpers
function randomId() {
  return Math.random().toString(36).substring(2, 10);
}

// Default function
export default function () {
  const idempotencyKey = `load-test-${randomId()}-${Date.now()}`;

  const payload = JSON.stringify({
    customer_id: `cust-${randomId()}`,
    item_id:     `item-${randomId()}`,
    quantity:    Math.floor(Math.random() * 10) + 1,
  });

  const headers = {
    "Content-Type":    "application/json",
    "Idempotency-Key": idempotencyKey,
  };

  const createRes = http.post(`${BASE_URL}/orders`, payload, { headers });

  createLatency.add(createRes.timings.duration);

  const createOk = check(createRes, {
    "POST /orders → 201": (r) => r.status === 201,
    "response has order_id": (r) => {
      try { return JSON.parse(r.body).order_id !== undefined; }
      catch { return false; }
    },
  });

  errorRate.add(!createOk);

  if (!createOk) {
    sleep(0.5);
    return;
  }

  orderCreated.add(1);
  const orderId = JSON.parse(createRes.body).order_id;

  const fetchRes = http.get(`${BASE_URL}/orders/${orderId}`);

  fetchLatency.add(fetchRes.timings.duration);

  const fetchOk = check(fetchRes, {
    "GET /orders/:id → 200":      (r) => r.status === 200,
    "returned correct order_id":  (r) => {
      try { return JSON.parse(r.body).order_id === orderId; }
      catch { return false; }
    },
  });

  errorRate.add(!fetchOk);
  if (fetchOk) orderFetched.add(1);

  if (Math.random() < 0.1) {
    const healthRes = http.get(`${BASE_URL}/health`);
    check(healthRes, {
      "GET /health → 200": (r) => r.status === 200,
    });
  }

  sleep(0.5);
}

// Summary printed at the end of the test
export function handleSummary(data) {
  const dur   = (t) => (t / 1000).toFixed(2) + "s";
  const ms    = (t) => (t  || 0).toFixed(2)  + "ms";
  const pct   = (r) => ((r || 0) * 100).toFixed(2) + "%";

  const d = data.metrics;

  const summary = `
========================================
  k6 Load Test Summary — Orders API
========================================
Duration       : ~105s (30s ramp-up, 60s sustained, 15s ramp-down)
Max VUs        : 20

--- HTTP Overview ---
Total requests : ${d.http_reqs?.values?.count        || 0}
RPS (avg)      : ${(d.http_reqs?.values?.rate || 0).toFixed(2)} req/s
Failed requests: ${pct(d.error_rate?.values?.rate)}

--- Latency (all requests) ---
p50            : ${ms(d.http_req_duration?.values?.["p(50)"])}
p90            : ${ms(d.http_req_duration?.values?.["p(90)"])}
p95            : ${ms(d.http_req_duration?.values?.["p(95)"])}
p99            : ${ms(d.http_req_duration?.values?.["p(99)"])}

--- POST /orders latency ---
p50            : ${ms(d.create_order_latency?.values?.["p(50)"])}
p95            : ${ms(d.create_order_latency?.values?.["p(95)"])}

--- GET /orders/:id latency ---
p50            : ${ms(d.fetch_order_latency?.values?.["p(50)"])}
p95            : ${ms(d.fetch_order_latency?.values?.["p(95)"])}

--- Counters ---
Orders created : ${d.orders_created?.values?.count  || 0}
Orders fetched : ${d.orders_fetched?.values?.count  || 0}

========================================
`;

  console.log(summary);
  return {
    "loadtest-summary.txt": summary,
    stdout: "\n",
  };
}
