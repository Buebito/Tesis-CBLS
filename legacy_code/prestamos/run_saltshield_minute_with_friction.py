import pandas as pd
import numpy as np
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import json

# Parameters
OUT_DIR = Path('prestamos/analysis_plots')
OUT_DIR.mkdir(parents=True, exist_ok=True)

# choose the minute CSV produced earlier
INPUT_CSV = OUT_DIR / 'saltshield_results_by_start_minute.csv'
if not INPUT_CSV.exists():
    raise FileNotFoundError(f"Expected input CSV at {INPUT_CSV}; run run_saltshield_minute.py first")

# friction params (configurable)
SLIPPAGE_PCT = 0.01   # 1% slippage on sale price
FEE_PCT = 0.005       # 0.5% fee on sale price

PRINCIPAL_USD = 10_000
INIT_LTV = 0.50

print('Loading minute results from', INPUT_CSV)
res = pd.read_csv(INPUT_CSV, parse_dates=['start'])

# compute effective recovery price after slippage+fees for terminated loans
recover_factor = 1.0 - SLIPPAGE_PCT - FEE_PCT
if recover_factor <= 0:
    raise ValueError('recover_factor <= 0, choose smaller slippage/fees')

res['pT_effective'] = res['pT'] * recover_factor

# recompute loss_frac and loss_usd using pT_effective
thresholds = INIT_LTV * res['p0']
res['liquidated_friction'] = (res['pT_effective'] < thresholds)
res['loss_frac_friction'] = np.maximum(0.0, 1.0 - res['pT_effective'] / thresholds)
res['loss_usd_friction'] = res['loss_frac_friction'] * PRINCIPAL_USD
res['ltv_end_friction'] = INIT_LTV * (res['p0'] / res['pT_effective'])
res['ltv_excess_friction'] = np.maximum(0.0, res['ltv_end_friction'] - 1.0)

# summaries
n = len(res)
n_liq_f = int(res['liquidated_friction'].sum())
freq_liq_f = n_liq_f / n if n>0 else 0.0
mean_loss_given_liq_f = res.loc[res['liquidated_friction'], 'loss_frac_friction'].mean() if n_liq_f>0 else 0.0
expected_loss_frac_f = res['loss_frac_friction'].mean()
expected_loss_usd_f = expected_loss_frac_f * PRINCIPAL_USD

print('After friction:')
print('Num starts:', n)
print('Num liquidations (friction):', n_liq_f)
print('P(liq) (friction):', f'{freq_liq_f:.6f}')
print('Mean loss given liq (fraction of P) (friction):', f'{mean_loss_given_liq_f:.6f}')
print('Expected loss per loan (fraction of P) (friction):', f'{expected_loss_frac_f:.6f}')
print('Expected loss per loan (USD) (friction):', f'${expected_loss_usd_f:,.2f}')

# save frictioned outputs
out_csv = OUT_DIR / f'saltshield_results_by_start_minute_friction_s{int(SLIPPAGE_PCT*100)}_f{int(FEE_PCT*1000)}.csv'
res.to_csv(out_csv, index=False)

summary = {
    'n_starts': int(n),
    'n_liq_friction': int(n_liq_f),
    'p_liq_friction': float(freq_liq_f),
    'mean_loss_given_liq_frac_friction': float(mean_loss_given_liq_f),
    'expected_loss_frac_friction': float(expected_loss_frac_f),
    'expected_loss_usd_friction': float(expected_loss_usd_f),
    'slippage_pct': SLIPPAGE_PCT,
    'fee_pct': FEE_PCT
}
with open(OUT_DIR / 'saltshield_summary_minute_friction.json', 'w') as f:
    json.dump(summary, f, indent=2)
print('Wrote frictioned summary and CSV to', OUT_DIR)

# simple plots to visualize effect
plt.figure(figsize=(8,4))
plt.hist(res['loss_usd_friction'][res['loss_usd_friction']>0], bins=100)
plt.title('Histogram of loss USD (with slippage+fees)')
plt.xlabel('Loss (USD)')
plt.tight_layout()
plt.savefig(OUT_DIR / 'loss_usd_hist_friction.png')
plt.close()

plt.figure(figsize=(8,4))
plt.hist(res['loss_usd'][res['loss_usd']>0], bins=100, alpha=0.6, label='no friction')
plt.hist(res['loss_usd_friction'][res['loss_usd_friction']>0], bins=100, alpha=0.6, label='friction')
plt.legend()
plt.title('Loss USD: no friction vs friction')
plt.tight_layout()
plt.savefig(OUT_DIR / 'loss_usd_comparison_friction.png')
plt.close()

print('Saved friction plots to', OUT_DIR)
