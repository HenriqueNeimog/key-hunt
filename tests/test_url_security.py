import pytest

from app.services.url_security import InvalidPlaylistUrl, normalize_playlist_url


def test_normalizes_supported_playlist() -> None:
    result = normalize_playlist_url("http://m.youtube.com/watch?v=abc&list=PLabcdefghij#ignored")
    assert result.playlist_id == "PLabcdefghij"
    assert result.url == "https://www.youtube.com/playlist?list=PLabcdefghij"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://evil.example/playlist?list=PLabcdefghij",
        "https://youtube.com.evil.example/playlist?list=PLabcdefghij",
        "https://user:pass@youtube.com/playlist?list=PLabcdefghij",
        "https://youtube.com:8080/playlist?list=PLabcdefghij",
        "https://youtube.com/playlist?list=short",
        "https://youtube.com/playlist?list=PLabcdefghij&list=PLotherother",
    ],
)
def test_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(InvalidPlaylistUrl):
        normalize_playlist_url(url)
