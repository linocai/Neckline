"""推送通道单测(plan §3.5/§3.6)。Console 恒成功;Bark 用 httpx.MockTransport
免联网(缺 BARK_URL 优雅跳过、非200/异常降级);macOS 通知用 monkeypatch
`shutil.which`/`subprocess.run` 免真的弹通知;`default_channels`/`push_all` 的
组装与"单通道异常不拖累其它通道"编排逻辑。"""

from __future__ import annotations

import logging

import httpx
import pytest

from neckline.config import Settings
from neckline.sentinel.channels import (
    BarkChannel,
    ConsoleChannel,
    LEVEL_CRITICAL,
    MacNotifyChannel,
    PushChannel,
    default_channels,
    push_all,
)


class TestConsoleChannel:
    def test_always_returns_true(self):
        assert ConsoleChannel().send("标题", "正文") is True

    def test_logs_title_and_body(self, caplog):
        with caplog.at_level(logging.INFO):
            ConsoleChannel().send("买点确认", "600519.SH 站稳支撑")
        assert "买点确认" in caplog.text
        assert "600519.SH 站稳支撑" in caplog.text


class TestBarkChannel:
    def test_no_url_configured_returns_false_without_network(self):
        called = {"n": 0}

        def handler(request):
            called["n"] += 1
            raise AssertionError("未配置 BARK_URL 不应发起网络请求")

        ch = BarkChannel(None)
        ok = ch.send("标题", "正文", transport=httpx.MockTransport(handler))
        assert ok is False
        assert called["n"] == 0

    def test_success_via_mock_transport(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 200})

        ch = BarkChannel("https://api.day.app/testkey")
        ok = ch.send("买点确认", "600519.SH", transport=httpx.MockTransport(handler))
        assert ok is True

    def test_critical_level_adds_sound_and_level_field(self):
        seen_payload = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen_payload.update(json.loads(request.content))
            return httpx.Response(200, json={"code": 200})

        ch = BarkChannel("https://api.day.app/testkey")
        ch.send("退潮刹车", "今日计划作废", level=LEVEL_CRITICAL, transport=httpx.MockTransport(handler))
        assert seen_payload["level"] == "critical"
        assert seen_payload["sound"] == "alarm"

    def test_non_200_returns_false(self):
        ch = BarkChannel("https://api.day.app/testkey")
        ok = ch.send("标题", "正文", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        assert ok is False

    def test_network_exception_degrades_to_false(self):
        def handler(request):
            raise httpx.ConnectError("boom")

        ch = BarkChannel("https://api.day.app/testkey")
        ok = ch.send("标题", "正文", transport=httpx.MockTransport(handler))
        assert ok is False

    def test_trailing_slash_stripped(self):
        ch = BarkChannel("https://api.day.app/testkey/")
        assert ch.bark_url == "https://api.day.app/testkey"

    def test_blank_url_treated_as_unconfigured(self):
        assert BarkChannel("   ").bark_url is None


class TestMacNotifyChannel:
    def test_returns_false_when_osascript_missing(self, monkeypatch):
        import neckline.sentinel.channels as ch_mod

        monkeypatch.setattr(ch_mod.shutil, "which", lambda name: None)
        assert MacNotifyChannel().send("标题", "正文") is False

    def test_returns_true_when_osascript_succeeds(self, monkeypatch):
        import neckline.sentinel.channels as ch_mod

        monkeypatch.setattr(ch_mod.shutil, "which", lambda name: "/usr/bin/osascript")
        monkeypatch.setattr(ch_mod.subprocess, "run", lambda *a, **k: None)
        assert MacNotifyChannel().send("标题", "正文") is True

    def test_subprocess_failure_degrades_to_false(self, monkeypatch):
        import neckline.sentinel.channels as ch_mod

        def _raise(*a, **k):
            raise ch_mod.subprocess.CalledProcessError(1, "osascript")

        monkeypatch.setattr(ch_mod.shutil, "which", lambda name: "/usr/bin/osascript")
        monkeypatch.setattr(ch_mod.subprocess, "run", _raise)
        assert MacNotifyChannel().send("标题", "正文") is False

    def test_quotes_in_message_do_not_crash(self, monkeypatch):
        import neckline.sentinel.channels as ch_mod

        captured = {}

        def fake_run(args, **kwargs):
            captured["script"] = args[2]

        monkeypatch.setattr(ch_mod.shutil, "which", lambda name: "/usr/bin/osascript")
        monkeypatch.setattr(ch_mod.subprocess, "run", fake_run)
        ok = MacNotifyChannel().send('标题"带引号"', "正文\\带反斜杠")
        assert ok is True
        assert '\\"' in captured["script"]


class TestDefaultChannels:
    def test_console_only_when_no_bark_url(self):
        s = Settings(tushare_token=None, llm_provider=None, llm_api_key=None, bark_url=None)
        names = [c.name for c in default_channels(s)]
        assert names == ["console"]

    def test_includes_bark_when_configured(self):
        s = Settings(tushare_token=None, llm_provider=None, llm_api_key=None, bark_url="https://api.day.app/x")
        names = [c.name for c in default_channels(s)]
        assert names == ["console", "bark"]


class TestPushAll:
    def test_aggregates_delivered_channel_names(self):
        class AlwaysTrue(PushChannel):
            name = "a"

            def send(self, title, body, *, level="info", transport=None):
                return True

        class AlwaysFalse(PushChannel):
            name = "b"

            def send(self, title, body, *, level="info", transport=None):
                return False

        delivered = push_all([AlwaysTrue(), AlwaysFalse()], "标题", "正文")
        assert delivered == ["a"]

    def test_one_channel_raising_does_not_block_others(self):
        class Broken(PushChannel):
            name = "broken"

            def send(self, title, body, *, level="info", transport=None):
                raise RuntimeError("boom")

        class Fine(PushChannel):
            name = "fine"

            def send(self, title, body, *, level="info", transport=None):
                return True

        delivered = push_all([Broken(), Fine()], "标题", "正文")
        assert delivered == ["fine"]
