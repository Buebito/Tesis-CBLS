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
from typing import Literal, Dict


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
    Toma una serie de retornos y permite muestrear nuevos retornos sintéticos:
    - Elige retornos históricos con reemplazo
    - Les agrega ruido ~ N(0, h^2) donde h viene de Silverman
    """
    def __init__(self, returns: pd.Series):
        clean = returns.dropna().astype(float).values
        self.returns = clean
        self.kernel_std = _silverman_bandwidth(clean)

    def sample(self, size: int) -> np.ndarray:
        base = np.random.choice(self.returns, size=size, replace=True)
        noise = np.random.normal(loc=0.0, scale=self.kernel_std, size=size)
        return base + noise


# ------------------------------------------------------------
# 2. Simulador Monte Carlo de trayectorias de precio
# ------------------------------------------------------------

class MonteCarloPriceSimulator:
    """
    Genera trayectorias de precio futuro asumiendo que:
    - Los retornos porcentuales paso a paso son i.i.d.
    - Cada retorno futuro luce como 'un retorno histórico + ruidito KDE'

    Importante:
    * La resolución temporal del paso simulado es la MISMA que uses
      en `price_series`. Si price_series es por hora -> cada step ~1 hora.
    """

    def __init__(self, price_series: pd.Series, n_sims: int = 2000):
        """
        price_series: Serie de precios (index = timestamps ordenados ASC)
        n_sims: número de trayectorias independientes a simular
        """
        # Ordenar, limpiar
        self.price_series = price_series.sort_index().astype(float)
        # Rendimientos relativos paso a paso
        self.returns = self.price_series.pct_change().dropna()
        # Sampler bootstrap+KDE sobre retornos
        self.sampler = KDEBootstrapSampler(self.returns)
        # Precio inicial = último precio real conocido
        self.start_price = float(self.price_series.iloc[-1])
        self.n_sims = n_sims

    def simulate_paths(self, horizon_steps: int) -> pd.DataFrame:
        """
        Genera 'n_sims' trayectorias de longitud 'horizon_steps'.

        Devuelve:
            DataFrame shape = (horizon_steps+1, n_sims)
            - fila 0 = precio inicial (repetido)
            - filas siguientes = evolución simulada
        """
        # 1. Muestra retornos sintéticos
        total_needed = horizon_steps * self.n_sims
        sampled_returns = self.sampler.sample(total_needed)  # array plano
        sampled_returns = sampled_returns.reshape(horizon_steps, self.n_sims)

        # 2. Convierte retornos porcentuales a factores multiplicativos
        #    r=0.01 -> factor=1.01
        factors = sampled_returns + 1.0  # shape (steps, sims)

        # 3. Creamos DataFrame de factores
        factors_df = pd.DataFrame(factors)

        # 4. Insertamos fila inicial de "1.0" (identidad multiplicativa)
        factors_df.loc[-1] = 1.0
        factors_df = factors_df.sort_index()  # ahora -1,0,1,... => 0,1,2,...

        # 5. Hacemos producto acumulado columna a columna y escalamos
        price_paths = self.start_price * factors_df.cumprod()
        price_paths.index = range(price_paths.shape[0])  # limpiar índice
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

    # 1. cargar serie del activo
    series = load_price_series(
        csv_path=csv_path,
        asset_col=asset_col,
        resample_rule=resample_rule,
    )

    # 2. inferir pasos necesarios para horizon_days
    horizon_steps = infer_horizon_steps(series, horizon_days=horizon_days)

    # 3. armar simulador y correrlo
    mc = MonteCarloPriceSimulator(series, n_sims=n_sims)
    price_paths = mc.simulate_paths(horizon_steps=horizon_steps)

    # 4. plots
    if do_plots:
        mc.plot_paths(price_paths)
        mc.plot_percentiles(price_paths)
        mc.plot_terminal_hist(price_paths)

    # 5. resumen de riesgo
    risk_summary = mc.summarize_risk(price_paths)

    return price_paths, risk_summary