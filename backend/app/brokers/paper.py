"""Paper broker — simulated fills at the latest market price.

This is the default (and only pre-enabled) execution path. Live adapters must be
added explicitly, keeping simulation-first as the platform's posture.
"""
from __future__ import annotations

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult
from app.data.market_data import DataError, last_price


class PaperBroker(BrokerAdapter):
    name = "paper"

    def get_quote(self, symbol: str) -> float:
        return last_price(symbol)

    def place_order(self, order: OrderRequest) -> OrderResult:
        try:
            px = order.limit_price if (order.order_type == "LIMIT" and order.limit_price) else self.get_quote(order.symbol)
        except DataError as exc:
            return OrderResult(ok=False, message=str(exc))
        return OrderResult(ok=True, fill_price=float(px), broker_order_id=f"paper-{order.symbol}", message="Simulated fill")
