# -*- coding: utf-8 -*-
"""ETF 实时行情源路由回归：source=tencent 时必须走腾讯直连。

背景：ETF 分支此前无视 source 参数一律走东财 fund_etf_spot_em，东财断连时
配置里的腾讯/新浪 fallback 槽位全部打到同一死接口，整条链失效（2026-10-19
ETF 信号通知「行情不可用」的根因）。
"""

from __future__ import annotations

from data_provider.akshare_fetcher import AkshareFetcher


def test_etf_realtime_quote_respects_tencent_source(monkeypatch):
    fetcher = AkshareFetcher()
    calls: list[tuple[str, str]] = []
    sentinel = object()

    def _tencent(code):
        calls.append(("tencent", code))
        return sentinel

    def _em(code):
        calls.append(("em", code))
        return None

    monkeypatch.setattr(fetcher, "_get_stock_realtime_quote_tencent", _tencent)
    monkeypatch.setattr(fetcher, "_get_etf_realtime_quote", _em)

    # 腾讯槽位（priority 首位）必须命中直连，而不是落到东财 ETF 接口
    assert fetcher.get_realtime_quote("513630", source="tencent") is sentinel
    assert calls == [("tencent", "513630")]

    # 其余 source 仍走东财 ETF 接口，不改变既有语义
    calls.clear()
    assert fetcher.get_realtime_quote("513630", source="em") is None
    assert calls == [("em", "513630")]
