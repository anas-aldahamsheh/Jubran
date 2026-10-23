import { test } from "node:test";
import assert from "node:assert/strict";
import { cleanUnits, dishUnits, unitLabel, unitSuggestions } from "../lib/units.ts";

test("a line's unit shows in the page language, and old lines show none", () => {
  const line = { unit: "علبة", unit_display_ar: "علبة", unit_display_en: "box" };
  assert.equal(unitLabel(line, "en"), "box");
  assert.equal(unitLabel(line, "ar"), "علبة");
  assert.equal(unitLabel({}, "en"), null);
});

test("a dish's ways of serving keep their saved value with a label to show", () => {
  const dish = { units: ["صحن", "علبة"], units_display_ar: ["صحن", "علبة"], units_display_en: ["plate", "box"] };
  assert.deepEqual(dishUnits(dish, "en"), [{ value: "صحن", label: "plate" }, { value: "علبة", label: "box" }]);
});

test("the admin's units are cleaned, and suggestions skip what is already chosen", () => {
  assert.deepEqual(cleanUnits([" صحن ", "صحن", "", "علبة  سفري"]), ["صحن", "علبة سفري"]);
  assert.ok(!unitSuggestions("", ["صحن"]).includes("صحن"));
  assert.deepEqual(unitSuggestions("كا", []), ["كاسة"]);
});
