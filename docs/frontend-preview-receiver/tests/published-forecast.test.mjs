import assert from "node:assert/strict";
import test from "node:test";

import { createPublishedForecastHandler } from "../server/published-forecast.mjs";

const publishToken = "synthetic-publisher";
const readToken = "synthetic-reader";
const publishTime = new Date("2026-11-21T12:00:00.000Z");

const analytics = {
  generated_at: "2026-11-18T00:00:00",
  total_transaction_count: 12,
  total_weekly_records: 2,
  total_revenue: 55,
  avg_weekly_bookings: 6,
  peak_week_date: "2026-11-16T00:00:00",
  peak_week_bookings: 7,
  growth_rate: 0.25,
  date_coverage: {
    start_date: "2026-11-09T00:00:00",
    end_date: "2026-11-16T00:00:00",
    span_weeks: 2,
  },
  bookings_by_year: [{ year: "2026", bookings: 12 }],
  bookings_by_month: [{ month: "2026-11", bookings: 12 }],
  holiday_breakdown: { holiday_weeks: 0, non_holiday_weeks: 2, holiday_pct: 0 },
  avg_lead_time_days: null,
  revenue_by_month: [],
  lead_time_distribution: [],
  top_airlines: [],
  top_routes: [],
  revenue_by_year: [],
  data_quality: null,
  available_years: ["2026"],
};

const forecastA = {
  schema_version: 1,
  run_id: "00000000-0000-4000-8000-000000000001",
  model_id: 12,
  run_created_at: "2026-11-20T12:00:00.123456+00:00",
  data_through: "2026-11-16",
  business_analytics: { overall: analytics, "2026": analytics },
  forecast_graph: { data: [{ date: "2026-11-23T00:00:00", predicted: 10 }] },
  forecast_outlook: {
    forecasted_bookings_2w: 10,
    highest_forecast_week_date: "2026-11-23T00:00:00",
    highest_forecast_week_value: 10,
    forecasted_bookings_10w: 10,
    critical_weeks: [{
      week_start: "2026-11-23T00:00:00",
      week_end: null,
      forecasted_volume: 10,
      risk_factor: "HIGH",
    }],
  },
  strategic_actions: {
    generated_for_period: "November 2026",
    actions: [{
      priority: "HIGH",
      category: "Staffing",
      action: "Synthetic action",
      trigger: "Synthetic condition",
    }],
  },
};

const forecastB = {
  ...forecastA,
  run_id: "00000000-0000-4000-8000-000000000002",
  model_id: 3,
  run_created_at: "2026-11-20T12:00:00.123457Z",
};

const forecastC = {
  ...forecastB,
  run_id: "00000000-0000-4000-8000-000000000003",
  run_created_at: "2026-11-21T09:00:00Z",
};

function memoryStore() {
  let current = null;
  let revision = 0;
  return {
    beforeWrite: undefined,
    async read() {
      return structuredClone(current);
    },
    async writeIfCurrent(published, expectedEtag) {
      await this.beforeWrite?.();
      if ((current?.etag ?? null) !== expectedEtag) return false;
      current = { published: JSON.parse(JSON.stringify(published)), etag: String(++revision) };
      return true;
    },
  };
}

function createTestHandler(store, options = {}) {
  return createPublishedForecastHandler({
    store,
    readToken,
    publishToken,
    now: () => publishTime,
    ...options,
  });
}

function post(handler, data, token = publishToken) {
  return handler(new Request("https://example.test/api/published-forecast", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  }));
}

function get(handler, token = readToken) {
  return handler(new Request("https://example.test/api/published-forecast", {
    headers: { Authorization: `Bearer ${token}` },
  }));
}

test("a failed replacement keeps forecast A readable and B can be retried", async () => {
  const store = memoryStore();
  const handler = createTestHandler(store);
  assert.equal((await post(handler, forecastA)).status, 200);

  store.beforeWrite = () => {
    throw new Error("Vercel Blob unavailable before commit");
  };
  assert.equal((await post(handler, forecastB)).status, 503);
  const afterFailure = await get(handler);
  assert.equal(afterFailure.status, 200);
  assert.match(afterFailure.headers.get("Cache-Control"), /private, no-store/);
  assert.deepEqual(await afterFailure.json(), {
    snapshot: forecastA,
    published_at: "2026-11-21T12:00:00.000Z",
  });

  store.beforeWrite = undefined;
  const retry = await post(handler, forecastB);
  assert.equal(retry.status, 200);
  assert.deepEqual(await retry.json(), {
    run_id: forecastB.run_id,
    published_at: "2026-11-21T12:00:00.000Z",
  });
  assert.deepEqual((await (await get(handler)).json()).snapshot, forecastB);
});

test("retrying the same run returns its original acknowledgement", async () => {
  const store = memoryStore();
  const handler = createTestHandler(store);
  const original = await (await post(handler, forecastA)).json();

  store.beforeWrite = () => assert.fail("Duplicate publication attempted a write");
  const restartedHandler = createTestHandler(store, {
    now: () => new Date("2026-11-21T13:00:00.000Z"),
  });
  const duplicate = await post(restartedHandler, forecastA);
  assert.equal(duplicate.status, 200);
  assert.deepEqual(await duplicate.json(), original);

  const collided = await post(restartedHandler, { ...forecastA, model_id: 99 });
  assert.equal(collided.status, 409);
  assert.deepEqual((await (await get(handler)).json()).snapshot, forecastA);
});


test("a retry remains idempotent after JSON storage normalizes negative zero", async () => {
  const handler = createTestHandler(memoryStore());
  const snapshot = {
    ...forecastA,
    business_analytics: { overall: { ...analytics, growth_rate: -0 } },
  };
  const request = () => new Request("https://example.test/api/published-forecast", {
    method: "POST",
    headers: { Authorization: `Bearer ${publishToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(snapshot).replace('"growth_rate":0', '"growth_rate":-0.0'),
  });

  assert.equal((await handler(request())).status, 200);
  assert.equal((await handler(request())).status, 200);
});

test("older and distinct tied runs cannot replace the current publication", async () => {
  const handler = createTestHandler(memoryStore());
  assert.equal((await post(handler, forecastB)).status, 200);
  assert.equal((await post(handler, forecastA)).status, 409);
  const tied = {
    ...forecastC,
    run_created_at: "2026-11-20T12:00:00.123457+00:00",
    model_id: 99,
  };
  assert.equal((await post(handler, tied)).status, 409);
  assert.deepEqual((await (await get(handler)).json()).snapshot, forecastB);
});

test("a writer that loses a conditional update cannot revert a competing newer publication", async () => {
  const store = memoryStore();
  const handler = createTestHandler(store);
  assert.equal((await post(handler, forecastA)).status, 200);

  store.beforeWrite = async () => {
    store.beforeWrite = undefined;
    assert.equal((await post(handler, forecastC)).status, 200);
  };

  assert.equal((await post(handler, forecastB)).status, 409);
  assert.deepEqual((await (await get(handler)).json()).snapshot, forecastC);
});

test("a conditional create race reloads the winner and acknowledges the same run", async () => {
  const store = memoryStore();
  const handler = createTestHandler(store);
  let winningAck;

  store.beforeWrite = async () => {
    store.beforeWrite = undefined;
    const competing = createTestHandler(store, {
      now: () => new Date("2026-11-21T13:00:00.000Z"),
    });
    const response = await post(competing, forecastA);
    assert.equal(response.status, 200);
    winningAck = await response.json();
  };

  const losingResponse = await post(handler, forecastA);
  assert.equal(losingResponse.status, 200);
  assert.equal(winningAck.published_at, "2026-11-21T13:00:00.000Z");
  assert.deepEqual(await losingResponse.json(), winningAck);
});

test("a newer writer can retry a conditional update after an intermediate run wins", async () => {
  const store = memoryStore();
  const handler = createTestHandler(store);
  assert.equal((await post(handler, forecastA)).status, 200);

  store.beforeWrite = async () => {
    store.beforeWrite = undefined;
    assert.equal((await post(handler, forecastB)).status, 200);
  };

  assert.equal((await post(handler, forecastC)).status, 200);
  assert.deepEqual((await (await get(handler)).json()).snapshot, forecastC);
});

test("reader and publisher credentials are separate and missing credentials fail closed", async () => {
  const store = memoryStore();
  const handler = createTestHandler(store);

  assert.equal((await get(handler, publishToken)).status, 401);
  assert.equal((await post(handler, forecastA, readToken)).status, 401);
  assert.equal((await get(handler)).status, 404);

  const unconfigured = createTestHandler(store, { readToken: undefined, publishToken: "" });
  assert.equal((await get(unconfigured)).status, 503);
  assert.equal((await post(unconfigured, forecastA)).status, 503);

  const sharedCredential = createTestHandler(store, { readToken: publishToken });
  assert.equal((await get(sharedCredential, publishToken)).status, 503);
  assert.equal((await post(sharedCredential, forecastA)).status, 503);
});

test("invalid envelope fields, extra data, malformed JSON and oversized bodies are refused", async () => {
  const handler = createTestHandler(memoryStore());
  const invalid = [
    { ...forecastA, schema_version: 2 },
    { ...forecastA, run_id: "not-a-uuid" },
    { ...forecastA, model_id: 0 },
    { ...forecastA, data_through: "2026-02-30" },
    { ...forecastA, run_created_at: "2026-11-20T12:00:00+08:00" },
    { ...forecastA, run_created_at: "2026-11-31T12:00:00Z" },
    { ...forecastA, raw_bookings: [] },
    { ...forecastA, forecast_graph: { data: [15] } },
    { ...forecastA, strategic_actions: {} },
    { ...forecastA, business_analytics: { "2026": analytics } },
    { ...forecastA, business_analytics: { overall: analytics, raw: analytics } },
    { ...forecastA, business_analytics: { overall: { ...analytics, raw_bookings: [] } } },
    {
      ...forecastA,
      business_analytics: { overall: { ...analytics, bookings_by_year: [{ year: "2026", bookings: 12, raw: [] }] } },
    },
  ];
  for (const entry of invalid) {
    assert.equal((await post(handler, entry)).status, 400);
  }

  const broken = new Request("https://example.test/api/published-forecast", {
    method: "POST",
    headers: { Authorization: `Bearer ${publishToken}`, "Content-Type": "application/json" },
    body: "{",
  });
  assert.equal((await handler(broken)).status, 400);

  const longBody = JSON.stringify({
    ...forecastA,
    strategic_actions: {
      ...forecastA.strategic_actions,
      generated_for_period: "a".repeat(2 * 1024 * 1024),
    },
  });
  const withoutLength = new Request("https://example.test/api/published-forecast", {
    method: "POST",
    headers: { Authorization: `Bearer ${publishToken}`, "Content-Type": "application/json" },
    body: longBody,
  });
  assert.equal((await handler(withoutLength)).status, 413);
  assert.equal((await get(handler)).status, 404);
});
