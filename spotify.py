import base64
import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib import error, parse, request
from urllib.parse import parse_qs, urlparse

from debuglog import _agent_log

TOKEN_FILE = Path(__file__).with_name("spotify_token.json")
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPES = (
    "user-top-read playlist-modify-private ugc-image-upload "
    "user-read-currently-playing user-modify-playback-state "
    "user-read-playback-state"
)


def spotify_credentials():
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("missing Spotify credentials")
    return client_id, client_secret


def _spotify_form(url, fields, headers):
    data = parse.urlencode(fields).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _save_token(payload):
    current = {}
    if TOKEN_FILE.exists():
        current = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    if "refresh_token" in payload:
        current["refresh_token"] = payload["refresh_token"]
    if payload.get("scope"):
        current["scope"] = payload["scope"]
    TOKEN_FILE.write_text(json.dumps(current), encoding="utf-8")
    return payload["access_token"]


def _refresh_access_token(client_id, client_secret, refresh_token):
    token = _spotify_form(
        "https://accounts.spotify.com/api/token",
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + _basic_auth(client_id, client_secret),
        },
    )
    return _save_token(token)


def _basic_auth(client_id, client_secret):
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _listen_for_code():
    found = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            found["code"] = parse_qs(urlparse(self.path).query).get("code", [None])[0]
            body = b"Spotify connected. You can close this window."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = HTTPServer(("127.0.0.1", 8888), Handler)
    server.handle_request()
    server.server_close()
    return found.get("code")


def spotify_access_token(force_login=False):
    client_id, client_secret = spotify_credentials()
    if TOKEN_FILE.exists() and not force_login:
        saved = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        refresh_token = saved.get("refresh_token")
        if refresh_token:
            return _refresh_access_token(client_id, client_secret, refresh_token)
    fields = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SPOTIFY_SCOPES,
    }
    if force_login:
        fields["show_dialog"] = "true"
    query = parse.urlencode(fields)
    webbrowser.open("https://accounts.spotify.com/authorize?" + query)
    code = _listen_for_code()
    if not code:
        raise RuntimeError("Spotify login was not completed")
    token = _spotify_form(
        "https://accounts.spotify.com/api/token",
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + _basic_auth(client_id, client_secret),
        },
    )
    return _save_token(token)


def spotify_api(token, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": "Bearer " + token}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(
        "https://api.spotify.com/v1" + path,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code == 204:
            return {}
        # region agent log
        _agent_log("B", "notes_ui.py:spotify_api", "spotify http error", {"method": method, "path": path.split("?")[0], "code": exc.code})
        # endregion
        raise

def _playback_request(token, method, path, payload=None):
    try:
        return spotify_api(token, method, path, payload)
    except error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("Spotify must be open on a device")
        raise


def _player_path(path, device_id):
    if not device_id:
        return path
    return path + "?" + parse.urlencode({"device_id": device_id})


def spotify_device_id(token):
    payload = spotify_api(token, "GET", "/me/player/devices")
    devices = payload.get("devices") or []
    for device in devices:
        if device.get("is_active") and device.get("id"):
            return device["id"]
    for device in devices:
        if device.get("id"):
            return device["id"]
    return None


def spotify_playback(token):
    try:
        payload = spotify_api(token, "GET", "/me/player")
    except error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise
    if not payload:
        return None
    return payload


def spotify_play(token, context_uri=None, device_id=None):
    payload = {"context_uri": context_uri} if context_uri else None
    return _playback_request(token, "PUT", _player_path("/me/player/play", device_id), payload)


def spotify_pause(token, device_id=None):
    return _playback_request(token, "PUT", _player_path("/me/player/pause", device_id))


def spotify_next(token, device_id=None):
    return _playback_request(token, "POST", _player_path("/me/player/next", device_id))


def spotify_previous(token, device_id=None):
    return _playback_request(token, "POST", _player_path("/me/player/previous", device_id))


def spotify_shuffle(token, enabled, device_id=None):
    params = {"state": "true" if enabled else "false"}
    if device_id:
        params["device_id"] = device_id
    path = "/me/player/shuffle?" + parse.urlencode(params)
    return _playback_request(token, "PUT", path)


def current_track(token):
    try:
        payload = spotify_api(token, "GET", "/me/player/currently-playing")
    except error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise
    item = payload.get("item") if isinstance(payload, dict) else None
    if not item:
        return None
    images = item.get("album", {}).get("images") or []
    artists = ", ".join(artist.get("name", "") for artist in item.get("artists", []))
    return {
        "name": item.get("name", ""),
        "artists": artists,
        "image": images[0]["url"] if images else "",
    }

def upcoming_tracks(token):
    payload = spotify_api(token, "GET", "/me/player/queue")
    current = payload.get("currently_playing") if isinstance(payload, dict) else None
    current_uri = current.get("uri") if isinstance(current, dict) else ""
    tracks = []
    seen = set()
    for item in payload.get("queue") or []:
        if not isinstance(item, dict) or item.get("type") not in (None, "track"):
            continue
        uri = item.get("uri") or ""
        images = (item.get("album") or {}).get("images") or []
        artists = ", ".join(
            artist.get("name", "") for artist in item.get("artists") or []
        )
        # Spotify appends the playlist again after the last upcoming track.
        key = uri or f"{item.get('name') or ''}\n{artists}"
        if key in seen or (tracks and uri and uri == current_uri):
            break
        seen.add(key)
        tracks.append({
            "name": item.get("name") or "",
            "artists": artists,
            "image": images[-1]["url"] if images else "",
        })
        if len(tracks) >= 25:
            break
    return tracks
