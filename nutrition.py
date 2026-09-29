import json
import re
import time
from urllib import error, parse

from debuglog import _dbg
from meal_photos import _read_url
from recipes import _BASE_SERVINGS, _LEADING_QTY, _parse_qty, trim_spaces

_GRAMS_PER_UNIT = {
    "g": 1,
    "gram": 1,
    "grams": 1,
    "kg": 1000,
    "kgs": 1000,
    "ml": 1,
    "cl": 10,
    "dl": 100,
    "l": 1000,
    "litre": 1000,
    "litres": 1000,
    "liter": 1000,
    "liters": 1000,
    "tsp": 5,
    "tsps": 5,
    "teaspoon": 5,
    "teaspoons": 5,
    "tbsp": 15,
    "tbsps": 15,
    "tablespoon": 15,
    "tablespoons": 15,
    "cup": 240,
    "cups": 240,
    "oz": 28.35,
    "ounce": 28.35,
    "ounces": 28.35,
    "lb": 453.6,
    "lbs": 453.6,
    "pound": 453.6,
    "pounds": 453.6,
}
_NUTRITION_CACHE = {}
def ingredient_grams(text):
    match = _LEADING_QTY.match(text or "")
    if match is None:
        return None
    qty = _parse_qty(match.group("qty"))
    if qty is None:
        return None
    per = _GRAMS_PER_UNIT.get((match.group("unit") or "").lower())
    if per is None:
        return None
    second_raw = match.group("qty2")
    second = _parse_qty(second_raw) if second_raw else None
    amount = qty if second is None else (qty + second) / 2
    return amount * per


def ingredient_food_name(text):
    match = _LEADING_QTY.match(text or "")
    if match is None:
        return ""
    rest = re.sub(r"\([^)]*\)", " ", match.group("rest") or "")
    rest = re.sub(r"^[\s,]*of\s+", "", rest, flags=re.IGNORECASE)
    return trim_spaces(rest).strip(" ,.")


def _nutrient(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def off_per_100g(name):
    key = " ".join(name.lower().split())
    if not key:
        return None
    if key in _NUTRITION_CACHE:
        return _NUTRITION_CACHE[key]
    url = "https://world.openfoodfacts.org/cgi/search.pl?" + parse.urlencode({
        "search_terms": name,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 5,
        "fields": "product_name,nutriments",
    })
    payload = None
    for attempt in range(3):
        try:
            payload = json.loads(_read_url(url).decode("utf-8"))
            break
        except error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == 2:
                raise
            time.sleep(1)
    found = None
    for product in payload.get("products") or []:
        if not isinstance(product, dict):
            continue
        nutriments = product.get("nutriments") or {}
        kcal = nutriments.get("energy-kcal_100g")
        try:
            kcal = float(kcal)
        except (TypeError, ValueError):
            continue
        found = {
            "kcal": kcal,
            "protein": _nutrient(nutriments.get("proteins_100g")),
            "carbs": _nutrient(nutriments.get("carbohydrates_100g")),
            "fat": _nutrient(nutriments.get("fat_100g")),
        }
        break
    _NUTRITION_CACHE[key] = found
    return found


def nutrition_per_serving(ingredients):
    totals = {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    used = 0
    attempted = 0
    failed = 0
    for line in ingredients:
        grams = ingredient_grams(line)
        name = ingredient_food_name(line)
        if grams is None or not name:
            continue
        attempted += 1
        try:
            per = off_per_100g(name)
        except (error.URLError, error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
            failed += 1
            continue
        if not per:
            continue
        used += 1
        factor = grams / 100.0
        for key in totals:
            totals[key] += per[key] * factor
    if used == 0 and failed and failed == attempted:
        # region agent log
        _dbg("D", "notes_ui.py:nutrition_per_serving", "all lookups failed", {"attempted": attempted, "failed": failed, "lines": list(ingredients)})
        # endregion
        raise error.URLError("could not reach Open Food Facts")
    if used == 0:
        # region agent log
        _dbg("D", "notes_ui.py:nutrition_per_serving", "nothing counted", {"attempted": attempted, "failed": failed, "lines": list(ingredients)})
        # endregion
        return None
    result = {key: totals[key] / _BASE_SERVINGS for key in totals}
    # region agent log
    _dbg("D", "notes_ui.py:nutrition_per_serving", "nutrition ok", {"attempted": attempted, "used": used, "failed": failed, "kcal": round(result["kcal"])})
    # endregion
    return result


def format_nutrition(totals):
    return "\n".join((
        f"{round(totals['kcal'])} kcal",
        f"{totals['protein']:.1f} g protein",
        f"{totals['carbs']:.1f} g carbs",
        f"{totals['fat']:.1f} g fat",
    ))
