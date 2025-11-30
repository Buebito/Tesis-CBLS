import pandas as pd
import json
from pathlib import Path

OUT_DIR = Path('prestamos/analysis_plots')

csv_min = OUT_DIR / 'saltshield_results_by_start_minute.csv'
csv_daily = OUT_DIR / 'saltshield_results_by_start.csv'

results = {}

if csv_min.exists():
    df_min = pd.read_csv(csv_min, parse_dates=['start'])
    results['minute'] = {
        'n_starts': int(len(df_min)),
        'n_liq': int((df_min['loss_usd']>0).sum()),
        'p_liq': float((df_min['loss_usd']>0).mean()),
        'expected_loss_usd': float(df_min['loss_usd'].mean())
    }
    print('Minute-level: starts', results['minute']['n_starts'], 'P(liq)', results['minute']['p_liq'])

    # hourly: keep starts where minute==0
    df_min['start'] = pd.to_datetime(df_min['start'], utc=True)
    df_hour = df_min[df_min['start'].dt.minute == 0].copy()
    results['hourly'] = {
        'n_starts': int(len(df_hour)),
        'n_liq': int((df_hour['loss_usd']>0).sum()),
        'p_liq': float((df_hour['loss_usd']>0).mean()),
        'expected_loss_usd': float(df_hour['loss_usd'].mean())
    }
    print('Hourly-level: starts', results['hourly']['n_starts'], 'P(liq)', results['hourly']['p_liq'])

if csv_daily.exists():
    df_daily = pd.read_csv(csv_daily, parse_dates=['start'])
    results['daily'] = {
        'n_starts': int(len(df_daily)),
        'n_liq': int((df_daily['loss_usd']>0).sum()),
        'p_liq': float((df_daily['loss_usd']>0).mean()),
        'expected_loss_usd': float(df_daily['loss_usd'].mean())
    }
    print('Daily-level: starts', results['daily']['n_starts'], 'P(liq)', results['daily']['p_liq'])

OUT_DIR.mkdir(parents=True, exist_ok=True)
with open(OUT_DIR / 'saltshield_granularity_comparison.json', 'w') as f:
    json.dump(results, f, indent=2)

print('Saved granularity comparison JSON to', OUT_DIR / 'saltshield_granularity_comparison.json')
