"""
tesis/bayesian/buffer/bayesian_buffer.py
----------------------------------------
Buffer de Volatilidad Óptimo Bayesiano.

Extensión del buffer_optimizer determinístico:
en lugar de usar trayectorias históricas, usa trayectorias
generadas con el modelo SV Bayesiano.

El buffer óptimo para nivel de confianza alpha es:
    B*(alpha) = inf{ b : P_{posterior}[max LTV_t(b) < L_liq] >= alpha }

Esto produce intervalos de credibilidad para el buffer,
no solo un estimador puntual.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def compute_bayesian_buffer_distribution(
    sv_trajectories: pd.DataFrame,
    init_ltv: float = 0.50,
    liq_ltv: float = 0.80,
    principal_usd: float = 10_000.0,
) -> pd.DataFrame:
    """Calcula buffer requerido por trayectoria simulada del modelo SV.

    Para cada trayectoria simulada:
    - p0 = precio inicial (fila 0)
    - pmin = precio mínimo en toda la trayectoria
    - buffer_pct = max(0, (L0/Lliq) * (p0/pmin) - 1)

    Parameters
    ----------
    sv_trajectories : DataFrame (steps x n_sims) de trayectorias SV.
    init_ltv : LTV inicial.
    liq_ltv : LTV de liquidación.
    principal_usd : monto de referencia.

    Returns
    -------
    DataFrame con una fila por trayectoria:
        p0, pmin, buffer_pct, buffer_usd.
    """
    p0s = sv_trajectories.iloc[0, :].values
    pmins = sv_trajectories.min().values
    n = len(p0s)

    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (init_ltv / liq_ltv) * (p0s / pmins)
    buf_pct = np.maximum(0.0, ratio - 1.0)

    c0s = principal_usd / (init_ltv * p0s)
    buf_coins = buf_pct * c0s
    buf_usd = buf_coins * p0s

    return pd.DataFrame(
        {
            "p0": p0s,
            "pmin": pmins,
            "buffer_pct": buf_pct,
            "buffer_usd": buf_usd,
        }
    )


def find_bayesian_buffer(
    buf_dist: pd.DataFrame,
    target_survival: float = 0.99,
) -> Dict[str, float]:
    """Encuentra el buffer para alcanzar la supervivencia objetivo.

    Parameters
    ----------
    buf_dist : output de compute_bayesian_buffer_distribution.
    target_survival : fracción de trayectorias que deben sobrevivir.

    Returns
    -------
    dict con buffer_pct, buffer_usd, y estadísticas de la distribución.
    """
    pcts = buf_dist["buffer_pct"].replace([np.inf, -np.inf], np.nan).dropna()
    usds = buf_dist["buffer_usd"].replace([np.inf, -np.inf], np.nan).dropna()

    buf_pct_q = float(pcts.quantile(target_survival))
    buf_usd_q = float(usds.quantile(target_survival))

    return {
        "target_survival": target_survival,
        "buffer_pct@target": buf_pct_q,
        "buffer_usd@target": buf_usd_q,
        "mean_buffer_pct": float(pcts.mean()),
        "median_buffer_pct": float(pcts.median()),
        "p50_buffer_pct": float(pcts.quantile(0.50)),
        "p90_buffer_pct": float(pcts.quantile(0.90)),
        "p95_buffer_pct": float(pcts.quantile(0.95)),
        "p99_buffer_pct": float(pcts.quantile(0.99)),
        "frac_no_buffer_needed": float((pcts == 0).mean()),
        "n_trajectories": len(pcts),
    }


def compare_buffers(
    historical_dist: pd.DataFrame,
    bayesian_dist: pd.DataFrame,
    bootstrap_dist: Optional[pd.DataFrame] = None,
    survival_levels: list[float] = [0.80, 0.85, 0.90, 0.95, 0.99],
) -> pd.DataFrame:
    """Compara buffers entre métodos (histórico, Bayesiano, bootstrap).

    Parameters
    ----------
    historical_dist : distribución de buffer del optimizer histórico.
    bayesian_dist : distribución de buffer del modelo SV Bayesiano.
    bootstrap_dist : distribución de buffer del MC bootstrap (opcional).
    survival_levels : niveles de supervivencia a comparar.

    Returns
    -------
    DataFrame con columnas: survival, historical_pct, bayesian_pct, bootstrap_pct.
    """

    def _get_quantiles(df: pd.DataFrame, col: str = "buffer_pct") -> dict:
        s = df[col].replace([np.inf, -np.inf], np.nan).dropna()
        return {q: float(s.quantile(q)) for q in survival_levels}

    # Para historical_dist el nombre de la columna puede variar
    hist_col = (
        "buffer_pct_of_init_collateral"
        if "buffer_pct_of_init_collateral" in historical_dist.columns
        else "buffer_pct"
    )
    hist_q = _get_quantiles(historical_dist, hist_col)
    bayes_q = _get_quantiles(bayesian_dist, "buffer_pct")

    rows = []
    for q in survival_levels:
        row = {
            "survival": q,
            "historical_buffer_pct": hist_q[q],
            "bayesian_buffer_pct": bayes_q[q],
        }
        if bootstrap_dist is not None:
            boot_col = (
                "buffer_pct"
                if "buffer_pct" in bootstrap_dist.columns
                else "buffer_pct_of_init_collateral"
            )
            boot_q = _get_quantiles(bootstrap_dist, boot_col)
            row["bootstrap_buffer_pct"] = boot_q[q]
        rows.append(row)

    return pd.DataFrame(rows)
