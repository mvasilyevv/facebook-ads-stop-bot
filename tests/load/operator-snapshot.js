import http from "k6/http";
import { check } from "k6";

const baseUrl = (__ENV.BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const apiKey = __ENV.API_KEY || "";

export const options = {
  scenarios: {
    operator_snapshot: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.REQUEST_RATE || 20),
      timeUnit: "1s",
      duration: __ENV.DURATION || "30s",
      preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 10),
      maxVUs: Number(__ENV.MAX_VUS || 50),
    },
  },
  thresholds: {
    checks: ["rate==1"],
    http_req_failed: ["rate==0"],
    "http_req_duration{endpoint:operator_snapshot}": [
      "p(95)<500",
      "p(99)<1000",
    ],
  },
};

export default function () {
  const headers = { Accept: "application/json", "Accept-Encoding": "gzip" };
  if (apiKey) headers["X-API-Key"] = apiKey;

  const response = http.get(`${baseUrl}/api/operator/snapshot`, {
    headers,
    tags: { endpoint: "operator_snapshot" },
    timeout: "5s",
  });

  check(response, {
    "snapshot returns 200": (result) => result.status === 200,
    "snapshot is bounded": (result) =>
      result.body && result.body.length <= 1024 * 1024,
    "snapshot has explicit state": (result) => {
      if (result.status !== 200) return false;
      try {
        const body = result.json();
        return Boolean(
          body && body.meta && body.system && body.economy && body.funnel,
        );
      } catch (_error) {
        return false;
      }
    },
  });
}
