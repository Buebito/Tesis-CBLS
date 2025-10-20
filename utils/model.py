# crypto_loan_simulator.py
"""
Simula un portafolio de préstamos respaldados por cripto (colateral BTC/ETH)
empleando Segment Trees para saltar directamente al próximo evento crítico
(margin‑call o liquidación) sin iterar minuto a minuto.

Parámetros base (sin rehypothecation)
    • LTV inicial: 50 %
    • Margin-Call LTV: 72 %
    • Liquidación LTV: 80 %
    • Tasa anual simple: 18 % (BTC) / 20 % (ETH)
    • Comisión de liquidación: 8 % del valor del colateral capturado.

Parámetros adicionales (rehypothecation)
    • Re‑hipotecación opcional del 0 %, 10 %, 20 %, 30 % o 40 % del colateral.
      Cada nivel ajusta los umbrales LTV y la tasa de interés conforme a
      PARAM_TABLE.
    • Nueve tamaños de préstamo en MXN (200k → 2 M) con pesos configurables.
    • El prestatario ya no puede amortizar parcialmente; solo puede:
          – Aportar colateral adicional (prob. inicial 25 %, que decae 25 %
            tras cada margin‑call), o
          – Permitir liquidación.
    • Renovación anual opcional: prob. inicial 50 %, que decae con cada
      renovación.
    • Ganancias/pérdidas de la porción rehypotecada se calculan utilizando
      señales técnicas (RSI, MA20/50, MACD) y se reportan al cierre.
    • evaluate_loan_sizes() permite estimar ROI por tamaño de préstamo
      incorporando la tarifa de custodia (0.4 % por transacción).

Salidas
    • loan_log — lista[dict] con el historial completo de cada préstamo.
    • summary_df — DataFrame con métricas agregadas de ingresos y riesgo.
"""

from __future__ import annotations
import random, numpy as np, pandas as pd, ta
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Dict, List

# ── CONFIG ───────────────────────────────────────────────────────────────────
CONFIG: Dict[str, float | List[int] | str] = {
    "MXN_USD": 19.0,
    # valores base (rehypo = 0 %)
    "BASE_LTV": 0.50,
    "BASE_MC": 0.72,
    "BASE_LIQ": 0.80,
    "BASE_INT_BTC": 0.18,
    "BASE_INT_ETH": 0.20,
    "LIQUIDATION_FEE_RATE": 0.08,
    "P_ADD_INIT": 0.25,
    "P_RENEW_INIT": 0.50,
    # tamaños de préstamo MXN
    "LOAN_SIZES_MXN": [200_000, 500_000, 750_000, 1_000_000,
                        1_250_000, 1_500_000, 1_750_000, 2_000_000],
    "SIZE_WEIGHTS": [1]*8,  # uniformes; editar si se desea
    "PRICE_CSV": "prices.csv",
    "N_LOANS": 10000,
    "SEED": 42,
}

# niveles de re‑hipotecación permitidos
REH_LEVELS = [0.0, 0.4]

# PARAM_TABLE: (LTV_inicial, MC_LTV, LIQ_LTV, Δtasa)
PARAM_TABLE = {
    0.0: (0.50, 0.72, 0.80,  0.0000),   # sin descuento
    #0.1: (0.49, 0.71, 0.79, -0.0026),   # −0.26 pp
    #0.2: (0.47, 0.70, 0.78, -0.0052),   # −0.52 pp
    #0.3: (0.45, 0.68, 0.77, -0.0078),   # −0.78 pp
    0.4: (0.43, 0.66, 0.75, -0.0104),   # −1.04 pp
}

random.seed(CONFIG["SEED"]); np.random.seed(CONFIG["SEED"])

# ── SegmentTree / data helpers (unchanged) ───────────────────────────────────
class SegmentTree:
    __slots__ = ("l", "r", "mid", "val", "ind", "L", "R")
    def __init__(self, arr, l=0, r=None):
        if r is None:
            r = len(arr)
        self.l, self.r = l, r; self.mid = (l + r) // 2
        self.val, self.ind = arr[l], l
        if r - l > 1:
            self.L = SegmentTree(arr, l, self.mid); self.R = SegmentTree(arr, self.mid, r)
            if self.L.val <= self.R.val:
                self.val, self.ind = self.L.val, self.L.ind
            else:
                self.val, self.ind = self.R.val, self.R.ind
        else:
            self.L = self.R = None

    def range_min(self, ql, qr):
        if qr <= self.l or ql >= self.r:
            return float("inf"), -1
        if ql <= self.l and qr >= self.r:
            return self.val, self.ind
        lv, li = self.L.range_min(ql, qr) if self.L else (float("inf"), -1)
        rv, ri = self.R.range_min(ql, qr) if self.R else (float("inf"), -1)
        return (lv, li) if lv <= rv else (rv, ri)


def load_price_df(p: str | Path) -> pd.DataFrame:
    return pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()


def align_series(df, coin):
    return df[coin].values.astype(float), df.index.to_list()


def pad_loc(idx, ts):
    if ts in idx:
        return idx.get_loc(ts)
    loc = idx.get_indexer([ts], method="pad")[0]
    return 0 if loc == -1 else int(loc)

# ── TA signals ────────────────────────────────────────────────────────────

def build_signals(df, coin):
    c = df[coin]; h = df[f"{coin}_HIGH"]; l = df[f"{coin}_LOW"]
    d = pd.DataFrame({"c": c, "h": h, "l": l})
    d["RSI"] = ta.momentum.rsi(d.c, 14)
    d["MA20"] = ta.trend.sma_indicator(d.c, 20)
    d["MA50"] = ta.trend.sma_indicator(d.c, 50)
    d["MACD"] = ta.trend.macd(d.c)
    d["MACDs"] = ta.trend.macd_signal(d.c)

    def _sig(r):
        return "BUY" if sum([r.MA20 > r.MA50, r.MACD > r.MACDs, r.RSI < 30]) >= 1 else (
            "SELL" if r.RSI > 60 else "HOLD")

    return d.apply(_sig, axis=1).values

# ── RehypoAccount ──────────────────────────────────────────────────────────

@dataclass
class RehypoAccount:
    coins: float; cash: float; trades: int = 0
    
    def buy_all(self, p):
        if self.cash <= 0:
            return
        self.coins += self.cash / p; self.cash = 0; self.trades += 1

    def sell_all(self, p):
        if self.coins <= 0:
            return
        self.cash += self.coins * p; self.coins = 0; self.trades += 1

    def value(self, p):
        return self.cash + self.coins * p

# ── Clase Loan ────────────────────────────────────────────────────────────
@dataclass
class Loan:
    loan_id: int
    coin: str
    principal_mxn: int
    start_idx: int
    df: pd.DataFrame
    tree: SegmentTree
    signals: np.ndarray

    # Campos calculados
    reh_f: float = field(init=False)
    principal_usd: float = field(init=False)
    collateral_coin_amt: float = field(init=False)
    init_ltv: float = field(init=False)
    mc_ltv: float = field(init=False)
    lq_ltv: float = field(init=False)
    rate:    float = field(init=False)

    # Estado dinámico
    p_add_base: float = CONFIG["P_ADD_INIT"]
    reh:            RehypoAccount = field(init=False)
    accrued_interest: float = 0
    interest_usd:     float = 0
    liq_fee:          float = 0
    liq_rec:          float = 0
    reh_gain:         float = 0
    shortfall:        float = 0

    status:        str = "active"
    margin_calls:  int = 0
    renewals:      int = 0
    end_idx:       int = field(init=False)

    # ── Inicialización ────────────────────────────────────────────────
    def __post_init__(self):
        # Parámetros por rehypo
        self.reh_f = random.choice(REH_LEVELS)
        self.init_ltv, self.mc_ltv, self.lq_ltv, delta = PARAM_TABLE[self.reh_f]
        base_rate = CONFIG["BASE_INT_BTC"] if self.coin == "BTC" else CONFIG["BASE_INT_ETH"]
        self.rate = base_rate + delta

        # Timestamp y precio inicial
        self.start_ts = self.df.index[self.start_idx]
        self.start_price = float(self.df.iloc[self.start_idx][self.coin])

        # Principal y colateral TOTAL necesario al LTV inicial (antes de rehypo)
        self.principal_usd = self.principal_mxn / CONFIG["MXN_USD"]
        tot_init_coll = (self.principal_usd / self.init_ltv) / self.start_price
        self.initial_total_collateral_coins = tot_init_coll

        # Separación de rehypo (una sola vez)
        reh_coins = tot_init_coll * self.reh_f
        self.collateral_coin_amt = tot_init_coll - reh_coins
        self.reh = RehypoAccount(coins=0.0, cash=reh_coins * self.start_price)

        self.end_idx = self.start_idx

    # ── Trading de la cuenta rehypotecada ─────────────────────────────
    def _trade(self, price: float, idx: int):
        sig = self.signals[idx]
        if sig == "BUY":
            self.reh.buy_all(price)
        elif sig == "SELL":
            self.reh.sell_all(price)

    # ── Ejecución del préstamo completo ───────────────────────────────
    def run(self) -> Dict:
        close = self.df[self.coin].values.astype(float)
        n     = len(close)
        idx   = self.start_idx
        matur = pad_loc(self.df.index, self.df.index[idx] + timedelta(days=365))
        matur = min(matur, n - 1)

        while self.status == "active":
            price = close[idx]
            self._trade(price, idx)

            # Precios umbral para MC y liquidación
            mc_p = self.principal_usd / (self.collateral_coin_amt * self.mc_ltv)
            lq_p = self.principal_usd / (self.collateral_coin_amt * self.lq_ltv)

            # Buscar el próximo punto donde el precio rompe mc_p
            lo, hi = idx, n
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                hi  = mid if self.tree.range_min(idx, mid + 1)[0] < mc_p else hi
                lo  = mid if self.tree.range_min(idx, mid + 1)[0] >= mc_p else lo

            nxt = min(v for v in [hi if hi < n else None, matur, n - 1] if v is not None)
            self.accrued_interest += self.principal_usd * self.rate * (nxt - idx) / 525_600
            idx = nxt
            self.end_idx = idx
            price = close[idx]

            # ── Caso 1: llegó al vencimiento ───────────────────────
            if idx >= matur:
                if random.random() < CONFIG["P_RENEW_INIT"] ** (self.renewals + 1) and idx + 525_600 < n:
                    self.renewals += 1
                    matur += 525_600
                    continue
                self.interest_usd += self.accrued_interest
                self.status = "repaid_maturity"
                break

            # ── Caso 2: Margin Call ────────────────────────────────
            if hi < n and idx == hi:
                self.margin_calls += 1

                # El cliente decide añadir colateral
                if random.random() < CONFIG["P_ADD_INIT"] ** self.margin_calls:
                    target = self.principal_usd / self.init_ltv
                    extra  = target - price * self.collateral_coin_amt
                    self.collateral_coin_amt += extra / price
                    continue

                # Si no añade colateral → liquidar
                lo2, hi2 = idx, n
                while lo2 + 1 < hi2:
                    mid2 = (lo2 + hi2) // 2
                    hi2  = mid2 if self.tree.range_min(idx, mid2 + 1)[0] < lq_p else hi2
                    lo2  = mid2 if self.tree.range_min(idx, mid2 + 1)[0] >= lq_p else lo2
                lq = hi2 if hi2 < n else idx

                self.accrued_interest += self.principal_usd * self.rate * (lq - idx) / 525_600
                idx = lq
                self.end_idx = idx
                price = close[idx]

                coll_val      = price * self.collateral_coin_amt
                self.liq_fee  = coll_val * CONFIG["LIQUIDATION_FEE_RATE"]
                self.liq_rec  = max(coll_val - self.liq_fee, 0)
                self.shortfall = max(self.principal_usd - self.liq_rec, 0)
                self.interest_usd += self.accrued_interest
                self.status = ("liquidated_full" if self.shortfall == 0
                               else "liquidated_shortfall")
                break

            # ── Caso 3: final de datos ─────────────────────────────
            self.interest_usd += self.accrued_interest
            self.status = "repaid_end_of_data"
            break

        # ── Cerrar la posición rehypotecada ─────────────────────────
        self.reh.sell_all(close[self.end_idx])
        init_reh   = self.principal_usd / self.init_ltv * self.reh_f
        self.reh_gain = self.reh.value(close[self.end_idx]) - init_reh

        cash_inflow = self.interest_usd + self.liq_fee + self.liq_rec + self.reh_gain
        net_profit  = self.interest_usd + self.liq_fee + self.reh_gain
        duration    = self.end_idx - self.start_idx


        self.end_price = float(self.df[self.coin].values.astype(float)[self.end_idx])
        self.end_ts    = self.df.index[self.end_idx]
        collateral_coin_final = self.collateral_coin_amt



        return {
            "loan_id":              self.loan_id,
            "principal_mxn":        self.principal_mxn,
            "principal_usd":        round(self.principal_usd, 2),
            "coin":                 self.coin,
            "rehypo_frac":          self.reh_f,
            "status":               self.status,
            "margin_calls":         self.margin_calls,
            "duration_days":        round(duration / 1_440, 2),
            "interest_usd":         round(self.interest_usd, 2),
            "liquidation_fee_usd":  round(self.liq_fee, 2),
            "liquidation_rec_usd":  round(self.liq_rec, 2),
            "rehypo_gain_usd":      round(self.reh_gain, 2),
            "cash_inflow_usd":      round(cash_inflow, 2),  # antes revenue_usd
            "net_profit_usd":       round(net_profit, 2),
            "shortfall_usd":        round(self.shortfall, 2), 
            "start_ts": self.start_ts,
            "end_ts":   self.end_ts,
            "start_price": self.start_price,
            "end_price":   self.end_price,
            "init_ltv":        round(self.init_ltv, 4),
            "mc_ltv":          round(self.mc_ltv, 4),
            "liq_ltv":         round(self.lq_ltv, 4),
            "collateral_btc_init_total": round(self.initial_total_collateral_coins, 10),
            "collateral_btc_final":      round(collateral_coin_final, 10)
        }


# ── Creation helper & simulate_portfolio ─────────────────────────────────


def _create_loan(lid, coin, df, tree, starts, sig):
    amt = random.choices(CONFIG["LOAN_SIZES_MXN"], weights=CONFIG["SIZE_WEIGHTS"], k=1)[0]
    st = random.choice(starts)
    return Loan(lid, coin, amt, st, df, tree, sig)

def simulate_portfolio():
    df   = load_price_df(CONFIG["PRICE_CSV"])
    logs: List[Dict] = []

    for coin in ("BTC", "ETH"):
        sig       = build_signals(df, coin)
        arr, _    = align_series(df, coin)
        tree      = SegmentTree(arr.tolist())

        horizon   = 525_600                      # minutos ≈ 1 año
        starts    = list(range(len(arr) - horizon - 1))
        per_coin  = CONFIG["N_LOANS"] // 2

        for _ in range(per_coin):
            principal = random.choices(
                CONFIG["LOAN_SIZES_MXN"],
                weights=CONFIG["SIZE_WEIGHTS"],
                k=1
            )[0]
            st   = random.choice(starts)
            loan = Loan(len(logs) + 1, coin, principal, st, df, tree, sig)
            logs.append(loan.run())

    return logs, pd.DataFrame(logs)


# ─────────────────────────────────────────────────────────────────────────────
# ROI helper
# ─────────────────────────────────────────────────────────────────────────────

def _set_seed(s:int):
    random.seed(s); np.random.seed(s)

def evaluate_loan_sizes(runs:int=10, loans_per_run:int=100,
                        custodian_rate:float=0.004, base_seed:int=1234) -> pd.DataFrame:
    import pandas as pd
    res = []; origN = CONFIG["N_LOANS"]
    for r in range(runs):
        CONFIG["N_LOANS"] = loans_per_run; _set_seed(base_seed + r)
        _, df = simulate_portfolio()
        # costo de custodia sobre colateral inicial (plataforma)
        df["custody_fee_usd"] = (df["principal_usd"] / df["init_ltv"]) * custodian_rate
        # ingreso neto plataforma = net_profit_usd - costos de custodia
        df["net_income_usd"] = df["net_profit_usd"] - df["custody_fee_usd"]
        df["roi_pct"] = df["net_income_usd"] / df["principal_usd"] * 100
        res.append(df[["principal_mxn","net_income_usd","roi_pct"]])
    CONFIG["N_LOANS"] = origN
    full = pd.concat(res, ignore_index=True)
    return (
        full.groupby("principal_mxn")
            .agg(mean_net_income_usd=("net_income_usd","mean"),
                 std_net_income_usd=("net_income_usd","std"),
                 mean_roi_pct=("roi_pct","mean"),
                 std_roi_pct=("roi_pct","std"))
            .sort_index()
    )
