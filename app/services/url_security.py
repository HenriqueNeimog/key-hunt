from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


class InvalidPlaylistUrl(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NormalizedPlaylistUrl:
    url: str
    playlist_id: str


_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def normalize_playlist_url(raw_url: str) -> NormalizedPlaylistUrl:
    try:
        parsed = urlsplit(raw_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise InvalidPlaylistUrl("URL malformada") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise InvalidPlaylistUrl("Use uma URL HTTP ou HTTPS")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _ALLOWED_HOSTS or parsed.username or parsed.password:
        raise InvalidPlaylistUrl("Use uma URL de playlist do YouTube")
    if port not in {None, 80, 443}:
        raise InvalidPlaylistUrl("Porta de URL não permitida")
    playlist_values = parse_qs(parsed.query, keep_blank_values=False).get("list", [])
    if len(playlist_values) != 1 or not _PLAYLIST_ID.fullmatch(playlist_values[0]):
        raise InvalidPlaylistUrl("A URL precisa conter um identificador de playlist válido")
    playlist_id = playlist_values[0]
    canonical = urlunsplit(
        ("https", "www.youtube.com", "/playlist", urlencode({"list": playlist_id}), "")
    )
    return NormalizedPlaylistUrl(url=canonical, playlist_id=playlist_id)
