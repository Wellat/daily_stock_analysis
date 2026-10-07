

def test_rebalance_buy_budget_follows_account_capital():
    """单批买入预算对齐 account_capital：10 只×8000=8 万不被默认 5 万总闸截断。"""
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = LiveStrategyService(db)
        service.save_config({"qmt_account": "testS", "enabled": True, "data_sync_before_run": False,
                             "parameters": {"max_positions": 10, "per_position_cash": 8000,
                                            "account_capital": 80000, "max_position_pct": 10}})
        trade_date = date(2024, 1, 2)
        _seed_cb_universe(db, trade_date, [
            {"code": f"1130{i:02d}", "name": f"债{i}", "close": 100.0, "premium": float(i)}
            for i in range(1, 11)
        ])
        QmtPositionService(db).report_positions(account="testS", positions=[])

        result = service.run(trade_date=trade_date, preview=True)

        buys = [o for o in result["rebalance"] if o["side"] == "buy"]
        assert len(buys) == 10
        assert all(o["quantity"] == 80 for o in buys)  # int(8000/100/10)*10 张
        skipped_reasons = [s["reason"] for s in result["diagnostics"]["skipped"]]
        assert "insufficient_cash" not in skipped_reasons
    finally:
        DatabaseManager.reset_instance()
