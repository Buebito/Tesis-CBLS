"""
Volatility Buffer Optimizer (cliente):
-------------------------------------

Objetivo (solo lado cliente):
Dado un préstamo respaldado por cripto con LTV inicial L0 y umbral de liquidación Lliq,
calcular el tamaño de "bolsa de volatilidad" (colateral extra en COINS) necesario para
que el préstamo **nunca** se liquide a lo largo de una ventana temporal (p. ej., 1 año)
o hasta el fin de la serie histórica, y encontrar el tamaño mínimo que asegura
**≥ 90%** de supervivencia (sin liquidación) considerando múltiples fechas de inicio.

Notas clave:
- No se modelan intereses, fees, ni comportamiento del cliente. Es un cálculo
  determinista del peor caso dado el trayecto de precios.
- Política: sólo **agregamos** colateral cuando hace falta (nunca retiramos).
- La bolsa se expresa en: (a) porcentaje del colateral inicial, (b) coins, (c) USD.
- Resultado principal: percentil q (p. ej., 0.90) del requerimiento de bolsa
  sobre múltiples fechas de inicio ⇒ si se provisiona al menos ese % de bolsa,
  se evita liquidación en ≥ q de los escenarios históricos.

Fundamento:
- Sea P el principal (USD), p0 el precio al inicio, pmin el mínimo en el futuro
  (dentro de la ventana). Con colateral inicial c0 = P/(L0·p0), el mínimo colateral
  para **no** liquidar en todo el trayecto es: creq = P/(Lliq·pmin).
  La bolsa requerida (coins) es max(0, creq − c0). En **porcentaje** de c0:
      buffer_pct = max(0, (L0/Lliq) · (p0/pmin) − 1)
  Obs.: P cancela ⇒ el % de bolsa **no depende** del tamaño del préstamo.

Entradas de datos:
- CSV con columnas: `date` (datetime), y una o más columnas de precio como `BTC`, `ETH`.
  Índice datetime a resolución minutal (o similar) y orden ascendente.

API principal:
- `compute_required_buffer_distribution(...)` → DataFrame con buffer requerido por
   fecha de inicio (p0, pmin, buffer_%/coins/usd).
- `find_buffer_for_target_success(...)` → tamaño mínimo de bolsa (percentil q).

Incluye dos modos de horizonte:
- "end": desde cada inicio hasta el fin de la serie (usa sufijos mínimos O(n)).
- Ventana fija en minutos (p. ej., 365 días × 1440 min) con mínimos deslizantes O(n).

Requisitos: numpy, pandas.
"""

from __future__ import annotations
import math
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

def load_price_df(path: str) -> pd.DataFrame:
    """Carga `historic_prices.csv` con columna 'date' y columnas de precio por coin.
    Devuelve DataFrame con índice datetime ascendente.
    """
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("El índice debe ser DatetimeIndex.")
    return df

# ---------------------------------------------------------------------------
# Utilidades de mínimos futuros / en ventana
# ---------------------------------------------------------------------------

def suffix_min(a: np.ndarray) -> np.ndarray:
    """Mínimo acumulado desde el final (modo 'end').
    out[i] = min(a[i:]). O(n).
    """
    out = np.empty_like(a)
    cur = math.inf
    for i in range(len(a) - 1, -1, -1):
        v = a[i]
        if v < cur:
            cur = v
        out[i] = cur
    return out


def window_min(a: np.ndarray, w: int) -> np.ndarray:
    """Mínimo deslizante sobre ventana [i, i+w] inclusiva de longitud w+1.
    Devuelve array m de tamaño len(a) tal que m[i] = min(a[i : i+w]). Para i > len(a)-w-1,
    el valor no es válido (puedes truncar externamente). O(n) con deque.
    """
    if w < 0:
        raise ValueError("w debe ser >= 0")
    n = len(a)
    m = np.empty(n, dtype=a.dtype)
    dq: deque[int] = deque()
    for i in range(n):
        # eliminar índices fuera de la ventana actual [i-w, i]
        while dq and dq[0] < i - w:
            dq.popleft()
        # mantener deque en orden creciente por valor
        while dq and a[dq[-1]] >= a[i]:
            dq.pop()
        dq.append(i)
        # el mínimo de ventana que termina en i es a[dq[0]];
        # para ventana [i-w, i], el inicio válido es cuando i >= w
        if i >= w:
            m[i - w] = a[dq[0]]  # asigna al índice de inicio de ventana
    return m  # los últimos w valores quedan sin usar por construcción

# ---------------------------------------------------------------------------
# Núcleo de cálculo de bolsa requerida
# ---------------------------------------------------------------------------

@dataclass
class BufferResult:
    start_ts: pd.Timestamp
    p0: float
    pmin: float
    buffer_pct_of_init_collateral: float
    buffer_coins: float
    buffer_usd_at_p0: float


def _compute_row_metrics(p0: float, pmin: float, L0: float, Lliq: float,
                         principal_usd: float) -> Tuple[float, float, float]:
    """Devuelve (buffer_%_c0, buffer_coins, buffer_usd_at_p0).
    - c0 = principal/(L0*p0)
    - creq = principal/(Lliq*pmin)
    - buffer = max(0, creq - c0)
    - buffer_% = buffer/c0
    USD de la bolsa se calcula al precio inicial p0
    (puedes preferir valorarla a pmin si tu bolsa es cash → cámbialo si hace falta).
    """
    if p0 <= 0 or pmin <= 0:
        return float("nan"), float("nan"), float("nan")
    c0 = principal_usd / (L0 * p0)
    creq = principal_usd / (Lliq * pmin)
    buf_coins = max(0.0, creq - c0)
    buf_pct = 0.0 if c0 == 0 else buf_coins / c0
    buf_usd_p0 = buf_coins * p0
    return buf_pct, buf_coins, buf_usd_p0


def compute_required_buffer_distribution(
    price_df: pd.DataFrame,
    coin: str,
    init_ltv: float = 0.50,
    liq_ltv: float = 0.80,
    principal_usd: float = 10_000.0,
    mode: str = "end",  # "end" | "window"
    window_minutes: Optional[int] = None,
    start_stride: int = 60,  # probar inicios cada 60 min para eficiencia
) -> pd.DataFrame:
    """Calcula la bolsa mínima necesaria por fecha de inicio para **no** liquidar.

    - price_df: DataFrame con índice datetime y columna `coin`.
    - coin: nombre de la columna (p. ej., 'BTC').
    - init_ltv, liq_ltv: L0 y Lliq.
    - principal_usd: sólo afecta a las métricas en coins/USD (no al %).
    - mode='end': usa todo el camino hasta fin de serie.
    - mode='window': requiere `window_minutes`; considera [t, t+window].
    - start_stride: usa cada k-ésimo minuto como inicio para acelerar.

    Devuelve DataFrame con columnas:
      ['start_ts','p0','pmin','buffer_pct_of_init_collateral',
       'buffer_coins','buffer_usd_at_p0']
    """
    if coin not in price_df.columns:
        raise ValueError(f"Columna {coin} no encontrada en price_df")

    prices = price_df[coin].astype(float).values
    idx = price_df.index
    n = len(prices)
    if n < 2:
        raise ValueError("Serie demasiado corta")

    if mode == "end":
        pmin_future = suffix_min(prices)
        valid_starts = np.arange(0, n - 1, start_stride)
        p0s = prices[valid_starts]
        pmins = pmin_future[valid_starts]
        starts_ts = idx[valid_starts]

    elif mode == "window":
        if window_minutes is None or window_minutes <= 0:
            raise ValueError("Para mode='window' especifica window_minutes > 0")
        # m[i] es el mínimo en [i, i+window]
        m = window_min(prices, window_minutes)
        valid = n - window_minutes
        if valid <= 1:
            raise ValueError("Ventana mayor o similar a la longitud de la serie")
        valid_starts = np.arange(0, valid, start_stride)
        p0s = prices[valid_starts]
        pmins = m[valid_starts]
        starts_ts = idx[valid_starts]
    else:
        raise ValueError("mode debe ser 'end' o 'window'")

    # Vectoriza el cálculo
    # fórmula equivalente para % (independiente de principal):
    # buf_pct = max(0, (L0/Lliq)*(p0/pmin) - 1)
    with np.errstate(divide='ignore', invalid='ignore'):
        ratio = (init_ltv / liq_ltv) * (p0s / pmins)
    buf_pct = np.maximum(0.0, ratio - 1.0)

    # Para coins/USD sí necesitamos principal
    c0s = principal_usd / (init_ltv * p0s)
    creqs = principal_usd / (liq_ltv * pmins)
    buf_coins = np.maximum(0.0, creqs - c0s)
    buf_usd_p0 = buf_coins * p0s

    out = pd.DataFrame(
        {
            "fecha_inicio": starts_ts,
            "p0": p0s,
            "pmin": pmins,
            "buffer_pct_of_init_collateral": buf_pct,
            #"buffer_coins": buf_coins,
            "buffer_usd_at_p0": buf_usd_p0,
        }
    )
    return out

# ---------------------------------------------------------------------------
# Selección de bolsa objetivo por nivel de éxito (e.g., 90%)
# ---------------------------------------------------------------------------

def find_buffer_for_target_success(
    dist_df: pd.DataFrame,
    q: float = 0.90,
    metric: str = "buffer_pct_of_init_collateral",
) -> float:
    """Devuelve el percentil q de la métrica elegida (p. ej., 0.90 para 90%).
    Interpr.: si aprovisionas al menos este valor de la métrica, evitas liquidación
    en ≥ q de los escenarios (fechas de inicio muestreadas).
    """
    if metric not in dist_df.columns:
        raise ValueError(f"Métrica {metric} no existe en dist_df")
    s = pd.Series(dist_df[metric]).replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return float("nan")
    return float(s.quantile(q))

# ---------------------------------------------------------------------------
# Atajos de conveniencia
# ---------------------------------------------------------------------------

def summarize_for_coin(
    price_df: pd.DataFrame,
    coin: str,
    init_ltv: float = 0.50,
    liq_ltv: float = 0.80,
    principal_usd: float = 10_000.0,
    mode: str = "window",
    window_minutes: int = 525_600,  # 365 días
    start_stride: int = 60,
    target_q: float = 0.90,
) -> Dict[str, float]:
    """Construye la distribución y devuelve un resumen con el tamaño de bolsa
    requerido para alcanzar ≥ target_q de supervivencia.
    """
    dist = compute_required_buffer_distribution(
        price_df, coin,
        init_ltv=init_ltv,
        liq_ltv=liq_ltv,
        principal_usd=principal_usd,
        mode=mode,
        window_minutes=window_minutes,
        start_stride=start_stride,
    )
    pct90 = find_buffer_for_target_success(dist, q=target_q, metric="buffer_pct_of_init_collateral")

    # Traducir ese % a coins/USD para un ejemplo representativo usando p0 mediano
    p0_med = float(dist["p0"].median())
    c0_med = principal_usd / (init_ltv * p0_med)
    coins90 = pct90 * c0_med
    usd90_at_p0 = coins90 * p0_med

    return {
        "coin": coin,
        "mode": mode,
        "window_minutes": float(window_minutes) if mode == "window" else float("nan"),
        "init_ltv": init_ltv,
        "liq_ltv": liq_ltv,
        "target_q": target_q,
        "buffer_pct_of_init_collateral@q": float(pct90),
        "buffer_coins@q (using median p0)": float(coins90),
        "buffer_usd_at_p0@q": float(usd90_at_p0),
        "n_starts": int(len(dist)),
    }