"""
Simulador de préstamos respaldados por cripto (BTC/ETH) con Segment Tree

"""

from __future__ import annotations
import random
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any

# ── CONFIG ───────────────────────────────────────────────────────────────────
CONFIG: Dict[str, Any] = {
    "MXN_USD": 19.0,
    # LTVs base
    "INIT_LTV": 0.50,   # LTV objetivo tras MC (y LTV inicial)
    "MC_LTV":   0.72,
    "LIQ_LTV":  0.80,
    # Tasas simples anuales
    "INT_BTC": 0.18,
    "INT_ETH": 0.20,
    # Comisión sobre colateral liquidado
    "LIQUIDATION_FEE_RATE": 0.08,
    # Probabilidades base de decisión en MC (decaen con cada MC: ^margin_calls)
    "P_ADD_INIT":     0.25,  # agregar colateral
    "P_AMORT_INIT":   0.25,  # amortizar principal
    # Renovación (decisión anual)
    "P_RENEW_INIT":   0.50,
    # tamaños de préstamo en MXN
    "LOAN_SIZES_MXN": [200_000, 500_000, 750_000, 1_000_000, 1_250_000, 1_500_000, 1_750_000, 2_000_000],
    "SIZE_WEIGHTS":   [1]*8,  # uniformes; editar si se desea
    # datos de precios
    "PRICE_CSV":      "data/processed/historic_prices.csv",  # columnas esperadas: date,BTC,ETH (minutales)
    # simulación
    "N_LOANS":        10_000,
    "SEED":           42,
    # logging detallado de eventos
    "LOG_EVENTS":     True,
}

MINUTES_PER_YEAR = 525_600
random.seed(CONFIG["SEED"]); np.random.seed(CONFIG["SEED"])

# ── SegmentTree / data helpers ───────────────────────────────────────────────
class SegmentTree:
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
        if qr <= self.l or ql >= self.r:
            return float("inf"), -1
        if ql <= self.l and qr >= self.r:
            return self.val, self.ind
        lv, li = self.L.range_min(ql, qr) if self.L else (float("inf"), -1)
        rv, ri = self.R.range_min(ql, qr) if self.R else (float("inf"), -1)
        return (lv, li) if lv <= rv else (rv, ri)


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

# ── Clase Loan ──────────────────────────────────────────────────────────────
@dataclass
class Loan:
    loan_id: int
    coin: str
    principal_mxn: int
    start_idx: int
    df: pd.DataFrame
    tree: SegmentTree

    # Parámetros derivados
    principal_usd: float = field(init=False)
    collateral_coin_amt: float = field(init=False)
    init_ltv: float = field(default=CONFIG["INIT_LTV"], init=False)
    mc_ltv:   float = field(default=CONFIG["MC_LTV"], init=False)
    liq_ltv:  float = field(default=CONFIG["LIQ_LTV"], init=False)
    rate:     float = field(init=False)

    # Estado dinámico
    accrued_interest: float = 0.0
    interest_usd:     float = 0.0
    liq_fee:          float = 0.0
    liq_rec:          float = 0.0
    shortfall:        float = 0.0
    amortized_usd:    float = 0.0

    status:        str = "active"
    margin_calls:  int = 0
    renewals:      int = 0
    end_idx:       int = field(init=False)

    # Logging
    events: List[Dict[str, Any]] = field(default_factory=list)

    def _now_ts(self, i: int) -> pd.Timestamp:
        return self.df.index[i]

    def _log(self, kind: str, i: int, **kw: Any) -> None:
        if CONFIG.get("LOG_EVENTS", False):
            rec = {"t": self._now_ts(i), "i": int(i), "event": kind} | kw
            self.events.append(rec)

    # ── Inicialización ────────────────────────────────────────────────
    def __post_init__(self):
        base_rate = CONFIG["INT_BTC"] if self.coin == "BTC" else CONFIG["INT_ETH"]
        self.rate = base_rate

        # Timestamp y precio inicial
        self.start_ts = self.df.index[self.start_idx]
        self.start_price = float(self.df.iloc[self.start_idx][self.coin])

        # Principal y colateral requerido al LTV inicial
        self.principal_usd = self.principal_mxn / CONFIG["MXN_USD"]
        tot_init_coll = (self.principal_usd / self.init_ltv) / self.start_price
        self.initial_total_collateral_coins = float(tot_init_coll)

        # Todo el colateral se mantiene como colateral (no rehípoteca)
        self.collateral_coin_amt = float(tot_init_coll)

        self.end_idx = self.start_idx
        self._log("start", self.start_idx, price=self.start_price,
                  principal_usd=self.principal_usd,
                  collateral_coins=self.collateral_coin_amt)

    # ── Ejecución del préstamo completo ───────────────────────────────
    def run(self) -> Dict[str, Any]:
        close = self.df[self.coin].values.astype(float)
        n     = len(close)
        idx   = self.start_idx

        matur = pad_loc(self.df.index, self.df.index[idx] + timedelta(days=365))
        matur = min(matur, n - 1)

        while self.status == "active":
            # Umbrales dinámicos con el principal/colateral vigentes
            price = close[idx]
            mc_p = self.principal_usd / (self.collateral_coin_amt * self.mc_ltv)
            lq_p = self.principal_usd / (self.collateral_coin_amt * self.liq_ltv)

            # Buscar el siguiente índice donde el mínimo cae por debajo de mc_p
            lo, hi = idx, n
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                mn, _ = self.tree.range_min(idx, mid + 1)
                if mn < mc_p:
                    hi = mid
                else:
                    lo = mid

            nxt = min(v for v in [hi if hi < n else None, matur, n - 1] if v is not None)

            # Devengar interés hasta el siguiente punto (nxt)
            minutes = max(nxt - idx, 0)
            self.accrued_interest += self.principal_usd * self.rate * (minutes / MINUTES_PER_YEAR)
            if minutes:
                self._log("accrue", nxt, minutes=minutes, accrued=self.accrued_interest)
            idx = nxt
            self.end_idx = idx
            price = close[idx]

            # ── Caso 1: llegó al vencimiento ───────────────────────
            if idx >= matur:
                # Decidir renovación
                if random.random() < (CONFIG["P_RENEW_INIT"] ** (self.renewals + 1)) and idx + MINUTES_PER_YEAR < n:
                    self.renewals += 1
                    matur = min(idx + MINUTES_PER_YEAR, n - 1)
                    self._log("renew", idx, renewals=self.renewals)
                    continue
                # Cierre por vencimiento
                self.interest_usd += self.accrued_interest; self.accrued_interest = 0.0
                self.status = "repaid_maturity"
                self._log("close_maturity", idx, interest_usd=self.interest_usd)
                break

            # ── Caso 2: Margin Call ────────────────────────────────
            if hi < n and idx == hi:
                self.margin_calls += 1
                self._log("margin_call", idx, price=price, margin_calls=self.margin_calls)

                # Probabilidades (decaen con # de MC)
                p_add   = CONFIG["P_ADD_INIT"]   ** self.margin_calls
                p_amort = CONFIG["P_AMORT_INIT"] ** self.margin_calls
                u = random.random()

                # 2.a) Agregar colateral
                if u < p_add:
                    target_val_usd = self.principal_usd / self.init_ltv
                    curr_val_usd   = price * self.collateral_coin_amt
                    extra_usd      = max(target_val_usd - curr_val_usd, 0.0)
                    if extra_usd > 0:
                        extra_coins = extra_usd / price
                        self.collateral_coin_amt += extra_coins
                        self._log("add_collateral", idx, extra_usd=extra_usd, extra_coins=extra_coins,
                                  new_collateral=self.collateral_coin_amt)
                    # continuar activo
                    continue

                # 2.b) Amortizar principal (pagar para bajar LTV)
                if u < p_add + p_amort:
                    # Consumar interés devengado hasta ahora
                    self.interest_usd += self.accrued_interest; self.accrued_interest = 0.0
                    # Nuevo principal objetivo para volver a INIT_LTV
                    new_principal = price * self.collateral_coin_amt * self.init_ltv
                    amort = max(self.principal_usd - new_principal, 0.0)
                    if amort > 0:
                        self.principal_usd -= amort
                        self.amortized_usd += amort
                    self._log("amortize", idx, amort_usd=amort, new_principal_usd=self.principal_usd,
                              ltv=self.principal_usd / (price * self.collateral_coin_amt))
                    # continuar activo
                    continue

                # 2.c) Ignorar → buscar liquidación
                lo2, hi2 = idx, n
                while lo2 + 1 < hi2:
                    mid2 = (lo2 + hi2) // 2
                    mn2, _ = self.tree.range_min(idx, mid2 + 1)
                    if mn2 < lq_p:
                        hi2 = mid2
                    else:
                        lo2 = mid2
                lq = hi2 if hi2 < n else idx

                # Interés hasta liquidación
                minutes2 = max(lq - idx, 0)
                self.accrued_interest += self.principal_usd * self.rate * (minutes2 / MINUTES_PER_YEAR)
                idx = lq; self.end_idx = idx; price = close[idx]

                # Liquidación de colateral
                coll_val      = price * self.collateral_coin_amt
                self.liq_fee  = coll_val * CONFIG["LIQUIDATION_FEE_RATE"]
                self.liq_rec  = max(coll_val - self.liq_fee, 0.0)
                self.shortfall = max(self.principal_usd - self.liq_rec, 0.0)
                # Consumar interés devengado
                self.interest_usd += self.accrued_interest; self.accrued_interest = 0.0

                self.status = ("liquidated_full" if self.shortfall == 0.0 else "liquidated_shortfall")
                self._log("liquidate", idx, price=price, liq_fee=self.liq_fee, liq_rec=self.liq_rec,
                          shortfall=self.shortfall, status=self.status)
                break

            # ── Caso 3: final de datos ─────────────────────────────
            self.interest_usd += self.accrued_interest; self.accrued_interest = 0.0
            self.status = "repaid_end_of_data"
            self._log("close_eod", idx, interest_usd=self.interest_usd)
            break

        # ── Totales de salida ─────────────────────────────────────────
        self.end_price = float(self.df[self.coin].values.astype(float)[self.end_idx])
        self.end_ts    = self.df.index[self.end_idx]
        collateral_coin_final = self.collateral_coin_amt

        cash_inflow = self.interest_usd + self.liq_fee + self.liq_rec
        brute_profit = self.interest_usd + self.liq_fee
        duration = self.end_idx - self.start_idx

        out: Dict[str, Any] = {
            "loan_id":              self.loan_id,
            "principal_mxn":        self.principal_mxn,
            "principal_usd":        round(self.principal_usd, 2),
            "coin":                 self.coin,
            "rehypo_frac":          0.0,  # compatibilidad
            "status":               self.status,
            "margin_calls":         self.margin_calls,
            "duration_days":        round(duration / 1_440, 2),
            "interest_usd":         round(self.interest_usd, 2),
            "liquidation_fee_usd":  round(self.liq_fee, 2),
            "liquidation_rec_usd":  round(self.liq_rec, 2),
            "cash_inflow_usd":      round(cash_inflow, 2),
            "brute_profit_usd":     round(brute_profit, 2),
            "shortfall_usd":        round(self.shortfall, 2),
            "amortized_usd":        round(self.amortized_usd, 2),
            "start_ts":             self.start_ts,
            "end_ts":               self.end_ts,
            "start_price":          self.start_price,
            "end_price":            self.end_price,
            "init_ltv":             round(self.init_ltv, 4),
            "mc_ltv":               round(self.mc_ltv, 4),
            "liq_ltv":              round(self.liq_ltv, 4),
            "collateral_btc_init_total": round(self.initial_total_collateral_coins, 10),
            "collateral_btc_final":       round(collateral_coin_final, 10),
        }

        if CONFIG.get("LOG_EVENTS", False):
            out["events"] = self.events
        return out

# ── Simulación de portafolio ────────────────────────────────────────────────

def _create_loan(lid: int, coin: str, df: pd.DataFrame, tree: SegmentTree, starts: List[int]):
    amt = random.choices(CONFIG["LOAN_SIZES_MXN"], weights=CONFIG["SIZE_WEIGHTS"], k=1)[0]
    st = random.choice(starts)
    return Loan(lid, coin, amt, st, df, tree)


def simulate_portfolio() -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
    """Devuelve (registros_por_prestamo, summary_df)."""
    df   = load_price_df(CONFIG["PRICE_CSV"])  # index: datetime, cols: BTC, ETH
    records: List[Dict[str, Any]] = []

    for coin in ("BTC", "ETH"):
        arr, _    = align_series(df, coin)
        tree      = SegmentTree(arr.tolist())

        horizon   = MINUTES_PER_YEAR
        starts    = list(range(max(len(arr) - horizon - 1, 1)))
        per_coin  = CONFIG["N_LOANS"] // 2

        for _ in range(per_coin):
            loan = _create_loan(len(records) + 1, coin, df, tree, starts)
            records.append(loan.run())

    return records, pd.DataFrame(records)

# ── Post‑hoc: rehípoteca fuera del core (opcional) ──────────────────────────

def rehypo_backtest_from_logs(results_df: pd.DataFrame, price_df: pd.DataFrame,
                              rehypo_frac: float = 0.1) -> pd.DataFrame:
    """
    Ejemplo mínimo de evaluación post‑hoc de rehípoteca **fuera** del simulador.
    Para cada préstamo, invierte `rehypo_frac` del *valor del colateral inicial*
    en buy‑and‑hold desde `start_ts` hasta `end_ts` y reporta PnL.

    NOTA: Este es un baseline (sin intradía, sin señales). Reemplaza por la
    estrategia deseada si necesitas explotar la trayectoria completa.
    """
    if not {"start_ts", "end_ts", "coin", "collateral_btc_init_total"}.issubset(results_df.columns):
        raise ValueError("results_df missing required columns for rehypo backtest")

    def _pnl_row(r: pd.Series) -> float:
        s, e = pd.Timestamp(r.start_ts), pd.Timestamp(r.end_ts)
        s_i = pad_loc(price_df.index, s)
        e_i = pad_loc(price_df.index, e)
        p0 = float(price_df.loc[price_df.index[s_i], r.coin])
        p1 = float(price_df.loc[price_df.index[e_i], r.coin])
        init_val = r.collateral_btc_init_total * rehypo_frac * p0
        final_val = r.collateral_btc_init_total * rehypo_frac * p1
        return final_val - init_val

    out = results_df.copy()
    out["rehypo_backtest_gain_usd"] = out.apply(_pnl_row, axis=1)
    return out

# ── Utilidad: comparación préstamo vs vender (lado cliente) ─────────────────

def compare_loan_vs_sell(results_df: pd.DataFrame) -> pd.DataFrame:
    need = [
        "coin", "status", "principal_usd", "interest_usd", "shortfall_usd",
        "start_price", "end_price", "collateral_btc_init_total", "rehypo_frac",
    ]
    for c in need:
        if c not in results_df.columns:
            raise ValueError(f"Falta columna requerida: {c}")

    df = results_df.copy()

    # Baseline: vender al inicio para obtener el principal
    df["btc_sold_baseline"] = df["principal_usd"] / df["start_price"]

    # BTC vendidos con préstamo (visión cliente)
    def btc_sold_with_loan(r: pd.Series) -> float:
        status = str(r["status"])
        if status.startswith("repaid"):  # repaid_maturity / repaid_end_of_data
            return (r["principal_usd"] + r["interest_usd"]) / r["end_price"]
        # Liquidación: se pierde el colateral inicial; si hay shortfall, vende extra al final
        sold = r["collateral_btc_init_total"]
        if r.get("shortfall_usd", 0) > 1e-9:
            sold += r["shortfall_usd"] / r["end_price"]
        return float(sold)

    df["btc_sold_loan"] = df.apply(btc_sold_with_loan, axis=1)
    df["delta_btc_saved"] = df["btc_sold_baseline"] - df["btc_sold_loan"]  # >0 conviene pedir

    # (opcional) aislar casos sin rehypo para medir el puro efecto “préstamo”
    if "rehypo_frac" in df.columns:
        df = df.query("rehypo_frac == 0.0").copy()

    return (
        df.groupby(["coin", "status"], as_index=False)
          .agg(loans=("delta_btc_saved", "size"),
               mean_delta_btc=("delta_btc_saved", "mean"),
               p50_delta_btc=("delta_btc_saved", "median"),
               p10=("delta_btc_saved", lambda s: s.quantile(0.10)),
               p90=("delta_btc_saved", lambda s: s.quantile(0.90)),
               neg_share=("delta_btc_saved", lambda s: (s < 0).mean()))
          .sort_values(["coin", "status"]))