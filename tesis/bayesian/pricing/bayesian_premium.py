"""
tesis/bayesian/pricing/bayesian_premium.py
------------------------------------------
Pricing Bayesiano de la cobertura de liquidación.

Implementa el modelo de frecuencia-severidad Bayesiano
(inspirado en la tesis de Gutiérrez Salgado, 2025)
adaptado a préstamos colateralizados con crypto.

El "siniestro" = evento de liquidación.
- Frecuencia: P(liquidación en horizonte tau)
- Severidad: LGL (Loss Given Liquidation), modelada como Gamma.

La prima de riesgo Bayesiana propaga incertidumbre paramétrica:
    PR(tau) = E_{posterior}[q(tau, theta) * E[LGL]]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
from scipy import stats


# ══════════════════════════════════════════════════════════════════════════════
# Modelo de Severidad: LGL ~ Gamma (Bayesiano)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class GammaPriors:
    """Priors para los parámetros de la distribución Gamma de severidad.

    LGL | alpha, beta ~ Gamma(alpha, beta)   (parametrización shape-rate)

    Priors:
        alpha ~ Exponential(lambda_alpha)  [shape > 0]
        beta  ~ Gamma(a_beta, b_beta)      [rate > 0]
    """

    # Prior para alpha (shape): Exp(lambda)
    lambda_alpha: float = 0.5  # difusa

    # Prior para beta (rate): Gamma(a, b)
    a_beta: float = 2.0
    b_beta: float = 2.0


class GammaSeverityMCMC:
    """MCMC (Metropolis-Hastings) para LGL ~ Gamma(alpha, beta).

    Muestrea la posterior conjunta p(alpha, beta | LGL_1, ..., LGL_n).
    """

    def __init__(
        self,
        losses: np.ndarray,
        priors: GammaPriors | None = None,
    ):
        """
        Parameters
        ----------
        losses : array de pérdidas observadas (LGL > 0) como fracciones.
        priors : hiperparámetros de las priors.
        """
        self.losses = np.asarray(losses[losses > 0], dtype=float)
        self.n = len(self.losses)
        self.priors = priors or GammaPriors()

        if self.n < 2:
            raise ValueError("Se necesitan al menos 2 observaciones de pérdida > 0.")

        # Estadísticos suficientes
        self.sum_log_x = np.sum(np.log(self.losses))
        self.sum_x = np.sum(self.losses)

    def _log_posterior(self, alpha: float, beta: float) -> float:
        """Log-posterior no normalizada."""
        if alpha <= 0 or beta <= 0:
            return -np.inf

        pr = self.priors
        n = self.n

        # Log-likelihood: Gamma(alpha, beta)
        ll = (
            n * alpha * np.log(beta)
            - n * np.math.lgamma(alpha)
            + (alpha - 1) * self.sum_log_x
            - beta * self.sum_x
        )

        # Log-prior
        lp_alpha = -pr.lambda_alpha * alpha  # Exp(lambda)
        lp_beta = (pr.a_beta - 1) * np.log(beta) - pr.b_beta * beta  # Gamma(a, b)

        return ll + lp_alpha + lp_beta

    def run(
        self,
        n_iter: int = 10_000,
        burn_in: int = 2_000,
        alpha_init: Optional[float] = None,
        beta_init: Optional[float] = None,
        proposal_alpha_std: float = 0.1,
        proposal_beta_std: float = 0.5,
        seed: int = 42,
        verbose: bool = True,
    ) -> Dict[str, np.ndarray]:
        """Ejecuta M-H para la posterior (alpha, beta).

        Returns
        -------
        dict con 'alpha', 'beta' arrays de muestras posteriores.
        """
        np.random.seed(seed)

        # Inicialización por método de momentos
        mean_x = np.mean(self.losses)
        var_x = np.var(self.losses, ddof=1)
        if alpha_init is None:
            alpha_init = max(0.1, mean_x**2 / (var_x + 1e-10))
        if beta_init is None:
            beta_init = max(0.1, mean_x / (var_x + 1e-10))

        alpha = alpha_init
        beta = beta_init

        chain_alpha = []
        chain_beta = []
        accept = 0

        total = n_iter + burn_in
        log_post_current = self._log_posterior(alpha, beta)

        for i in range(total):
            # Propuesta conjunta (random walk en log-space para positividad)
            log_alpha_star = np.log(alpha) + np.random.normal(0, proposal_alpha_std)
            log_beta_star = np.log(beta) + np.random.normal(0, proposal_beta_std)
            alpha_star = np.exp(log_alpha_star)
            beta_star = np.exp(log_beta_star)

            # Jacobiano de la transformación log
            log_post_star = self._log_posterior(alpha_star, beta_star)
            log_jacobian = (log_alpha_star - np.log(alpha)) + (
                log_beta_star - np.log(beta)
            )

            log_accept_ratio = log_post_star - log_post_current + log_jacobian

            if np.log(np.random.uniform()) < log_accept_ratio:
                alpha = alpha_star
                beta = beta_star
                log_post_current = log_post_star
                if i >= burn_in:
                    accept += 1

            if i >= burn_in:
                chain_alpha.append(alpha)
                chain_beta.append(beta)

        result = {
            "alpha": np.array(chain_alpha),
            "beta": np.array(chain_beta),
            "accept_rate": accept / n_iter if n_iter > 0 else 0.0,
        }

        if verbose:
            print(
                f"Gamma Severity MCMC: {n_iter} muestras, "
                f"accept_rate={result['accept_rate']:.3f}"
            )
            print(
                f"  alpha: mean={np.mean(chain_alpha):.4f}, "
                f"std={np.std(chain_alpha):.4f}"
            )
            print(
                f"  beta:  mean={np.mean(chain_beta):.4f}, std={np.std(chain_beta):.4f}"
            )
            exp_lgl = np.array(chain_alpha) / np.array(chain_beta)
            print(
                f"  E[LGL]: mean={np.mean(exp_lgl):.4f}, "
                f"95% CI=[{np.percentile(exp_lgl, 2.5):.4f}, "
                f"{np.percentile(exp_lgl, 97.5):.4f}]"
            )

        return result


# ══════════════════════════════════════════════════════════════════════════════
# Pricing Bayesiano Completo
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class OperationalCosts:
    """Cargas operativas para calcular prima de tarifa.

    Estructura de la tesis de referencia:
    PT = PR / (1 - (G_adm + C_adq + U))
    """

    g_adm: float = 0.05  # gastos administrativos (5%)
    c_adq: float = 0.10  # costos de adquisición (10%)
    u: float = 0.10  # margen de utilidad (10%)

    @property
    def loading_denominator(self) -> float:
        return 1.0 - (self.g_adm + self.c_adq + self.u)


def compute_bayesian_premium(
    sv_trajectories: "pd.DataFrame",
    mcmc_result_sv: Dict[str, np.ndarray],
    init_ltv: float = 0.50,
    liq_ltv: float = 0.80,
    friction: float = 0.015,
    principal_usd: float = 10_000.0,
    severity_losses: Optional[np.ndarray] = None,
    severity_priors: Optional[GammaPriors] = None,
    costs: Optional[OperationalCosts] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """Calcula la prima de riesgo Bayesiana completa.

    Combina:
    1. Frecuencia de liquidación de las trayectorias SV.
    2. Severidad Bayesiana (Gamma MCMC) si hay datos de pérdida.
    3. Cargas operativas para prima de tarifa.

    Parameters
    ----------
    sv_trajectories : DataFrame de trayectorias (steps x sims) del modelo SV.
    mcmc_result_sv : resultado del MCMC del modelo SV.
    init_ltv, liq_ltv : parámetros del préstamo.
    friction : slippage + fees en liquidación.
    principal_usd : monto del préstamo.
    severity_losses : array de LGL observadas (opcional; si None, estima de trayectorias).
    severity_priors : priors para el modelo Gamma de severidad.
    costs : cargas operativas.
    seed : semilla.

    Returns
    -------
    dict con prima de riesgo, prima de tarifa, intervalos de credibilidad, etc.
    """
    import pandas as pd

    costs = costs or OperationalCosts()
    np.random.seed(seed)

    first_prices = sv_trajectories.iloc[0, :].values
    last_prices = sv_trajectories.iloc[-1, :].values
    min_prices = sv_trajectories.min().values

    n_traj = len(first_prices)

    # ── 1. Frecuencia de liquidación ──────────────────────────────────
    # Liquidación: min(P_t) < (init_ltv / liq_ltv) * P_0
    liq_ratio = init_ltv / liq_ltv
    liq_mask = (min_prices / first_prices) < liq_ratio
    p_liq = float(np.mean(liq_mask))

    # ── 2. Severidad (LGL) ────────────────────────────────────────────
    # LGL = max(0, 1 - P_T * (1-friction) / (init_ltv * P_0))
    recovery = last_prices * (1.0 - friction)
    threshold_prices = init_ltv * first_prices
    lgl_all = np.maximum(0.0, 1.0 - recovery / threshold_prices)
    lgl_liquidated = lgl_all[liq_mask]

    # Si hay datos externos de LGL, usar esos para el modelo Gamma
    if severity_losses is not None and len(severity_losses[severity_losses > 0]) >= 2:
        loss_data = severity_losses
    elif len(lgl_liquidated[lgl_liquidated > 0]) >= 2:
        loss_data = lgl_liquidated
    else:
        # No hay suficientes datos → usar estimación puntual
        mean_lgl = float(lgl_liquidated.mean()) if len(lgl_liquidated) > 0 else 0.0
        risk_premium_frac = p_liq * mean_lgl
        risk_premium_usd = risk_premium_frac * principal_usd
        tariff = risk_premium_frac / costs.loading_denominator

        return {
            "method": "point_estimate (insufficient data for Gamma MCMC)",
            "p_liq": p_liq,
            "mean_lgl": mean_lgl,
            "risk_premium_frac": risk_premium_frac,
            "risk_premium_usd": risk_premium_usd,
            "tariff_premium_frac": tariff,
            "tariff_premium_usd": tariff * principal_usd,
            "n_trajectories": n_traj,
            "n_liquidations": int(liq_mask.sum()),
        }

    # Bayesian severity via MCMC
    gamma_mcmc = GammaSeverityMCMC(loss_data, priors=severity_priors)
    gamma_result = gamma_mcmc.run(n_iter=5_000, burn_in=1_000, seed=seed, verbose=False)

    # E[LGL] para cada muestra posterior = alpha / beta
    expected_lgl_samples = gamma_result["alpha"] / gamma_result["beta"]

    # ── 3. Prima de riesgo Bayesiana ──────────────────────────────────
    # PR = E[q * E[LGL]]
    # Propagamos incertidumbre: para cada muestra posterior de LGL,
    # combinamos con la frecuencia observada de las trayectorias.
    risk_premium_samples = p_liq * expected_lgl_samples
    risk_premium_frac = float(np.mean(risk_premium_samples))
    risk_premium_usd = risk_premium_frac * principal_usd

    # Prima de tarifa
    tariff_frac = risk_premium_frac / costs.loading_denominator
    tariff_usd = tariff_frac * principal_usd

    return {
        "method": "bayesian_gamma_severity",
        # Frecuencia
        "p_liq": p_liq,
        "n_trajectories": n_traj,
        "n_liquidations": int(liq_mask.sum()),
        # Severidad
        "mean_lgl_posterior": float(np.mean(expected_lgl_samples)),
        "lgl_95ci": [
            float(np.percentile(expected_lgl_samples, 2.5)),
            float(np.percentile(expected_lgl_samples, 97.5)),
        ],
        # Prima de riesgo
        "risk_premium_frac": risk_premium_frac,
        "risk_premium_usd": risk_premium_usd,
        "risk_premium_95ci_frac": [
            float(np.percentile(risk_premium_samples, 2.5)),
            float(np.percentile(risk_premium_samples, 97.5)),
        ],
        # Prima de tarifa
        "tariff_premium_frac": tariff_frac,
        "tariff_premium_usd": tariff_usd,
        "operational_costs": {
            "g_adm": costs.g_adm,
            "c_adq": costs.c_adq,
            "u": costs.u,
        },
        # Parámetros Gamma posteriores
        "gamma_alpha_mean": float(np.mean(gamma_result["alpha"])),
        "gamma_beta_mean": float(np.mean(gamma_result["beta"])),
    }


def compute_premium_table(
    mcmc_result_sv: Dict[str, np.ndarray],
    start_price: float,
    horizons_days: list[int] = [90, 180, 365],
    init_ltvs: list[float] = [0.40, 0.50, 0.60, 0.70],
    liq_ltv: float = 0.80,
    friction: float = 0.015,
    principal_usd: float = 10_000.0,
    n_trajectories: int = 2000,
    steps_per_day: int = 1,
    seed: int = 42,
) -> "pd.DataFrame":
    """Genera tabla de primas por combinación de plazo y LTV.

    Returns
    -------
    DataFrame con columnas: horizon_days, init_ltv, p_liq,
    risk_premium_frac, risk_premium_usd, tariff_premium_usd.
    """
    import pandas as pd
    from .bayesian_premium import compute_bayesian_premium
    from ..sv_model.trajectory_simulator import simulate_sv_trajectories_fast

    rows = []
    for h_days in horizons_days:
        horizon_steps = h_days * steps_per_day

        # Generar trayectorias una vez por horizonte
        paths = simulate_sv_trajectories_fast(
            mcmc_result_sv,
            start_price,
            horizon_steps,
            n_trajectories=n_trajectories,
            seed=seed,
        )

        for ltv in init_ltvs:
            result = compute_bayesian_premium(
                paths,
                mcmc_result_sv,
                init_ltv=ltv,
                liq_ltv=liq_ltv,
                friction=friction,
                principal_usd=principal_usd,
                seed=seed,
            )
            rows.append(
                {
                    "horizon_days": h_days,
                    "init_ltv": ltv,
                    "p_liq": result["p_liq"],
                    "risk_premium_frac": result["risk_premium_frac"],
                    "risk_premium_usd": result["risk_premium_usd"],
                    "tariff_premium_frac": result.get("tariff_premium_frac", np.nan),
                    "tariff_premium_usd": result.get("tariff_premium_usd", np.nan),
                }
            )

    return pd.DataFrame(rows)
