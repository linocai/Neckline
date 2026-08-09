"""`v2.2-k8` 治下 `GET /positions` 的契约面守门(V2.2-⑤-A,🔴 端点级)。

单独成文件的理由:本文件用 `api_env`/`client` 夹具,而 `tests/test_charter_v22k8.py` 有
**模块级** `usefixtures("isolated_env")` —— 两套夹具都改 `settings` 绑定,叠在一起时谁先谁后
不由我们决定,是个不必要的脆点(v1.4-④ 测试隔离纪律的同一条精神:别让隔离靠运气)。

锁死三件:
  · `max_hold_days=None` 下端点**不炸**(`d >= None` 这类 `TypeError` 是本次最现实的翻车方式);
  · 两个 D 上限字段如实发 `null`,⛔ 不拿 5 顶上冒充"有时间退出"(§3.11-E 否决哨兵位同一种病);
  · 止损文案**跟着现役章程走**:`v2.2-k8` → 「止损警戒 / 离场决策在你」;
    **激活前(K1/v1.3.3)逐字不变** —— §2.1 前置提示「激活前本节其余全文一字有效」的落点。
"""

from __future__ import annotations

class TestPositionsEndpointUnderK8Charter:
    """🔴 `d >= None` 这类 `TypeError` 是本次最现实的翻车方式 —— 端点级正面兜一遍。"""

    @staticmethod
    def _activate_k8(api_env):
        from neckline.strategy import brain
        from tests.conftest import TEST_RULE_V1_CONFIG

        cfg = dict(TEST_RULE_V1_CONFIG, take_profit_retrace=None, max_hold_days=None,
                   max_hold_days_profit=None, time_exit_only_if_unprofitable=False,
                   stop_pct=0.05, single_cap=40000.0, max_positions=3, max_exposure_frac=1.0,
                   forbid_high_elasticity=False)
        brain.save_version("v2.2-k8", {"config": cfg, "lineage": "K1"},
                           "测试:v2.2-k8", activate=True, db_path=api_env.db_path)

    def test_positions_endpoint_survives_and_sends_null(self, client, AUTH, api_env):
        self._activate_k8(api_env)
        client.post("/api/v1/positions", headers=AUTH,
                    json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
        r = client.get("/api/v1/positions", headers=AUTH)
        assert r.status_code == 200
        h = r.json()["holdings"][0]
        # ⛔ 不拿 5 顶上冒充"有时间退出"(§3.11-E 否决哨兵位的同一种病)
        assert h["maxHoldDays"] is None
        assert h["maxHoldDaysEffective"] is None
        assert h["timeExitState"] == "holding"       # 没有判定点 → 永远到不了
        assert h["timeExitLockedDay"] is None and h["timeExitLockedLateDays"] == 0
        assert h["stopLine"] == 9.5                  # ⚠ stop_pct=0.05 一字未动
        assert "无时间退出条款" in h["todayAction"]   # 如实说明,不编一个 D 上限

    def test_stop_wording_switches_with_the_active_charter(self, client, AUTH, api_env, monkeypatch):
        """现役是 `v2.2-k8` → `todayAction` 走「止损警戒 / 离场决策在你」。"""
        import neckline.api.app as app_mod
        from neckline.sentinel.quotes import Quote

        self._activate_k8(api_env)
        client.post("/api/v1/positions", headers=AUTH,
                    json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
        monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {
            "600001.SH": Quote(code="600001.SH", name="", price=9.4, pre_close=10.0, open=9.4,
                               high=9.5, low=9.4, volume=0.0, amount=0.0, ts="", source="t")})
        h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
        assert "止损警戒" in h["todayAction"] and "离场决策在你" in h["todayAction"]
        assert "条件单" not in h["todayAction"]

    def test_stop_wording_unchanged_before_activation(self, client, AUTH, api_env, monkeypatch):
        """🔴 **激活前逐字不变**(§2.1 前置提示):现役 `v1`(K1 口径)→ 仍是条件单文案。"""
        import neckline.api.app as app_mod
        from neckline.sentinel.quotes import Quote
        from tests.conftest import seed_active_rule_v1

        seed_active_rule_v1(api_env)
        client.post("/api/v1/positions", headers=AUTH,
                    json={"code": "600001.SH", "buy_price": 10.0, "qty": 100})
        monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {
            "600001.SH": Quote(code="600001.SH", name="", price=9.4, pre_close=10.0, open=9.4,
                               high=9.5, low=9.4, volume=0.0, amount=0.0, ts="", source="t")})
        h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
        assert "条件单" in h["todayAction"] and "止损警戒" not in h["todayAction"]
        assert h["maxHoldDays"] == 5                 # 老口径照发,零变化
