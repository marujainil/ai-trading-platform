"""Backtesting Engine.

Simulates the technical leg of the Decision Engine bar-by-bar over history
(fundamentals/news can't be reconstructed point-in-time from free data, so the
backtest is technical-only — stated plainly in the result).

Rules (long-only, one position per symbol):
  * ENTER  when score ≥ entry_threshold and Supertrend is bullish
  * SIZE   by risk: qty = equity × risk_per_trade / (entry − stop)
  * STOP   entry − atr_stop_mult × ATR   (intrabar low triggers)
  * T1     entry + atr_t1_mult × ATR → stop moves to breakeven
  * T2     entry + atr_t2_mult × ATR → full exit
  * EXIT   also when score ≤ exit_threshold or Supertrend flips bearish

Costs: `cost_bps` per side approximates brokerage + STT + slippage.

Metrics: win rate, profit factor, Sharpe, Sortino, max drawdown, CAGR,
average trade, expectancy — honest numbers, no survivorship massage.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.engines.technical import technical_score_vector

DEFAULT_PARAMS = {
    "entry_threshold": 65.0,
    "exit_threshold": 45.0,
    "risk_per_trade_pct": 1.0,
    "atr_stop_mult": 1.5,
    "atr_t1_mult": 2.25,
    "atr_t2_mult": 4.5,
    "starting_equity": 1_000_000.0,
    "cost_bps": 12.0,   # per side: ~0.12% for delivery incl. slippage; tune to your broker
    "warmup_bars": 60,
}


def run_backtest(df: pd.DataFrame, params: dict | None = None) -> dict:
    p = {**DEFAULT_PARAMS, **(params or {})}
    score, aux = technical_score_vector(df)

    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    atr = aux["atr"].to_numpy()
    st_dir = aux["st_dir"].to_numpy()
    sc = score.to_numpy()
    dates = df.index

    equity = p["starting_equity"]
    cash = equity
    cost = p["cost_bps"] / 10_000.0

    pos_qty = 0
    entry_px = stop = t1 = t2 = 0.0
    entry_i = 0
    t1_hit = False

    trades: list[dict] = []
    equity_curve = np.empty(len(df))

    def close_position(i: int, px: float, reason: str):
        nonlocal cash, pos_qty, t1_hit
        proceeds = pos_qty * px * (1 - cost)
        cash += proceeds
        cost_basis = pos_qty * entry_px * (1 + cost)
        pnl = proceeds - cost_basis
        trades.append({
            "entry_date": str(dates[entry_i].date()),
            "exit_date": str(dates[i].date()),
            "entry": round(entry_px, 2),
            "exit": round(px, 2),
            "qty": pos_qty,
            "pnl": round(pnl, 2),
            "pnl_pct": round(100 * pnl / cost_basis, 2) if cost_basis else 0.0,
            "bars_held": i - entry_i,
            "reason": reason,
        })
        pos_qty = 0
        t1_hit = False

    start = max(p["warmup_bars"], 1)
    for i in range(start, len(df)):
        # ---- manage open position (check exits on this bar) ----
        if pos_qty > 0:
            if low[i] <= stop:
                close_position(i, stop, "breakeven_stop" if t1_hit else "stop_loss")
            elif high[i] >= t2:
                close_position(i, t2, "target_2")
            else:
                if not t1_hit and high[i] >= t1:
                    t1_hit = True
                    stop = entry_px  # move to breakeven after T1
                if sc[i] <= p["exit_threshold"] or st_dir[i] == -1:
                    close_position(i, close[i], "signal_exit")

        # ---- consider new entry ----
        if pos_qty == 0 and sc[i] >= p["entry_threshold"] and st_dir[i] == 1 and atr[i] > 0:
            e = close[i]
            s = e - p["atr_stop_mult"] * atr[i]
            if s > 0:
                mtm_equity = cash  # flat here, so equity == cash
                risk_amt = mtm_equity * p["risk_per_trade_pct"] / 100.0
                qty = math.floor(risk_amt / (e - s))
                qty = min(qty, math.floor(cash / (e * (1 + cost))))
                if qty > 0:
                    pos_qty = qty
                    entry_px, stop = e, s
                    t1 = e + p["atr_t1_mult"] * atr[i]
                    t2 = e + p["atr_t2_mult"] * atr[i]
                    entry_i = i
                    t1_hit = False
                    cash -= qty * e * (1 + cost)

        equity_curve[i] = cash + pos_qty * close[i]

    equity_curve[:start] = p["starting_equity"]
    if pos_qty > 0:  # mark final open position closed at last close for accounting
        close_position(len(df) - 1, close[-1], "end_of_data")
        equity_curve[-1] = cash

    return {
        "params": p,
        "note": ("Technical-signal backtest only: point-in-time fundamentals/news are not "
                 "reconstructable from free data. Past performance does not guarantee future results."),
        "metrics": compute_metrics(equity_curve, trades, dates),
        "trades": trades[-50:],
        "total_trades": len(trades),
        "equity_curve": _downsample_curve(equity_curve, dates),
    }


def compute_metrics(equity_curve: np.ndarray, trades: list[dict], dates) -> dict:
    eq = pd.Series(equity_curve)
    daily_ret = eq.pct_change().dropna()

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)

    n = len(trades)
    win_rate = len(wins) / n if n else 0.0
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

    std = daily_ret.std()
    sharpe = float(daily_ret.mean() / std * np.sqrt(252)) if std and std > 0 else 0.0
    downside = daily_ret[daily_ret < 0].std()
    sortino = float(daily_ret.mean() / downside * np.sqrt(252)) if downside and downside > 0 else 0.0

    running_max = eq.cummax()
    drawdown = eq / running_max - 1.0
    max_dd = float(drawdown.min())

    years = max(len(eq) / 252.0, 1e-9)
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) if eq.iloc[0] > 0 else 0.0

    return {
        "total_trades": n,
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else (None if gross_profit > 0 else 0.0),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "total_return_pct": round((eq.iloc[-1] / eq.iloc[0] - 1) * 100, 2),
        "avg_trade_pnl": round(sum(t["pnl"] for t in trades) / n, 2) if n else 0.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy_per_trade": round(expectancy, 2),
        "final_equity": round(float(eq.iloc[-1]), 2),
    }


def _downsample_curve(equity_curve: np.ndarray, dates, max_points: int = 200) -> list[dict]:
    step = max(1, len(equity_curve) // max_points)
    return [{"date": str(dates[i].date()), "equity": round(float(equity_curve[i]), 2)}
            for i in range(0, len(equity_curve), step)]
