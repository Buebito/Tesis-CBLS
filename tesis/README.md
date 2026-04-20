# Tesis: Modelos Bayesianos para Crypto-Backed Loans

Investigacion activa sobre modelos bayesianos de volatilidad estocastica aplicados a la gestion de colateral y pricing de prestamos crypto. Este modulo refactoriza el legacy code en una arquitectura limpia y agrega modelos nuevos.

## Estructura interna

```
tesis/
├── baseline/          # Modelos de referencia (refactorizacion del legacy code)
├── bayesian/          # Modelos bayesianos nuevos (investigacion)
├── data/              # Cargadores de datos unificados
├── notebooks/         # Notebooks de analisis y validacion
├── results/           # Resultados generados (vacio, para outputs futuros)
├── VF Tesis AGS.pdf   # Tesis de referencia (PDF)
├── VF_Tesis_AGS.md    # Tesis de referencia (Markdown)
└── requirements.txt
```

## Modulos

### `baseline/`
Refactorizacion limpia y modular del legacy code. Misma logica, pero con configs pasadas como parametro (no globales mutables) y sin I/O embebido en las funciones.

- **`loan_simulator/model.py`** -- `SegmentTree`, `Loan`, `simulate_portfolio()`. Identico al legacy pero acepta `price_df` y `config` como argumentos.
- **`mc_bootstrap/simulator.py`** -- Version unificada de los modulos `precios_futuros` y `bolsa_volatilidad`. Incluye `BiasConfig`, `KDEBootstrapSampler`, `MonteCarloPriceSimulator`.
- **`saltshield/pricing.py`** -- Pricing del Salt Shield refactorizado. Agrega VaR95 y ES95 como metricas de riesgo.
- **`buffer_optimizer/optimizer.py`** -- Optimizador de buffer refactorizado. Recibe arrays en vez de DataFrames con columna de moneda.

### `bayesian/`
Modelos nuevos de la investigacion:

- **`sv_model/stochastic_volatility.py`** -- Modelo de Volatilidad Estocastica Bayesiano (Taylor 1982 / Kim-Shephard-Chib 1998). MCMC con Metropolis-within-Gibbs y wrapper PyMC/NUTS.
- **`sv_model/trajectory_simulator.py`** -- Simulador de trayectorias posterior-predictivas que propaga incertidumbre epistemica y aleatorica.
- **`buffer/bayesian_buffer.py`** -- Optimizador de buffer bayesiano usando trayectorias SV. Comparacion historica vs. bayesiana vs. bootstrap.
- **`pricing/bayesian_premium.py`** -- Pricing bayesiano de primas con descomposicion frecuencia-severidad (Gamma posterior para LGL + costos operativos).

### `data/`
Utilidades de carga de datos compartidas por todos los modulos.

- **`loader.py`** -- `load_price_df()`, `load_price_series()`, `compute_log_returns()`, `compute_pct_returns()`, `infer_horizon_steps()`, `split_train_test()`.

### `notebooks/`
Notebooks de analisis exploratorio y validacion de modelos:

- **`01_analisis_exploratorio.ipynb`** -- EDA de precios historicos BTC/ETH.
- **`02_sv_sintetico_validacion.ipynb`** -- Validacion del modelo SV con datos sinteticos.

### `results/`
Directorio para outputs generados por los notebooks y scripts. Actualmente vacio.
