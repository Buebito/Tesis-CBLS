"""
tesis/data/loader.py
---------------------
Cargadores de datos unificados para todos los modulos de la tesis.

Lee los precios crudos minuto-a-minuto de ``data/raw/`` (BTC y ETH), normaliza
el formato (los dos CSV tienen nombres de columna distintos) y expone utilidades
para resamplear, calcular retornos y dividir en train/test.

Los CSV crudos son grandes (BTC ~2.9M filas desde 2012, ETH ~4M filas desde
2017), por lo que el resultado de la lectura minuto-a-minuto se cachea en memoria
(``functools.lru_cache``) y los resampleos se derivan de esa copia en lugar de
releer el archivo.

Formato de los CSV crudos
--------------------------
BTC (``btcusd_1-min_data.csv``):  Timestamp,Open,High,Low,Close,Volume   (unix seg, float)
ETH (``ethusd_1min_ohlc.csv``):   timestamp,open,high,low,close,volume   (unix seg, int)

Funciones publicas
------------------
- ``load_price_df(asset, resample_rule=None, start=None, end=None)``  -> DataFrame OHLCV
- ``load_price_series(asset, resample_rule=None, column='close', ...)`` -> Series de precios
- ``compute_log_returns(prices)``   -> Series de log-retornos
- ``compute_pct_returns(prices)``   -> Series de retornos porcentuales
- ``infer_horizon_steps(horizon, resample_rule)`` -> numero de pasos para un horizonte
- ``split_train_test(data, split_date)`` -> (train, test)
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

# ── Localizacion de los datos ─────────────────────────────────────────────────
# loader.py -> tesis/data/  ->  parents[2] = raiz del repo
_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"

_FILES = {
    "BTC": "btcusd_1-min_data.csv",
    "ETH": "ethusd_1min_ohlc.csv",
}

_OHLCV = ["open", "high", "low", "close", "volume"]

_RESAMPLE_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


# ══════════════════════════════════════════════════════════════════════════════
# Lectura cruda (cacheada)
# ══════════════════════════════════════════════════════════════════════════════


@functools.lru_cache(maxsize=2)
def _load_raw(asset: str) -> pd.DataFrame:
    """Lee el CSV minuto-a-minuto crudo y devuelve un DataFrame OHLCV indexado por fecha.

    El resultado se cachea: la primera llamada por activo lee el archivo (lento),
    las siguientes son instantaneas. Devuelve una vista de solo-lectura conceptual;
    quien la consuma debe copiar antes de mutar.
    """
    asset = asset.upper()
    if asset not in _FILES:
        raise ValueError(f"Activo desconocido: {asset!r}. Opciones: {list(_FILES)}")

    path = _DATA_DIR / _FILES[asset]
    if not path.exists():
        raise FileNotFoundError(f"No se encontro el archivo de precios: {path}")

    # Solo necesitamos OHLCV + timestamp; usecols ignora columnas extra.
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    if "timestamp" not in df.columns:
        raise ValueError(f"{path.name}: no se encontro columna 'timestamp'. Cols: {list(df.columns)}")

    # unix segundos (int o float) -> datetime naive (UTC)
    idx = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(None)
    df = df.set_index(idx)
    df.index.name = "datetime"

    # Conservar solo OHLCV existentes, en orden
    cols = [c for c in _OHLCV if c in df.columns]
    df = df[cols]

    # Limpieza: ordenar, quitar duplicados de timestamp, quitar precios no validos
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if "close" in df.columns:
        df = df[df["close"] > 0]

    return df


# ══════════════════════════════════════════════════════════════════════════════
# API publica
# ══════════════════════════════════════════════════════════════════════════════


def load_price_df(
    asset: str = "BTC",
    resample_rule: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Carga precios OHLCV de un activo, opcionalmente resampleados.

    Parameters
    ----------
    asset : 'BTC' o 'ETH'.
    resample_rule : regla de pandas ('1h', '1D', '1W', ...). None = minuto-a-minuto crudo.
    start, end : recortes de fecha inclusivos ('2020-01-01').

    Returns
    -------
    DataFrame con columnas OHLCV e indice DatetimeIndex.
    """
    df = _load_raw(asset).copy()

    if resample_rule is not None:
        agg = {c: _RESAMPLE_AGG[c] for c in df.columns}
        df = df.resample(resample_rule).agg(agg)
        # Quitar buckets vacios (periodos sin operaciones)
        df = df.dropna(subset=["close"]) if "close" in df.columns else df.dropna(how="all")

    if start is not None:
        df = df.loc[start:]
    if end is not None:
        df = df.loc[:end]

    return df


def load_price_series(
    asset: str = "BTC",
    resample_rule: Optional[str] = None,
    column: str = "close",
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.Series:
    """Carga una sola serie de precios (por defecto 'close') de un activo.

    Devuelve una pandas Series indexada por fecha. Conveniente para EDA y para
    alimentar el modelo SV (que trabaja sobre log-retornos de una serie).
    """
    df = load_price_df(asset, resample_rule=resample_rule, start=start, end=end)
    if column not in df.columns:
        raise ValueError(f"Columna {column!r} no disponible. Cols: {list(df.columns)}")
    s = df[column].copy()
    s.name = f"{asset.upper()}_{column}"
    return s


def _as_series(prices: Union[pd.Series, pd.DataFrame]) -> pd.Series:
    """Normaliza la entrada a una Series de precios (usa 'close' si es DataFrame)."""
    if isinstance(prices, pd.DataFrame):
        if "close" not in prices.columns:
            raise ValueError("DataFrame sin columna 'close'; pasa una Series explicita.")
        return prices["close"]
    return prices


def compute_log_returns(prices: Union[pd.Series, pd.DataFrame]) -> pd.Series:
    """Log-retornos r_t = ln(P_t / P_{t-1}). Descarta el primer NaN.

    Acepta una Series de precios o un DataFrame OHLCV (usa 'close').
    """
    p = _as_series(prices)
    r = np.log(p / p.shift(1))
    r = r.dropna()
    r.name = "log_return"
    return r


def compute_pct_returns(prices: Union[pd.Series, pd.DataFrame]) -> pd.Series:
    """Retornos porcentuales simples r_t = P_t / P_{t-1} - 1. Descarta el primer NaN."""
    p = _as_series(prices)
    r = p.pct_change().dropna()
    r.name = "pct_return"
    return r


# Pasos por dia para reglas de resampleo comunes (aproximado, para horizontes)
_STEPS_PER_DAY = {
    "T": 1440, "min": 1440, "1min": 1440,
    "H": 24, "h": 24, "1h": 24, "1H": 24,
    "D": 1, "d": 1, "1D": 1, "1d": 1,
}


def infer_horizon_steps(horizon_days: float, resample_rule: Optional[str]) -> int:
    """Numero de pasos que cubren ``horizon_days`` a la frecuencia ``resample_rule``.

    Ej: infer_horizon_steps(90, '1D') -> 90 ; infer_horizon_steps(1, '1h') -> 24.
    Si ``resample_rule`` es None se asume frecuencia minuto-a-minuto.
    """
    rule = resample_rule if resample_rule is not None else "1min"
    if rule not in _STEPS_PER_DAY:
        raise ValueError(
            f"Frecuencia {rule!r} no soportada para horizonte. "
            f"Opciones: {sorted(_STEPS_PER_DAY)}"
        )
    return int(round(horizon_days * _STEPS_PER_DAY[rule]))


def split_train_test(
    data: Union[pd.Series, pd.DataFrame],
    split_date: str,
) -> Tuple[Union[pd.Series, pd.DataFrame], Union[pd.Series, pd.DataFrame]]:
    """Divide una serie/DataFrame indexado por fecha en train (< split_date) y test (>= split_date).

    El corte es exclusivo en train e inclusivo en test, de modo que ninguna
    observacion aparece en ambos conjuntos.
    """
    ts = pd.Timestamp(split_date)
    train = data[data.index < ts]
    test = data[data.index >= ts]
    return train, test
