# Fase 1 — Resultados y Decisiones

> Fundamentos del marco Bayesiano: análisis exploratorio de datos (Cap. 3) y
> validación del modelo de Volatilidad Estocástica (SV) con datos sintéticos (Cap. 4).
>
> **Fecha de cierre:** 2026-06-17 · **Estado:** ✅ completada

Corresponde a la **Fase 1** del roadmap en `.opencode/plans/PLAN_TESIS.md`.

---

## 1. Qué se construyó

| Entregable | Ubicación | Estado |
|---|---|---|
| Cargador de datos unificado | `tesis/data/loader.py` | ✅ |
| EDA de precios BTC/ETH (Cap. 3) | `tesis/notebooks/01_analisis_exploratorio.ipynb` | ✅ ejecutado |
| Validación SV con datos sintéticos (Cap. 4) | `tesis/notebooks/02_sv_sintetico_validacion.ipynb` | ✅ ejecutado |
| Sampler SV "a mano" (Metropolis-within-Gibbs) | `tesis/bayesian/sv_model/stochastic_volatility.py` | ✅ verificado |

El **cargador** (`loader.py`) lee los CSV crudos minuto-a-minuto de `data/raw/`
(BTC y ETH tienen nombres de columna distintos; se normalizan), cachea la lectura,
y expone: `load_price_df`, `load_price_series`, `compute_log_returns`,
`compute_pct_returns`, `infer_horizon_steps`, `split_train_test`.

---

## 2. Findings del Análisis Exploratorio (Cap. 3)

Datos: BTC diario 2012-01-01 → 2026-04-15 (5,219 obs); ETH diario 2017-08-16 →
2026-01-04 (3,064 obs). Log-retornos diarios.

### 2.1 Colas pesadas y no-normalidad
- **Curtosis (exceso) BTC diario = 36.46**, asimetría = −1.93.
- **Jarque-Bera:** BTC stat=291,656 (p≈0); ETH stat=15,600 (p≈0) → se rechaza
  normalidad de forma contundente en ambos.
- **Implicación:** justifica un modelo con volatilidad variable en el tiempo;
  la Normal i.i.d. es inadecuada.

### 2.2 Clustering de volatilidad
- ACF de log-retornos ≈ 0, pero ACF de log-retornos² es significativa y persistente.
- **Implicación:** evidencia directa de clustering de volatilidad → motiva el
  componente latente persistente `h_t` del modelo SV.

### 2.3 Estacionariedad de los retornos
- **ADF:** estadístico −10.61, p≈0 → se rechaza raíz unitaria.
- **KPSS:** estadístico 0.41, p=0.074 → no se rechaza estacionariedad.
- **Implicación:** los **log-retornos** son estacionarios (los precios no), por lo
  que el SV se modela sobre retornos.

### 2.4 Cambios de régimen
Volatilidad anualizada por período:

| Período | Vol. anualizada | Curtosis |
|---|---|---|
| Bull 2021 | 85.7% | 1.32 |
| Bear 2022 | 64.7% | 3.16 |
| Recovery 2023–25 | 48.7% | 2.65 |

- **Implicación:** la volatilidad varía fuertemente entre regímenes → un modelo de
  volatilidad constante subestima el riesgo en ciertos períodos.

### 2.5 Elección de frecuencia
Curtosis por frecuencia (BTC): **1 min = 6,929.75**, 1 hora = 114.98, 1 día = 36.46.

- **Decisión:** estimar el SV con **retornos diarios**. La frecuencia de 1 min está
  dominada por ruido de microestructura (curtosis extrema), y la diaria ofrece el
  mejor balance señal/ruido con ~5,200 observaciones, suficiente para MCMC.

---

## 3. Findings de la Validación SV (Cap. 4)

Datos sintéticos: T=1,500, parámetros verdaderos μ=−9.0, φ=0.97, σ_η=0.15.
Sampler manual Metropolis-within-Gibbs: 8,000 muestras (burn-in 2,000), con
adaptación de propuestas.

### 3.1 Recuperación de parámetros ✅
| Parámetro | Verdadero | Post. mean | IC 95% | ESS |
|---|---|---|---|---|
| μ | −9.00 | −9.05 | [−9.26, −8.83] | 587 |
| φ | 0.97 | 0.957 | [0.929, 0.979] | **21.7** |
| σ_η | 0.15 | 0.164 | [0.115, 0.219] | **12.1** |

Los tres verdaderos caen dentro del IC 95%. **Coverage de la log-volatilidad
latente h_t = 95.00%** (ideal). → **El sampler está correctamente implementado.**

### 3.2 Limitación detectada: mezcla lenta (ESS bajo)
- ESS de φ ≈ 22 y σ_η ≈ 12 sobre 8,000 muestras → alta autocorrelación.
- **Causa:** muestreo single-site M-H de `h_t` + random-walk M-H de φ.
- **Consecuencia:** las **medias** posteriores son confiables, pero las
  **desviaciones** posteriores son ruidosas; no es eficiente para correr muchas
  configuraciones (plazos × LTV) en la fase de pricing.

---

## 4. Decisiones para la Fase 2

1. **Estimar el SV sobre BTC real (diario).** Usar `load_price_series('BTC','1D')`
   + `compute_log_returns`, con split train/test en `2024-01-01`
   (train 4,383 / test 836 obs) ya definido en `loader.split_train_test`.

2. **Migrar a NUTS/PyMC (Opción C del plan) para producción.** El ESS bajo del
   sampler manual lo hace caro para simulaciones extensivas. Mantener el manual
   como demostración de comprensión (lo que espera un sinodal) y como benchmark,
   pero usar PyMC/NUTS para la estimación real y las simulaciones del Cap. 5.
   - Alternativa si se quiere mejorar el manual: KSC (Kim-Shephard-Chib) con mezcla
     de 7 normales + FFBS para muestrear `h` en bloque (mejora drástica de ESS).

3. **Corregir bug latente en `fit_sv_pymc()`** (`stochastic_volatility.py`): usa
   `pytensor.scan(...)` pero solo importa `pytensor.tensor as pt`. Agregar
   `import pytensor`. Sin esto, el wrapper PyMC lanza `NameError`.

4. **Instalar el stack Bayesiano restante.** Solo se instaló `statsmodels` para la
   Fase 1. Falta: `pymc`, `arviz`, `arch`, `numba`, `properscoring`
   (`VIRTUAL_ENV=.venv uv pip install pymc arviz arch numba properscoring`).

5. **Validar antes de confiar:** comparar posteriors del sampler manual vs. NUTS
   sobre los mismos datos (sintéticos y reales) — deben coincidir; las diferencias
   se explican por ESS/mezcla.

6. **Benchmark GARCH(1,1)** (con `arch`) como comparación frecuentista del Cap. 4,
   tal como pide el plan (§4.4).

---

## 5. Notas de entorno

- El `.venv` es gestionado por **uv** (no hay `pip`); instalar con
  `VIRTUAL_ENV=.venv uv pip install <pkg>`.
- Los notebooks usan el kernel **`tesis`** (apunta a `.venv/bin/python`); el kernel
  `python3` por defecto apunta al Python del sistema y **no** ve las librerías.
- Ejecutar notebooks:
  `.venv/bin/jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.kernel_name=tesis --ExecutePreprocessor.timeout=2400 <nb>`
