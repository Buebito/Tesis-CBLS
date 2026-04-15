import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from pathlib import Path

# Parameters
CSV = Path('/Users/alexis/Documents/Deep_dive/btcusd_bitstamp_1min_2012-2025.csv')
OUT_DIR = Path('prestamos/analysis_plots')
OUT_DIR.mkdir(parents=True, exist_ok=True)
START_DATE = pd.Timestamp('2021-01-01', tz='UTC')
HORIZON_DAYS = 365
INIT_LTV = 0.50
PRINCIPAL_USD = 10_000

print('CSV exists?', CSV.exists())
if not CSV.exists():
    raise FileNotFoundError(CSV)

print('Reading full minute CSV into memory (this may use ~200-800MB RAM)...')
df = pd.read_csv(CSV, usecols=['timestamp','close'])
df['date'] = pd.to_datetime(df['timestamp'].astype(int), unit='s', utc=True)
df = df[['date','close']].drop_duplicates('date').set_index('date').sort_index()
price_min = df['close']

first = price_min.index.min()
last = price_min.index.max()
expected_minutes = int((last - first).total_seconds() / 60) + 1
missing_minutes = expected_minutes - len(price_min)
print(f'minute series range: {first} -> {last} (rows: {len(price_min)}); missing minutes: {missing_minutes}')

# Filter to START_DATE
price_min = price_min.loc[price_min.index >= START_DATE]
last_date = price_min.index.max()
print('After START_DATE filter:', price_min.index.min(), '->', last_date)

# Build start index where we have full horizon
horizon = pd.Timedelta(days=HORIZON_DAYS)
start_idx = price_min.index[price_min.index + horizon <= last_date]
print('Start indices available (minute-level):', len(start_idx))

if len(start_idx) == 0:
    raise SystemExit('No start indices available for given START_DATE and horizon')

# Vectorized lookup: p0 = price_min.reindex(start_idx), pT = price_min.reindex(start_idx + horizon)
p0 = price_min.reindex(start_idx).values
end_idx = start_idx + horizon
pT = price_min.reindex(end_idx).values

res = pd.DataFrame({'start': start_idx, 'p0': p0, 'pT': pT})
res = res.dropna()
print('Rows with full horizon (after dropna):', len(res))

# Compute liquidations and losses
thresholds = INIT_LTV * res['p0']
res['liquidated'] = (res['pT'] < thresholds)
res['loss_frac'] = np.maximum(0.0, 1.0 - res['pT'] / thresholds)
res['loss_usd'] = res['loss_frac'] * PRINCIPAL_USD
res['ltv_end'] = INIT_LTV * (res['p0'] / res['pT'])
res['ltv_excess'] = np.maximum(0.0, res['ltv_end'] - 1.0)

n = len(res)
n_liq = int(res['liquidated'].sum())
freq_liq = n_liq / n if n>0 else 0.0
mean_loss_given_liq = res.loc[res['liquidated'], 'loss_frac'].mean() if n_liq>0 else 0.0
expected_loss_frac = res['loss_frac'].mean()
expected_loss_usd = expected_loss_frac * PRINCIPAL_USD

print('Num starts:', n)
print('Num liquidations:', n_liq)
print('P(liq):', f'{freq_liq:.6f}')
print('Mean loss given liq (fraction of P):', f'{mean_loss_given_liq:.6f}')
print('Expected loss per loan (fraction of P):', f'{expected_loss_frac:.6f}')
print('Expected loss per loan (USD):', f'${expected_loss_usd:,.2f}')

# Diagnostics
print('\nLTV excess stats (for liquidations):')
print(res.loc[res['liquidated'], 'ltv_excess'].describe())
print('\nLoss USD stats (for liquidations):')
print(res.loc[res['liquidated'], 'loss_usd'].describe())

# Yearly breakdown
res['start'] = pd.to_datetime(res['start'], utc=True)
res['start_year'] = res['start'].dt.year
yearly = res.groupby('start_year').agg(
    starts=('start', 'count'),
    n_liq=('liquidated', 'sum'),
    p_liq=('liquidated', 'mean'),
    mean_loss_given_liq=('loss_frac', lambda x: x[x>0].mean()),
    expected_loss_frac=('loss_frac', 'mean')
).reset_index()
print('\nYearly summary sample:')
print(yearly.head())

# Save results (minute suffix)
res[['start','p0','pT','liquidated','ltv_end','ltv_excess','loss_frac','loss_usd']].to_csv(OUT_DIR / 'saltshield_results_by_start_minute.csv', index=False)
yearly.to_csv(OUT_DIR / 'saltshield_yearly_summary_minute.csv', index=False)
print('Saved detailed per-start CSV and yearly summary (minute)')

# premiums
margins = [0.0, 0.10, 0.20, 0.30]
premiums = {m: expected_loss_frac * (1.0 + m) for m in margins}
for m,p in premiums.items():
    print(f'margin {int(m*100)}% -> premium = {p*100:.6f}% of principal -> ${p*PRINCIPAL_USD:,.2f} for P={PRINCIPAL_USD}')

summary = {
    'n_starts': int(n),
    'n_liq': int(n_liq),
    'p_liq': float(freq_liq),
    'mean_loss_given_liq_frac': float(mean_loss_given_liq),
    'expected_loss_frac': float(expected_loss_frac),
    'expected_loss_usd': float(expected_loss_usd),
    'premiums_frac': {str(int(m*100)): float(p) for m,p in premiums.items()}
}
import json
with open(OUT_DIR / 'saltshield_summary_minute.json', 'w') as f:
    json.dump(summary, f, indent=2)
print('Wrote summary to', OUT_DIR / 'saltshield_summary_minute.json')

# Plots (save some key charts; keep them lighter to avoid heavy plotting overhead)
ratio = res['pT'] / res['p0']
plt.figure(figsize=(9,4))
plt.hist(ratio, bins=200, range=(0,2))
plt.axvline(INIT_LTV, color='r', linestyle='--', label=f'liquidation threshold = {INIT_LTV:.2f} (pT/p0)')
plt.title('Histogram of p_T / p0 (0..2) - minute starts')
plt.xlabel('p_T / p0')
plt.ylabel('Counts')
plt.legend()
plt.tight_layout()
plt.savefig(OUT_DIR / 'ratio_pT_p0_hist_minute.png')
plt.close()

plt.figure(figsize=(9,4))
plt.hist(res['loss_frac'][res['loss_frac']>0], bins=100)
plt.title('Histogram of loss fraction (liquidations) - minute starts')
plt.xlabel('Loss fraction of principal')
plt.ylabel('Counts')
plt.tight_layout()
plt.savefig(OUT_DIR / 'loss_frac_hist_minute.png')
plt.close()

res.to_csv(OUT_DIR / 'saltshield_results_by_start_minute.csv', index=False)
print('Saved minute-level results and plots to', OUT_DIR)
