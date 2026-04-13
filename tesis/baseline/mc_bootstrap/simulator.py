"""
tesis/baseline/mc_bootstrap/simulator.py
----------------------------------------
Simulación Monte Carlo de trayectorias futuras de precio mediante
bootstrap-KDE sobre retornos históricos.

Adaptado de precios_futuros/utils/bootstrap_mc.py y
bolsa_volatilidad/utils/bootstrap_mc.py.

Combina ambas versiones:
- Versión simple (sin bias) por defecto.
- BiasConfig opcional para stress testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional

import numpy as np
import pandas as pd


# ── Bias config para stress testing ──────────────────────────────────────────


@dataclass
class BiasConfig:
    """Configuración de sesgos para el muestreo bootstrap.

    Parameters
    ----------
    use_log_returns : usar log-retornos en lugar de porcentuales.
    drift_mode : "none" (sin ajuste), "zero" (re-centra a 0), "shift" (drift fijo).
    mu_shift_per_step : drift por step si drift_mode == "shift" (negativo = bajista).
    tilt_theta : exponential tilt (negativo = más peso a retornos negativos).
    pos_scale : factor de escala para retornos positivos (<1 encoge subidas).
    neg_scale : factor de escala para retornos negativos (>1 amplifica bajadas).
    clip_pos_q : cuantil para winsorizar cola positiva (e.g. 0.995).
    clip_neg_q : cuantil para winsorizar cola negativa.
    """

    use_log_returns: bool = True
    drift_mode: Literal["none", "zero", "shift"] = "none"
    mu_shift_per_step: float = 0.0
    tilt_theta: Optional[float] = None
    pos_scale: float = 1.0
    neg_scale: float = 1.0
    clip_pos_q: Optional[float] = None
    clip_neg_q: Optional[float] = None


# ── Utilidades estadísticas ──────────────────────────────────────────────────


def silverman_bandwidth(x: np.ndarray) -> float:
    """Regla de Silverman para ancho de banda KDE en 1D.

    h = 0.9 * min(std, IQR/1.34) * n^(-1/5)
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return 1e-8
    std = np.std(x, ddof=1)
    q75, q25 = np.percentile(x, [75, 25])
    iqr = q75 - q25
    sigma = min(std, iqr / 1.34) if iqr > 0 else std
    h = 0.9 * sigma * (n ** (-1.0 / 5.0))
    return max(h, 1e-8)


class KDEBootstrapSampler:
    """Bootstrap + KDE con opciones de tilt, winsorización y escalado asimétrico.

    Toma retornos históricos y genera retornos sintéticos:
    1. Elige retornos con reemplazo (opcionalmente ponderados con tilt).
    2. Agrega ruido ~ N(0, h^2) con h de Silverman.
    3. Aplica winsorización y escalado asimétrico.
    4. Ajusta drift si se configura.
    """

    def __init__(
        self, returns: pd.Series | np.ndarray, bias: Optional[BiasConfig] = None
    ):
        if isinstance(returns, pd.Series):
            clean = returns.dropna().astype(float).values
        else:
            clean = np.asarray(returns, dtype=float)
        self.returns = clean
        self.bias = bias or BiasConfig()
        self.kernel_std = silverman_bandwidth(clean)

        # Winsorización: umbrales precalculados
        self.pos_clip_val = None
        if self.bias.clip_pos_q is not None:
            pos = self.returns[self.returns > 0]
            if len(pos) > 0:
                self.pos_clip_val = np.quantile(pos, self.bias.clip_pos_q)

        self.neg_clip_val = None
        if self.bias.clip_neg_q is not None:
            neg = self.returns[self.returns < 0]
            if len(neg) > 0:
                self.neg_clip_val = np.quantile(neg, 1.0 - self.bias.clip_neg_q)

        # Tilt: probabilidades de muestreo ponderadas
        self.probs = None
        if self.bias.tilt_theta is not None and np.isfinite(self.bias.tilt_theta):
            scores = self.bias.tilt_theta * self.returns
            scores = scores - np.max(scores)  # estabilidad numérica
            w = np.exp(scores)
            self.probs = w / w.sum()

    def _apply_transforms(self, r: np.ndarray) -> np.ndarray:
        """Aplica winsorización, escalado asimétrico y ajuste de drift."""
        # Winsorización
        if self.pos_clip_val is not None:
            r = np.where(r > self.pos_clip_val, self.pos_clip_val, r)
        if self.neg_clip_val is not None:
            r = np.where(r < self.neg_clip_val, self.neg_clip_val, r)

        # Escalado asimétrico
        if self.bias.pos_scale != 1.0 or self.bias.neg_scale != 1.0:
            r = np.where(r >= 0, r * self.bias.pos_scale, r * self.bias.neg_scale)

        # Drift
        if self.bias.drift_mode == "zero":
            r = r - np.mean(self.returns)
        elif self.bias.drift_mode == "shift" and self.bias.mu_shift_per_step != 0.0:
            r = r + self.bias.mu_shift_per_step

        return r

    def sample(self, size: int) -> np.ndarray:
        """Genera `size` retornos sintéticos."""
        base = np.random.choice(self.returns, size=size, replace=True, p=self.probs)
        noise = np.random.normal(loc=0.0, scale=self.kernel_std, size=size)
        r = base + noise
        return self._apply_transforms(r)


# ── Simulador Monte Carlo ───────────────────────────────────────────────────


class MonteCarloPriceSimulator:
    """Genera trayectorias de precio futuro con bootstrap-KDE.

    Asume que los retornos paso a paso son i.i.d. (limitación principal).
    Cada retorno futuro se muestra como 'retorno histórico + ruido KDE'.
    """

    def __init__(
        self,
        price_series: pd.Series,
        n_sims: int = 2_000,
        bias: Optional[BiasConfig] = None,
    ):
        self.price_series = price_series.sort_index().astype(float)
        self.bias = bias or BiasConfig()

        if self.bias.use_log_returns:
            self.returns = np.log(self.price_series).diff().dropna()
        else:
            self.returns = self.price_series.pct_change().dropna()

        self.sampler = KDEBootstrapSampler(self.returns, bias=self.bias)
        self.start_price = float(self.price_series.iloc[-1])
        self.n_sims = n_sims

    def simulate_paths(self, horizon_steps: int) -> pd.DataFrame:
        """Genera n_sims trayectorias de longitud horizon_steps.

        Returns
        -------
        DataFrame shape = (horizon_steps+1, n_sims)
            Fila 0 = precio inicial; filas siguientes = evolución simulada.
        """
        total = horizon_steps * self.n_sims
        sampled = self.sampler.sample(total).reshape(horizon_steps, self.n_sims)

        if self.bias.use_log_returns:
            factors = np.exp(sampled)
        else:
            factors = sampled + 1.0

        factors_df = pd.DataFrame(factors)
        factors_df.loc[-1] = 1.0
        factors_df = factors_df.sort_index()
        price_paths = self.start_price * factors_df.cumprod()
        price_paths.index = range(price_paths.shape[0])
        price_paths.index.name = "step"
        return price_paths

    @staticmethod
    def summarize_risk(price_paths: pd.DataFrame) -> Dict[str, float]:
        """Métricas de riesgo al horizonte final."""
        first = price_paths.iloc[0, :].values
        last = price_paths.iloc[-1, :].values
        drops = (last / first) - 1.0

        return {
            "start_price": float(first[0]),
            "median_final_price": float(np.median(last)),
            "p05_final_price": float(np.percentile(last, 5)),
            "p25_final_price": float(np.percentile(last, 25)),
            "p75_final_price": float(np.percentile(last, 75)),
            "p95_final_price": float(np.percentile(last, 95)),
            "prob_drop_20pct_or_more": float(np.mean(drops <= -0.20)),
            "prob_drop_40pct_or_more": float(np.mean(drops <= -0.40)),
            "prob_drop_50pct_or_more": float(np.mean(drops <= -0.50)),
            "max_drawdown_median": float(
                np.median(1.0 - price_paths.min() / price_paths.iloc[0])
            ),
        }


def extract_scenario_series(
    price_paths: pd.DataFrame,
    hist_index: pd.DatetimeIndex,
    pessimistic_q: float = 5,
    base_q: float = 50,
    optimistic_q: float = 95,
) -> Dict[str, pd.Series]:
    """Extrae trayectorias representativas (percentiles) con índice temporal futuro."""
    qs = [pessimistic_q / 100, base_q / 100, optimistic_q / 100]
    desc = price_paths.T.describe(percentiles=qs)

    last_ts = hist_index[-1]
    freq = hist_index.to_series().diff().dropna().median()
    future_index = pd.date_range(start=last_ts, periods=price_paths.shape[0], freq=freq)

    scenarios = {}
    for q, name in zip(
        [pessimistic_q, base_q, optimistic_q],
        ["pessimistic", "base", "optimistic"],
    ):
        key = f"{q}%"
        scenarios[name] = pd.Series(
            desc.loc[key].values, index=future_index, name="price"
        )
    return scenarios
