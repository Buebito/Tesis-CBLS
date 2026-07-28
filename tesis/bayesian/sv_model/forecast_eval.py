"""
tesis/bayesian/sv_model/forecast_eval.py
----------------------------------------
Evaluación de pronóstico out-of-sample (Fase 4, Cap. 8).

Provee métricas *one-step-ahead* sobre un conjunto de test para comparar el modelo
de Volatilidad Estocástica (SV) contra benchmarks (GARCH(1,1)-t, Gaussiano i.i.d.):

- **Log-score**   : log-verosimilitud predictiva de cada retorno realizado (↑ mejor).
- **CRPS**        : Continuous Ranked Probability Score de la densidad predictiva (↓ mejor).
- **Coverage 95%**: fracción de retornos dentro del IC predictivo 95% (objetivo 0.95).
- **PIT**         : Probability Integral Transform; si el modelo está calibrado, los PIT
                    son Uniforme(0,1) (test KS).

Para el SV, la densidad predictiva de un paso requiere la distribución de filtrado
p(h_t | y_{1:t-1}); se aproxima con un **filtro de partículas bootstrap**. Los parámetros
se fijan en la media posterior (Fase 2) y el estado inicial se toma de la posterior de
h_T al final del train, condicionando el pronóstico en el estado de volatilidad vigente.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
from scipy.stats import norm, t as tdist, kstest

try:
    import properscoring as _ps
    _HAS_PS = True
except Exception:  # pragma: no cover
    _HAS_PS = False


def _crps_ensemble(obs: float, samples: np.ndarray) -> float:
    """CRPS de una observación contra una muestra predictiva (usa properscoring si está)."""
    if _HAS_PS:
        return float(_ps.crps_ensemble(obs, samples))
    # Fallback: forma de energía CRPS = E|X-y| - 0.5 E|X-X'|
    x = np.sort(samples)
    n = len(x)
    e1 = np.mean(np.abs(x - obs))
    # E|X-X'| via estadístico de orden: (2/n^2) sum (2i-n+1) x_i  (i desde 0)
    i = np.arange(n)
    e2 = (2.0 / (n * n)) * np.sum((2 * i - n + 1) * x)
    return float(e1 - 0.5 * e2)


# ══════════════════════════════════════════════════════════════════════════════
# Filtro de partículas para el SV
# ══════════════════════════════════════════════════════════════════════════════


def sv_particle_filter(
    y: np.ndarray,
    mu: float,
    phi: float,
    sigma_eta: float,
    n_particles: int = 4000,
    h_init: Optional[np.ndarray] = None,
    n_crps_samples: int = 1000,
    seed: int = 0,
) -> Dict[str, np.ndarray]:
    """Filtro de partículas bootstrap: densidades predictivas one-step del SV.

    Parameters
    ----------
    y : retornos de test (centrados), shape (T,).
    mu, phi, sigma_eta : parámetros del SV (media posterior).
    n_particles : número de partículas.
    h_init : muestras del estado inicial h (p. ej. posterior de h_T al final del train).
        Si None, se inicializa de la distribución estacionaria N(mu, sigma^2/(1-phi^2)).
    seed : semilla.

    Returns
    -------
    dict con arrays de longitud T: 'logscore', 'crps', 'pit', 'pred_vol'.
    """
    rng = np.random.default_rng(seed)
    T = len(y)

    if h_init is not None and len(h_init) > 0:
        h = rng.choice(np.asarray(h_init, dtype=float), size=n_particles, replace=True)
    else:
        h = rng.normal(mu, sigma_eta / np.sqrt(1.0 - phi**2), size=n_particles)

    logscore = np.empty(T)
    crps = np.empty(T)
    pit = np.empty(T)
    pred_vol = np.empty(T)

    for t in range(T):
        # Paso de predicción: propagar log-volatilidad -> predictiva de y_t
        h = mu + phi * (h - mu) + sigma_eta * rng.standard_normal(n_particles)
        sd = np.exp(h / 2.0)

        dens = norm.pdf(y[t], 0.0, sd)              # densidad por partícula
        logscore[t] = np.log(dens.mean() + 1e-300)  # mezcla sobre partículas
        pit[t] = norm.cdf(y[t] / sd).mean()         # CDF predictiva en y_t
        pred_vol[t] = np.sqrt((sd**2).mean())

        y_pred = sd[:n_crps_samples] * rng.standard_normal(min(n_crps_samples, n_particles))
        crps[t] = _crps_ensemble(y[t], y_pred)

        # Paso de actualización: pesos por verosimilitud y remuestreo
        total = dens.sum()
        if total <= 0 or not np.isfinite(total):
            continue  # partículas degeneradas: mantener estado
        w = dens / total
        idx = rng.choice(n_particles, size=n_particles, p=w)
        h = h[idx]

    return {"logscore": logscore, "crps": crps, "pit": pit, "pred_vol": pred_vol}


# ══════════════════════════════════════════════════════════════════════════════
# Benchmarks
# ══════════════════════════════════════════════════════════════════════════════


def garch_onestep_eval(
    y_train: np.ndarray,
    y_test: np.ndarray,
    n_crps_samples: int = 1500,
    seed: int = 0,
) -> Dict[str, np.ndarray]:
    """Evaluación one-step de GARCH(1,1) con errores t, filtrando por el test.

    Ajusta GARCH sobre el train (en %), luego propaga la varianza condicional por el
    test usando los retornos realizados. Densidades predictivas en unidades de retorno
    crudo (comparables con el SV).
    """
    from arch import arch_model

    rng = np.random.default_rng(seed)
    am = arch_model(np.asarray(y_train) * 100.0, mean="Zero", vol="GARCH", p=1, q=1, dist="t")
    res = am.fit(disp="off")
    w = res.params["omega"]; a = res.params["alpha[1]"]; b = res.params["beta[1]"]
    nu = res.params["nu"]

    s2 = float(res.conditional_volatility[-1] ** 2)   # última var. condicional del train (%^2)
    prev_r2 = float((np.asarray(y_train)[-1] * 100.0) ** 2)
    scale = (nu - 2.0) / nu                             # var(t_nu) = nu/(nu-2)

    yte = np.asarray(y_test) * 100.0
    T = len(yte)
    logscore = np.empty(T); crps = np.empty(T); pit = np.empty(T); pred_vol = np.empty(T)

    for i in range(T):
        s2 = w + a * prev_r2 + b * s2
        sd_pct = np.sqrt(s2)
        lam = sd_pct * np.sqrt(scale)                  # escala de la t
        z = yte[i] / lam
        pdf_raw = (tdist.pdf(z, nu) / lam) * 100.0     # a unidades crudas
        logscore[i] = np.log(pdf_raw + 1e-300)
        pit[i] = float(tdist.cdf(z, nu))
        pred_vol[i] = sd_pct / 100.0
        samp = (lam * tdist.rvs(nu, size=n_crps_samples, random_state=rng)) / 100.0
        crps[i] = _crps_ensemble(yte[i] / 100.0, samp)
        prev_r2 = yte[i] ** 2

    persistence = float(a + b)
    return {"logscore": logscore, "crps": crps, "pit": pit, "pred_vol": pred_vol,
            "persistence": persistence, "nu": float(nu)}


def gaussian_iid_eval(
    y_train: np.ndarray,
    y_test: np.ndarray,
    n_crps_samples: int = 1500,
    seed: int = 0,
) -> Dict[str, np.ndarray]:
    """Benchmark Gaussiano i.i.d. con volatilidad constante (std del train)."""
    rng = np.random.default_rng(seed)
    s = float(np.std(y_train))
    yte = np.asarray(y_test, dtype=float)
    logscore = norm.logpdf(yte, 0.0, s)
    pit = norm.cdf(yte / s)
    samp = s * rng.standard_normal(n_crps_samples)
    crps = np.array([_crps_ensemble(v, samp) for v in yte])
    pred_vol = np.full(len(yte), s)
    return {"logscore": logscore, "crps": crps, "pit": pit, "pred_vol": pred_vol}


def summarize_eval(name: str, res: Dict[str, np.ndarray]) -> Dict[str, float]:
    """Resume un resultado de evaluación en métricas escalares."""
    pit = res["pit"]
    return {
        "modelo": name,
        "log_score": float(np.mean(res["logscore"])),
        "CRPS": float(np.mean(res["crps"])),
        "coverage_95": float(np.mean((pit > 0.025) & (pit < 0.975))),
        "PIT_KS_pvalue": float(kstest(pit, "uniform").pvalue),
    }
