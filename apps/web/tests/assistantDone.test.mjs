import { test } from "node:test";
import assert from "node:assert/strict";
import { doneBadges } from "../lib/assistantDone.ts";

test("each real action of a reply gets its badge, in the page language", () => {
  const done = [
    { kind: "basket", changes: [] },
    { kind: "service", type: "STAFF", already_open: false },
    { kind: "complaint", added_to_earlier: false },
    { kind: "rating", stars: 4 },
  ];
  assert.deepEqual(doneBadges(done, "ar"), ["✓ انطلب موظف", "✓ انسجلت الشكوى", "✓ انحفظ التقييم 4 من 5"]);
  assert.deepEqual(doneBadges(done, "en"), ["✓ Staff member called", "✓ Complaint recorded", "✓ Rating saved: 4 of 5"]);
});

test("a request already on its way and a complaint added to the earlier one say so", () => {
  assert.deepEqual(doneBadges([{ kind: "service", type: "BILL", already_open: true }], "ar"), ["✓ انطلب الحساب (من قبل)"]);
  assert.deepEqual(doneBadges([{ kind: "complaint", added_to_earlier: true }], "ar"), ["✓ انضافت لشكواك"]);
  assert.deepEqual(doneBadges([{ kind: "complaint", added_to_earlier: true, nothing_new: true }], "en"),
    ["✓ Your complaint is on record"]);
});

test("a cancelled service request gets its own badge", () => {
  assert.deepEqual(doneBadges([{ kind: "service_cancelled", type: "STAFF" }], "ar"), ["✓ انلغى طلب الموظف"]);
  assert.deepEqual(doneBadges([{ kind: "service_cancelled", type: "BILL" }], "en"), ["✓ Bill request cancelled"]);
  assert.deepEqual(doneBadges([{ kind: "service", type: "TISSUES", already_open: false },
    { kind: "service_cancelled", type: "TISSUES" }], "ar"), ["✓ انطلبت مناديل", "✓ انلغى طلب المناديل"]);
});

test("an order sent or changed shows a badge only when the reply has no card for it", () => {
  const done = [{ kind: "order_sent", order_number: "AJ-116" }, { kind: "order_changed", order_number: "AJ-117", cancelled: false }];
  assert.deepEqual(doneBadges(done, "ar"), ["✓ انبعت الطلب AJ-116", "✓ تعدّل الطلب AJ-117"]);
  assert.deepEqual(doneBadges(done, "ar", { changed: "AJ-117" }), ["✓ انبعت الطلب AJ-116"]);
  assert.deepEqual(doneBadges([{ kind: "order_changed", order_number: "AJ-117", cancelled: true }], "en"),
    ["✓ Order AJ-117 cancelled"]);
  assert.deepEqual(doneBadges(undefined, "ar"), []);
});
