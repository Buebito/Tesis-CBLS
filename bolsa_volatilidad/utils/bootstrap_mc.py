"""
simulator.py
-------------
Simulación Monte Carlo de trayectorias futuras de precio (BTC / ETH)
a partir de series históricas de alta frecuencia.

Flujo:
1. Carga histórico desde ../data/processed/historic_prices.csv
2. Elige activo ("BTC" o "ETH")
3. (Opcional) downsamplea a una frecuencia más cómoda (ej. 1H)
4. Ajusta un bootstrap-KDE sobre retornos históricos
5. Genera N trayectorias futuras hasta un horizonte dado (en días)
6. Grafica y resume riesgos

Este módulo está pensado para integrarse con el motor de riesgo
(LTV / margin call / liquidación) en el futuro.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from typing import Literal, Dict, Optional
from dataclasses import dataclass

#------------------------------------------------------------
# Configuración de sesgos en el muestreo bootstrap
#------------------------------------------------------------

@dataclass
class BiasConfig:
    # 1) Drift
    use_log_returns: bool = True            # usar log-retornos (recomendado)
    drift_mode: Literal["none","zero","shift"] = "zero"
    mu_shift_per_step: float = 0.0          # solo si drift_mode == "shift" (negativo para sesgo bajista)

    # 2) Tilt de muestreo (reponderar histórico)
    tilt_theta: Optional[float] = None      # e.g. -4.0 (más peso a retornos negativos)

    # 3) Transformación asimétrica + winsor
    pos_scale: float = 1.0                  # <1 encoge subidas
    neg_scale: float = 1.0                  # >1 amplifica bajadas
    clip_pos_q: Optional[float] = None      # e.g. 0.995 capea cola positiva
    clip_neg_q: Optional[float] = None      # opcional, por si quisieras capear cola negativa también


# ------------------------------------------------------------
# 1. Utilidades estadísticas básicas
# ------------------------------------------------------------

def _silverman_bandwidth(x: np.ndarray) -> float:
    """
    Aproximación de la regla de Silverman para el ancho de banda de KDE en 1D.
    Esto nos da la desviación estándar del 'ruido' gaussiano que vamos a
    inyectar a los retornos bootstrapeados.

    Fórmula clásica:
        h = 0.9 * min(std, IQR/1.34) * n^(-1/5)

    Regresamos h como escala (std del kernel).
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return 1e-8  # fallback pequeño

    std = np.std(x, ddof=1)
    q75, q25 = np.percentile(x, [75, 25])
    iqr = q75 - q25
    sigma = min(std, iqr / 1.34) if iqr > 0 else std
    h = 0.9 * sigma * (n ** (-1.0 / 5.0))

    # Evitar 0 en casos degenerados
    return max(h, 1e-8)


class KDEBootstrapSampler:
    """
    Bootstrap+KDE con opciones de:
    - tilt (pondera más retornos negativos: theta<0)
    - winsorización de colas
    - escalado asimétrico (pos_scale / neg_scale)
    """
    def __init__(self, returns: pd.Series, bias: Optional[BiasConfig] = None):
        clean = returns.dropna().astype(float).values
        self.returns = clean
        self.bias = bias or BiasConfig()
        self.kernel_std = _silverman_bandwidth(clean)

        # Precalcular umbrales para winsor (sobre histórico)
        if self.bias.clip_pos_q is not None:
            pos = self.returns[self.returns > 0]
            self.pos_clip_val = np.quantile(pos, self.bias.clip_pos_q) if len(pos)>0 else None
        else:
            self.pos_clip_val = None

        if self.bias.clip_neg_q is not None:
            neg = self.returns[self.returns < 0]
            self.neg_clip_val = np.quantile(neg, 1.0 - self.bias.clip_neg_q) if len(neg)>0 else None
        else:
            self.neg_clip_val = None

        # Probabilidades con tilt (suaviza con softmax)
        self.probs = None
        if self.bias.tilt_theta is not None and np.isfinite(self.bias.tilt_theta):
            # softmax(theta * r) estable
            scores = self.bias.tilt_theta * self.returns
            scores = scores - np.max(scores)
            w = np.exp(scores)
            self.probs = w / w.sum()

    def _apply_winsor_and_scale(self, r: np.ndarray) -> np.ndarray:
        # winsor positivo
        if self.pos_clip_val is not None:
            r = np.where(r > self.pos_clip_val, self.pos_clip_val, r)
        # winsor negativo (opcional)
        if self.neg_clip_val is not None:
            r = np.where(r < self.neg_clip_val, self.neg_clip_val, r)
        # escalado asimétrico
        if self.bias.pos_scale != 1.0 or self.bias.neg_scale != 1.0:
            r = np.where(r >= 0.0, r * self.bias.pos_scale, r * self.bias.neg_scale)
        return r

    def sample(self, size: int) -> np.ndarray:
        base = np.random.choice(self.returns, size=size, replace=True, p=self.probs)
        noise = np.random.normal(loc=0.0, scale=self.kernel_std, size=size)
        r = base + noise
        r = self._apply_winsor_and_scale(r)

        # Drift adjustments (para log o pct lo tratamos igual, es por-step)
        if self.bias.drift_mode == "zero":
            r = r - np.mean(self.returns)      # re-centra a 0 el retorno esperado
        elif self.bias.drift_mode == "shift" and self.bias.mu_shift_per_step != 0.0:
            r = r + self.bias.mu_shift_per_step  # permite sesgo bajista controlado

        return r



# ------------------------------------------------------------
# 2. Simulador Monte Carlo de trayectorias de precio
# ------------------------------------------------------------

class MonteCarloPriceSimulator:
    def __init__(self, price_series: pd.Series, n_sims: int = 2000, bias: Optional[BiasConfig] = None):
        self.price_series = price_series.sort_index().astype(float)
        self.bias = bias or BiasConfig()

        if self.bias.use_log_returns:
            self.returns = np.log(self.price_series).diff().dropna()   # log-retornos
        else:
            self.returns = self.price_series.pct_change().dropna()     # pct returns

        self.sampler = KDEBootstrapSampler(self.returns, bias=self.bias)
        self.start_price = float(self.price_series.iloc[-1])
        self.n_sims = n_sims

    def simulate_paths(self, horizon_steps: int) -> pd.DataFrame:
        total_needed = horizon_steps * self.n_sims
        sampled = self.sampler.sample(total_needed).reshape(horizon_steps, self.n_sims)

        if self.bias.use_log_returns:
            # factors = exp(r_log)
            factors = np.exp(sampled)
        else:
            # factors = 1 + r_pct
            factors = sampled + 1.0

        factors_df = pd.DataFrame(factors)
        factors_df.loc[-1] = 1.0
        factors_df = factors_df.sort_index()
        price_paths = self.start_price * factors_df.cumprod()
        price_paths.index = range(price_paths.shape[0])
        price_paths.index.name = "step"
        return price_paths

    # --------- Plots de diagnóstico ---------

    @staticmethod
    def plot_paths(price_paths: pd.DataFrame, max_paths: int = 100):
        """
        Grafica varias trayectorias (hasta max_paths).
        Colorea cada trayectoria verde si termina arriba del inicio,
        naranja si termina abajo.
        """
        start_vals = price_paths.iloc[0, :]
        end_vals = price_paths.iloc[-1, :]

        # Seleccionar subconjunto para no saturar la gráfica
        cols_to_plot = min(max_paths, price_paths.shape[1])
        fig, ax = plt.subplots(figsize=(10, 6))
        for i in range(cols_to_plot):
            series_i = price_paths.iloc[:, i]
            c = "#006d77" if series_i.iloc[-1] >= series_i.iloc[0] else "#e29578"
            ax.plot(series_i.values, alpha=0.4, linewidth=1.0, color=c)

        ax.set_title("Trayectorias simuladas (precio futuro)")
        ax.set_xlabel("Step futuro")
        ax.set_ylabel("Precio simulado")
        ax.grid(True, alpha=0.2)
        plt.tight_layout()
        plt.show()

    @staticmethod
    def plot_percentiles(price_paths: pd.DataFrame):
        """
        Grafica percentiles 25%, 50% (mediana) y 75% paso a paso.
        Esto es útil como 'banda de escenario pesimista / base / optimista'.
        """
        desc = price_paths.T.describe(percentiles=[0.25, 0.5, 0.75])
        p25 = desc.loc["25%"].values
        p50 = desc.loc["50%"].values
        p75 = desc.loc["75%"].values

        steps = np.arange(len(p25))
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(steps, p50, label="P50 (mediana)", color="#003049")
        ax.plot(steps, p25, label="P25", color="#d62828", linestyle="--", alpha=0.7)
        ax.plot(steps, p75, label="P75", color="#d62828", linestyle="--", alpha=0.7)

        ax.set_title("Banda de percentiles simulados")
        ax.set_xlabel("Step futuro")
        ax.set_ylabel("Precio simulado")
        ax.legend()
        ax.grid(True, alpha=0.2)
        plt.tight_layout()
        plt.show()

    @staticmethod
    def plot_terminal_hist(price_paths: pd.DataFrame):
        """
        Histograma del retorno al final del horizonte:
        (precio_final / precio_inicial - 1)
        Esto da la distribución de 'qué tan mal o qué tan bien puedo acabar'.
        """
        first_vals = price_paths.iloc[0, :].values
        last_vals = price_paths.iloc[-1, :].values
        terminal_return = (last_vals / first_vals) - 1.0

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.hist(terminal_return, bins=50, alpha=0.8, edgecolor="k")
        ax.set_title("Distribución de retorno al final del horizonte")
        ax.set_xlabel("Retorno (%) final vs inicio")
        ax.set_ylabel("Frecuencia")
        ax.grid(True, alpha=0.2)
        plt.tight_layout()
        plt.show()

    @staticmethod
    def summarize_risk(price_paths: pd.DataFrame) -> Dict[str, float]:
        """
        Regresa métricas rápidas de riesgo al horizonte final:
        - Prob caída ≥20%
        - Prob caída ≥40%
        - Valor mediano final
        - Percentil 5% del precio final (escenario feo extremo)

        Estas métricas son candidatas directas para
        'probabilidad de margin call / liquidación'.
        """
        first_vals = price_paths.iloc[0, :].values
        last_vals = price_paths.iloc[-1, :].values
        drops = (last_vals / first_vals) - 1.0  # retornos finales

        prob_drop_20 = np.mean(drops <= -0.20)
        prob_drop_40 = np.mean(drops <= -0.40)

        summary = {
            "start_price": float(first_vals[0]),
            "median_final_price": float(np.median(last_vals)),
            "p05_final_price": float(np.percentile(last_vals, 5)),
            "prob_drop_20pct_or_more": float(prob_drop_20),
            "prob_drop_40pct_or_more": float(prob_drop_40),
        }
        return summary


# ------------------------------------------------------------
# 3. Carga de datos y wrapper de alto nivel
# ------------------------------------------------------------

def load_price_series(
    csv_path: str,
    asset_col: Literal["BTC", "ETH"] = "BTC",
    date_col: str = "date",
    resample_rule: str = "1H",
) -> pd.Series:
    """
    Lee el CSV histórico y regresa una Serie de precios limpia
    lista para alimentar al simulador.

    Parámetros:
    - csv_path: ruta al histórico. Ej: "../data/processed/historic_prices.csv"
    - asset_col: "BTC" o "ETH" (usamos la columna spot, NO HIGH/LOW)
    - date_col: nombre de la columna timestamp en el CSV
    - resample_rule:
        * "1H"  -> tomamos el último precio de cada hora
        * None  -> no se hace resample (usa cada minuto)
        * "5T"  -> 5 minutos, etc.
      Esto existe porque simular 1 año minuto-a-minuto es enorme.
      Para horizontes largos conviene bajar a 1H.

    Regresa:
    - pd.Series con índice datetime y valores float.
    """
    df = pd.read_csv(csv_path)
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col)
    df = df.set_index(date_col)

    if asset_col not in df.columns:
        raise ValueError(
            f"La columna {asset_col} no existe en el CSV. "
            f"Columnas disponibles: {list(df.columns)}"
        )

    series = df[asset_col].astype(float)

    if resample_rule is not None:
        # downsample: tomamos el último precio observado en cada ventana
        series = series.resample(resample_rule).last().dropna()

    return series


def infer_horizon_steps(series: pd.Series, horizon_days: int) -> int:
    """
    Calcula cuántos 'steps' futuros corresponden a X días,
    basado en la resolución real de la serie.

    Ejemplo:
    - Si la serie está en 1H -> 24 steps por día.
    - Si está en 1min -> 1440 steps por día.

    horizon_days: típicamente 365 para "1 año".
    """
    # mediana de la diferencia de timestamps
    deltas = series.index.to_series().diff().dropna()
    median_delta = deltas.median()  # Timedelta

    # minutos por step
    minutes_per_step = median_delta / pd.Timedelta(minutes=1)

    # steps por día = (24*60)/minutes_per_step
    steps_per_day = (24 * 60) / minutes_per_step
    total_steps = int(round(steps_per_day * horizon_days))

    return total_steps


def run_future_price_simulation(
    csv_path: str = "../data/processed/historic_prices.csv",
    asset_col: Literal["BTC", "ETH"] = "BTC",
    horizon_days: int = 365,
    n_sims: int = 2000,
    resample_rule: str = "1H",
    do_plots: bool = True,
    bias: Optional[BiasConfig] = None,
):
    """
    Función de alto nivel:
    - Carga histórico
    - Ajusta simulador
    - Corre Monte Carlo para 'horizon_days'
    - Grafica diagnósticos (opcional)
    - Devuelve:
        * price_paths (DataFrame pasos x simulaciones)
        * risk_summary (dict)

    Ejemplo de uso:
        paths, summary = run_future_price_simulation(
            asset_col="BTC",
            horizon_days=365,
            n_sims=2000,
            resample_rule="1H",
        )
    """
    series = load_price_series(csv_path=csv_path, asset_col=asset_col, resample_rule=resample_rule)
    horizon_steps = infer_horizon_steps(series, horizon_days=horizon_days)

    mc = MonteCarloPriceSimulator(series, n_sims=n_sims, bias=bias)
    price_paths = mc.simulate_paths(horizon_steps=horizon_steps)

    if do_plots:
        mc.plot_paths(price_paths)
        mc.plot_percentiles(price_paths)
        mc.plot_terminal_hist(price_paths)

    risk_summary = mc.summarize_risk(price_paths)

    return price_paths, risk_summary


def extract_scenario_series(price_paths: pd.DataFrame,
                            hist_index: pd.DatetimeIndex,
                            pessimistic_q: float = 5,
                            base_q: float = 50,
                            optimistic_q: float = 95) -> dict:
    """
    Convierte el cubo Monte Carlo (price_paths) en 3 trayectorias únicas:
    - pesimista (pessimistic_q)
    - base (base_q)
    - optimista (optimistic_q)

    price_paths: DataFrame (steps_futuros x n_sims)
    hist_index: índice datetime del histórico original ya resampleado
                (lo usamos para inferir timestamp base y frecuencia)

    Return:
        {
          "pessimistic": pd.Series(...),
          "base": pd.Series(...),
          "optimistic": pd.Series(...)
        }
        Todas con un DatetimeIndex futuro.
    """

    # 1. Percentiles por fila (es decir, en cada paso futuro)
    # describe() necesita los percentiles como decimales [0-1]
    qs = [pessimistic_q/100.0, base_q/100.0, optimistic_q/100.0]
    desc = price_paths.T.describe(percentiles=qs)

    # Pandas etiqueta esos percentiles como "5%", "50%", etc.
    pess_key = f"{pessimistic_q}%"
    base_key = f"{base_q}%"
    opt_key = f"{optimistic_q}%"

    pess_path = desc.loc[pess_key].values  # array largo T+1
    base_path = desc.loc[base_key].values
    opt_path  = desc.loc[opt_key].values

    # 2. Construir índice futuro:
    #    - último timestamp histórico como punto inicial
    #    - frecuencia = mediana del delta histórico
    last_ts = hist_index[-1]
    freq = hist_index.to_series().diff().dropna().median()

    future_index = pd.date_range(
        start=last_ts,
        periods=price_paths.shape[0],  # mismo largo que las trayectorias
        freq=freq
    )

    # 3. Regresar como Series con índice temporal
    scenarios = {
        "pessimistic": pd.Series(pess_path, index=future_index, name="price"),
        "base":        pd.Series(base_path, index=future_index, name="price"),
        "optimistic":  pd.Series(opt_path, index=future_index, name="price"),
    }

    return scenarios
