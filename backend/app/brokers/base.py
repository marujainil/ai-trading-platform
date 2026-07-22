"""Broker adapter interface.

Core engines never talk to a broker directly — they emit intents; an adapter
translates them. Add a broker by subclassing `BrokerAdapter` and registering it
in `get_broker` (routes stay untouched).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OrderRequest:
    symbol: str
    side: str          # "BUY" | "SELL"
    quantity: int
    order_type: str = "MARKET"
    limit_price: float | None = None


@dataclass
class OrderResult:
    ok: bool
    fill_price: float | None = None
    broker_order_id: str | None = None
    message: str = ""


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def place_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def get_quote(self, symbol: str) -> float: ...
