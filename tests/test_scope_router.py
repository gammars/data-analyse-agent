import json

from langchain_core.messages import AIMessage

from app.agent.scope_router import classify_scope, classify_scope_by_rules


class FakeScopeModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = []

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=json.dumps(self.payload, ensure_ascii=False))


def test_stock_market_question_is_out_of_scope_for_ecommerce_schema() -> None:
    decision = classify_scope_by_rules(
        "明年股市会好起来没啊",
        '数据表数量：1\n表 orders 字段："order_id", "sales", "customer_state"',
    )

    assert decision is not None
    assert decision.scope == "out_of_scope"
    assert decision.should_plan is False
    assert "股市" in decision.response


def test_stock_market_question_can_be_in_scope_for_finance_schema() -> None:
    decision = classify_scope_by_rules(
        "帮我分析这批股票明年的趋势",
        '数据表数量：1\n表 stock_prices 字段："ticker", "close", "volume", "trade_date"',
    )

    assert decision is None


def test_general_help_does_not_enter_planner() -> None:
    decision = classify_scope_by_rules(
        "python_analysis 和 Docker 沙箱是什么关系？",
        '数据表数量：1\n表 orders 字段："order_id"',
    )

    assert decision is not None
    assert decision.scope == "general_help"
    assert decision.should_plan is False


def test_scope_router_uses_model_when_rules_do_not_match() -> None:
    model = FakeScopeModel(
        {
            "scope": "in_scope",
            "intent": "data_analysis",
            "confidence": 0.9,
            "reason": "用户询问当前订单数据中的销售额统计。",
            "should_plan": True,
            "response": "",
        }
    )

    decision = classify_scope(
        model,
        "统计每个月销售额",
        '数据表数量：1\n表 orders 字段："month", "sales"',
    )

    assert decision.scope == "in_scope"
    assert decision.should_plan is True
    assert len(model.calls) == 1
