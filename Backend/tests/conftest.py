"""Offline guards. Tests use temporary databases only and do not load `.env`."""
from __future__ import annotations

import copy as _copy
import os as _os
from pathlib import Path

import pytest


def cover_sector_history(path, day):
    """给发酵统计补上完整采集覆盖。不算业务数据，只让 C 路在历史不全时不要挡住别的测试。"""
    from datetime import date as date_cls

    from neckline.morning import db
    from neckline.morning.report import load_config
    from neckline.morning.sector_ferment import history_bounds

    trade_day = day if isinstance(day, date_cls) else date_cls.fromisoformat(str(day)[:10])
    rules = load_config().rules
    periods = rules["sectors"]["fermenting"]["recentDays"] + rules["sectors"]["fermenting"]["baseDays"]
    start, end = history_bounds(trade_day, rules["schedule"]["windowEnd"], periods)
    run_id = f"cover-{trade_day.isoformat()}"
    with db.write(path) as conn:
        existing = conn.execute("SELECT 1 FROM collection_runs WHERE run_id=?", (run_id,)).fetchone()
        if existing:
            return
        db.begin_collection(conn, run_id=run_id, slot_at=end, started_at=end)
        db.finish_collection(conn, run_id=run_id, status="completed", finished_at=end, sources=[
            {"sourceKey": key, "requestedStart": start.isoformat(), "requestedEnd": end.isoformat(),
             "complete": True, "error": None, "documents": 0}
            for key in ("tushare-major-news", "jin10-flash", "jin10-news")
        ])


def approved_config(base=None):
    """已批准的配置副本。文件里的 approval 是 pending，需要跑晨报的测试都从这里取。"""
    from neckline.morning.report import LoadedConfig, load_config

    base = base or load_config()
    rules = _copy.deepcopy(base.rules)
    rules["approval"] = "approved"
    return LoadedConfig(rules=rules, sha256=base.sha256, pool=base.pool,
                        profiles=dict(base.profiles), alias_seed=base.alias_seed)

_os.environ["PYTHON_DOTENV_DISABLED"] = "1"


@pytest.fixture(autouse=True)
def cli_uses_the_approved_copy(monkeypatch):
    """文件里的筛选规则是 pending。命令行和回放要跑晨报时，用这一份已批准的副本。"""
    from neckline.morning.report import load_config as load_rules

    def _load_approved(*args, **kwargs):
        loaded = load_rules(*args, **kwargs) if args or kwargs else load_rules()
        return approved_config(loaded)

    monkeypatch.setattr("neckline.morning.cli.load_config", _load_approved)
    monkeypatch.setattr("neckline.morning.replay.load_config", _load_approved)


@pytest.fixture(autouse=True)
def isolate_market_and_mcp(monkeypatch):
    """行情和 MCP 凭据不从开发环境漏进测试。每个用例结束后关掉默认客户端。"""
    for key in ("TUSHARE_TOKEN", "MAIRUI_LICENCE", "NECKLINE_PUBLIC_BASE",
                "NECKLINE_MCP_PASSWORD_SALT", "NECKLINE_MCP_PASSWORD_HASH", "NECKLINE_MCP_AUTH_DB",
                "KX_READ_KEY", "KX_PICKS_TOKEN", "KX_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    from neckline.kx import services as kx_services
    from neckline.kx.board import reset_board_state
    from neckline.market import services
    from neckline.mcp.status import set_mcp_serving

    set_mcp_serving(False)
    services.use(None)
    kx_services.use(None)
    reset_board_state()
    yield
    set_mcp_serving(False)
    services.use(None)
    kx_services.use(None)
    reset_board_state()
    import asyncio
    asyncio.run(kx_services.close_default_services())
    asyncio.run(services.close_default_services())


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch):
    """Offline acceptance: deny DNS and sockets except explicit loopback addresses."""
    import ipaddress
    import socket

    def allowed(host):
        if host in {None, "localhost"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    resolve, connect, connect_ex, sendto = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto

    def checked_resolve(host, *args, **kwargs):
        if not allowed(host):
            raise RuntimeError(f"Offline tests deny external DNS: {host}")
        return resolve(host, *args, **kwargs)

    def check_address(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6} and not allowed(address[0]):
            raise RuntimeError(f"Offline tests deny external network: {address[0]}")

    def checked_connect(sock, address):
        check_address(sock, address)
        return connect(sock, address)

    def checked_connect_ex(sock, address):
        check_address(sock, address)
        return connect_ex(sock, address)

    def checked_sendto(sock, data, *args):
        check_address(sock, args[-1])
        return sendto(sock, data, *args)

    monkeypatch.setattr(socket, "getaddrinfo", checked_resolve)
    monkeypatch.setattr(socket.socket, "connect", checked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", checked_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", checked_sendto)


@pytest.fixture(autouse=True)
def inherit_offline_subprocess_guard(monkeypatch):
    import subprocess
    original = subprocess.Popen
    guard = str(Path(__file__).parent / 'offline_guard')

    def guarded(*args, **kwargs):
        import shlex
        command = args[0] if args else kwargs.get('args', [])
        words = shlex.split(command) if isinstance(command, str) else list(command)
        # Python children inherit the socket/DNS guard. Reject native network
        # clients and shell wrappers rather than letting them bypass Python.
        if words and Path(str(words[0])).name in {'curl','wget','ssh','scp','sftp','nc','ncat','netcat','sh','bash','zsh','fish','env'}:
            raise RuntimeError('Offline tests deny native network subprocess')
        if kwargs.get('shell'):
            raise RuntimeError('Offline tests deny shell subprocess bypass')
        env = dict(kwargs.get('env') or _os.environ)
        env['PYTHONPATH'] = guard + _os.pathsep + env.get('PYTHONPATH', '')
        kwargs['env'] = env
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', guarded)
