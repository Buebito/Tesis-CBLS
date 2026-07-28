# Fase 4 — Resultados y Decisiones

> Validación y backtesting: evaluación de pronóstico *one-step-ahead* del SV contra
> benchmarks (GARCH(1,1)-t, Gaussiano i.i.d.) con log-score, CRPS y calibración (PIT),
> en BTC y ETH (Cap. 8).
>
> **Fecha de cierre:** 2026-07-28 · **Estado:** ✅ código completado (redacción pendiente)

Corresponde a la **Fase 4** del roadmap en `.opencode/plans/PLAN_TESIS.md`.
Continúa desde [`FASE3_RESULTADOS.md`](FASE3_RESULTADOS.md).

---

## 1. Qué se construyó

| Entregable | Ubicación | Estado |
|---|---|---|
| Módulo de evaluación de pronóstico | `tesis/bayesian/sv_model/forecast_eval.py` | ✅ |
| Notebook de validación / backtest | `tesis/notebooks/06_validacion_backtest.ipynb` | ✅ ejecutado |

**Módulo `forecast_eval.py`:**
- `sv_particle_filter(y, mu, phi, sigma_eta, h_init, ...)` — filtro de partículas bootstrap que
  produce densidades predictivas one-step del SV, condicionadas en el estado de volatilidad al
  final del train (`h_init` = posterior de $h_T$).
- `garch_onestep_eval(y_train, y_test)` — GARCH(1,1)-t ajustado en train y filtrado por el test.
- `gaussian_iid_eval(y_train, y_test)` — benchmark de volatilidad constante.
- `summarize_eval(name, res)` — log-score, CRPS, coverage 95%, p-valor KS del PIT.

**Validación del filtro** (datos sintéticos SV conocidos): el SV-PF supera al Gaussiano i.i.d.
en log-score, PIT ≈ Uniforme (KS p=0.52) y coverage 94.3% ≈ 95%. El filtro está bien implementado.

---

## 2. Resultados BTC (test 2024-01-01 → 2026-04-15, 836 obs)

Vol. anualizada realizada en el test: **40.1%**.

| Modelo | Log-score ↑ | CRPS ↓ | Coverage 95% | PIT KS p |
|---|---|---|---|---|
| SV (full 2012–23) | 2.301 | 0.0136 | 0.964 | 0.001 |
| GARCH(1,1)-t | **2.320** | **0.0135** | 0.944 | 0.003 |
| Gaussiano i.i.d. | 2.042 | 0.0154 | 0.993 | 0.000 |

- **La volatilidad condicional importa:** SV y GARCH superan claramente al Gaussiano i.i.d.
  (log-score 2.30–2.32 vs 2.04; CRPS ~0.0135 vs 0.0154).
- **SV ≈ GARCH:** el SV Bayesiano es competitivo; GARCH queda marginalmente por encima.
- **El SV sobre-cubre** (coverage 96.4% > 95%): intervalos algo anchos, coherente con la
  sobre-proyección de volatilidad detectada en la Fase 3. GARCH sub-cubre levemente (94.4%).
- **Calibración:** ambos condicionales tienen PIT KS bajo. El SV con densidad de observación
  **Gaussiana** subestima las colas de BTC (curtosis extrema); GARCH usa errores t (ν≈3.3).

### 2.1 Historia completa vs ventana reciente

| Modelo | Log-score | CRPS | Coverage | PIT KS p |
|---|---|---|---|---|
| SV full 2012–23 | 2.3007 | 0.0136 | 0.964 | 0.0010 |
| SV reciente 2021–23 | 2.3077 | 0.0136 | 0.964 | 0.0011 |

La ventana reciente mejora **marginalmente** el log-score. El filtro condiciona en los retornos
realizados en cada paso, por lo que la elección de ventana afecta poco al pronóstico one-step
(a diferencia de la simulación multi-paso de la Fase 3, donde sí importaba).

---

## 3. Resultados ETH (test 2024-01-01 → 2026-01-04, 735 obs)

| Modelo | Log-score ↑ | CRPS ↓ | Coverage 95% | PIT KS p |
|---|---|---|---|---|
| SV | 1.950 | 0.0193 | **0.952** | **0.089** |
| GARCH(1,1)-t | **1.958** | 0.0193 | 0.946 | 0.641 |
| Gaussiano i.i.d. | 1.819 | 0.0204 | 0.976 | 0.000 |

- **El marco generaliza sin re-tunear:** mismo patrón cualitativo que BTC (condicionales ≫ i.i.d.,
  SV ≈ GARCH).
- **En ETH el SV SÍ calibra bien:** coverage 95.2% (casi perfecto) y PIT KS p=0.089 (no se rechaza
  uniformidad al 5%). Las colas de ETH son menos extremas que las de BTC, así que la observación
  Gaussiana del SV resulta adecuada. Refuerza que la mis-calibración en BTC viene de sus colas.
- SV(ETH): μ=−6.56 (vol. mayor que BTC), φ=0.904, σ_η=0.453.

---

## 4. Decisiones / trabajo futuro (Cap. 9)

1. **SV-t (errores Student-t):** la mejora natural para la mis-calibración en BTC. Sustituir la
   densidad de observación Gaussiana por una t (o añadir *leverage*/asimetría). Es la recomendación
   principal derivada de la validación.
2. **Particle-MCMC:** propagar incertidumbre paramétrica dentro del filtro (aquí se usan medias
   posteriores); PMMH o SMC² darían densidades predictivas totalmente Bayesianas.
3. **Pérdida económica:** evaluar con el costo de liquidación (función de pérdida del negocio),
   además de las métricas estadísticas, para conectar con el pricing de la Fase 3.

---

## 5. Notas de entorno

Sin cambios respecto a las fases previas (`.venv` uv + Python 3.12, kernel `tesis`, datos en
`data/raw/`). El notebook 06 ajusta el SV tres veces (BTC full, BTC reciente, ETH); para iterar
conviene cachear los `InferenceData` (ver decisión #5 de la Fase 3). Ejecutar:

```
.venv/bin/jupyter nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.kernel_name=tesis --ExecutePreprocessor.timeout=2400 \
  tesis/notebooks/06_validacion_backtest.ipynb
```
