"""
tesis/baseline/buffer_optimizer/optimizer.py
--------------------------------------------
Optimizador de buffer de volatilidad (perspectiva del cliente).
Adaptado de bolsa_volatilidad/utils/volatility_buffer_optimizer.py.

Calcula el colateral extra ("bolsa de volatilidad") necesario para
que un préstamo nunca se liquide, dado un trayecto de precios.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd


# ── Utilidades de mínimos futuros / ventana ──────────────────────────────────


def suffix_min(a: np.ndarray) -> np.ndarray:
    """Mínimo acumulado desde el final: out[i] = min(a[i:]). O(n)."""
    out = np.empty_like(a)
    cur = math.inf
    for i in range(len(a) - 1, -1, -1):
        v = a[i]
        if v < cur:
            cur = v
        out[i] = cur
    return out


def window_min(a: np.ndarray, w: int) -> np.ndarray:
    """Mínimo deslizante [i, i+w] con deque. O(n).

    m[i] = min(a[i : i+w+1]) para i <= len(a)-w-1.
    Los últimos w valores quedan sin usar.
    """
    if w < 0:
        raise ValueError("w debe ser >= 0")
    n = len(a)
    m = np.empty(n, dtype=a.dtype)
    dq: deque[int] = deque()
    for i in range(n):
        while dq and dq[0] < i - w:
            dq.popleft()
        while dq and a[dq[-1]] >= a[i]:
            dq.pop()
        dq.append(i)
        if i >= w:
            m[i - w] = a[dq[0]]
    return m


# ── Cálculo de buffer requerido ──────────────────────────────────────────────


def compute_required_buffer_distribution(
    prices: np.ndarray,
    timestamps: pd.DatetimeIndex,
    init_ltv: float = 0.50,
    liq_ltv: float = 0.80,
    principal_usd: float = 10_000.0,
    mode: str = "window",
    window_minutes: Optional[int] = 525_600,  # 365 días
    start_stride: int = 60,
) -> pd.DataFrame:
    """Calcula el buffer mínimo por fecha de inicio para no liquidar.

    Parameters
    ----------
    prices : array de precios (minuto a minuto o similar).
    timestamps : DatetimeIndex correspondiente.
    init_ltv : LTV inicial.
    liq_ltv : LTV de liquidación.
    principal_usd : principal de referencia (solo afecta coins/USD, no %).
    mode : "end" (hasta fin de serie) o "window" (ventana fija).
    window_minutes : tamaño de ventana en minutos (solo si mode="window").
    start_stride : probar inicios cada N minutos (eficiencia).

    Returns
    -------
    DataFrame con columnas: fecha_inicio, p0, pmin,
    buffer_pct_of_init_collateral, buffer_usd_at_p0.
    """
    n = len(prices)
    if n < 2:
        raise ValueError("Serie demasiado corta")

    if mode == "end":
        pmin_future = suffix_min(prices)
        valid_starts = np.arange(0, n - 1, start_stride)
        p0s = prices[valid_starts]
        pmins = pmin_future[valid_starts]
        starts_ts = timestamps[valid_starts]

    elif mode == "window":
        if window_minutes is None or window_minutes <= 0:
            raise ValueError("window_minutes debe ser > 0 para mode='window'")
        m = window_min(prices, window_minutes)
        valid = n - window_minutes
        if valid <= 1:
            raise ValueError("Ventana mayor o similar a la longitud de la serie")
        valid_starts = np.arange(0, valid, start_stride)
        p0s = prices[valid_starts]
        pmins = m[valid_starts]
        starts_ts = timestamps[valid_starts]
    else:
        raise ValueError("mode debe ser 'end' o 'window'")

    # Buffer porcentual (independiente de principal)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (init_ltv / liq_ltv) * (p0s / pmins)
    buf_pct = np.maximum(0.0, ratio - 1.0)

    # Buffer en coins y USD
    c0s = principal_usd / (init_ltv * p0s)
    creqs = principal_usd / (liq_ltv * pmins)
    buf_coins = np.maximum(0.0, creqs - c0s)
    buf_usd_p0 = buf_coins * p0s

    return pd.DataFrame(
        {
            "fecha_inicio": starts_ts,
            "p0": p0s,
            "pmin": pmins,
            "buffer_pct_of_init_collateral": buf_pct,
            "buffer_usd_at_p0": buf_usd_p0,
        }
    )


def find_buffer_for_target_success(
    dist_df: pd.DataFrame,
    q: float = 0.90,
    metric: str = "buffer_pct_of_init_collateral",
) -> float:
    """Percentil q de la métrica de buffer.

    Si aprovisionas al menos este valor, evitas liquidación en >= q%
    de los escenarios históricos.
    """
    s = pd.Series(dist_df[metric]).replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return float("nan")
    return float(s.quantile(q))


def summarize_buffer(
    prices: np.ndarray,
    timestamps: pd.DatetimeIndex,
    init_ltv: float = 0.50,
    liq_ltv: float = 0.80,
    principal_usd: float = 10_000.0,
    mode: str = "window",
    window_minutes: int = 525_600,
    start_stride: int = 60,
    target_q: float = 0.90,
) -> Dict[str, float]:
    """Wrapper: distribución + buffer objetivo en un solo paso."""
    dist = compute_required_buffer_distribution(
        prices,
        timestamps,
        init_ltv,
        liq_ltv,
        principal_usd,
        mode,
        window_minutes,
        start_stride,
    )
    pct_q = find_buffer_for_target_success(dist, q=target_q)

    p0_med = float(dist["p0"].median())
    c0_med = principal_usd / (init_ltv * p0_med)
    coins_q = pct_q * c0_med
    usd_q = coins_q * p0_med

    return {
        "mode": mode,
        "window_minutes": float(window_minutes) if mode == "window" else float("nan"),
        "init_ltv": init_ltv,
        "liq_ltv": liq_ltv,
        "target_q": target_q,
        "buffer_pct@q": float(pct_q),
        "buffer_coins@q": float(coins_q),
        "buffer_usd@q": float(usd_q),
        "n_starts": int(len(dist)),
    }
