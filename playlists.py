import base64
import io
import json
import random
import re
from pathlib import Path
from urllib import error, parse, request

from debuglog import _dbg
from meal_photos import meal_photo_bytes
from spotify import TOKEN_FILE, spotify_access_token, spotify_api

def pick_tracks_for_duration(tracks, minutes):
    target = minutes * 60 * 1000
    chosen = []
    seen = set()
    total = 0
    for track in tracks:
        uri = track.get("uri")
        duration = track.get("duration_ms") or 0
        if not uri or uri in seen:
            continue
        seen.add(uri)
        chosen.append(uri)
        total += duration
        if total >= target or len(chosen) >= 300:
            break
    return chosen


def tracks_for_cook_time(token, minutes):
    target = minutes * 60 * 1000
    chosen = []
    seen = set()
    total = 0

    def take(tracks):
        nonlocal total
        for track in tracks:
            uri = track.get("uri")
            duration = track.get("duration_ms") or 0
            if not uri or uri in seen:
                continue
            if duration <= 0:
                duration = 180000
            chosen.append({"uri": uri, "duration": duration})
            seen.add(uri)
            total += duration
            if total >= target or len(chosen) >= 300:
                return True
        return False

    top = spotify_api(
        token, "GET", "/me/top/tracks?time_range=long_term&limit=50"
    ).get("items", [])
    random.shuffle(top)
    if take(top):
        return chosen
    artists = spotify_api(
        token, "GET", "/me/top/artists?time_range=long_term&limit=50"
    ).get("items", [])
    random.shuffle(artists)
    extra = []
    extra_seen = set(seen)
    extra_total = 0
    for artist in artists:
        if extra_total >= target - total:
            break
        name = artist.get("name", "")
        if not name:
            continue
        query = parse.quote(f'artist:"{name}"')
        for offset in range(0, 50, 10):
            found = spotify_api(
                token,
                "GET",
                f"/search?q={query}&type=track&limit=10&offset={offset}",
            )
            batch = found.get("tracks", {}).get("items", [])
            if not batch:
                break
            added = 0
            for track in batch:
                uri = track.get("uri") or ""
                if not uri.startswith("spotify:track:") or uri in extra_seen:
                    continue
                duration = track.get("duration_ms") or 180000
                extra.append(track)
                extra_seen.add(uri)
                extra_total += duration
                added += 1
            if extra_total >= target - total or len(batch) < 10 or added == 0:
                break
    random.shuffle(extra)
    take(extra)
    return chosen


_JAPANESE_GENRES = (
    "j-pop",
    "j-rock",
    "city pop",
    "anime",
    "vocaloid",
    "enka",
)
_JAPANESE_ARTISTS = (
    "YOASOBI",
    "Ado",
    "Yorushika",
    "Kenshi Yonezu",
    "Official HIGE DANdism",
    "King Gnu",
    "LiSA",
    "RADWIMPS",
    "Mrs. GREEN APPLE",
    "Vaundy",
    "Fujii Kaze",
    "Aimyon",
    "ZUTOMAYO",
    "Sakanaction",
    "Perfume",
    "ONE OK ROCK",
    "Gen Hoshino",
    "Creepy Nuts",
    "Aimer",
    "Bump of Chicken",
)


def _search_track_uri(token, query, market):
    offsets = [random.choice((0, 10, 20, 30, 40, 50))]
    if offsets[0] != 0:
        offsets.append(0)
    for attempt in offsets:
        params = {"q": query, "type": "track", "limit": 10, "offset": attempt}
        if market:
            params["market"] = market
        found = spotify_api(token, "GET", "/search?" + parse.urlencode(params))
        items = (found.get("tracks") or {}).get("items") or []
        found_tracks = []
        for track in items:
            uri = track.get("uri") or ""
            if not uri.startswith("spotify:track:"):
                continue
            if track.get("is_playable") is False:
                continue
            duration = track.get("duration_ms") or 180000
            found_tracks.append({"uri": uri, "duration": duration})
        if found_tracks:
            return random.choice(found_tracks)
        if attempt == 0:
            break
    return None


def random_japanese_track(token):
    market = ""
    try:
        market = (spotify_api(token, "GET", "/me").get("country") or "").strip()
    except (error.URLError, error.HTTPError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
        market = ""
    artists = list(_JAPANESE_ARTISTS)
    random.shuffle(artists)
    queries = [f'artist:"{artist}"' for artist in artists[:4]]
    genres = list(_JAPANESE_GENRES)
    random.shuffle(genres)
    queries += [f'genre:"{genre}"' for genre in genres[:2]]
    for query in queries:
        try:
            uri = _search_track_uri(token, query, market)
        except (error.URLError, error.HTTPError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
            continue
        if uri:
            return uri
    return None


_PLAYLIST_SLACK_MS = 2 * 60 * 1000


def fit_japanese_track(tracks, japanese, target_ms):
    # The Japanese song counts toward the cook time. Other songs fill
    # what is left, and the total may run over by at most two minutes.
    others = [item for item in tracks if item["uri"] != japanese["uri"]]
    japanese_track = {
        "uri": japanese["uri"],
        "duration": japanese["duration"] or 180000,
    }
    chosen = [japanese_track]
    total = japanese_track["duration"]
    for item in others:
        if total >= target_ms:
            break
        nxt = total + item["duration"]
        overshoot = nxt - target_ms
        shortfall = target_ms - total
        fits = nxt <= target_ms or (
            overshoot <= shortfall and overshoot <= _PLAYLIST_SLACK_MS
        )
        if not fits:
            break
        chosen.append(item)
        total = nxt
    spot = random.randrange(len(chosen))
    chosen.insert(spot, chosen.pop(0))
    return chosen


def _with_cutout(image_bytes, cutout_name):
    from PIL import Image

    meal = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    side = min(meal.size)
    left = (meal.width - side) // 2
    top = (meal.height - side) // 2
    meal = meal.crop((left, top, left + side, top + side)).resize((640, 640))
    person = Image.open(Path(__file__).with_name(cutout_name)).convert("RGBA")
    bounds = person.getbbox()
    if bounds:
        person = person.crop(bounds)
    scale = min((640 * 0.98) / person.width, (640 * 0.80) / person.height)
    person = person.resize(
        (max(1, int(person.width * scale)), max(1, int(person.height * scale))),
        Image.Resampling.LANCZOS,
    )
    meal.alpha_composite(person, ((640 - person.width) // 2, 640 - person.height))
    buf = io.BytesIO()
    meal.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _with_grohl_cutout(image_bytes):
    return _with_cutout(image_bytes, "dave_grohl.png")


def _playlist_cover_jpeg(image_bytes):
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    edge = 640
    data = b""
    while edge >= 160:
        sized = image.resize((edge, edge))
        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            sized.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= 200_000:
                return data
        edge //= 2
    return data


def _attach_meal_cover(token, playlist_id, meal, photo_bytes):
    photo = photo_bytes or meal_photo_bytes(meal)
    # region agent log
    _dbg("B", "notes_ui.py:_attach_meal_cover", "cover attempt", {"meal": meal, "photoBytes": len(photo) if photo else 0, "cached": bool(photo_bytes)})
    # endregion
    if not photo:
        return
    try:
        jpeg = _playlist_cover_jpeg(photo)
    except (OSError, ValueError):
        return
    saved_scope = ""
    if TOKEN_FILE.exists():
        try:
            saved_scope = json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("scope") or ""
        except (OSError, json.JSONDecodeError):
            saved_scope = ""
    if "ugc-image-upload" not in saved_scope:
        try:
            token = spotify_access_token(force_login=True)
        except (RuntimeError, error.URLError, error.HTTPError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
            return
    try:
        body = base64.b64encode(jpeg)
        req = request.Request(
            f"https://api.spotify.com/v1/playlists/{playlist_id}/images",
            data=body,
            method="PUT",
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "image/jpeg",
            },
        )
        with request.urlopen(req, timeout=30) as response:
            # region agent log
            _dbg("B", "notes_ui.py:_attach_meal_cover", "cover uploaded", {"status": response.status, "jpegBytes": len(jpeg), "hadCachedPhoto": bool(photo_bytes)})
            # endregion
            response.read()
    except error.HTTPError as exc:
        # region agent log
        _dbg("B", "notes_ui.py:_attach_meal_cover", "cover upload http error", {"code": exc.code, "jpegBytes": len(jpeg)})
        # endregion
        return
    except (error.URLError, TimeoutError, OSError) as exc:
        # region agent log
        _dbg("B", "notes_ui.py:_attach_meal_cover", "cover upload failed", {"error": type(exc).__name__})
        # endregion
        return


def _foo_song_key(name):
    text = re.sub(
        r"(\s*[-–—]\s*|\s*\().*(?:remaster|live|acoustic|demo|version|edit|mix|anniversary|deluxe).*",
        "",
        name or "",
        flags=re.IGNORECASE,
    )
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _foo_fighters_artist_id(token, market, artist_name="Foo Fighters"):
    params = {"type": "artist", "limit": 10}
    if market:
        params["market"] = market
    path = "/search?q=" + parse.quote(artist_name) + "&" + parse.urlencode(params)
    payload = spotify_api(token, "GET", path)
    names = []
    for artist in (payload.get("artists") or {}).get("items") or []:
        names.append(artist.get("name") or "")
        if artist.get("name") == artist_name and artist.get("id"):
            # region agent log
            _dbg("D", "notes_ui.py:_foo_fighters_artist_id", "artist matched", {"nameCount": len(names)})
            # endregion
            return artist["id"]
    # region agent log
    _dbg("D", "notes_ui.py:_foo_fighters_artist_id", "artist missing", {"names": names[:5]})
    # endregion
    return ""


def _collect_album_ids(token, artist_id, market):
    album_ids = []
    seen = set()
    page_size = 10
    for offset in range(0, 300, page_size):
        # Spotify rejects this call above limit 10, and when the comma is encoded.
        path = (
            f"/artists/{artist_id}/albums"
            f"?include_groups=album,single&limit={page_size}&offset={offset}"
        )
        if market:
            path += "&market=" + parse.quote(market)
        try:
            payload = spotify_api(token, "GET", path)
        except error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:180]
            except OSError:
                detail = ""
            # region agent log
            _dbg("A", "notes_ui.py:_collect_album_ids", "albums http error", {"code": exc.code, "detail": detail, "comma": "," in path, "offset": offset})
            # endregion
            break
        batch = payload.get("items") or []
        if not batch:
            break
        for album in batch:
            album_id = album.get("id")
            if not album_id or album_id in seen:
                continue
            seen.add(album_id)
            album_ids.append(album_id)
        if len(batch) < page_size:
            break
    # region agent log
    _dbg("A", "notes_ui.py:_collect_album_ids", "album ids collected", {"count": len(album_ids)})
    # endregion
    return album_ids


def _tracks_on_album(token, album_id, market):
    items = []
    offset = 0
    while offset < 80:
        path = f"/albums/{album_id}/tracks?limit=10&offset={offset}"
        if market:
            path += "&market=" + parse.quote(market)
        try:
            page = spotify_api(token, "GET", path)
        except error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:180]
            except OSError:
                detail = ""
            # region agent log
            _dbg("B", "playlists.py:_tracks_on_album", "album tracks http error", {"code": exc.code, "detail": detail, "offset": offset})
            # endregion
            break
        batch = page.get("items") or []
        if not batch:
            break
        items.extend(batch)
        offset += len(batch)
        if len(batch) < 10:
            break
    return items


def foo_fighters_tracks(token, artist_name="Foo Fighters"):
    # Albums and singles, one entry per song, then shuffled later.
    # Search would return the most-played tracks first.
    market = ""
    try:
        market = (spotify_api(token, "GET", "/me").get("country") or "").strip()
    except (error.URLError, error.HTTPError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
        market = ""
    try:
        artist_id = _foo_fighters_artist_id(token, market, artist_name)
    except (error.URLError, error.HTTPError, TimeoutError, OSError, KeyError, json.JSONDecodeError):
        artist_id = ""
    if not artist_id:
        # region agent log
        _dbg("D", "notes_ui.py:foo_fighters_tracks", "no artist id", {"market": market})
        # endregion
        return []
    by_song = {}
    stats = {"seen": 0, "kept": 0, "noUri": 0, "badArtist": 0, "unplayable": 0, "nullAlbum": 0, "albumHttp": 0, "sampleArtists": []}
    album_ids = _collect_album_ids(token, artist_id, market)
    for album_id in album_ids:
        for track in _tracks_on_album(token, album_id, market):
            stats["seen"] += 1
            uri = track.get("uri") or ""
            names = [artist.get("name") or "" for artist in track.get("artists") or []]
            key = _foo_song_key(track.get("name") or "")
            if not key or not uri.startswith("spotify:track:"):
                stats["noUri"] += 1
                continue
            if artist_name not in names:
                stats["badArtist"] += 1
                if len(stats["sampleArtists"]) < 4:
                    stats["sampleArtists"].append(names[:3])
                continue
            if track.get("is_playable") is False:
                stats["unplayable"] += 1
                continue
            stats["kept"] += 1
            by_song.setdefault(key, []).append({
                "uri": uri,
                "duration": track.get("duration_ms") or 180000,
            })
    pool = [random.choice(versions) for versions in by_song.values()]
    # region agent log
    _dbg("C", "notes_ui.py:foo_fighters_tracks", "track filter", {**stats, "songs": len(by_song), "pool": len(pool), "albums": len(album_ids)})
    # endregion
    return pool


def tracks_near_duration(pool, target_ms):
    songs = list(pool)
    random.shuffle(songs)
    chosen = []
    total = 0
    for item in songs:
        if total >= target_ms:
            break
        nxt = total + item["duration"]
        overshoot = nxt - target_ms
        shortfall = target_ms - total
        fits = nxt <= target_ms or (
            overshoot <= shortfall and overshoot <= _PLAYLIST_SLACK_MS
        )
        if not fits:
            continue
        chosen.append(item)
        total = nxt
    return chosen


def create_band_playlist(meal, minutes, photo_bytes, artist_name, cutout_name):
    token = spotify_access_token()
    pool = foo_fighters_tracks(token, artist_name)
    if not pool:
        # region agent log
        _dbg("E", "notes_ui.py:create_foo_fighters_playlist", "empty pool", {"minutes": minutes, "artist": artist_name})
        # endregion
        raise RuntimeError(f"could not find {artist_name} songs")
    target_ms = minutes * 60 * 1000
    tracks = tracks_near_duration(pool, target_ms)
    if not tracks:
        # region agent log
        _dbg("E", "notes_ui.py:create_foo_fighters_playlist", "none fit duration", {"pool": len(pool), "minutes": minutes, "artist": artist_name})
        # endregion
        raise RuntimeError(f"could not find {artist_name} songs")
    uris = [item["uri"] for item in tracks]
    playlist = spotify_api(token, "POST", "/me/playlists", {
        "name": f"Cooking {meal} {artist_name}"[:100],
        "public": False,
        "description": f"About {minutes} minutes of {artist_name}",
    })
    playlist_id = playlist["id"]
    for start in range(0, len(uris), 100):
        spotify_api(
            token,
            "POST",
            f"/playlists/{playlist_id}/items",
            {"uris": uris[start:start + 100]},
        )
    photo = photo_bytes or meal_photo_bytes(meal)
    if photo:
        try:
            photo = _with_cutout(photo, cutout_name)
        except (OSError, ValueError):
            pass
    _attach_meal_cover(token, playlist_id, meal, photo)
    return playlist.get("name", meal), playlist_id


def create_foo_fighters_playlist(meal, minutes, photo_bytes=None):
    return create_band_playlist(meal, minutes, photo_bytes, "Foo Fighters", "dave_grohl.png")


def create_oasis_playlist(meal, minutes, photo_bytes=None):
    return create_band_playlist(meal, minutes, photo_bytes, "Oasis", "oasis.png")


def create_cooking_playlist(meal, minutes, photo_bytes=None):
    token = spotify_access_token()
    tracks = tracks_for_cook_time(token, minutes)
    uris = [item["uri"] for item in tracks]
    if not uris:
        raise RuntimeError("could not find tracks")
    japanese = random_japanese_track(token)
    # region agent log
    _dbg("E", "notes_ui.py:create_cooking_playlist", "japanese track", {"meal": meal, "found": bool(japanese), "trackCount": len(uris)})
    # endregion
    if not japanese:
        raise RuntimeError("could not find a Japanese song")
    target_ms = minutes * 60 * 1000
    before_ms = sum(item["duration"] for item in tracks)
    before_count = len(tracks)
    tracks = fit_japanese_track(tracks, japanese, target_ms)
    uris = [item["uri"] for item in tracks]
    after_ms = sum(item["duration"] for item in tracks)
    # region agent log
    _dbg("F", "notes_ui.py:create_cooking_playlist", "playlist duration", {
        "targetMs": target_ms,
        "beforeMs": before_ms,
        "afterMs": after_ms,
        "overshootMs": after_ms - target_ms,
        "japaneseMs": japanese["duration"],
        "containsJapanese": japanese["uri"] in uris,
        "beforeCount": before_count,
        "afterCount": len(uris),
    })
    # endregion
    playlist = spotify_api(token, "POST", "/me/playlists", {
        "name": f"Cooking {meal}"[:100],
        "public": False,
        "description": f"About {minutes} minutes from your most played songs, including a Japanese song",
    })
    playlist_id = playlist["id"]
    for start in range(0, len(uris), 100):
        spotify_api(
            token,
            "POST",
            f"/playlists/{playlist_id}/items",
            {"uris": uris[start:start + 100]},
        )
    _attach_meal_cover(token, playlist_id, meal, photo_bytes)
    return playlist.get("name", meal), playlist_id
