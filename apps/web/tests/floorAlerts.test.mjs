import { test } from "node:test";
import assert from "node:assert/strict";
import { alertKeys, newAlerts } from "../lib/floorAlerts.ts";

const snapshot = (orders, calls) => ({
  active_orders: orders.map(([order_id, status]) => ({ order_id, status })),
  service_queue: calls.map(([id, kind, status]) => ({ id, kind, status })),
});

test("the first load never rings", () => {
  assert.deepEqual(newAlerts(null, alertKeys(snapshot([["o1", "PENDING_APPROVAL"]], []))), { order: false, call: false });
});

test("a new order rings even while another one is approved at the same moment", () => {
  const before = alertKeys(snapshot([["o1", "PENDING_APPROVAL"]], []));
  const after = alertKeys(snapshot([["o1", "PREPARING"], ["o2", "PENDING_APPROVAL"]], []));
  assert.deepEqual(newAlerts(before, after), { order: true, call: false });
});

test("a new guest call or complaint rings; nothing new stays quiet", () => {
  const before = alertKeys(snapshot([], [["s1", "SERVICE", "OPEN"]]));
  const after = alertKeys(snapshot([], [["s1", "SERVICE", "IN_PROGRESS"], ["c1", "COMPLAINT", "OPEN"]]));
  assert.deepEqual(newAlerts(before, after), { order: false, call: true });
  assert.deepEqual(newAlerts(after, after), { order: false, call: false });
});

test("a guest changing an order the kitchen is preparing rings; a change before approval does not", () => {
  const cooking = (amendments) => ({ active_orders: [{ order_id: "o1", status: "PREPARING", amendments }], service_queue: [] });
  const before = alertKeys(cooking([]));
  assert.deepEqual(newAlerts(before, alertKeys(cooking([{ id: "a1", needs_attention: true }]))), { order: true, call: false });
  const seen = alertKeys(cooking([{ id: "a1", needs_attention: false }]));
  assert.deepEqual(newAlerts(before, seen), { order: false, call: false });
});
