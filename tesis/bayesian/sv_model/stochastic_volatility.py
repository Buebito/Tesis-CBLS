"""
tesis/bayesian/sv_model/stochastic_volatility.py
-------------------------------------------------
Modelo de Volatilidad Estocástica (SV) Bayesiano.

Implementa el modelo canónico de Taylor (1982) / Kim, Shephard & Chib (1998):

    y_t = exp(h_t / 2) * eps_t,    eps_t ~ N(0, 1)
    h_t = mu + phi*(h_{t-1} - mu) + sigma_eta * eta_t,   eta_t ~ N(0, 1)
    h_0 ~ N(mu, sigma_eta^2 / (1 - phi^2))

donde:
    y_t     = log-retorno observado
    h_t     = log-volatilidad latente (no observada)
    mu      = nivel medio de log-volatilidad
    phi     = persistencia (|phi| < 1)
    sigma_eta = volatilidad de la volatilidad

Incluye:
1. Implementación "a mano" con Metropolis-within-Gibbs (Opción B del plan).
2. Wrapper para PyMC (Opción C) para validación cruzada.
3. Generador de datos sintéticos para verificación.
4. Diagnósticos de convergencia.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy import stats


# ══════════════════════════════════════════════════════════════════════════════
# Priors
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class SVPriors:
    """Hiperparámetros de las distribuciones prior.

    mu ~ N(mu_0, sigma_mu^2)
    (phi+1)/2 ~ Beta(a_phi, b_phi)   =>  phi ∈ (-1, 1)
    sigma_eta^2 ~ IG(a_sigma, b_sigma)
    """

    # Prior para mu (log-volatilidad media)
    mu_0: float = -9.0  # ~ log(vol diaria BTC) ≈ ln(0.03^2) ≈ -7
    sigma_mu: float = 3.0  # difusa

    # Prior para phi (persistencia) via Beta en (phi+1)/2
    a_phi: float = 20.0  # centra cerca de phi=0.95
    b_phi: float = 1.5

    # Prior para sigma_eta^2 (vol de vol) via Inverse-Gamma
    a_sigma: float = 2.5
    b_sigma: float = 0.025


# ══════════════════════════════════════════════════════════════════════════════
# Generador de datos sintéticos
# ══════════════════════════════════════════════════════════════════════════════


def simulate_sv_data(
    T: int = 1000,
    mu: float = -9.0,
    phi: float = 0.97,
    sigma_eta: float = 0.15,
    seed: int = 42,
) -> Dict[str, np.ndarray]:
    """Genera datos sintéticos del proceso SV para verificación.

    Returns
    -------
    dict con claves:
        'y'     : log-retornos observados (T,)
        'h'     : log-volatilidades latentes reales (T,)
        'params': dict con mu, phi, sigma_eta usados
    """
    rng = np.random.default_rng(seed)

    h = np.zeros(T)
    y = np.zeros(T)

    # h_0 de la distribución estacionaria
    h[0] = rng.normal(mu, sigma_eta / np.sqrt(1 - phi**2))

    for t in range(1, T):
        h[t] = mu + phi * (h[t - 1] - mu) + sigma_eta * rng.normal()

    # Observaciones
    for t in range(T):
        y[t] = np.exp(h[t] / 2) * rng.normal()

    return {
        "y": y,
        "h": h,
        "params": {"mu": mu, "phi": phi, "sigma_eta": sigma_eta},
    }


# ══════════════════════════════════════════════════════════════════════════════
# Metropolis-within-Gibbs Sampler  (Opción B: implementación "a mano")
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class SVSamplerConfig:
    """Configuración del sampler MCMC."""

    n_iter: int = 10_000  # iteraciones totales
    burn_in: int = 2_000  # descarte inicial
    thin: int = 1  # adelgazamiento

    # Propuestas random walk para M-H
    proposal_h_std: float = 0.3  # std de propuesta para cada h_t
    proposal_mu_std: float = 0.1
    proposal_phi_std: float = 0.02
    proposal_sigma_std: float = 0.02

    # Adaptación (tuning de propuestas durante burn-in)
    adapt: bool = True
    adapt_every: int = 100
    target_accept: float = 0.35  # tasa de aceptación objetivo


class SVMetropolisWithinGibbs:
    """Sampler Metropolis-within-Gibbs para el modelo SV.

    Estrategia:
    1. Para cada t: muestrear h_t | h_{t-1}, h_{t+1}, y_t, params  (M-H)
    2. Muestrear mu | h, phi, sigma_eta  (Gibbs, condicional Normal)
    3. Muestrear phi | h, mu, sigma_eta  (M-H con propuesta random walk)
    4. Muestrear sigma_eta | h, mu, phi  (Gibbs, condicional IG)
    """

    def __init__(
        self,
        y: np.ndarray,
        priors: SVPriors | None = None,
        config: SVSamplerConfig | None = None,
    ):
        self.y = np.asarray(y, dtype=float)
        self.T = len(self.y)
        self.priors = priors or SVPriors()
        self.config = config or SVSamplerConfig()

        # Storage para las cadenas
        self.chain_h: list[np.ndarray] = []
        self.chain_mu: list[float] = []
        self.chain_phi: list[float] = []
        self.chain_sigma_eta: list[float] = []

        # Contadores de aceptación
        self._accept_h = np.zeros(self.T)
        self._total_h = np.zeros(self.T)
        self._accept_phi = 0
        self._total_phi = 0

    # ── Log-densidades ────────────────────────────────────────────────

    @staticmethod
    def _log_obs_density(y_t: float, h_t: float) -> float:
        """log p(y_t | h_t) = -h_t/2 - y_t^2/(2*exp(h_t)) + const"""
        return -0.5 * h_t - 0.5 * y_t**2 * np.exp(-h_t)

    @staticmethod
    def _log_transition(
        h_t: float, h_prev: float, mu: float, phi: float, sigma_eta: float
    ) -> float:
        """log p(h_t | h_{t-1}, mu, phi, sigma_eta)"""
        mean = mu + phi * (h_prev - mu)
        return -0.5 * ((h_t - mean) / sigma_eta) ** 2

    def _log_full_conditional_h(
        self,
        t: int,
        h_t: float,
        h: np.ndarray,
        mu: float,
        phi: float,
        sigma_eta: float,
    ) -> float:
        """Log de la condicional completa de h_t (sin normalizar).

        p(h_t | h_{-t}, y, params) ∝ p(y_t|h_t) * p(h_t|h_{t-1}) * p(h_{t+1}|h_t)
        """
        val = self._log_obs_density(self.y[t], h_t)

        if t == 0:
            # p(h_0) ~ N(mu, sigma_eta^2 / (1-phi^2))
            s0 = sigma_eta / np.sqrt(1 - phi**2)
            val += -0.5 * ((h_t - mu) / s0) ** 2
        else:
            val += self._log_transition(h_t, h[t - 1], mu, phi, sigma_eta)

        if t < self.T - 1:
            val += self._log_transition(h[t + 1], h_t, mu, phi, sigma_eta)

        return val

    # ── Pasos del sampler ─────────────────────────────────────────────

    def _sample_h(
        self,
        h: np.ndarray,
        mu: float,
        phi: float,
        sigma_eta: float,
        proposal_std: float,
    ) -> np.ndarray:
        """Un barrido completo de M-H sobre todos los h_t."""
        h_new = h.copy()
        for t in range(self.T):
            # Propuesta random walk
            h_star = h_new[t] + np.random.normal(0, proposal_std)

            log_alpha = self._log_full_conditional_h(
                t, h_star, h_new, mu, phi, sigma_eta
            ) - self._log_full_conditional_h(t, h_new[t], h_new, mu, phi, sigma_eta)

            if np.log(np.random.uniform()) < log_alpha:
                h_new[t] = h_star
                self._accept_h[t] += 1
            self._total_h[t] += 1

        return h_new

    def _sample_mu(
        self,
        h: np.ndarray,
        phi: float,
        sigma_eta: float,
    ) -> float:
        """Gibbs: mu | h, phi, sigma_eta  (condicional Normal conjugada).

        La condicional es Normal con:
        precision_post = precision_prior + T_eff * (1-phi)^2 / sigma_eta^2
        mean_post = precision_post^{-1} * (precision_prior*mu_0 + ...)
        """
        pr = self.priors
        se2 = sigma_eta**2

        # "Datos efectivos" para mu
        # h_0 contribuye: (h_0 - mu) con varianza se2/(1-phi^2)
        # h_t para t>=1: h_t - phi*h_{t-1} = mu*(1-phi) + sigma_eta*eta_t
        # => contribución a la media de mu: sum(h_t - phi*h_{t-1}) / (1-phi)

        # Prior precision
        tau_prior = 1.0 / pr.sigma_mu**2

        # Likelihood precision from h_0
        tau_h0 = (1.0 - phi**2) / se2

        # Likelihood precision from h_1,...,h_{T-1}
        tau_trans = (self.T - 1) * (1.0 - phi) ** 2 / se2

        tau_post = tau_prior + tau_h0 + tau_trans

        # Weighted mean
        S_trans = np.sum(h[1:] - phi * h[:-1])  # sum of (h_t - phi*h_{t-1})
        mean_num = tau_prior * pr.mu_0 + tau_h0 * h[0] + (1.0 - phi) / se2 * S_trans
        mean_post = mean_num / tau_post
        std_post = np.sqrt(1.0 / tau_post)

        return np.random.normal(mean_post, std_post)

    def _sample_sigma_eta_sq(
        self,
        h: np.ndarray,
        mu: float,
        phi: float,
    ) -> float:
        """Gibbs: sigma_eta^2 | h, mu, phi  (condicional Inverse-Gamma).

        IG(a_post, b_post) donde:
        a_post = a_prior + T/2
        b_post = b_prior + 0.5 * [sum((h_t - mu - phi*(h_{t-1}-mu))^2) + (h_0-mu)^2*(1-phi^2)]
        """
        pr = self.priors

        # Residuos de la transición
        resid = h[1:] - mu - phi * (h[:-1] - mu)
        SSR = np.sum(resid**2)

        # Contribución de h_0
        SSR += (h[0] - mu) ** 2 * (1.0 - phi**2)

        a_post = pr.a_sigma + self.T / 2.0
        b_post = pr.b_sigma + SSR / 2.0

        # Muestrear de IG(a, b) = 1 / Gamma(a, 1/b)
        return 1.0 / np.random.gamma(a_post, 1.0 / b_post)

    def _sample_phi(
        self,
        h: np.ndarray,
        mu: float,
        sigma_eta: float,
        current_phi: float,
        proposal_std: float,
    ) -> float:
        """M-H para phi | h, mu, sigma_eta con propuesta random walk en (-1, 1).

        Prior: (phi+1)/2 ~ Beta(a_phi, b_phi)
        """
        pr = self.priors
        se2 = sigma_eta**2

        # Propuesta: random walk con reflejo en (-1, 1)
        phi_star = current_phi + np.random.normal(0, proposal_std)
        # Reflejo para mantener en (-1, 1)
        while abs(phi_star) >= 1.0:
            if phi_star >= 1.0:
                phi_star = 2.0 - phi_star
            elif phi_star <= -1.0:
                phi_star = -2.0 - phi_star

        def log_target(phi_val: float) -> float:
            if abs(phi_val) >= 1.0:
                return -np.inf
            # Prior: Beta((phi+1)/2 | a, b)
            u = (phi_val + 1.0) / 2.0
            lp = (pr.a_phi - 1) * np.log(u) + (pr.b_phi - 1) * np.log(1 - u)

            # Likelihood de h_0
            lp += 0.5 * np.log(1 - phi_val**2)  # from det of stationary dist
            lp -= (1 - phi_val**2) / (2 * se2) * (h[0] - mu) ** 2

            # Likelihood de transiciones h_1,...,h_{T-1}
            resid = h[1:] - mu - phi_val * (h[:-1] - mu)
            lp -= np.sum(resid**2) / (2 * se2)

            return lp

        log_alpha = log_target(phi_star) - log_target(current_phi)

        self._total_phi += 1
        if np.log(np.random.uniform()) < log_alpha:
            self._accept_phi += 1
            return phi_star
        return current_phi

    # ── Adaptación de propuestas ──────────────────────────────────────

    def _adapt_proposals(self, iteration: int) -> None:
        """Ajusta std de propuestas para acercarse a tasa de aceptación objetivo."""
        cfg = self.config
        target = cfg.target_accept
        factor = 1.1  # factor de ajuste

        # h: tasa promedio
        mask = self._total_h > 0
        h_rate = np.mean(self._accept_h[mask] / self._total_h[mask])
        if h_rate > target + 0.05:
            cfg.proposal_h_std *= factor
        elif h_rate < target - 0.05:
            cfg.proposal_h_std /= factor

        # phi
        if self._total_phi > 0:
            phi_rate = self._accept_phi / self._total_phi
            if phi_rate > target + 0.05:
                cfg.proposal_phi_std *= factor
            elif phi_rate < target - 0.05:
                cfg.proposal_phi_std /= factor

        # Reset counters
        self._accept_h[:] = 0
        self._total_h[:] = 0
        self._accept_phi = 0
        self._total_phi = 0

    # ── Run ───────────────────────────────────────────────────────────

    def run(
        self,
        mu_init: Optional[float] = None,
        phi_init: float = 0.95,
        sigma_eta_init: float = 0.15,
        verbose: bool = True,
    ) -> Dict[str, np.ndarray]:
        """Ejecuta el sampler MCMC.

        Returns
        -------
        dict con claves:
            'mu'        : (n_samples,) muestras posteriores de mu
            'phi'       : (n_samples,) muestras posteriores de phi
            'sigma_eta' : (n_samples,) muestras posteriores de sigma_eta
            'h'         : (n_samples, T) muestras posteriores de log-volatilidad
            'accept_rate_h'   : tasa de aceptación promedio de h
            'accept_rate_phi' : tasa de aceptación de phi
        """
        cfg = self.config
        T = self.T

        # Inicialización
        if mu_init is None:
            # Estimador de momentos: mu ~ log(var(y))
            mu_init = float(np.log(np.var(self.y) + 1e-10))

        mu = mu_init
        phi = phi_init
        sigma_eta = sigma_eta_init
        se2 = sigma_eta**2

        # Inicializar h con estimador simple
        h = np.log(self.y**2 + 1e-6)  # log(y^2) es un estimador ruidoso de h

        # Almacenamiento
        all_mu = []
        all_phi = []
        all_sigma_eta = []
        all_h = []

        total_iter = cfg.n_iter + cfg.burn_in

        for i in range(total_iter):
            # 1. Muestrear h
            h = self._sample_h(h, mu, phi, sigma_eta, cfg.proposal_h_std)

            # 2. Muestrear mu (Gibbs)
            mu = self._sample_mu(h, phi, sigma_eta)

            # 3. Muestrear sigma_eta^2 (Gibbs) → sigma_eta
            se2 = self._sample_sigma_eta_sq(h, mu, phi)
            sigma_eta = np.sqrt(se2)

            # 4. Muestrear phi (M-H)
            phi = self._sample_phi(h, mu, sigma_eta, phi, cfg.proposal_phi_std)

            # Adaptación durante burn-in
            if cfg.adapt and i < cfg.burn_in and (i + 1) % cfg.adapt_every == 0:
                self._adapt_proposals(i)

            # Almacenar post burn-in con thinning
            if i >= cfg.burn_in and (i - cfg.burn_in) % cfg.thin == 0:
                all_mu.append(mu)
                all_phi.append(phi)
                all_sigma_eta.append(sigma_eta)
                all_h.append(h.copy())

            if verbose and (i + 1) % 1000 == 0:
                phase = "burn-in" if i < cfg.burn_in else "sampling"
                print(
                    f"  iter {i + 1}/{total_iter} ({phase}) | "
                    f"mu={mu:.3f} phi={phi:.4f} sigma_eta={sigma_eta:.4f}"
                )

        result = {
            "mu": np.array(all_mu),
            "phi": np.array(all_phi),
            "sigma_eta": np.array(all_sigma_eta),
            "h": np.array(all_h),  # shape (n_samples, T)
        }

        if verbose:
            n_samples = len(all_mu)
            print(f"\nMCMC completado: {n_samples} muestras posteriores.")
            print(f"  mu:        mean={np.mean(all_mu):.4f}, std={np.std(all_mu):.4f}")
            print(
                f"  phi:       mean={np.mean(all_phi):.4f}, std={np.std(all_phi):.4f}"
            )
            print(
                f"  sigma_eta: mean={np.mean(all_sigma_eta):.4f}, "
                f"std={np.std(all_sigma_eta):.4f}"
            )

        return result


# ══════════════════════════════════════════════════════════════════════════════
# Wrapper para PyMC  (Opción C: validación)
# ══════════════════════════════════════════════════════════════════════════════


def fit_sv_pymc(
    y: np.ndarray,
    priors: SVPriors | None = None,
    n_samples: int = 2_000,
    n_chains: int = 4,
    target_accept: float = 0.9,
    tune: int | None = None,
    seed: int = 42,
) -> Any:
    """Ajusta el modelo SV usando PyMC con NUTS.

    Requiere `pip install pymc`.

    Returns
    -------
    arviz.InferenceData  (trace completo con diagnósticos).
    """
    try:
        import pymc as pm
        import pytensor.tensor as pt
    except ImportError:
        raise ImportError("PyMC no está instalado. Ejecuta: pip install pymc")

    pr = priors or SVPriors()
    y = np.asarray(y, dtype=float)

    with pm.Model() as sv_model:
        # Priors
        mu = pm.Normal("mu", mu=pr.mu_0, sigma=pr.sigma_mu)

        # phi via transformación Beta
        phi_raw = pm.Beta("phi_raw", alpha=pr.a_phi, beta=pr.b_phi)
        phi = pm.Deterministic("phi", 2.0 * phi_raw - 1.0)

        sigma_eta = pm.InverseGamma("sigma_eta_sq", alpha=pr.a_sigma, beta=pr.b_sigma)
        sigma = pm.Deterministic("sigma_eta", pt.sqrt(sigma_eta))

        # Proceso latente h: AR(1) estacionario centrado en mu.
        #   h_t = mu + phi*(h_{t-1} - mu) + sigma_eta * eta_t
        #       = mu*(1 - phi) + phi*h_{t-1} + sigma_eta * eta_t
        # Se usa pm.AR (logp vectorizado, sin scan de Python -> mucho más rápido
        # que la construcción recursiva). El estado inicial usa la distribución
        # estacionaria N(mu, sigma_eta^2 / (1 - phi^2)).
        rho = pt.stack([mu * (1.0 - phi), phi])  # [constante, coef AR(1)]
        init_dist = pm.Normal.dist(mu=mu, sigma=sigma / pt.sqrt(1.0 - phi**2))
        h = pm.AR(
            "h",
            rho=rho,
            sigma=sigma,
            init_dist=init_dist,
            constant=True,
            steps=len(y) - 1,
        )

        # Observaciones: y_t | h_t ~ N(0, exp(h_t))
        pm.Normal(
            "y_obs",
            mu=0,
            sigma=pt.exp(h / 2),
            observed=y,
        )

        # Sampleo
        # progressbar=False: evita que el widget de progreso de ipywidgets
        # "trague" el stdout posterior al ejecutar vía nbconvert.
        trace = pm.sample(
            draws=n_samples,
            tune=tune if tune is not None else n_samples,
            chains=n_chains,
            target_accept=target_accept,
            random_seed=seed,
            progressbar=False,
            return_inferencedata=True,
        )

    return trace


def idata_to_mcmc_result(idata: Any) -> Dict[str, np.ndarray]:
    """Convierte el InferenceData de :func:`fit_sv_pymc` al dict que consumen el
    simulador de trayectorias y el pricing.

    El simulador (`trajectory_simulator`) y el pricing (`bayesian_premium`) esperan
    el mismo formato que produce el sampler manual ``SVMetropolisWithinGibbs.run()``:
    un dict con ``'mu'``, ``'phi'``, ``'sigma_eta'`` (cada uno shape ``(n_samples,)``)
    y ``'h'`` (shape ``(n_samples, T)``). Este helper aplana las dimensiones
    ``(chain, draw)`` de la posterior NUTS en un único eje de muestras.
    """
    post = idata.posterior

    def _flat(name: str) -> np.ndarray:
        # stack -> última dimensión es 'sample'; para escalares queda (n,),
        # para h queda (T, n) que trasponemos a (n, T).
        return post[name].stack(sample=("chain", "draw")).values

    return {
        "mu": _flat("mu"),
        "phi": _flat("phi"),
        "sigma_eta": _flat("sigma_eta"),
        "h": _flat("h").T,  # (T, n) -> (n, T)
    }


# ══════════════════════════════════════════════════════════════════════════════
# Diagnósticos
# ══════════════════════════════════════════════════════════════════════════════


def compute_diagnostics(chain: np.ndarray) -> Dict[str, float]:
    """Calcula diagnósticos básicos para una cadena MCMC 1D.

    Parameters
    ----------
    chain : array 1D de muestras.

    Returns
    -------
    dict con mean, std, q025, q975, ess (effective sample size).
    """
    n = len(chain)
    mean = np.mean(chain)
    std = np.std(chain, ddof=1)

    # ESS via autocorrelación (método simple)
    max_lag = min(n // 2, 500)
    centered = chain - mean
    var = np.var(chain, ddof=0)

    if var < 1e-20:
        ess = float(n)
    else:
        rho_sum = 0.0
        for lag in range(1, max_lag + 1):
            rho = np.mean(centered[:-lag] * centered[lag:]) / var
            if rho < 0.05:
                break
            rho_sum += rho
        ess = n / (1 + 2 * rho_sum)

    return {
        "mean": float(mean),
        "std": float(std),
        "q025": float(np.percentile(chain, 2.5)),
        "q975": float(np.percentile(chain, 97.5)),
        "ess": float(max(ess, 1.0)),
        "ess_per_iter": float(max(ess, 1.0) / n),
    }


def summary_table(result: Dict[str, np.ndarray]) -> Dict[str, Dict[str, float]]:
    """Resume diagnósticos de todas las cadenas del resultado MCMC.

    Parameters
    ----------
    result : output de SVMetropolisWithinGibbs.run()

    Returns
    -------
    dict[param_name, diagnostics_dict]
    """
    summary = {}
    for param in ["mu", "phi", "sigma_eta"]:
        if param in result:
            summary[param] = compute_diagnostics(result[param])

    # h: resumir solo algunos puntos representativos
    if "h" in result:
        h = result["h"]
        n_samples, T = h.shape
        for idx in [0, T // 4, T // 2, 3 * T // 4, T - 1]:
            summary[f"h[{idx}]"] = compute_diagnostics(h[:, idx])

    return summary
