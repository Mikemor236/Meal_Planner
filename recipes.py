import json
import re
import time
from urllib import error, request

from debuglog import _dbg, _session_dbg

MAX_LEN = 128
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "llama3.2"
MEAL_PROMPT = (
    "Write two home recipes for the exact dish named on the Dish line. "
    "Every ingredient and every step must belong to that dish. "
    "Do not substitute a different dish, and do not copy an example food "
    "that is not part of the named dish. "
    "Meals include foods people eat, such as steak, fish, pasta, pizza, "
    "salad, soup, and cake. Everyday food names are meals even if brief. "
    "Only reply NOT_A_MEAL when the text is clearly not food "
    "(for example a bicycle, a car, a book, or a person). "
    "If it is not a meal, reply with exactly NOT_A_MEAL and nothing else. "
    "If it is a meal, reply with two ways to cook that same dish and nothing else. "
    "The tastier way is richer. "
    "The healthier way is the same dish with less fat, salt, and sugar, "
    "and more vegetables or leaner ingredients where that still fits the dish. "
    "Use exactly this shape:\n"
    "TASTIER\n"
    "INGREDIENTS\n"
    "one ingredient per line for 2 servings. "
    "Use at most 8 ingredients. "
    "Every line must start with a number and an amount, "
    "such as 300 g beef, 1 onion, or 1 tbsp oil. "
    "Do not write a line without a number.\n"
    "TIME\n"
    "one line with only the home-cooking minutes as a number.\n"
    "METHOD\n"
    "four short cooking steps, one per line\n"
    "HEALTHIER\n"
    "INGREDIENTS\n"
    "one ingredient per line for 2 servings. "
    "Use at most 8 ingredients. "
    "Every line must start with a number and an amount.\n"
    "TIME\n"
    "one line with only the home-cooking minutes as a number.\n"
    "METHOD\n"
    "four short cooking steps, one per line\n"
    "No numbering and no extra text.\n"
    "Dish: "
)
def trim_spaces(text):
    return text.strip(" \t\r")
def strip_list_marker(text):
    if (text.startswith("- ") or text.startswith("* ")):
        return text[2:]
    index = 0
    while index < len(text) and text[index].isdigit():
        index += 1
    if index > 0 and text[index:index + 2] == ". ":
        return text[index + 2:]
    return text


def is_not_a_meal(content):
    # Only reject when the entire reply is NOT_A_MEAL (ignore whitespace).
    # Do not treat a first-line token inside a normal recipe as rejection.
    return "".join(content.split()) == "NOT_A_MEAL"


def owned_items(text):
    items = []
    for chunk in text.replace("\n", ",").split(","):
        item = trim_spaces(chunk).lower()
        # Ignore empty / very short tokens so matching stays predictable.
        if len(item) >= 3:
            items.append(item)
    return items


def already_have(ingredient, owned):
    lowered = ingredient.lower()
    for item in owned:
        # Whole-word / phrase match: "pasta" skips "200 g pasta",
        # but short substrings inside unrelated words do not.
        pattern = r"(?<!\w)" + re.escape(item) + r"(?!\w)"
        if re.search(pattern, lowered):
            return True
    return False


def parse_meal_reply(content):
    if is_not_a_meal(content):
        return None
    section = None
    ingredients = []
    method = []
    cook_minutes = None
    for raw in content.split("\n"):
        text = trim_spaces(raw)
        heading = text.strip("*#> ").upper().rstrip(":")
        inline_time = re.fullmatch(
            r"TIME\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?(?:\s*(?:HOURS?|HRS?|MINUTES?|MINS?))?)",
            heading,
        )
        if inline_time:
            section = "time"
            if cook_minutes is None:
                cook_minutes = parse_minutes(inline_time.group(1))
            continue
        if heading in ("INGREDIENTS", "METHOD", "TIME"):
            section = heading.lower()
            continue
        if section == "ingredients":
            item = trim_spaces(strip_list_marker(text))
            if item:
                ingredients.append(item[:MAX_LEN])
        elif section == "method":
            step = trim_spaces(strip_list_marker(text))
            if step:
                method.append(step)
        elif section == "time":
            bare = re.fullmatch(r"\d{1,3}", text)
            if cook_minutes is None and (bare or (
                parse_minutes(text) is not None
                and len(text) <= 20
                and strip_list_marker(text) == text
            )):
                cook_minutes = parse_minutes(text)
                continue
            step = trim_spaces(strip_list_marker(text))
            if step:
                section = "method"
                method.append(step)
    return ingredients, "\n".join(method), cook_minutes

# The model writes one home recipe. Treat that text as 2 servings and
# scale later changes from those original amounts, not from already scaled text.
_BASE_SERVINGS = 2
_QTY_TOKEN = (
    r"(?:\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+\s*[½¼¾⅓⅔⅛]"
    r"|\d+[.,]\d+|\d+|[½¼¾⅓⅔⅛])"
)
_UNIT_PATTERN = (
    r"tablespoons?|teaspoons?|tbsps?|tsps?|ounces?|pounds?|cloves?|slices?|"
    r"bunches?|pinches?|sprigs?|stalks?|pieces?|heads?|grams?|litres?|liters?|"
    r"cups?|cans?|kgs?|lbs?|oz|ml|kg|lb|cl|dl|g|l"
)
_LEADING_QTY = re.compile(
    rf"""(?ix)
    ^(?P<space>\s*)
    (?P<qty>{_QTY_TOKEN})
    (?:\s*(?:-|–|—|\bto\b)\s*(?P<qty2>{_QTY_TOKEN}))?
    (?P<gap>\s*)
    (?:(?P<unit>{_UNIT_PATTERN})(?![A-Za-z]))?
    (?P<rest>.*)$
    """
)
_MEASURE = re.compile(
    rf"(?i)(?<![A-Za-z0-9])({_QTY_TOKEN})(\s*)({_UNIT_PATTERN})(?![A-Za-z])"
)
_UNICODE_QTY = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125}
_FRACTION_LABELS = (
    (1 / 8, "1/8"),
    (1 / 4, "1/4"),
    (1 / 3, "1/3"),
    (3 / 8, "3/8"),
    (1 / 2, "1/2"),
    (2 / 3, "2/3"),
    (3 / 4, "3/4"),
    (7 / 8, "7/8"),
)
_UNIT_PAIRS = (
    ("tablespoon", "tablespoons"),
    ("teaspoon", "teaspoons"),
    ("tbsp", "tbsp"),
    ("tsp", "tsp"),
    ("cup", "cups"),
    ("ounce", "ounces"),
    ("pound", "pounds"),
    ("clove", "cloves"),
    ("slice", "slices"),
    ("can", "cans"),
    ("bunch", "bunches"),
    ("pinch", "pinches"),
    ("sprig", "sprigs"),
    ("head", "heads"),
    ("stalk", "stalks"),
    ("piece", "pieces"),
    ("gram", "grams"),
    ("litre", "litres"),
    ("liter", "liters"),
    ("kg", "kg"),
    ("lb", "lbs"),
    ("oz", "oz"),
    ("ml", "ml"),
    ("cl", "cl"),
    ("dl", "dl"),
    ("g", "g"),
    ("l", "l"),
)
_UNIT_TO_SINGULAR = {}
_UNIT_TO_PLURAL = {}
for _singular, _plural in _UNIT_PAIRS:
    _UNIT_TO_SINGULAR[_singular] = _singular
    _UNIT_TO_SINGULAR[_plural] = _singular
    _UNIT_TO_PLURAL[_singular] = _plural
_MASS_NOUNS = {
    "salt", "pepper", "water", "milk", "flour", "sugar", "rice", "pasta",
    "oil", "butter", "cheese", "meat", "garlic", "bread", "stock", "broth",
    "cream", "yogurt", "honey", "vinegar", "sauce", "basil", "parsley",
}
_IRREGULAR_PLURAL = {
    "potato": "potatoes",
    "tomato": "tomatoes",
    "leaf": "leaves",
    "loaf": "loaves",
}
_IRREGULAR_SINGULAR = {plural: singular for singular, plural in _IRREGULAR_PLURAL.items()}


def _parse_qty(text):
    text = text.strip()
    if text in _UNICODE_QTY:
        return _UNICODE_QTY[text]
    mixed = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", text)
    if mixed:
        denominator = int(mixed.group(3))
        if denominator == 0:
            return None
        return int(mixed.group(1)) + int(mixed.group(2)) / denominator
    unicode_mixed = re.fullmatch(r"(\d+)\s*([½¼¾⅓⅔⅛])", text)
    if unicode_mixed:
        return int(unicode_mixed.group(1)) + _UNICODE_QTY[unicode_mixed.group(2)]
    fraction = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if fraction:
        denominator = int(fraction.group(2))
        if denominator == 0:
            return None
        return int(fraction.group(1)) / denominator
    number = re.fullmatch(r"(\d+)[.,](\d+)", text)
    if number:
        return float(number.group(1) + "." + number.group(2))
    if re.fullmatch(r"\d+", text):
        return float(text)
    return None


def _format_qty(value):
    if value <= 0:
        return "0"
    nearest = round(value)
    if abs(value - nearest) < 0.05:
        return str(int(nearest))
    whole = int(value)
    remainder = value - whole
    for number, label in _FRACTION_LABELS:
        if abs(remainder - number) < 0.04:
            if whole:
                return f"{whole} {label}"
            return label
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 0.05:
        return str(int(round(rounded)))
    return f"{rounded:.1f}"


def _singular_amount(qty):
    return qty < 1.05


def _match_case(source, chosen):
    if source[:1].isupper():
        return chosen[:1].upper() + chosen[1:]
    return chosen


def _agree_unit(unit, qty):
    singular = _UNIT_TO_SINGULAR.get(unit.lower())
    if singular is None:
        return unit
    chosen = singular if _singular_amount(qty) else _UNIT_TO_PLURAL[singular]
    return _match_case(unit, chosen)


def _pluralize_word(word):
    lower = word.lower()
    if lower in _IRREGULAR_PLURAL:
        chosen = _IRREGULAR_PLURAL[lower]
    elif re.search(r"[^aeiou]y$", lower):
        chosen = word[:-1] + "ies"
    elif re.search(r"(?:s|x|z|ch|sh)$", lower):
        chosen = word + "es"
    else:
        chosen = word + "s"
    return _match_case(word, chosen)


def _singularize_word(word):
    lower = word.lower()
    if lower in _IRREGULAR_SINGULAR:
        return _match_case(word, _IRREGULAR_SINGULAR[lower])
    if lower.endswith("ies") and len(lower) > 4:
        return word[:-3] + "y"
    if re.search(r"(?:ches|shes|xes|zes|sses)$", lower) and len(lower) > 4:
        return word[:-2]
    if lower.endswith("s") and not lower.endswith("ss") and len(lower) > 3:
        return word[:-1]
    return word


def _agree_food(word, qty):
    lower = word.lower()
    if lower in _MASS_NOUNS:
        return word
    if lower in _UNIT_TO_SINGULAR:
        return _agree_unit(word, qty)
    base = _singularize_word(word)
    chosen = base if _singular_amount(qty) else _pluralize_word(base)
    return chosen


def _agree_only_word(rest, qty):
    match = re.fullmatch(r"(\s*)([A-Za-z]+)([^A-Za-z]*)", rest)
    if not match:
        return rest
    return match.group(1) + _agree_food(match.group(2), qty) + match.group(3)


def _scale_leading(text, factor):
    match = _LEADING_QTY.match(text)
    if match is None:
        return None
    first = _parse_qty(match.group("qty"))
    if first is None:
        return None
    second_raw = match.group("qty2")
    second = _parse_qty(second_raw) if second_raw else None
    if second_raw and second is None:
        return None
    scaled_first = first * factor
    scaled_second = second * factor if second is not None else None
    agree_qty = scaled_second if scaled_second is not None else scaled_first
    unit = match.group("unit") or ""
    rest = match.group("rest") or ""
    if unit:
        unit = _agree_unit(unit, agree_qty)
    else:
        rest = _agree_only_word(rest, agree_qty)
    qty_text = _format_qty(scaled_first)
    if scaled_second is not None:
        middle = text[match.end("qty"):match.start("qty2")]
        separator = " to " if re.search(r"to", middle, re.IGNORECASE) else "-"
        qty_text = f"{qty_text}{separator}{_format_qty(scaled_second)}"
    return f"{match.group('space')}{qty_text}{match.group('gap')}{unit}{rest}"


def _scale_measures(text, factor):
    def replace(match):
        qty = _parse_qty(match.group(1))
        if qty is None:
            return match.group(0)
        scaled = qty * factor
        return _format_qty(scaled) + match.group(2) + _agree_unit(match.group(3), scaled)

    return _MEASURE.sub(replace, text)


def scale_ingredient(text, factor):
    if factor == 1 or not text:
        return text
    leading = _scale_leading(text, factor)
    if leading is not None:
        return leading[:MAX_LEN]
    return _scale_measures(text, factor)[:MAX_LEN]


def scale_method(text, factor):
    if factor == 1 or not text:
        return text
    return _scale_measures(text, factor)


def line_has_amount(text):
    match = _LEADING_QTY.match(text or "")
    if match is not None and _parse_qty(match.group("qty")) is not None:
        return True
    return _MEASURE.search(text or "") is not None


def ingredients_need_amounts(ingredients):
    if not ingredients:
        return False
    with_amount = sum(1 for item in ingredients if line_has_amount(item))
    return with_amount * 2 < len(ingredients)


def _ingredient_lines(content):
    lines = []
    for raw in content.split("\n"):
        text = trim_spaces(strip_list_marker(raw))
        if not text:
            continue
        heading = text.upper().rstrip(":")
        if heading in ("INGREDIENTS", "METHOD", "TIME"):
            continue
        lines.append(text[:MAX_LEN])
    measured = [line for line in lines if line_has_amount(line)]
    if measured and not ingredients_need_amounts(measured):
        return measured
    return lines


def parse_minutes(text):
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\b", text, re.IGNORECASE)
    if hours is not None:
        value = int(round(float(hours.group(1)) * 60))
    else:
        match = re.search(r"\d+", text)
        if match is None:
            return None
        value = int(match.group())
    if value <= 0 or value > 24 * 60:
        return None
    return value


def minutes_from_meal_text(meal):
    total = 0
    found = False
    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", meal, re.IGNORECASE
    ):
        total += int(round(float(match.group(1)) * 60))
        found = True
    for match in re.finditer(
        r"(\d+)\s*(?:minutes?|mins?)\b", meal, re.IGNORECASE
    ):
        total += int(match.group(1))
        found = True
    if not found or total <= 0 or total > 24 * 60:
        return None
    return total


def ollama_chat(prompt):
    body = json.dumps({
        "model": MODEL,
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 650},
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    with request.urlopen(req, timeout=70) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = (payload.get("message") or {}).get("content") or ""
    # region agent log
    _dbg("M", "notes_ui.py:ollama_chat", "ollama reply", {"ms": int((time.time() - started) * 1000), "eval": payload.get("eval_count"), "done": payload.get("done_reason"), "chars": len(content)})
    # endregion
    # region agent log
    upper = content.upper()
    _session_dbg("A", "recipes.py:ollama_chat", "reply shape", {
        "done": payload.get("done_reason"),
        "eval": payload.get("eval_count"),
        "chars": len(content),
        "hasTastier": "TASTIER" in upper,
        "hasHealthier": "HEALTHIER" in upper,
        "hasIngredients": "INGREDIENTS" in upper,
        "hasTime": "\nTIME" in upper or upper.startswith("TIME"),
        "hasMethod": "METHOD" in upper,
        "tail": content[-80:],
    })
    # endregion
    return content


def fetch_ingredients(meal):
    return ollama_chat(
        MEAL_PROMPT + meal + f"\nThe only dish to cook is {meal}."
    )


def measure_ingredients(meal, ingredients):
    listing = "\n".join(ingredients)
    prompt = (
        "Write ingredient amounts for 2 servings of this meal. "
        "Every line must start with a number, then a unit or a count, then the food. "
        "Use amounts like 200 g, 1 tbsp, 2, or 1/2 tsp. "
        "Keep the same foods. No headings and no extra text.\n"
        f"Meal: {meal}\n"
        f"{listing}"
    )
    return _ingredient_lines(ollama_chat(prompt))

_VERSION_ORDER = ("tastier", "healthier")
_VERSION_LABELS = {"tastier": "Tastier", "healthier": "Healthier"}
def _version_record(ingredients, method, minutes):
    return {
        "ingredients": ingredients,
        "method": method,
        "minutes": minutes,
        "nutrition": None,
        "nutrition_note": "estimating...",
    }


def _version_heading(text):
    cleaned = trim_spaces(text).strip("*#> ").upper().rstrip(":")
    if cleaned in ("TASTIER", "HEALTHIER"):
        return cleaned.lower()
    for name in ("TASTIER", "HEALTHIER"):
        if cleaned.startswith(name + " ") and len(cleaned) < 40:
            return name.lower()
    return ""


def _split_ingredient_line(line):
    parts = [trim_spaces(part) for part in line.split(",")]
    numbered = [part for part in parts if part and _LEADING_QTY.match(part)]
    if len(numbered) >= 2:
        return [part[:MAX_LEN] for part in numbered]
    item = trim_spaces(line)
    if item:
        return [item[:MAX_LEN]]
    return []


def _is_cook_time_line(line):
    # "30" and "30 minutes" are cook times. "2 cloves garlic" is not.
    return re.fullmatch(
        r"(?:time\s*[:\-]?\s*)?(?:about|around|approx(?:imately)?|roughly)?\s*"
        r"(?:\d{1,3}|\d{1,3}(?:\.\d+)?\s*(?:minutes?|mins?|hours?|hrs?))\.?",
        line,
        flags=re.IGNORECASE,
    ) is not None


def _loose_recipe(text):
    # The model often skips the INGREDIENTS / TIME / METHOD labels and writes
    # one comma-separated ingredient line, a minute line, then the steps.
    lines = []
    for raw in (text or "").split("\n"):
        line = trim_spaces(strip_list_marker(trim_spaces(raw)))
        if line and not _version_heading(line):
            lines.append(line)
    time_index = None
    for index, line in enumerate(lines):
        if index == 0:
            continue
        if _is_cook_time_line(line):
            time_index = index
            break
    if time_index is None:
        # A reply can be only measured foods, with the cook-time line cut off.
        ingredients = []
        for line in lines:
            for part in _split_ingredient_line(line):
                match = _LEADING_QTY.match(part)
                rest = ""
                if match is not None:
                    rest = (match.group("unit") or "") + (match.group("rest") or "")
                if match is not None and re.search(r"[A-Za-z]{3,}", rest):
                    ingredients.append(part)
        if len(ingredients) >= 2:
            return ingredients, "", None
        return None
    ingredients = []
    for line in lines[:time_index]:
        ingredients.extend(_split_ingredient_line(line))
    method = lines[time_index + 1:]
    minutes = parse_minutes(lines[time_index])
    if not ingredients and not method:
        return None
    return ingredients, "\n".join(method), minutes


def _ingredients_before_sections(text):
    # A reply can list foods on the line before TIME, with no INGREDIENTS heading.
    lines = []
    for raw in (text or "").split("\n"):
        line = trim_spaces(strip_list_marker(trim_spaces(raw)))
        if not line or _version_heading(line):
            continue
        heading = line.strip("*#> ").upper().rstrip(":")
        if heading in ("INGREDIENTS", "METHOD", "TIME"):
            break
        lines.append(line)
    ingredients = []
    for line in lines:
        ingredients.extend(_split_ingredient_line(line))
    return [item for item in ingredients if line_has_amount(item)]


def _recipe_from_chunk(text):
    parsed = parse_meal_reply(text)
    loose = None
    chosen = None
    if parsed is not None:
        ingredients, method, minutes = parsed
        # A labeled TIME or METHOD means this is the structured recipe.
        # A bare minute line, with no labels, is handled underneath.
        if ingredients or method or minutes is not None:
            if not ingredients:
                ingredients = _ingredients_before_sections(text)
            if ingredients or method:
                chosen = "labeled"
                result = ingredients, method, minutes
            else:
                result = None
        else:
            result = None
    else:
        result = None
    if result is None:
        loose = _loose_recipe(text)
        result = loose
        if loose is not None:
            chosen = "loose"
    # region agent log
    lines = [trim_spaces(line) for line in (text or "").split("\n") if trim_spaces(line)]
    _session_dbg("B", "recipes.py:_recipe_from_chunk", "chunk parse", {
        "lines": len(lines),
        "first": lines[:2],
        "last": lines[-2:],
        "labeledEmpty": parsed == ([], "", None) or parsed == ([], "", None),
        "parsedMinutes": None if not parsed else parsed[2],
        "parsedIngredients": 0 if not parsed else len(parsed[0]),
        "parsedMethod": 0 if not parsed else len(parsed[1]),
        "loose": loose is not None,
        "chosen": chosen,
    })
    # endregion
    return result


def parse_two_meals(content):
    if is_not_a_meal(content):
        return None
    chunks = {"tastier": [], "healthier": []}
    current = None
    saw_heading = False
    for raw in (content or "").split("\n"):
        heading = _version_heading(raw)
        if heading:
            current = heading
            saw_heading = True
            continue
        if current is not None:
            chunks[current].append(raw)
    if not saw_heading:
        parsed = _recipe_from_chunk(content)
        if not parsed:
            # region agent log
            _session_dbg("C", "recipes.py:parse_two_meals", "no heading, empty", {"chars": len(content or "")})
            # endregion
            return {}
        ingredients, method, minutes = parsed
        if not ingredients and not method:
            # region agent log
            _session_dbg("C", "recipes.py:parse_two_meals", "no heading, blank recipe", {})
            # endregion
            return {}
        return {"tastier": _version_record(ingredients, method, minutes)}
    result = {}
    dropped = []
    for key in _VERSION_ORDER:
        chunk = "\n".join(chunks[key])
        parsed = _recipe_from_chunk(chunk)
        if not parsed:
            dropped.append(key)
            continue
        ingredients, method, minutes = parsed
        if ingredients or method:
            result[key] = _version_record(ingredients, method, minutes)
        else:
            dropped.append(key + ":empty")
    # region agent log
    _session_dbg("C", "recipes.py:parse_two_meals", "split versions", {
        "keys": list(result),
        "dropped": dropped,
        "tastierLines": len(chunks["tastier"]),
        "healthierLines": len(chunks["healthier"]),
    })
    # endregion
    return result
