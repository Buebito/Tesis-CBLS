import numpy as np
import pandas as pd
from pathlib import Path
import json

OUT_DIR = Path('prestamos/analysis_plots')
DATA_PATH = Path('/Users/alexis/Documents/Deep_dive/btcusd_bitstamp_1min_2012-2025.csv')

# Config
N_SIM = 20000
HORIZON_DAYS = 365
RECOVER_FACTOR = 1.0  # set <1.0 to include slippage/fees
PRINCIPAL_USD = 10_000
INIT_LTV = 0.50

# Build daily returns
if not DATA_PATH.exists():
    raise FileNotFoundError(f"DATA_PATH not found: {DATA_PATH}")

print('Building daily series from minute CSV (may take a moment)...')
usecols = ['timestamp','close']
reader = pd.read_csv(DATA_PATH, usecols=usecols, chunksize=2_000_000)
parts = []
for chunk in reader:
    chunk['date'] = pd.to_datetime(chunk['timestamp'].astype(int), unit='s', utc=True)
    parts.append(chunk[['date','close']])
price_min = pd.concat(parts).set_index('date').sort_index()
price_daily = price_min['close'].resample('D').last().dropna()

returns = np.log(price_daily).diff().dropna()
print('Daily returns length =', len(returns))

# Bootstrap sampling
idx = np.random.randint(0, len(returns), size=(N_SIM, HORIZON_DAYS))
sampled = returns.values[idx]
cum_log_returns = sampled.sum(axis=1)
R = np.exp(cum_log_returns)  # pT/p0
R_eff = R * RECOVER_FACTOR

loss_frac = np.maximum(0.0, 1.0 - R / INIT_LTV)
loss_frac_eff = np.maximum(0.0, 1.0 - R_eff / INIT_LTV)

p_liq_mc = float((R < INIT_LTV).mean())
mean_loss_given_liq_mc = float(loss_frac[R < INIT_LTV].mean()) if (R < INIT_LTV).any() else 0.0
expected_loss_frac_mc = float(loss_frac.mean())
expected_loss_usd_mc = expected_loss_frac_mc * PRINCIPAL_USD

p_liq_mc_eff = float((R_eff < INIT_LTV).mean())
expected_loss_frac_mc_eff = float(loss_frac_eff.mean())
expected_loss_usd_mc_eff = expected_loss_frac_mc_eff * PRINCIPAL_USD

mc_summary = {
    'N_SIM': int(N_SIM),
    'p_liq_mc': p_liq_mc,
    'mean_loss_given_liq_mc': mean_loss_given_liq_mc,
    'expected_loss_frac_mc': expected_loss_frac_mc,
    'expected_loss_usd_mc': expected_loss_usd_mc,
    'recover_factor': RECOVER_FACTOR,
    'p_liq_mc_eff': p_liq_mc_eff,
    'expected_loss_frac_mc_eff': expected_loss_frac_mc_eff,
    'expected_loss_usd_mc_eff': expected_loss_usd_mc_eff
}

OUT_DIR.mkdir(parents=True, exist_ok=True)
with open(OUT_DIR / 'saltshield_mc_summary.json', 'w') as f:
    json.dump(mc_summary, f, indent=2)

print('Saved Monte Carlo summary to', OUT_DIR / 'saltshield_mc_summary.json')
