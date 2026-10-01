import { test } from "node:test";
import assert from "node:assert/strict";
import { formatFils, priceToFils } from "../lib/price.ts";

test("dinars become whole fils without rounding errors", () => {
  assert.equal(priceToFils("3.45"), 3450);
  assert.equal(priceToFils("0.1"), 100);
  assert.equal(priceToFils(" 12 "), 12000);
  assert.equal(priceToFils("1.005"), 1005);
});

test("anything that is not a clean price is refused", () => {
  for (const value of ["3.4abc", "3.4567", "", "0", "0.000", "-1", "1,5", "1e3", "12345"]) {
    assert.equal(priceToFils(value), null, value);
  }
});

test("fils are shown like the server shows them", () => {
  assert.equal(formatFils(3450, "en"), "3.45 JOD");
  assert.equal(formatFils(3000, "ar"), "3 د.أ");
  assert.equal(formatFils(150, "en"), "0.15 JOD");
  assert.equal(formatFils(1005, "en"), "1.005 JOD");
  assert.equal(formatFils(0, "en"), "0 JOD");
});
