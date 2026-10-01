import { test } from "node:test";
import assert from "node:assert/strict";
import { restaurantTime } from "../lib/time.ts";

test("times are shown on the restaurant's clock (Amman), whatever the device zone", () => {
  // 18:30 UTC is 21:30 in Amman (UTC+3).
  assert.equal(restaurantTime("2026-09-28T18:30:00Z", "en-GB", { hour: "2-digit", minute: "2-digit" }), "21:30");
});

test("an invalid time shows nothing instead of 'Invalid Date'", () => {
  assert.equal(restaurantTime("not a date", "en-GB"), "");
});
