"""Unit tests for TradeCalculator."""

from app.broker.trade_calculator import TradeCalculator


def test_calculate_quantity_safe_leverage():
    """Verify that calculate_quantity limits leverage safely and rounds down quantity."""
    # 1000 USD balance, BTC at 50,000, contract_value 0.001 (1 mBTC)
    # 30% capital = 300 USD
    # With 20x broker leverage, halved to 10x
    setup = TradeCalculator.calculate_quantity(
        capital=1000.0,
        mark_price=50000.0,
        contract_value=0.001,
        leverage=20,
        side="buy",
        capital_pct=30.0,
        risk_ratio=0.01,
    )

    assert setup["used_capital"] == 300.0
    assert setup["leverage"] == 10
    # raw_qty = (300 * 10) / (50000 * 0.001) = 3000 / 50 = 60
    assert setup["quantity"] == 60


def test_calculate_quantity_zero_balance():
    """Verify safe behavior when capital is 0."""
    setup = TradeCalculator.calculate_quantity(
        capital=0.0,
        mark_price=50000.0,
        contract_value=0.001,
        leverage=20,
    )
    assert setup["quantity"] == 0
    assert setup["used_capital"] == 0.0


def test_calculate_stop_target():
    """Verify stop-loss and target calculations for BUY and SELL."""
    buy_levels = TradeCalculator.calculate_stop_target(
        current_price=100.0,
        side="buy",
        risk_ratio=0.01,
        reward_ratio=0.02,
    )
    assert buy_levels["stop_loss"] == 99.0
    assert buy_levels["target"] == 102.0

    sell_levels = TradeCalculator.calculate_stop_target(
        current_price=100.0,
        side="sell",
        risk_ratio=0.01,
        reward_ratio=0.02,
    )
    assert sell_levels["stop_loss"] == 101.0
    assert sell_levels["target"] == 98.0
