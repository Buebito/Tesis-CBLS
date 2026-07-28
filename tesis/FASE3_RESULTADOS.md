# Fase 3 — Resultados y Decisiones

> Simulación Monte Carlo con la posterior del SV (Cap. 5), modelo frecuencia-severidad
> Bayesiano (Cap. 6) y pricing + buffer óptimo con incertidumbre (Cap. 7).
>
> **Fecha de cierre:** 2026-07-28 · **Estado:** ✅ código completado (redacción pendiente)

Corresponde a la **Fase 3** del roadmap en `.opencode/plans/PLAN_TESIS.md`.
Continúa desde [`FASE2_RESULTADOS.md`](FASE2_RESULTADOS.md).

---

## 1. Qué se construyó

| Entregable | Ubicación | Estado |
|---|---|---|
| Simulación de trayectorias SV + comparación bootstrap (Cap. 5) | `tesis/notebooks/04_sv_simulacion_trayectorias.ipynb` | ✅ ejecutado |
| Pricing frecuencia-severidad + buffer Bayesiano (Cap. 6-7) | `tesis/notebooks/05_pricing_buffer_bayesiano.ipynb` | ✅ ejecutado |
| Helper `idata_to_mcmc_result` (NUTS → dict simulador) | `bayesian/sv_model/stochastic_volatility.py` | ✅ |

**Setup común:** SV reestimado con NUTS (`2000×4, tune=3000, target_accept=0.99`) sobre el
train BTC 2012–2023 centrado. Precio inicial = último del train = **\$42 639** (fin 2023).
Estado de volatilidad inicial $h_T$ ≈ **23% anualizado** (régimen calmo). Todas las
simulaciones son **zero-drift** (aíslan la dinámica de volatilidad).

---

## 2. Cap. 5 — Simulación de trayectorias

### 2.1 SV vs. Bootstrap i.i.d. (365 días)

| Métrica | SV Bayesiano | Bootstrap i.i.d. |
|---|---|---|
| P05 precio final | \$11 875 | \$10 368 |
| Mediana | \$43 398 | \$43 081 |
| P95 | \$165 512 | \$162 781 |
| P(caída ≥20%) | 0.381 | 0.397 |
| P(caída ≥40%) | 0.244 | 0.269 |
| P(caída ≥50%) | 0.179 | 0.204 |

A 365 días el riesgo de cola es **comparable**: el bootstrap de 12 años de BTC ya es muy
fat-tailed (incluye la volatilidad extrema de 2013–2014).

### 2.2 El SV es condicional (hallazgo central)

| Horizonte | SV P(caída≥40%) | BS P(caída≥40%) | SV vol.ann | BS vol.ann |
|---|---|---|---|---|
| 90 d | **0.079** | 0.111 | 0.558 | 0.646 |
| 180 d | 0.156 | 0.194 | 0.610 | 0.658 |
| 365 d | 0.243 | 0.270 | 0.643 | 0.668 |

- A **corto plazo** el SV asigna **menor riesgo** que el bootstrap porque arranca del régimen
  de volatilidad vigente (calmo, 23%) y revierte gradualmente. El bootstrap es incondicional.
- A **365 d** ambos convergen (el SV alcanza su vol. incondicional). Esto valida la hipótesis
  del plan §5.2: la ventaja del SV está en la **estructura temporal condicional**.

### 2.3 Backtest 2024–2026 (honesto)

- Volatilidad realizada en test: **42.6%** → **percentil ~2** de la distribución simulada SV.
- Máximo drawdown realizado: **9.4%** → percentil ~13.
- **Interpretación:** 2024–2025 fue **más calmo** de lo que el SV proyecta. El modelo, entrenado
  sobre la historia completa (con la volatilidad extrema temprana de BTC), **sobre-proyecta** la
  volatilidad reciente. Limitación real → motiva ventana de entrenamiento más reciente o un nivel
  de volatilidad incondicional variable en el tiempo (discutir en Cap. 8).
- El **nivel** de precio no se modela (zero-drift); el bull de 2024 quedó sobre el p95.

---

## 3. Cap. 6-7 — Pricing y buffer Bayesiano

### 3.1 Prima Bayesiana (config referencia: 365 d, LTV 0.50)

- Frecuencia de liquidación **q = 0.517** (2069/4000 trayectorias).
- Severidad **E[LGL] = 0.323**, IC95% [0.302, 0.346]; Gamma posterior α=1.46, β=4.53.
- **Prima de riesgo = 16.7 %** (\$1 672), IC95% **[15.6 %, 17.9 %]**.
- **Prima de tarifa = 22.3 %** (\$2 229), con carga operativa PT = PR/0.75.
- **Aporte Bayesiano:** la prima trae un **intervalo de credibilidad** (incertidumbre epistémica
  de la severidad) que un estimador puntual frecuentista no entrega.

### 3.2 Tabla de primas de tarifa (fracción del principal)

| LTV inicial \ horizonte | 90 d | 180 d | 365 d |
|---|---|---|---|
| 0.40 | 0.019 | 0.057 | 0.148 |
| 0.50 | 0.049 | 0.114 | 0.225 |
| 0.60 | 0.096 | 0.183 | 0.315 |
| 0.70 | 0.174 | 0.274 | 0.412 |

(P(liquidación) correspondiente va de 0.067 en 90 d/LTV 0.40 a 0.831 en 365 d/LTV 0.70.)

### 3.3 Buffer óptimo Bayesiano (LTV 0.50, 365 d)

| Supervivencia α | Buffer SV (% colateral) | Buffer Bootstrap |
|---|---|---|
| 0.80 | 0.608 | 0.794 |
| 0.90 | 1.198 | 1.452 |
| 0.95 | 1.974 | 2.166 |
| 0.99 | 4.719 | 4.993 |

- El SV requiere **menos buffer** que el bootstrap en casi todos los niveles: al condicionar en el
  régimen calmo actual, el riesgo cercano es menor. **48.3 %** de las trayectorias SV no requieren
  buffer alguno.
- Los buffers al 99 % son grandes (≈4.7× el colateral) porque representan la cola extrema de un año
  de volatilidad de BTC sin deriva.

---

## 4. Cambios de código en la Fase 3

- **`idata_to_mcmc_result(idata)`** añadido a `stochastic_volatility.py`: aplana la posterior NUTS
  `(chain, draw)` al dict `{'mu','phi','sigma_eta','h'}` (con `h` shape `(n_samples, T)`) que
  consumen `trajectory_simulator` y `bayesian_premium`. Es el puente entre la estimación (Fase 2)
  y la simulación/pricing (Fase 3).
- No se modificaron los módulos `trajectory_simulator`, `bayesian_premium` ni `bayesian_buffer`:
  sus APIs ya eran compatibles con el dict de muestras posteriores.

---

## 5. Decisiones para la Fase 4 (Validación)

1. **Backtesting formal con split temporal:** ya se hizo un backtest de vol/drawdown; falta el
   backtest de **calibración** (¿el q predicho coincide con la frecuencia realizada de liquidación
   en ventanas móviles del test?) y métricas propias de pronóstico.

2. **Métricas de evaluación (plan §Fase 4):** CRPS de la distribución de precios/retornos,
   coverage de los intervalos de credibilidad, log-score. Usar `properscoring` (ya instalado).

3. **Abordar la sobre-proyección de volatilidad:** comparar el SV entrenado en historia completa
   vs. ventana reciente (p. ej. 2020→2023); reportar cuál calibra mejor el test 2024–2026.

4. **Extensión a ETH** como caso de contraste (loader ya soporta `'ETH'`).

5. **Cachear la posterior:** los notebooks 04 y 05 reestiman el SV (~120 s c/u). Para la Fase 4
   conviene persistir el `InferenceData` (p. ej. `arviz.to_netcdf` en `tesis/results/`) y cargarlo,
   evitando reestimar en cada notebook.

---

## 6. Notas de entorno

Sin cambios respecto a la Fase 2: `.venv` (uv + Python 3.12), kernel `tesis`, datos en `data/raw/`.
Ejecutar los notebooks:

```
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.kernel_name=tesis --ExecutePreprocessor.timeout=2400 \
  tesis/notebooks/04_sv_simulacion_trayectorias.ipynb \
  tesis/notebooks/05_pricing_buffer_bayesiano.ipynb
```
