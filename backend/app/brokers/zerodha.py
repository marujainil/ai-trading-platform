"""Zerodha Kite Connect adapter — STUB (intentionally not wired to live orders).

To go live (after paper results satisfy you AND you have confirmed the current
SEBI/exchange algo-trading requirements with your broker):

  1. `pip install kiteconnect`, subscribe to Kite Connect at developers.kite.trade
  2. Complete the daily login flow to obtain an access token:
         kite = KiteConnect(api_key=...)
         # redirect user to kite.login_url(), exchange request_token:
         data = kite.generate_session(request_token, api_secret=...)
         kite.set_access_token(data["access_token"])
  3. Implement the two methods below with:
         kite.ltp(f"NSE:{tradingsymbol}")                      # get_quote
         kite.place_order(variety=kite.VARIETY_REGULAR,        # place_order
                          exchange=kite.EXCHANGE_NSE,
                          tradingsymbol=..., transaction_type=...,
                          quantity=..., product=kite.PRODUCT_CNC,
                          order_type=kite.ORDER_TYPE_MARKET)
  4. Symbol mapping: Yahoo "RELIANCE.NS" → Kite "RELIANCE" (strip the suffix).

The same pattern applies for Upstox and Angel One SmartAPI — one file each.

⚠ Automated order placement in India is regulated. Exchanges/SEBI require
brokers to approve/tag algorithmic order flow; requirements change. Verify the
current rules with your broker before enabling any live adapter.
"""
from __future__ import annotations

from app.brokers.base import BrokerAdapter, OrderRequest, OrderResult


class ZerodhaBroker(BrokerAdapter):
    name = "zerodha"

    def __init__(self, api_key: str | None = None, access_token: str | None = None):
        self.api_key = api_key
        self.access_token = access_token

    def get_quote(self, symbol: str) -> float:
        raise NotImplementedError("Zerodha adapter is a stub — see module docstring to enable it.")

    def place_order(self, order: OrderRequest) -> OrderResult:
        return OrderResult(ok=False, message="Zerodha adapter not enabled. Use paper broker, "
                                             "or implement this adapter per the module docstring.")
