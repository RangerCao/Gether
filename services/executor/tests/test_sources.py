import pytest

from app.contracts import VideoSourceName
from app.core.config import Settings
from app.ingest.sources.base import InvalidVideoUrlError, UnsupportedSourceError
from app.ingest.sources.bilibili import BilibiliSource
from app.ingest.sources.registry import SourceRegistry
from app.ingest.sources.youtube import YouTubeSource


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, llm_provider="mock", **kwargs)


@pytest.mark.asyncio
async def test_bilibili_bvid_and_page():
    source = BilibiliSource()
    ref = await source.normalize("https://www.bilibili.com/video/BV1xx411c7mD/?p=3")
    assert ref.source == VideoSourceName.BILIBILI
    assert ref.source_video_id == "BV1xx411c7mD"
    assert ref.page == 3
    assert ref.url.endswith("?p=3")


@pytest.mark.asyncio
async def test_bilibili_av_and_ep():
    source = BilibiliSource()
    av = await source.normalize("https://www.bilibili.com/video/av170001")
    assert av.source_video_id == "av170001"

    ep = await source.normalize("https://www.bilibili.com/bangumi/play/ep123456")
    assert ep.source_video_id == "ep123456"
    assert ep.url.endswith("/ep123456")


@pytest.mark.asyncio
async def test_bilibili_short_link_resolved(monkeypatch):
    source = BilibiliSource()

    async def fake_resolve(url: str) -> str:
        return "https://www.bilibili.com/video/BV1xx411c7mD?p=2"

    monkeypatch.setattr(source, "_resolve_short", fake_resolve)
    ref = await source.normalize("https://b23.tv/abc123")
    assert ref.source_video_id == "BV1xx411c7mD"
    assert ref.page == 2


@pytest.mark.asyncio
async def test_bilibili_invalid():
    source = BilibiliSource()
    with pytest.raises(InvalidVideoUrlError):
        await source.normalize("https://www.bilibili.com/read/cv123")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123", "dQw4w9WgXcQ"),
    ],
)
async def test_youtube_variants(url: str, expected: str):
    ref = await YouTubeSource().normalize(url)
    assert ref.source == VideoSourceName.YOUTUBE
    assert ref.source_video_id == expected
    assert ref.url == f"https://www.youtube.com/watch?v={expected}"


@pytest.mark.asyncio
async def test_youtube_playlist_only_rejected():
    with pytest.raises(InvalidVideoUrlError):
        await YouTubeSource().normalize("https://www.youtube.com/playlist?list=PL123")


@pytest.mark.asyncio
async def test_registry_routes_both_sources():
    registry = SourceRegistry(_settings(app_env="local"))
    bili = await registry.normalize("https://www.bilibili.com/video/BV1xx411c7mD")
    yt = await registry.normalize("https://youtu.be/dQw4w9WgXcQ")
    assert bili.source == VideoSourceName.BILIBILI
    assert yt.source == VideoSourceName.YOUTUBE


@pytest.mark.asyncio
async def test_registry_rejects_unknown_source():
    registry = SourceRegistry(_settings(app_env="local"))
    with pytest.raises(UnsupportedSourceError):
        await registry.normalize("https://vimeo.com/123456")


@pytest.mark.asyncio
async def test_local_source_only_in_local_env(tmp_path):
    local_file = tmp_path / "demo.mp4"
    local_file.write_bytes(b"fake")
    local = SourceRegistry(_settings(app_env="local"))
    assert (await local.normalize(str(local_file))).source == VideoSourceName.LOCAL

    prod = SourceRegistry(_settings(app_env="prod"))
    with pytest.raises(UnsupportedSourceError):
        await prod.normalize(str(local_file))


@pytest.mark.asyncio
async def test_bilibili_bare_id():
    ref = await BilibiliSource().normalize("BV1xx411c7mD")
    assert ref.source_video_id == "BV1xx411c7mD"
    assert ref.url == "https://www.bilibili.com/video/BV1xx411c7mD"

    ep = await BilibiliSource().normalize("ep123456")
    assert ep.source_video_id == "ep123456"
