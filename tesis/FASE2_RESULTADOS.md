# Fase 2 — Resultados y Decisiones

> Estimación real del modelo de Volatilidad Estocástica (SV) sobre BTC diario:
> sampler manual vs. PyMC/NUTS, diagnósticos de convergencia, volatilidad latente,
> benchmark GARCH(1,1) y análisis de sensibilidad a priors (Cap. 4).
>
> **Fecha de cierre:** 2026-07-27 · **Estado:** ✅ completada

Corresponde a la **Fase 2** del roadmap en `.opencode/plans/PLAN_TESIS.md`.
Continúa desde [`FASE1_RESULTADOS.md`](FASE1_RESULTADOS.md).

---

## 1. Qué se construyó

| Entregable | Ubicación | Estado |
|---|---|---|
| Notebook de estimación SV sobre BTC real | `tesis/notebooks/03_sv_estimacion_btc_real.ipynb` | ✅ ejecutado |
| Wrapper PyMC/NUTS eficiente (reescrito) | `tesis/bayesian/sv_model/stochastic_volatility.py` → `fit_sv_pymc` | ✅ |
| Comparación manual vs. NUTS | notebook 03, §5 | ✅ |
| Benchmark GARCH(1,1) | notebook 03, §7 | ✅ |
| Sensibilidad a priors | notebook 03, §8 | ✅ |

**Datos:** BTC diario, train **2012-01-02 → 2023-12-31** (4 382 obs), test
**2024-01-01 → 2026-04-15** (836 obs). Split fijado en Fase 1. Log-retornos
**centrados** con la media del train (deriva 0.00207/día); std 0.0438, curtosis
(exceso) 34.4.

---

## 2. Cambios de código en la Fase 2

1. **Bug corregido** en `fit_sv_pymc`: usaba `pytensor.scan` sin `import pytensor`
   (lanzaba `NameError`). Registrado como pendiente en Fase 1 §4.3.

2. **Modelo PyMC reescrito con `pm.AR`** (antes construía el proceso latente `h`
   con `pytensor.scan` recursivo). El `scan` era inviable sobre datos reales:
   **>400 s solo para 100 draws** (T=4 382). Con `pm.AR` (logp vectorizado, mismo
   modelo AR(1) estacionario centrado en μ con init estacionaria) baja a
   **~120 s para 2 000 draws × 4 cadenas**.

3. **Parámetro `tune`** añadido a `fit_sv_pymc` (default = `n_samples`).

4. **`progressbar=False`** en `pm.sample`: el widget de progreso de ipywidgets
   "tragaba" el stdout posterior al ejecutar vía nbconvert (perdía tablas impresas).

---

## 3. Estimación sobre BTC real

### 3.1 Resultados (posterior)

Priors: μ~N(−9, 3²); (φ+1)/2~Beta(20, 1.5); σ_η²~IG(2.5, 0.025) — idénticas en
ambos métodos.

| Parámetro | Manual (M-w-Gibbs, 8 000) | NUTS (2 000×4) | Interpretación |
|---|---|---|---|
| μ | −7.203  [−7.383, −7.015] | −7.209  [−7.397, −7.034] | vol diaria mediana ≈ e^(μ/2) ≈ **2.7 %** |
| φ | 0.906  [0.879, 0.930] | 0.903  [0.879, 0.927] | persistencia alta pero **estacionaria** |
| σ_η | 0.566  [0.491, 0.650] | 0.578  [0.504, 0.654] | vol-de-vol elevada (mercado heterogéneo) |

Ambos métodos **coinciden en las medias** posteriores (diferencias < 1 % relativo),
confirmando que la reimplementación NUTS es correcta.

### 3.2 Eficiencia: NUTS ≫ manual

| Parámetro | ESS manual (8 000) | ESS NUTS (8 000) | mejora |
|---|---|---|---|
| μ | 3 077 | 5 162 | 1.7× |
| φ | **25** | **179** | **7.2×** |
| σ_η | **18** | **122** | **6.8×** |

- El manual reproduce el problema de **mezcla lenta** documentado en Fase 1: ESS
  de φ y σ_η en el rango 18–25 sobre 8 000 muestras.
- NUTS: **0 divergencias**, R̂ = 1.00 (μ), 1.01 (φ), 1.02 (σ_η) con `target_accept=0.99`
  y `tune=3 000`. ESS de φ, σ_η ~120–180 → **~7× más eficiente** por muestra.
- **Decisión:** usar **NUTS** para toda la estimación y simulación de la Fase 3;
  el manual queda como demostración algorítmica y benchmark (lo que espera un sinodal).

### 3.3 Volatilidad latente estimada

Vol. anualizada implícita σ_t = e^(h_t/2)·√252 (mediana posterior): **min 7.1 %,
mediana 42.7 %, máx 572.1 %**. El máximo corresponde a episodios de 2013 y marzo-2020.

---

## 4. Benchmark GARCH(1,1)

GARCH(1,1) con errores t de Student (`arch`, retornos ×100), media cero:

| Parámetro | Valor |
|---|---|
| ω | 0.285 |
| α₁ | 0.150 |
| β₁ | 0.850 |
| **α₁ + β₁ (persistencia)** | **1.0000** |

- **Hallazgo:** GARCH se pega a la frontera **IGARCH** (α+β = 1.0000) → implica
  **volatilidad no estacionaria** (shocks de varianza permanentes) sobre la historia
  completa de BTC. El SV, en cambio, estima φ ≈ 0.90 → **volatilidad estacionaria**
  con reversión a la media. Es un argumento a favor del SV: no fuerza persistencia
  unitaria y cuantifica la incertidumbre sobre φ.
- Pese a la diferencia estructural, las series de volatilidad condicional de ambos
  modelos están **correlacionadas 0.801** (capturan el mismo clustering).

---

## 5. Sensibilidad a priors

NUTS reestimado (1 500×4) bajo tres priors para φ:

| Prior | μ | φ | σ_η | máx R̂ |
|---|---|---|---|---|
| base (a_φ=20, b_φ=1.5) | −7.21 | 0.903 | 0.580 | 1.04 |
| difusa (a_φ=2, b_φ=2) | −7.21 | 0.901 | 0.584 | 1.05 |
| informativa (a_φ=50, b_φ=1) | −7.21 | 0.906 | 0.572 | 1.06 |

- **Las posteriors son robustas:** μ invariante, φ ∈ [0.901, 0.906], σ_η ∈ [0.572, 0.584].
  Las conclusiones no dependen de la prior → la **verosimilitud domina** (4 382 obs).
- Nota: con solo 1 500 draws el máx R̂ queda en 1.04–1.06; para la corrida definitiva
  del Cap. 4 conviene subir draws/tune (como en la corrida principal, R̂ ≤ 1.02).

---

## 6. Decisiones para la Fase 3

1. **Estimación de referencia:** NUTS con `n_samples=2 000, n_chains=4,
   target_accept=0.99, tune=3 000` sobre `y_train` centrado. Guardar el `InferenceData`
   para reutilizar la posterior en las simulaciones (evitar reestimar).

2. **Simulación posterior-predictiva (Cap. 5):** usar
   `bayesian/sv_model/trajectory_simulator.py` alimentado con las muestras posteriores
   (μ*, φ*, σ_η*) para propagar incertidumbre epistémica + aleatórica.

3. **Comparar contra la línea base** (`baseline/mc_bootstrap/simulator.py`): distribución
   de precios finales, P(caída ≥20 %/40 %), drawdown máximo, a 90/180/365 días.

4. **Mantener GARCH** como benchmark de volatilidad en el Cap. 4 escrito; discutir el
   contraste IGARCH (α+β=1) vs. SV estacionario (φ≈0.90).

5. **Pendiente de convergencia:** el ESS de φ/σ_η (~120–180) es aceptable pero mejorable.
   Si la Fase 3 necesita colas más finas, evaluar reparametrización no-centrada del
   proceso latente (requiere volver a `scan` o un `CustomDist`; medir costo/beneficio).

---

## 7. Notas de entorno (actualizado)

- El `.venv` de la Fase 1 se perdió (estaba en `.gitignore`, gestionado por uv). Se
  **reconstruyó con `uv` + Python 3.12.13** (el Python 3.14 del sistema no tiene wheels
  de PyMC/pytensor). Stack instalado desde `tesis/requirements.txt`
  (pymc 5.25, arviz, arch 6.3, numba, properscoring, jupyter).
- **Datos crudos:** los CSV minuto-a-minuto van en `data/raw/` (raíz del repo), **no**
  en `tesis/data/` (que es el paquete Python del loader). Ambos están en `.gitignore`.
- Kernel Jupyter `tesis` re-registrado (apunta a `.venv/bin/python`).
- Ejecutar el notebook:
  `.venv/bin/jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.kernel_name=tesis --ExecutePreprocessor.timeout=2400 tesis/notebooks/03_sv_estimacion_btc_real.ipynb`
