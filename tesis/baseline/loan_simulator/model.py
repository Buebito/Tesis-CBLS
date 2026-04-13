"""
tesis/baseline/loan_simulator/model.py
--------------------------------------
Simulador de préstamos respaldados por cripto con Segment Tree.
Adaptado de prestamos/utils/model.py para uso modular en la tesis.

Cambios respecto al original:
- CONFIG se recibe como parámetro (no global mutable).
- Rutas relativas resueltas desde la raíz del proyecto.
- Imports limpiados.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


# ── CONFIG por defecto ───────────────────────────────────────────────────────

DEFAULT_CONFIG: Dict[str, Any] = {
    "MXN_USD": 19.0,
    # LTVs
    "INIT_LTV": 0.50,
    "MC_LTV": 0.72,
    "LIQ_LTV": 0.80,
    # Tasas simples anuales
    "INT_BTC": 0.18,
    "INT_ETH": 0.20,
    # Comisión sobre colateral liquidado
    "LIQUIDATION_FEE_RATE": 0.08,
    # Probabilidades base de decisión en MC (decaen: ^margin_calls)
    "P_ADD_INIT": 0.25,
    "P_AMORT_INIT": 0.25,
    "P_RENEW_INIT": 0.50,
    # Tamaños de préstamo en MXN
    "LOAN_SIZES_MXN": [
        200_000,
        500_000,
        750_000,
        1_000_000,
        1_250_000,
        1_500_000,
        1_750_000,
        2_000_000,
    ],
    "SIZE_WEIGHTS": [1] * 8,
    # Simulación
    "N_LOANS": 10_000,
    "SEED": 42,
    "LOG_EVENTS": True,
}

MINUTES_PER_YEAR = 525_600


# ── Segment Tree ─────────────────────────────────────────────────────────────


class SegmentTree:
    """Range-minimum-query O(log n) sobre un array de precios."""

    __slots__ = ("l", "r", "mid", "val", "ind", "L", "R")

    def __init__(self, arr: List[float], l: int = 0, r: int | None = None):
        if r is None:
            r = len(arr)
        self.l, self.r = l, r
        self.mid = (l + r) // 2
        self.val, self.ind = arr[l], l
        if r - l > 1:
            self.L = SegmentTree(arr, l, self.mid)
            self.R = SegmentTree(arr, self.mid, r)
            if self.L.val <= self.R.val:
                self.val, self.ind = self.L.val, self.L.ind
            else:
                self.val, self.ind = self.R.val, self.R.ind
        else:
            self.L = self.R = None

    def range_min(self, ql: int, qr: int) -> Tuple[float, int]:
        """Mínimo en [ql, qr)."""
        if qr <= self.l or ql >= self.r:
            return float("inf"), -1
        if ql <= self.l and qr >= self.r:
            return self.val, self.ind
        lv, li = self.L.range_min(ql, qr) if self.L else (float("inf"), -1)
        rv, ri = self.R.range_min(ql, qr) if self.R else (float("inf"), -1)
        return (lv, li) if lv <= rv else (rv, ri)


# ── Helpers ──────────────────────────────────────────────────────────────────


def load_price_df(p: str | Path) -> pd.DataFrame:
    """Lee CSV con índice datetime ascendente y columnas de precios por coin."""
    return pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()


def align_series(df: pd.DataFrame, coin: str) -> Tuple[np.ndarray, List[pd.Timestamp]]:
    return df[coin].values.astype(float), df.index.to_list()


def pad_loc(idx: pd.DatetimeIndex, ts: pd.Timestamp) -> int:
    if ts in idx:
        return idx.get_loc(ts)
    loc = idx.get_indexer([ts], method="pad")[0]
    return 0 if loc == -1 else int(loc)


# ── Loan ─────────────────────────────────────────────────────────────────────


@dataclass
class Loan:
    loan_id: int
    coin: str
    principal_mxn: int
    start_idx: int
    df: pd.DataFrame
    tree: SegmentTree
    config: Dict[str, Any] = field(default_factory=lambda: DEFAULT_CONFIG)

    # Derivados
    principal_usd: float = field(init=False)
    collateral_coin_amt: float = field(init=False)
    init_ltv: float = field(init=False)
    mc_ltv: float = field(init=False)
    liq_ltv: float = field(init=False)
    rate: float = field(init=False)

    # Estado dinámico
    accrued_interest: float = 0.0
    interest_usd: float = 0.0
    liq_fee: float = 0.0
    liq_rec: float = 0.0
    shortfall: float = 0.0
    amortized_usd: float = 0.0
    status: str = "active"
    margin_calls: int = 0
    renewals: int = 0
    end_idx: int = field(init=False)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def _now_ts(self, i: int) -> pd.Timestamp:
        return self.df.index[i]

    def _log(self, kind: str, i: int, **kw: Any) -> None:
        if self.config.get("LOG_EVENTS", False):
            rec = {"t": self._now_ts(i), "i": int(i), "event": kind} | kw
            self.events.append(rec)

    def __post_init__(self):
        cfg = self.config
        self.init_ltv = cfg["INIT_LTV"]
        self.mc_ltv = cfg["MC_LTV"]
        self.liq_ltv = cfg["LIQ_LTV"]
        self.rate = cfg["INT_BTC"] if self.coin == "BTC" else cfg["INT_ETH"]

        self.start_ts = self.df.index[self.start_idx]
        self.start_price = float(self.df.iloc[self.start_idx][self.coin])

        self.principal_usd = self.principal_mxn / cfg["MXN_USD"]
        tot_init_coll = (self.principal_usd / self.init_ltv) / self.start_price
        self.initial_total_collateral_coins = float(tot_init_coll)
        self.collateral_coin_amt = float(tot_init_coll)

        self.end_idx = self.start_idx
        self._log(
            "start",
            self.start_idx,
            price=self.start_price,
            principal_usd=self.principal_usd,
            collateral_coins=self.collateral_coin_amt,
        )

    def run(self) -> Dict[str, Any]:
        cfg = self.config
        close = self.df[self.coin].values.astype(float)
        n = len(close)
        idx = self.start_idx

        matur = pad_loc(self.df.index, self.df.index[idx] + timedelta(days=365))
        matur = min(matur, n - 1)

        while self.status == "active":
            price = close[idx]
            mc_p = self.principal_usd / (self.collateral_coin_amt * self.mc_ltv)
            lq_p = self.principal_usd / (self.collateral_coin_amt * self.liq_ltv)

            # Binary search para siguiente margin call
            lo, hi = idx, n
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                mn, _ = self.tree.range_min(idx, mid + 1)
                if mn < mc_p:
                    hi = mid
                else:
                    lo = mid

            nxt = min(
                v for v in [hi if hi < n else None, matur, n - 1] if v is not None
            )

            minutes = max(nxt - idx, 0)
            self.accrued_interest += (
                self.principal_usd * self.rate * (minutes / MINUTES_PER_YEAR)
            )
            if minutes:
                self._log("accrue", nxt, minutes=minutes, accrued=self.accrued_interest)
            idx = nxt
            self.end_idx = idx
            price = close[idx]

            # Caso 1: vencimiento
            if idx >= matur:
                p_renew = cfg["P_RENEW_INIT"] ** (self.renewals + 1)
                if random.random() < p_renew and idx + MINUTES_PER_YEAR < n:
                    self.renewals += 1
                    matur = min(idx + MINUTES_PER_YEAR, n - 1)
                    self._log("renew", idx, renewals=self.renewals)
                    continue
                self.interest_usd += self.accrued_interest
                self.accrued_interest = 0.0
                self.status = "repaid_maturity"
                self._log("close_maturity", idx, interest_usd=self.interest_usd)
                break

            # Caso 2: Margin Call
            if hi < n and idx == hi:
                self.margin_calls += 1
                self._log(
                    "margin_call", idx, price=price, margin_calls=self.margin_calls
                )

                p_add = cfg["P_ADD_INIT"] ** self.margin_calls
                p_amort = cfg["P_AMORT_INIT"] ** self.margin_calls
                u = random.random()

                if u < p_add:
                    target_val_usd = self.principal_usd / self.init_ltv
                    curr_val_usd = price * self.collateral_coin_amt
                    extra_usd = max(target_val_usd - curr_val_usd, 0.0)
                    if extra_usd > 0:
                        extra_coins = extra_usd / price
                        self.collateral_coin_amt += extra_coins
                        self._log(
                            "add_collateral",
                            idx,
                            extra_usd=extra_usd,
                            extra_coins=extra_coins,
                            new_collateral=self.collateral_coin_amt,
                        )
                    continue

                if u < p_add + p_amort:
                    self.interest_usd += self.accrued_interest
                    self.accrued_interest = 0.0
                    new_principal = price * self.collateral_coin_amt * self.init_ltv
                    amort = max(self.principal_usd - new_principal, 0.0)
                    if amort > 0:
                        self.principal_usd -= amort
                        self.amortized_usd += amort
                    self._log(
                        "amortize",
                        idx,
                        amort_usd=amort,
                        new_principal_usd=self.principal_usd,
                    )
                    continue

                # Ignorar → buscar liquidación
                lo2, hi2 = idx, n
                while lo2 + 1 < hi2:
                    mid2 = (lo2 + hi2) // 2
                    mn2, _ = self.tree.range_min(idx, mid2 + 1)
                    if mn2 < lq_p:
                        hi2 = mid2
                    else:
                        lo2 = mid2
                lq = hi2 if hi2 < n else idx

                minutes2 = max(lq - idx, 0)
                self.accrued_interest += (
                    self.principal_usd * self.rate * (minutes2 / MINUTES_PER_YEAR)
                )
                idx = lq
                self.end_idx = idx
                price = close[idx]

                coll_val = price * self.collateral_coin_amt
                self.liq_fee = coll_val * cfg["LIQUIDATION_FEE_RATE"]
                self.liq_rec = max(coll_val - self.liq_fee, 0.0)
                self.shortfall = max(self.principal_usd - self.liq_rec, 0.0)
                self.interest_usd += self.accrued_interest
                self.accrued_interest = 0.0

                self.status = (
                    "liquidated_full"
                    if self.shortfall == 0.0
                    else "liquidated_shortfall"
                )
                self._log(
                    "liquidate",
                    idx,
                    price=price,
                    liq_fee=self.liq_fee,
                    liq_rec=self.liq_rec,
                    shortfall=self.shortfall,
                    status=self.status,
                )
                break

            # Caso 3: fin de datos
            self.interest_usd += self.accrued_interest
            self.accrued_interest = 0.0
            self.status = "repaid_end_of_data"
            self._log("close_eod", idx, interest_usd=self.interest_usd)
            break

        # Salida
        self.end_price = float(close[self.end_idx])
        self.end_ts = self.df.index[self.end_idx]
        cash_inflow = self.interest_usd + self.liq_fee + self.liq_rec
        brute_profit = self.interest_usd + self.liq_fee
        duration = self.end_idx - self.start_idx

        out: Dict[str, Any] = {
            "loan_id": self.loan_id,
            "principal_mxn": self.principal_mxn,
            "principal_usd": round(self.principal_usd, 2),
            "coin": self.coin,
            "status": self.status,
            "margin_calls": self.margin_calls,
            "duration_days": round(duration / 1_440, 2),
            "interest_usd": round(self.interest_usd, 2),
            "liquidation_fee_usd": round(self.liq_fee, 2),
            "liquidation_rec_usd": round(self.liq_rec, 2),
            "cash_inflow_usd": round(cash_inflow, 2),
            "brute_profit_usd": round(brute_profit, 2),
            "shortfall_usd": round(self.shortfall, 2),
            "amortized_usd": round(self.amortized_usd, 2),
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "init_ltv": round(self.init_ltv, 4),
            "mc_ltv": round(self.mc_ltv, 4),
            "liq_ltv": round(self.liq_ltv, 4),
            "collateral_coin_init": round(self.initial_total_collateral_coins, 10),
            "collateral_coin_final": round(self.collateral_coin_amt, 10),
        }
        if cfg.get("LOG_EVENTS", False):
            out["events"] = self.events
        return out


# ── Simulación de portafolio ────────────────────────────────────────────────


def simulate_portfolio(
    price_df: pd.DataFrame,
    config: Dict[str, Any] | None = None,
    coins: tuple[str, ...] = ("BTC", "ETH"),
) -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
    """Simula un portafolio de préstamos sobre datos históricos.

    Parameters
    ----------
    price_df : DataFrame con índice datetime y columnas de precio por coin.
    config : diccionario de configuración (usa DEFAULT_CONFIG si None).
    coins : tupla de monedas a simular.

    Returns
    -------
    (records, summary_df)
    """
    cfg = config or DEFAULT_CONFIG
    random.seed(cfg["SEED"])
    np.random.seed(cfg["SEED"])

    records: List[Dict[str, Any]] = []

    for coin in coins:
        arr = price_df[coin].values.astype(float)
        tree = SegmentTree(arr.tolist())

        horizon = MINUTES_PER_YEAR
        starts = list(range(max(len(arr) - horizon - 1, 1)))
        per_coin = cfg["N_LOANS"] // len(coins)

        for _ in range(per_coin):
            amt = random.choices(
                cfg["LOAN_SIZES_MXN"], weights=cfg["SIZE_WEIGHTS"], k=1
            )[0]
            st = random.choice(starts)
            loan = Loan(len(records) + 1, coin, amt, st, price_df, tree, cfg)
            records.append(loan.run())

    return records, pd.DataFrame(records)
