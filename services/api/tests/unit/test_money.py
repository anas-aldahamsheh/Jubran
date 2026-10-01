"""Domain money handling unit tests."""
from decimal import Decimal
from jubran.domain.money import jod_to_fils, fils_to_jod, format_jod


def test_jod_to_fils_conversion():
    assert jod_to_fils("3.45") == 3450
    assert jod_to_fils(3.45) == 3450
    assert jod_to_fils("5.50") == 5500
    assert jod_to_fils("0.15") == 150
    assert jod_to_fils("0.45") == 450
    assert jod_to_fils("1.15") == 1150
    assert jod_to_fils("1.25") == 1250
    assert jod_to_fils("1.50") == 1500
    assert jod_to_fils(Decimal("1.95")) == 1950


def test_fils_to_jod_conversion():
    assert fils_to_jod(3450) == Decimal("3.450")
    assert fils_to_jod(150) == Decimal("0.150")
    assert fils_to_jod(5500) == Decimal("5.500")


def test_format_jod():
    assert format_jod(3450, locale="ar") == "3.45 د.أ"
    assert format_jod(3450, locale="en") == "3.45 JOD"
    assert format_jod(150, locale="ar") == "0.15 د.أ"
    assert format_jod(5500, locale="ar") == "5.5 د.أ"
