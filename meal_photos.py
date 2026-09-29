import re
from urllib import error, parse, request
from urllib.parse import urlparse

from debuglog import _dbg

def meal_lookup_name(meal):
    return re.sub(
        r"^\d+(?:\.\d+)?\s*(?:hours?|hrs?|h)\b\s*",
        "",
        (meal or "").strip(),
        flags=re.IGNORECASE,
    ).strip()


def _read_url(url):
    req = request.Request(url, headers={"User-Agent": "meal-planner/1.0"})
    with request.urlopen(req, timeout=15) as response:
        return response.read()


def _google_image_urls(query):
    url = "https://www.google.com/search?" + parse.urlencode({
        "q": query,
        "tbm": "isch",
        "hl": "en",
        "gl": "us",
    })
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with request.urlopen(req, timeout=15) as response:
        text = response.read().decode("utf-8", "replace")
    if "unusual traffic" in text or "/sorry/" in text:
        return []
    found = []
    patterns = (
        r"https://encrypted-tbn0\.gstatic\.com/images\?[^\"\\ ]+",
        r"\[" + r"\"(https://[^\"]+\.(?:jpg|jpeg|png|webp))\"" + r",\d+,\d+\]",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text):
            cleaned = match.replace("\\u003d", "=").replace("\\u0026", "&")
            if cleaned not in found:
                found.append(cleaned)
    return found


def _image_search_urls(query):
    # Used when Google answers with a robot check instead of pictures.
    # The query is the meal name, for every meal.
    url = "https://www.bing.com/images/async?" + parse.urlencode({
        "q": query,
        "first": "1",
        "count": "10",
        "mmasync": "1",
    })
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bing.com/",
        },
    )
    with request.urlopen(req, timeout=15) as response:
        text = response.read().decode("utf-8", "replace")
    found = []
    for match in re.findall(r"murl&quot;:&quot;(https:.*?)&quot;", text):
        cleaned = match.replace("&amp;", "&")
        if cleaned not in found:
            found.append(cleaned)
    return found


def _download_image(url):
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        },
    )
    with request.urlopen(req, timeout=15) as response:
        return response.read()


def _is_image(photo):
    if photo.startswith(b"\xff\xd8\xff") or photo.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if photo.startswith(b"GIF87a") or photo.startswith(b"GIF89a"):
        return True
    return photo.startswith(b"RIFF") and photo[8:12] == b"WEBP"


def meal_photo_bytes(meal):
    name = meal_lookup_name(meal)
    if not name:
        return None
    source = "google"
    try:
        urls = _google_image_urls(name)
    except (error.URLError, error.HTTPError, TimeoutError, OSError):
        urls = []
    if not urls:
        source = "web"
        try:
            urls = _image_search_urls(name)
        except (error.URLError, error.HTTPError, TimeoutError, OSError) as exc:
            # region agent log
            _dbg("H", "notes_ui.py:meal_photo_bytes", "image search failed", {"meal": name, "error": type(exc).__name__, "code": getattr(exc, "code", None)})
            # endregion
            return None
    # region agent log
    _dbg("H", "notes_ui.py:meal_photo_bytes", "image search", {"meal": name, "source": source, "results": len(urls)})
    # endregion
    for url in urls[:6]:
        try:
            photo = _download_image(url)
        except (error.URLError, error.HTTPError, TimeoutError, OSError) as exc:
            # region agent log
            _dbg("H", "notes_ui.py:meal_photo_bytes", "image download failed", {"meal": name, "error": type(exc).__name__, "code": getattr(exc, "code", None)})
            # endregion
            continue
        if not _is_image(photo):
            continue
        # region agent log
        _dbg("H", "notes_ui.py:meal_photo_bytes", "image downloaded", {"meal": name, "bytes": len(photo), "magic": photo[:8].hex(), "host": urlparse(url).netloc})
        # endregion
        return photo
    # region agent log
    _dbg("H", "notes_ui.py:meal_photo_bytes", "no photo", {"meal": name})
    # endregion
    return None
