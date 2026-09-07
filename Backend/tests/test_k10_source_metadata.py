from datetime import datetime, timezone

from neckline.k10.source_metadata import PublicationMetadataResolver, TransportResponse


def _resolver(response, *, requests=1):
    return PublicationMetadataResolver(allowed_https_hosts={"news.example.com"}, max_requests=requests,
        timeout_seconds=2, max_bytes=500, transport=lambda *_args, **_kwargs: response,
        clock=lambda: datetime(2026, 9, 5, 1, tzinfo=timezone.utc))


def test_standard_published_times_choose_latest_explicit_timezone_value():
    html = b'<meta property="article:published_time" content="2026-09-04T18:51:45+08:00"><meta property="bytedance:published_time" content="2026-09-04T18:50:22+08:00">'
    value = _resolver(TransportResponse(200, {}, html)).resolve("https://news.example.com/a")
    assert value.published_at == "2026-09-04T18:51:45+08:00"
    assert value.precision == "exact" and len(value.metadata["publishedValues"]) == 2


def test_updated_only_and_timezone_less_dates_are_not_publication_times():
    html = b'<meta property="article:modified_time" content="2026-09-04T20:00:00+08:00"><script type="application/ld+json">{"datePublished":"2026-09-04"}</script>'
    value = _resolver(TransportResponse(200, {}, html)).resolve("https://news.example.com/a")
    assert value.published_at is None and value.precision == "unknown"


def test_rejects_unsafe_targets_redirects_and_request_limit():
    response = TransportResponse(302, {"Location": "https://evil.example"}, b"")
    resolver = _resolver(response)
    assert resolver.resolve("https://127.0.0.1/a").metadata["reason"] == "target_rejected"
    assert resolver.resolve("https://[::1]/a").metadata["reason"] == "target_rejected"
    assert resolver.resolve("https://169.254.1.2/a").metadata["reason"] == "target_rejected"
    assert resolver.resolve("https://news.example.com:444/a").metadata["reason"] == "target_rejected"
    assert resolver.resolve("https://news.example.com:bad/a").metadata["reason"] == "invalid_url"
    assert resolver.resolve("https://news.example.com/a").metadata["reason"] == "redirect_rejected"
    assert resolver.resolve("https://news.example.com/b").metadata["reason"] == "request_limit"


def test_byte_limit_is_not_parsed():
    value = _resolver(TransportResponse(200, {}, b"x" * 501)).resolve("https://news.example.com/a")
    assert value.metadata["reason"] == "byte_limit" and value.published_at is None
