# Legacy Code

Codigo original de simulaciones de prestamos colateralizados con criptomonedas. Construido sobre precios historicos por minuto de Binance (enero 2021 - julio 2025) para BTC y ETH.

## Estructura interna

```
legacy_code/
├── bolsa_volatilidad/     # Optimizacion de bolsa de volatilidad (lado cliente)
├── precios_futuros/       # Simulacion Monte Carlo de precios futuros
├── prestamos/             # Simulador de portafolio de prestamos (Segment Tree + Markov)
├── modelo_financiero/     # Modelo financiero del negocio (break-even, P&L, proyecciones)
├── real_time_api/         # API de liquidacion en tiempo real (Binance WebSocket)
├── X2O shield/            # Analisis de cobertura Salt Shield (liquidation insurance)
└── requirements.txt
```

## Modulos

### `bolsa_volatilidad/`
Calculo determinista del colateral extra ("bolsa de volatilidad") que un cliente necesita provisionar para evitar liquidacion. Usa minimos deslizantes O(n) sobre trayectorias historicas y reporta el percentil q (e.g., 90%) del requerimiento.

- `utils/bootstrap_mc.py` -- Simulador Monte Carlo con Bootstrap-KDE (version avanzada con BiasConfig: tilt, winsorization, stress-testing).
- `utils/volatility_buffer_optimizer.py` -- Optimizador de buffer: `suffix_min()`, `window_min()`, calculo de buffer requerido por fecha de inicio y percentil objetivo.
- `volatilidad.ipynb` -- Notebook de ejecucion y visualizacion.

### `precios_futuros/`
Simulacion Monte Carlo de trayectorias futuras de BTC/ETH. Los retornos historicos se muestrean con Bootstrap + ruido KDE (Silverman) y se proyectan N paths.

- `utils/bootstrap_mc.py` -- `KDEBootstrapSampler`, `MonteCarloPriceSimulator`, metricas de riesgo (P(caida >= 20%), P(caida >= 40%)).
- `pruebitas.ipynb` -- Notebook experimental.

### `prestamos/`
Motor principal de simulacion. Genera 10,000 prestamos (5K BTC + 5K ETH) con:
- **Segment Tree** para deteccion de margin call en O(log^2 n).
- **Cadenas de Markov** para modelar decisiones del cliente (agregar colateral, amortizar, ignorar, renovar) con probabilidades que decaen con cada margin call.

Archivos clave:
- `utils/model.py` -- `SegmentTree`, `Loan`, `simulate_portfolio()`, `rehypo_backtest_from_logs()`.
- `run_saltshield*.py` -- Backtesting historico del Salt Shield a distintas granularidades.
- `resultados_sim.ipynb` -- Analisis de resultados de simulaciones.

### `modelo_financiero/`
Modelo financiero del negocio de crypto-backed loans. Integra las simulaciones de prestamos con costos reales (BitGo, Complif) y proyecciones de P&L, break-even y ROI, asumiendo una inyeccion de capital de $20M USD.

- `modelo_financiero.ipynb` -- Notebook-reporte con simulaciones actualizadas (LTV 10-50%, prestamos $100K-$20M, sin rehipoteca, cuota de apertura, probabilidad de recapitalizacion).

### `real_time_api/`
API interactiva de simulacion de prestamos en tiempo real usando precios de Binance (WebSocket/REST). Implementa micro-ventas automaticas de buffer y alertas de buffer call.

- `micro_liq/app.py` -- Clase `Prestamo` con polling cada 3 segundos y mecanismo de micro-sale.

### `X2O shield/`
Resultados del analisis de cobertura Salt Shield. Contiene JSONs de resumen y graficos PNG generados por los scripts `run_saltshield*.py`.
