"""
tesis/baseline/saltshield/pricing.py
------------------------------------
Pricing frecuentista del Saltshield (seguro de liquidación).
Adaptado de prestamos/run_saltshield.py y run_saltshield_minute.py.

Calcula P(liquidación), pérdida esperada y prima de riesgo
usando backtesting histórico puro.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def compute_saltshield_metrics(
    price_series: pd.Series,
    horizon_days: int = 365,
    init_ltv: float = 0.50,
    principal_usd: float = 10_000.0,
    friction: float = 0.0,
    start_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Calcula métricas del Saltshield por backtesting histórico.

    Para cada posible fecha de inicio t0 donde existe t0 + horizon:
    - p0 = precio en t0
    - pT = precio en t0 + horizon
    - Liquidación si pT < INIT_LTV * p0
    - Pérdida = max(0, 1 - pT*(1-friction) / (INIT_LTV * p0))

    Parameters
    ----------
    price_series : Serie de precios (índice datetime).
    horizon_days : horizonte del préstamo en días.
    init_ltv : LTV inicial.
    principal_usd : principal para calcular pérdida en USD.
    friction : tasa de fricción (slippage + fees), e.g. 0.015.
    start_date : fecha mínima para considerar inicios (None = toda la serie).

    Returns
    -------
    Dict con métricas: n_starts, n_liq, p_liq, mean_loss_given_liq,
    expected_loss_frac, expected_loss_usd, premiums, var95, es95,
    y el DataFrame detallado en clave 'detail_df'.
    """
    series = price_series.sort_index().astype(float)

    if start_date is not None:
        series = series.loc[pd.Timestamp(start_date) :]

    horizon = pd.Timedelta(days=horizon_days)
    last_date = series.index.max()

    # Fechas de inicio con horizonte completo
    valid_mask = series.index + horizon <= last_date
    start_dates = series.index[valid_mask]

    if len(start_dates) == 0:
        raise ValueError("No hay fechas de inicio con horizonte completo.")

    p0 = series.reindex(start_dates).values
    end_dates = start_dates + horizon
    pT = series.reindex(end_dates).values

    # Construir DataFrame de resultados
    res = pd.DataFrame({"start": start_dates, "p0": p0, "pT": pT}).dropna()

    # Liquidaciones
    thresholds = init_ltv * res["p0"]
    recovery_factor = 1.0 - friction
    res["liquidated"] = res["pT"] * recovery_factor < thresholds
    res["loss_frac"] = np.maximum(0.0, 1.0 - (res["pT"] * recovery_factor) / thresholds)
    res["loss_usd"] = res["loss_frac"] * principal_usd

    # Métricas agregadas
    n = len(res)
    n_liq = int(res["liquidated"].sum())
    freq_liq = n_liq / n if n > 0 else 0.0
    mean_loss_given_liq = (
        res.loc[res["liquidated"], "loss_frac"].mean() if n_liq > 0 else 0.0
    )
    expected_loss_frac = res["loss_frac"].mean()
    expected_loss_usd = expected_loss_frac * principal_usd

    # Risk metrics
    var95 = float(res["loss_frac"].quantile(0.95))
    losses_above_var = res.loc[res["loss_frac"] >= var95, "loss_frac"]
    es95 = float(losses_above_var.mean()) if len(losses_above_var) > 0 else var95

    # Primas con distintos márgenes
    margins = [0.0, 0.10, 0.20, 0.30]
    premiums = {
        f"margin_{int(m * 100)}pct": expected_loss_frac * (1.0 + m) for m in margins
    }

    return {
        "n_starts": int(n),
        "n_liq": int(n_liq),
        "p_liq": float(freq_liq),
        "mean_loss_given_liq_frac": float(mean_loss_given_liq),
        "expected_loss_frac": float(expected_loss_frac),
        "expected_loss_usd": float(expected_loss_usd),
        "var95_frac": float(var95),
        "es95_frac": float(es95),
        "premiums_frac": premiums,
        "friction": float(friction),
        "horizon_days": horizon_days,
        "init_ltv": init_ltv,
        "detail_df": res,
    }


def compute_mc_bootstrap_metrics(
    price_series: pd.Series,
    n_sim: int = 20_000,
    horizon_days: int = 365,
    init_ltv: float = 0.50,
    principal_usd: float = 10_000.0,
    recover_factor: float = 1.0,
    seed: int = 42,
) -> Dict[str, Any]:
    """Monte Carlo bootstrap de log-retornos diarios (i.i.d.).

    Muestrea retornos diarios con reemplazo y acumula para generar
    ratio pT/p0. Es la línea base que asume independencia temporal.

    Parameters
    ----------
    price_series : Serie de precios (se resamplea a diario internamente).
    n_sim : número de simulaciones.
    horizon_days : días por simulación.
    init_ltv : LTV inicial.
    principal_usd : principal de referencia.
    recover_factor : 1 - friction (e.g. 0.985 con 1.5% slippage+fees).
    seed : semilla para reproducibilidad.

    Returns
    -------
    Dict con p_liq, expected_loss, etc.
    """
    np.random.seed(seed)

    daily = price_series.resample("1D").last().dropna()
    returns = np.log(daily).diff().dropna().values

    idx = np.random.randint(0, len(returns), size=(n_sim, horizon_days))
    sampled = returns[idx]
    cum_log_returns = sampled.sum(axis=1)
    R = np.exp(cum_log_returns)  # pT / p0
    R_eff = R * recover_factor

    loss_frac = np.maximum(0.0, 1.0 - R / init_ltv)
    loss_frac_eff = np.maximum(0.0, 1.0 - R_eff / init_ltv)

    p_liq = float((R < init_ltv).mean())
    mean_lgl = float(loss_frac[R < init_ltv].mean()) if (R < init_ltv).any() else 0.0
    exp_loss = float(loss_frac.mean())

    p_liq_eff = float((R_eff < init_ltv).mean())
    exp_loss_eff = float(loss_frac_eff.mean())

    return {
        "n_sim": n_sim,
        "p_liq_mc": p_liq,
        "mean_loss_given_liq_mc": mean_lgl,
        "expected_loss_frac_mc": exp_loss,
        "expected_loss_usd_mc": exp_loss * principal_usd,
        "recover_factor": recover_factor,
        "p_liq_mc_with_friction": p_liq_eff,
        "expected_loss_frac_mc_with_friction": exp_loss_eff,
        "expected_loss_usd_mc_with_friction": exp_loss_eff * principal_usd,
    }
