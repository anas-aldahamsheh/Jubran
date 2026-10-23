"""A dish's serving units, read from the database (the rules are in ``domain/units``)."""
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jubran.domain.units import is_drink, saved_units, serving_units, unit_label, unit_labels
from jubran.infrastructure.db.models import MenuCategoryModel, ProductModel

CategoryNames = Dict[str, Tuple[str, str]]


async def category_names(db: AsyncSession) -> CategoryNames:
    """Every menu section's Arabic and English name (a dish's automatic unit may depend on it)."""
    rows = (await db.execute(select(MenuCategoryModel.id, MenuCategoryModel.name_ar,
                                    MenuCategoryModel.name_en))).all()
    return {row.id: (row.name_ar or "", row.name_en or "") for row in rows}


def dish_units(product: ProductModel, categories: CategoryNames) -> List[str]:
    """The ways a dish can be ordered: the admin's units, else its automatic unit."""
    category_ar, category_en = categories.get(product.category_id, ("", ""))
    return serving_units(product.units_json, product.name_ar, product.name_en, category_ar, category_en)


def dish_is_drink(product: ProductModel, categories: CategoryNames) -> bool:
    return is_drink(product.name_ar, product.name_en, *categories.get(product.category_id, ("", "")))


def dish_units_fields(product: ProductModel, categories: CategoryNames) -> Dict[str, Any]:
    """What screens show about a dish's units (the admin form also needs whether they're automatic)."""
    units = dish_units(product, categories)
    drink = dish_is_drink(product, categories)
    return {"units": units, "units_automatic": not saved_units(product.units_json),
            "units_display_ar": [unit_label(unit, "ar", drink) for unit in units],
            "units_display_en": [unit_label(unit, "en", drink) for unit in units]}


def line_unit_fields(unit: Optional[str], name_ar: str, name_en: Optional[str]) -> Dict[str, Any]:
    """A basket or order line's unit with its Arabic and English labels (None on old lines)."""
    return unit_labels(unit, is_drink(name_ar, name_en))
