"""
tesis/bayesian/sv_model/trajectory_simulator.py
-----------------------------------------------
Simulación de trayectorias de precio futuro usando la posterior
del modelo de volatilidad estocástica.

A diferencia del bootstrap-KDE (que asume retornos i.i.d.),
aquí se propaga:
1. Incertidumbre aleatoria (variabilidad intrínseca de precios).
2. Incertidumbre epistémica (no conocemos los parámetros exactos).

Procedimiento:
    Para cada muestra posterior (mu*, phi*, sigma_eta*):
        1. Simular h*_1, ..., h*_T con la ecuación de transición
        2. Simular y*_t = exp(h*_t / 2) * eps_t,  eps_t ~ N(0,1)
        3. Reconstruir precio: P*_t = P_0 * exp(sum(y*_s, s=1..t))
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def simulate_sv_trajectories(
    mcmc_result: Dict[str, np.ndarray],
    start_price: float,
    horizon_steps: int,
    n_trajectories_per_sample: int = 1,
    max_posterior_samples: Optional[int] = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Genera trayectorias de precio usando muestras de la posterior SV.

    Parameters
    ----------
    mcmc_result : output de SVMetropolisWithinGibbs.run() con
        claves 'mu', 'phi', 'sigma_eta', 'h'.
    start_price : precio inicial para las trayectorias.
    horizon_steps : número de pasos futuros a simular.
    n_trajectories_per_sample : trayectorias por cada muestra posterior.
    max_posterior_samples : limitar número de muestras (None = usar todas).
    seed : semilla para reproducibilidad.

    Returns
    -------
    pd.DataFrame de shape (horizon_steps+1, n_total_trajectories)
        Fila 0 = start_price para todas las columnas.
    """
    rng = np.random.default_rng(seed)

    mus = mcmc_result["mu"]
    phis = mcmc_result["phi"]
    sigmas = mcmc_result["sigma_eta"]
    h_chains = mcmc_result["h"]  # (n_samples, T_original)

    n_samples = len(mus)
    if max_posterior_samples is not None and max_posterior_samples < n_samples:
        idx_sel = rng.choice(n_samples, size=max_posterior_samples, replace=False)
        mus = mus[idx_sel]
        phis = phis[idx_sel]
        sigmas = sigmas[idx_sel]
        h_chains = h_chains[idx_sel]
        n_samples = max_posterior_samples

    n_total = n_samples * n_trajectories_per_sample
    all_paths = np.zeros((horizon_steps + 1, n_total))
    all_paths[0, :] = start_price

    col = 0
    for s in range(n_samples):
        mu_s = mus[s]
        phi_s = phis[s]
        sigma_s = sigmas[s]
        # Usar el último h de la cadena como punto de partida
        h_last = h_chains[s, -1]

        for _ in range(n_trajectories_per_sample):
            # Simular trayectoria de h
            h_prev = h_last
            cum_log_return = 0.0

            for t in range(1, horizon_steps + 1):
                # Transición de log-volatilidad
                h_t = mu_s + phi_s * (h_prev - mu_s) + sigma_s * rng.normal()
                # Log-retorno
                y_t = np.exp(h_t / 2) * rng.normal()
                cum_log_return += y_t
                all_paths[t, col] = start_price * np.exp(cum_log_return)
                h_prev = h_t

            col += 1

    paths_df = pd.DataFrame(all_paths)
    paths_df.index.name = "step"
    return paths_df


def simulate_sv_trajectories_fast(
    mcmc_result: Dict[str, np.ndarray],
    start_price: float,
    horizon_steps: int,
    n_trajectories: int = 2000,
    seed: int = 42,
) -> pd.DataFrame:
    """Versión vectorizada (más rápida) para generar trayectorias.

    Selecciona aleatoriamente parámetros de la posterior para cada
    trayectoria y genera todas en paralelo con operaciones numpy.
    """
    rng = np.random.default_rng(seed)

    mus = mcmc_result["mu"]
    phis = mcmc_result["phi"]
    sigmas = mcmc_result["sigma_eta"]
    h_chains = mcmc_result["h"]

    n_samples = len(mus)

    # Seleccionar parámetros para cada trayectoria
    idx = rng.choice(n_samples, size=n_trajectories, replace=True)
    mu_vec = mus[idx]  # (n_traj,)
    phi_vec = phis[idx]  # (n_traj,)
    sigma_vec = sigmas[idx]  # (n_traj,)
    h_start = h_chains[idx, -1]  # (n_traj,)

    # Simular paso a paso (necesario por la dependencia AR(1))
    all_paths = np.zeros((horizon_steps + 1, n_trajectories))
    all_paths[0, :] = start_price

    h_prev = h_start
    cum_log_ret = np.zeros(n_trajectories)

    for t in range(1, horizon_steps + 1):
        eta = rng.normal(size=n_trajectories)
        h_t = mu_vec + phi_vec * (h_prev - mu_vec) + sigma_vec * eta

        eps = rng.normal(size=n_trajectories)
        y_t = np.exp(h_t / 2) * eps

        cum_log_ret += y_t
        all_paths[t, :] = start_price * np.exp(cum_log_ret)
        h_prev = h_t

    paths_df = pd.DataFrame(all_paths)
    paths_df.index.name = "step"
    return paths_df


def summarize_sv_risk(
    price_paths: pd.DataFrame,
    init_ltv: float = 0.50,
    liq_ltv: float = 0.80,
) -> Dict[str, float]:
    """Métricas de riesgo incluyendo probabilidades de liquidación.

    Parameters
    ----------
    price_paths : DataFrame de trayectorias (steps x sims).
    init_ltv : LTV inicial del préstamo.
    liq_ltv : LTV de liquidación.

    Returns
    -------
    dict con métricas de riesgo y liquidación.
    """
    first = price_paths.iloc[0, :].values
    last = price_paths.iloc[-1, :].values
    path_min = price_paths.min().values  # mínimo por trayectoria

    drops = (last / first) - 1.0
    max_drops = (path_min / first) - 1.0  # drawdown máximo

    # Umbral de liquidación: precio cae a init_ltv/liq_ltv del inicial
    liq_threshold_ratio = init_ltv / liq_ltv  # e.g. 0.625
    # Liquidación si min(P_t/P_0) < liq_threshold_ratio
    liq_mask = (path_min / first) < liq_threshold_ratio

    # Loss given liquidation (simplificado: usando precio final)
    lgl = np.where(
        liq_mask,
        np.maximum(0, 1.0 - last / (init_ltv * first)),
        0.0,
    )

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
        "median_max_drawdown": float(np.median(-max_drops)),
        # Métricas de liquidación
        "prob_liquidation": float(np.mean(liq_mask)),
        "mean_loss_given_liq": float(lgl[liq_mask].mean()) if liq_mask.any() else 0.0,
        "expected_loss_frac": float(np.mean(lgl)),
        "var95_loss": float(np.percentile(lgl, 95)),
        "es95_loss": float(lgl[lgl >= np.percentile(lgl, 95)].mean())
        if np.any(lgl > 0)
        else 0.0,
    }
