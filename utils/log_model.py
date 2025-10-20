from __future__ import annotations
import numpy as np, pandas as pd, random, ta
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Dict, List

from model import simulate_portfolio, SegmentTree, Loan, CONFIG, REH_LEVELS, PARAM_TABLE, RehypoAccount, pad_loc, build_signals, load_price_df

random.seed(CONFIG["SEED"]); np.random.seed(CONFIG["SEED"])

# ── (Reutilizamos CONFIG y helpers de la v2; añade estos campos si quieres vol‑adj) ──
CONFIG.setdefault("VOL_WINDOW_MIN", 43200)
CONFIG.setdefault("VOL_ADJ_K_MC",   0.0)
CONFIG.setdefault("VOL_ADJ_K_LIQ",  0.0)


def load_price_df_log(p: str | Path) -> pd.DataFrame:
    return pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()


# ── SegmentTree “normal” sirve en log‑espacio también ─────────────────────────
class LogSegmentTree(SegmentTree):
    """Semánticamente separado; internamente es el mismo SegmentTree.
    Lo usamos sobre arrays de log‑precio."""
    pass


# ── Clase LoanLogSpace (drawdown relativo en log) ─────────────────────────────
@dataclass
class LoanLogSpace:
    loan_id: int
    coin: str
    principal_mxn: int
    start_idx: int
    df_lin: pd.DataFrame          # precios lineales
    df_log: pd.DataFrame          # precios logarítmicos
    tree_log: SegmentTree         # SegmentTree sobre log-precios absolutos
    signals: np.ndarray

    # caches
    close_lin: np.ndarray = field(init=False)
    close_log: np.ndarray = field(init=False)

    # parámetros del préstamo
    reh_f: float = field(init=False)
    principal_usd: float = field(init=False)
    collateral_coin_amt: float = field(init=False)
    init_ltv: float = field(init=False)
    mc_ltv: float = field(init=False)
    lq_ltv: float = field(init=False)
    rate:    float = field(init=False)

    # estado
    p_add_base: float = CONFIG["P_ADD_INIT"]
    reh: RehypoAccount = field(init=False)
    accrued_interest: float = 0.0
    interest_usd:     float = 0.0
    liq_fee:          float = 0.0
    liq_rec:          float = 0.0
    reh_gain:         float = 0.0
    shortfall:        float = 0.0

    status:       str = "active"
    margin_calls: int = 0
    renewals:     int = 0
    end_idx:      int = field(init=False)

    # baseline para drawdown relativo
    baseline_idx: int = field(init=False)
    baseline_log: float = field(init=False)

    def __post_init__(self):
        self.close_lin = self.df_lin[self.coin].values.astype(float)
        self.close_log = np.log(self.close_lin)

        self.reh_f = random.choice(REH_LEVELS)
        self.init_ltv, base_mc, base_lq, delta = PARAM_TABLE[self.reh_f]

        # Ajuste por volatilidad (opcional)
        if CONFIG["VOL_ADJ_K_MC"] or CONFIG["VOL_ADJ_K_LIQ"]:
            sigma = self.df_log[self.coin].diff().rolling(CONFIG["VOL_WINDOW_MIN"]).std().iloc[self.start_idx]
            sigma = 0.0 if np.isnan(sigma) else float(sigma)
            base_mc = min(max(base_mc - CONFIG["VOL_ADJ_K_MC"] * sigma, 0.10), 0.99)
            base_lq = min(max(base_lq - CONFIG["VOL_ADJ_K_LIQ"] * sigma, 0.10), 0.99)
        self.mc_ltv, self.lq_ltv = base_mc, base_lq

        base_rate = CONFIG["BASE_INT_BTC"] if self.coin == "BTC" else CONFIG["BASE_INT_ETH"]
        self.rate = base_rate + delta

        p0 = self.close_lin[self.start_idx]
        self.principal_usd = self.principal_mxn / CONFIG["MXN_USD"]
        self.collateral_coin_amt = (self.principal_usd / self.init_ltv) / p0

        reh_coins = self.collateral_coin_amt * self.reh_f
        self.collateral_coin_amt *= (1 - self.reh_f)
        self.reh = RehypoAccount(coins=0, cash=reh_coins * p0)

        self.end_idx = self.start_idx
        self.baseline_idx = self.start_idx
        self.baseline_log = self.close_log[self.start_idx]

    # ── Helpers ─────────────────────────────────────────────────────
    def _trade(self, price: float, idx: int):
        sig = self.signals[idx]
        if sig == "BUY":
            self.reh.buy_all(price)
        elif sig == "SELL":
            self.reh.sell_all(price)

    def _mc_threshold_log(self) -> float:
        """Devuelve el log-price absoluto que representa el MC usando drawdown relativo.
        mc_p_abs: precio lineal al que el LTV llega a mc_ltv.
        baseline: precio lineal en baseline_idx.  Queremos que la comparación
        sea sobre log-precios, pero controlando el drawdown relativo.
        threshold_log = baseline_log + log(mc_p_abs / price_baseline)
        """
        price_base = self.close_lin[self.baseline_idx]
        mc_p_abs   = self.principal_usd / (self.collateral_coin_amt * self.mc_ltv)
        return self.baseline_log + np.log(mc_p_abs / price_base)

    def _lq_threshold_log(self) -> float:
        price_base = self.close_lin[self.baseline_idx]
        lq_p_abs   = self.principal_usd / (self.collateral_coin_amt * self.lq_ltv)
        return self.baseline_log + np.log(lq_p_abs / price_base)

    def _binary_search_first_breach(self, start: int, end: int, thr_log: float) -> int:
        """Binary search sobre el SegmentTree para hallar el primer índice
        donde el min(log_price) < thr_log."""
        lo, hi = start, end
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            mn, _ = self.tree_log.range_min(start, mid + 1)
            if mn < thr_log:
                hi = mid
            else:
                lo = mid
        return hi if hi < end else end

    # ── Ejecución ────────────────────────────────────────────────────
    def run(self) -> Dict:
        n   = len(self.close_lin)
        idx = self.start_idx
        matur = pad_loc(self.df_lin.index, self.df_lin.index[idx] + timedelta(days=365))
        matur = min(matur, n - 1)

        while self.status == "active":
            price = self.close_lin[idx]
            self._trade(price, idx)

            mc_thr_log = self._mc_threshold_log()
            lq_thr_log = self._lq_threshold_log()

            # Encuentra próximo MC
            nxt = self._binary_search_first_breach(idx, n, mc_thr_log)
            # min entre ese, el maturity y el fin de datos
            nxt = min(v for v in [nxt, matur, n - 1] if v is not None)

            # Acumula intereses hasta nxt
            self.accrued_interest += self.principal_usd * self.rate * (nxt - idx) / 525_600
            idx = nxt
            self.end_idx = idx
            price = self.close_lin[idx]

            # Caso 1: vencimiento
            if idx >= matur:
                if random.random() < CONFIG["P_RENEW_INIT"] ** (self.renewals + 1) and idx + 525_600 < n:
                    self.renewals += 1
                    matur += 525_600
                    # reset baseline a la nueva fecha
                    self.baseline_idx = idx
                    self.baseline_log = self.close_log[idx]
                    continue
                self.interest_usd += self.accrued_interest
                self.status = "repaid_maturity"
                break

            # Caso 2: margin call alcanzado
            # Verificamos si hi realmente fue un breach
            if idx < n and self.tree_log.range_min(idx, idx + 1)[0] < mc_thr_log:
                self.margin_calls += 1
                # decide si agrega colateral
                if random.random() < CONFIG["P_ADD_INIT"] ** self.margin_calls:
                    target = self.principal_usd / self.init_ltv
                    extra  = target - price * self.collateral_coin_amt
                    self.collateral_coin_amt += extra / price
                    # Nuevo baseline después de aportar colateral
                    self.baseline_idx = idx
                    self.baseline_log = self.close_log[idx]
                    continue

                # Si no agrega → liquidar (buscar punto lq)
                lq_idx = self._binary_search_first_breach(idx, n, lq_thr_log)
                lq_idx = min(lq_idx, n - 1)

                self.accrued_interest += self.principal_usd * self.rate * (lq_idx - idx) / 525_600
                idx = lq_idx
                self.end_idx = idx
                price = self.close_lin[idx]

                coll_val       = price * self.collateral_coin_amt
                self.liq_fee   = coll_val * CONFIG["LIQUIDATION_FEE_RATE"]
                self.liq_rec   = max(coll_val - self.liq_fee, 0)
                self.shortfall = max(self.principal_usd - self.liq_rec, 0)
                self.interest_usd += self.accrued_interest
                self.status = "liquidated_full" if self.shortfall == 0 else "liquidated_shortfall"
                break

            # Caso 3: fin de datos
            self.interest_usd += self.accrued_interest
            self.status = "repaid_end_of_data"
            break

        # Cerrar rehypo
        self.reh.sell_all(self.close_lin[self.end_idx])
        init_reh   = self.principal_usd / self.init_ltv * self.reh_f
        self.reh_gain = self.reh.value(self.close_lin[self.end_idx]) - init_reh

        cash_inflow = self.interest_usd + self.liq_fee + self.liq_rec + self.reh_gain
        net_profit  = self.interest_usd + self.liq_fee + self.reh_gain
        duration    = self.end_idx - self.start_idx

        return {
            "loan_id":             self.loan_id,
            "principal_mxn":       self.principal_mxn,
            "principal_usd":       round(self.principal_usd, 2),
            "coin":                self.coin,
            "rehypo_frac":         self.reh_f,
            "status":              self.status,
            "margin_calls":        self.margin_calls,
            "duration_days":       round(duration / 1_440, 2),
            "interest_usd":        round(self.interest_usd, 2),
            "liquidation_fee_usd": round(self.liq_fee, 2),
            "liquidation_rec_usd": round(self.liq_rec, 2),
            "rehypo_gain_usd":     round(self.reh_gain, 2),
            "cash_inflow_usd":     round(cash_inflow, 2),
            "net_profit_usd":      round(net_profit, 2),
            "shortfall_usd":       round(self.shortfall, 2)
        }


# ── Simulación en log‑espacio ───────────────────────────────────────────────
def simulate_portfolio_log():
    df_lin = load_price_df(CONFIG["PRICE_CSV"])
    df_log = load_price_df_log("prices_log.csv")

    logs: List[Dict] = []

    for coin in ("BTC", "ETH"):
        sig = build_signals(df_lin, coin)
        close_lin = df_lin[coin].values.astype(float)
        close_log = np.log(close_lin)

        tree_log = LogSegmentTree(close_log.tolist())

        horizon  = 525_600
        starts   = list(range(len(close_lin) - horizon - 1))
        per_coin = CONFIG["N_LOANS"] // 2

        for _ in range(per_coin):
            amount = random.choices(CONFIG["LOAN_SIZES_MXN"], weights=CONFIG["SIZE_WEIGHTS"], k=1)[0]
            st     = random.choice(starts)
            loan   = LoanLogSpace(len(logs) + 1, coin, amount, st, df_lin, df_log, tree_log, sig)
            logs.append(loan.run())

    return logs, pd.DataFrame(logs)
