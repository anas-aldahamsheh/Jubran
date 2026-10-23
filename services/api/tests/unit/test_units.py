"""How a dish is counted: automatic units, units written any way, and their labels on screen."""
from jubran.domain.units import automatic_unit, clean_units, resolve_unit, saved_units, unit_label, units_to_json


def test_a_dish_without_units_gets_one_from_its_name_its_kind_or_its_section():
    assert automatic_unit("فلافل الحبة الواحدة", "Falafel (1 piece)") == "حبة"
    assert automatic_unit("مياه غازية", "Sparkling Water", "المشروبات", "Drinks") == "قنينة"
    assert automatic_unit("مشروبات غازية", "Soft Drinks", "المشروبات", "Drinks") == "علبة"
    assert automatic_unit("عصير جبران", "Jubran Juice", "المشروبات", "Drinks") == "كاسة"
    assert automatic_unit("ليمون بالنعنع", "Mint Lemonade") == "كاسة"
    assert automatic_unit("قهوة عربية", "Arabic Coffee") == "فنجان"
    assert automatic_unit("ساندويش فول", "Foul Sandwich", "السندويشات", "Sandwiches") == "ساندويشة"
    assert automatic_unit("متبل", "Mutabbal", "مقبلات", "Starters") == "صحن"


def test_a_unit_is_understood_however_it_is_written():
    units = ["صحن", "علبة"]
    for written, unit in (("صحون", "صحن"), ("Plates", "صحن"), ("العلبة", "علبة"), ("box", "علبة"), ("cans", "علبة")):
        assert resolve_unit(written, units) == unit, written
    assert resolve_unit("كاسة", units) is None
    assert resolve_unit("piece", ["قطعة"]) == "قطعة"  # the same unit in English


def test_screens_show_a_unit_in_their_language():
    assert (unit_label("علبة", "en"), unit_label("علبة", "en", drink=True)) == ("box", "can")
    assert (unit_label("plate", "ar"), unit_label("صحن", "ar")) == ("صحن", "صحن")
    assert unit_label("طاسة", "en") == "طاسة"  # not a common unit: as the admin wrote it


def test_the_admins_units_are_saved_clean():
    assert clean_units([" صحن ", "plate", "", "-", "علبة", "علبة"]) == ["صحن", "علبة"]
    assert units_to_json([" "]) is None and saved_units(units_to_json(["صحن"])) == ["صحن"]
    assert saved_units("not json") == []
