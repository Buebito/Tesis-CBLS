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

# Read in chunks, keep timestamp+close, convert timestamp->datetime UTC, build daily close series
chunks = pd.read_csv(CSV, usecols=['timestamp','close'], chunksize=2_000_000)
parts = []
for i,chunk in enumerate(chunks):
    chunk['date'] = pd.to_datetime(chunk['timestamp'].astype(int), unit='s', utc=True)
    parts.append(chunk[['date','close']])
    print(f'read chunk {i+1}, rows {len(chunk)}')

price_min = pd.concat(parts).set_index('date').sort_index()
price_daily = price_min['close'].resample('D').last().dropna()
print('daily len total:', len(price_daily))
price_daily = price_daily.loc[START_DATE:]
last_date = price_daily.index.max()
print('daily range after filter:', price_daily.index.min(), '->', last_date)

# Build start dates where we have full horizon
horizon = pd.Timedelta(days=HORIZON_DAYS)
start_dates = price_daily.index[price_daily.index + horizon <= last_date]
print('start dates available:', len(start_dates))

p0 = price_daily.reindex(start_dates).values
end_dates = start_dates + horizon
pT = price_daily.reindex(end_dates).values
res = pd.DataFrame({'start': start_dates, 'p0': p0, 'pT': pT})
res = res.dropna()
print('rows with full horizon:', len(res))

# liquidations and losses
thresholds = INIT_LTV * res['p0']
res['liquidated'] = (res['pT'] < thresholds)
res['loss_frac'] = np.maximum(0.0, 1.0 - res['pT'] / thresholds)
res['loss_usd'] = res['loss_frac'] * PRINCIPAL_USD

# Compute LTV at end: LTV_end = L0 * p0 / pT
res['ltv_end'] = INIT_LTV * (res['p0'] / res['pT'])
res['ltv_excess'] = np.maximum(0.0, res['ltv_end'] - 1.0)  # how much LTV > 1.0

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

# Additional diagnostics requested: how much LTV>100% and interpretation as loss
# Count and percent of loans exceeding LTV=1 by how much
num_exceed = n_liq
pct_exceed = freq_liq
ltv_excess_stats = res.loc[res['liquidated'], 'ltv_excess'].describe()
loss_usd_stats = res.loc[res['liquidated'], 'loss_usd'].describe()
print('\nLTV excess stats (for liquidations):')
print(ltv_excess_stats)
print('\nLoss USD stats (for liquidations):')
print(loss_usd_stats)

# Yearly breakdown (by start year)
res['start_year'] = res['start'].dt.year
yearly = res.groupby('start_year').agg(
    starts=('start', 'count'),
    n_liq=('liquidated', 'sum'),
    p_liq=('liquidated', 'mean'),
    mean_loss_given_liq=('loss_frac', lambda x: x[x>0].mean()),
    expected_loss_frac=('loss_frac', 'mean')
)
yearly = yearly.reset_index()
print('\nYearly summary sample:')
print(yearly.head())

# Save extra diagnostics
res[['start','p0','pT','liquidated','ltv_end','ltv_excess','loss_frac','loss_usd']].to_csv(OUT_DIR / 'saltshield_results_by_start.csv', index=False)
yearly.to_csv(OUT_DIR / 'saltshield_yearly_summary.csv', index=False)

print('Saved detailed per-start CSV and yearly summary')

# premiums
margins = [0.0, 0.10, 0.20, 0.30]
premiums = {m: expected_loss_frac * (1.0 + m) for m in margins}
for m,p in premiums.items():
    print(f'margin {int(m*100)}% -> premium = {p*100:.6f}% of principal -> ${p*PRINCIPAL_USD:,.2f} for P={PRINCIPAL_USD}')

# save summary
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
with open(OUT_DIR / 'saltshield_summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
print('Wrote summary to', OUT_DIR / 'saltshield_summary.json')

# plots
ratio = res['pT'] / res['p0']
plt.figure(figsize=(9,4))
plt.hist(ratio, bins=100, range=(0,2))
plt.axvline(INIT_LTV, color='r', linestyle='--', label=f'liquidation threshold = {INIT_LTV:.2f} (pT/p0)')
plt.title('Histogram of p_T / p0 (0..2)')
plt.xlabel('p_T / p0')
plt.ylabel('Counts')
plt.legend()
plt.tight_layout()
plt.savefig(OUT_DIR / 'ratio_pT_p0_hist.png')
plt.close()

plt.figure(figsize=(9,4))
plt.hist(res['loss_frac'][res['loss_frac']>0], bins=50)
plt.title('Histogram of loss fraction (liquidations)')
plt.xlabel('Loss fraction of principal')
plt.ylabel('Counts')
plt.tight_layout()
plt.savefig(OUT_DIR / 'loss_frac_hist.png')
plt.close()

# save aggregated CSV of results
res.to_csv(OUT_DIR / 'saltshield_results_by_start.csv', index=False)
print('Saved detailed results and plots to', OUT_DIR)
