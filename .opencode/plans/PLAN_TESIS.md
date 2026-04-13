# Plan de Tesis: Modelación Bayesiana del Riesgo de Liquidación en Préstamos Respaldados por Bitcoin

**Programa:** Licenciatura en Matemáticas Aplicadas, ITAM  
**Tema propuesto:** *Inferencia Bayesiana para Modelos de Volatilidad Estocástica con Aplicación al Diseño de Coberturas en Préstamos Colateralizados con Bitcoin*

---

## 1. Resumen Ejecutivo

Esta tesis propone un marco Bayesiano completo para modelar el riesgo de liquidación en préstamos respaldados por Bitcoin y diseñar una cobertura (seguro/buffer) que minimice la probabilidad de liquidación. El trabajo combina:

- **Modelo de Volatilidad Estocástica (SV)** estimado con MCMC para capturar la dinámica no estacionaria de los retornos de BTC.
- **Simulación Monte Carlo** con el modelo SV estimado para generar trayectorias futuras de precios realistas.
- **Modelo Bayesiano de frecuencia-severidad** para la distribución de pérdidas por liquidación.
- **Diseño óptimo de cobertura** que determina primas y buffers para distintos plazos (3, 6, 12 meses).
- **Validación** con backtesting histórico y extensión a ETH como caso de contraste.

El proyecto se apoya en un cuerpo de trabajo previo de simulaciones (Segment Tree para préstamos, KDE Bootstrap Monte Carlo, buffer de volatilidad) que sirve como línea base frecuentista contra la cual se comparan los resultados Bayesianos.

---

## 2. Motivación y Contexto

### 2.1 El Problema
En plataformas de préstamos cripto (CeFi/DeFi), un prestatario deposita criptomonedas como colateral para obtener un préstamo en moneda fiat o stablecoins. Si el precio del colateral cae y el Loan-to-Value (LTV) supera un umbral (~80%), se ejecuta una **liquidación forzosa**. Esto genera:
- **Pérdida para el prestatario:** pierde su colateral a precios desfavorables.
- **Riesgo para el prestamista:** si la liquidación no cubre el principal (shortfall).

### 2.2 El Gap
Los modelos actuales para pricing de riesgo en crypto lending son mayoritariamente frecuentistas (backtesting histórico, Monte Carlo bootstrap con i.i.d.). Esto tiene dos limitaciones fundamentales:
1. **No capturan incertidumbre paramétrica:** un solo estimador puntual no refleja la incertidumbre sobre la verdadera distribución de riesgo.
2. **Asumen estacionaridad implícita:** el bootstrap clásico rompe la estructura temporal (clustering de volatilidad, cambios de régimen), produciendo estimaciones de riesgo subestimadas (el bootstrap MC da P(liq)=4% vs. 14% histórico).

### 2.3 La Propuesta
Usar estadística Bayesiana computacional (MCMC) para:
1. Estimar un modelo de volatilidad estocástica que respeta la estructura temporal.
2. Propagar la incertidumbre paramétrica a las simulaciones y al pricing.
3. Diseñar una cobertura con garantías probabilísticas robustas.

---

## 3. Estructura de la Tesis (Capítulos)

### Capítulo 1: Introducción
- Contexto del mercado de préstamos cripto (CeFi vs. DeFi).
- Mecánica de un préstamo colateralizado: LTV, margin call, liquidación.
- Planteamiento del problema y objetivos.
- Contribuciones de la tesis.
- Estructura del documento.

### Capítulo 2: Marco Teórico
Dos bloques principales:

#### 2.1 Estadística Bayesiana y MCMC
- **Teorema de Bayes y inferencia posterior.** Formulación rigurosa: π(θ|y) ∝ L(y|θ)π(θ).
- **Monte Carlo vía Cadenas de Markov (MCMC):**
  - Cadenas de Markov: definiciones, ergodicidad, convergencia.
  - **Muestreo de Gibbs:** para distribuciones condicionales completas conocidas.
  - **Metropolis-Hastings:** para posteriors intratables — propuesta, razón de aceptación, convergencia.
  - **Hamiltonian Monte Carlo (HMC):** opcional pero recomendado para el modelo SV — gradientes, leapfrog, NUTS.
- **Diagnósticos de convergencia:** R-hat (Gelman-Rubin), ESS (Effective Sample Size), trace plots, autocorrelación.
- **Distribución predictiva posterior:** p(y_{n+1}|y) = ∫ p(y_{n+1}|θ)π(θ|y)dθ.

#### 2.2 Modelos de Volatilidad Estocástica
- **Hechos estilizados de retornos cripto:** colas pesadas, clustering de volatilidad, asimetría, no estacionaridad.
- **Modelo SV canónico (Taylor, 1982; Kim, Shephard & Chib, 1998):**

```
y_t = exp(h_t / 2) · ε_t,        ε_t ~ N(0,1)
h_t = μ + φ(h_{t-1} - μ) + σ_η · η_t,   η_t ~ N(0,1)
```

  donde y_t es el log-retorno, h_t es la log-volatilidad latente, μ es el nivel medio de log-volatilidad, φ ∈ (-1,1) es la persistencia, y σ_η > 0 es la volatilidad de la volatilidad.

- **Comparación con GARCH:** por qué SV es más natural para un enfoque Bayesiano (variables latentes → Gibbs sampling).

#### 2.3 Pricing de Seguros y Coberturas
- Principio de prima de riesgo: Prima = E[Pérdida] + carga de seguridad.
- Modelo colectivo de riesgo: frecuencia × severidad.
- Credibilidad Bayesiana (como en la tesis de referencia de Gutiérrez Salgado).
- Aplicación a préstamos cripto: la liquidación como "siniestro".

### Capítulo 3: Datos y Análisis Exploratorio

**Datos disponibles (ya existentes):**
- Precios minuto-a-minuto BTC/USDT y ETH/USDT de Binance (2020-2025).
- ~2.36 millones de observaciones por activo.

**Análisis a realizar:**
1. **Series de precios y retornos:** gráficas de precio, log-retornos, distribución empírica.
2. **Hechos estilizados:**
   - Exceso de curtosis (QQ-plot vs. Normal).
   - Clustering de volatilidad (ACF de retornos vs. ACF de retornos al cuadrado).
   - Asimetría (leverage effect).
3. **Análisis por frecuencia:** comparar retornos a 1 minuto, 1 hora, 1 día para elegir la frecuencia de estimación del modelo SV.
   - **Recomendación:** estimar el modelo SV con retornos **diarios** (más estable, menos ruido de microestructura) y luego interpolar para simulaciones de mayor frecuencia si es necesario.
4. **Estacionaridad:** tests ADF/KPSS sobre log-retornos.
5. **Análisis por ventanas temporales:** dividir en períodos (bull 2020-21, crash 2022, recuperación 2023-25) para motivar la necesidad de un modelo que capture cambios de régimen/volatilidad.

**Qué ya existe que sirve:** `data/create_data.ipynb` y `data/processed/historic_prices.csv`.  
**Qué falta:** el análisis exploratorio formal con los puntos de arriba.

### Capítulo 4: Modelo de Volatilidad Estocástica Bayesiano (Corazón Matemático)

Este es el capítulo central de la tesis desde el punto de vista de matemáticas aplicadas.

#### 4.1 Especificación del Modelo

**Modelo SV con prior conjugada/semi-conjugada:**

```
y_t | h_t  ~  N(0, exp(h_t)),          t = 1, ..., T
h_t | h_{t-1}, μ, φ, σ_η  ~  N(μ + φ(h_{t-1} - μ), σ_η²)
h_0  ~  N(μ, σ_η² / (1 - φ²))
```

**Priors:**
- μ ~ N(μ_0, σ_μ²) — prior difusa centrada en la log-volatilidad histórica media.
- (φ + 1)/2 ~ Beta(a_φ, b_φ) — asegura φ ∈ (-1,1); prior centrada cerca de 1 refleja alta persistencia.
- σ_η² ~ IG(a_σ, b_σ) — Inverse-Gamma para la varianza.

#### 4.2 Estrategia de Estimación con MCMC

**Opción A — Gibbs Sampling (Kim, Shephard & Chib, 1998):**
Transformar el modelo usando y*_t = log(y_t²) para obtener un modelo lineal gaussiano con errores log(χ²_1), que se aproxima con una mezcla de 7 normales. Esto permite:
1. Muestrear **h** | μ, φ, σ_η, **s**, **y** con el filtro de simulación (FFBS — Forward Filtering Backward Sampling).
2. Muestrear **s** | **h**, **y** (indicadores de la mezcla).
3. Muestrear (μ, φ, σ_η²) | **h** de sus condicionales completas (cerradas con priors conjugadas).

**Opción B — Metropolis-within-Gibbs (más directo):**
1. Muestrear cada h_t individualmente con M-H usando la condicional completa (depende solo de h_{t-1}, h_{t+1}, y_t y los parámetros).
2. Muestrear μ, φ, σ_η con pasos M-H o Gibbs según la condicional.

**Opción C — PyMC / Stan (recomendada para implementación extensiva):**
Usar un framework probabilístico que implementa NUTS (No-U-Turn Sampler, variante de HMC) para muestrear eficientemente. Esto permite enfocarse en la formulación del modelo y los resultados sin reimplementar el sampler.

> **Recomendación:** Implementar al menos uno de (A) o (B) "a mano" en Python para demostrar comprensión profunda del algoritmo (esto es lo que espera un sinodal de Mat. Aplicadas del ITAM), y luego usar (C) para validar y para las simulaciones extensivas.

#### 4.3 Diagnósticos de Convergencia
- Trace plots de μ, φ, σ_η y una muestra de h_t's.
- R-hat (Gelman-Rubin) con múltiples cadenas.
- ESS (Effective Sample Size) — reportar y discutir.
- Autocorrelación de las cadenas.
- **Análisis de sensibilidad a priors:** correr con priors difusas vs. informativas y comparar posteriors.

#### 4.4 Resultados de la Estimación
- Posteriors marginales de μ, φ, σ_η.
- Serie estimada de log-volatilidad {h_t} con bandas de credibilidad al 95%.
- Comparación con volatilidad realizada y con GARCH(1,1) como benchmark.
- Interpretación: ¿qué tan persistente es la volatilidad de BTC? ¿Cuánta incertidumbre hay sobre el nivel medio de volatilidad?

### Capítulo 5: Simulación de Trayectorias de Precio

#### 5.1 Monte Carlo con el Modelo SV Posterior

A diferencia del bootstrap actual (que asume retornos i.i.d.), aquí las simulaciones **propagan la incertidumbre paramétrica**:

```
Para cada muestra posterior (μ*, φ*, σ_η*) ~ π(θ|y):
    Simular h*_1, h*_2, ..., h*_T con la ecuación de transición
    Simular y*_t = exp(h*_t / 2) · ε_t, ε_t ~ N(0,1)
    Reconstruir trayectoria de precio: P*_t = P_0 · exp(Σ y*_s)
```

Esto genera un ensemble de trayectorias que refleja **dos fuentes de incertidumbre:**
1. Incertidumbre aleatoria (variabilidad intrínseca de precios).
2. Incertidumbre epistémica (no conocemos los parámetros exactos del modelo).

#### 5.2 Comparación con Métodos Existentes

Comparar las distribuciones de trayectorias generadas por:
1. **KDE Bootstrap** (método actual en `precios_futuros/utils/bootstrap_mc.py`).
2. **KDE Bootstrap con bias** (método en `bolsa_volatilidad/utils/bootstrap_mc.py`).
3. **Modelo SV Bayesiano** (nuevo).

Métricas de comparación:
- Distribución de precios finales (P05, mediana, P95).
- P(caída >= 20%), P(caída >= 40%).
- Distribución de drawdown máximo en la ventana.
- Distribución de la volatilidad realizada de las trayectorias simuladas vs. histórica.

**Hipótesis:** El modelo SV producirá trayectorias con mayor dispersión (especialmente en las colas) porque captura clustering de volatilidad y propaga incertidumbre paramétrica.

#### 5.3 Análisis por Plazo
Generar trayectorias para horizontes de 90, 180 y 365 días. Analizar cómo cambia el perfil de riesgo con el plazo.

### Capítulo 6: Modelo Bayesiano de Frecuencia-Severidad de Liquidación

Este capítulo conecta la tesis de referencia (modelo Bayesiano de severidad de siniestros) con la aplicación a crypto lending.

#### 6.1 Definición del "Siniestro"

Un préstamo con LTV inicial L_0 y liquidación en L_liq se liquida cuando:

```
P_principal / (C · p_t) >= L_liq  ⟺  p_t <= (L_0 / L_liq) · p_0
```

La **pérdida por liquidación** (loss given liquidation) es:

```
LGL = max(0, 1 - p_T · (1 - f) / (L_0 · p_0))
```

donde f es la tasa de fricción (slippage + fees) y p_T es el precio al momento de la liquidación.

#### 6.2 Modelo de Frecuencia

**Probabilidad de liquidación** en un horizonte τ:

Usando las trayectorias simuladas del Capítulo 5, para cada muestra posterior θ*:

```
q(τ, θ*) = (1/N) · Σ 𝟙[min_{t∈[0,τ]} P_t^(i)(θ*) <= (L_0/L_liq) · P_0]
```

La **distribución posterior de la probabilidad de liquidación** se obtiene integrando sobre la posterior de los parámetros, lo que da intervalos de credibilidad para P(liquidación), no un solo número.

#### 6.3 Modelo de Severidad

Dado que ocurre liquidación, modelar la severidad con distribución Gamma (siguiendo la tesis de referencia):

```
LGL | α, β  ~  Gamma(α, β)
```

**Priors:**
- Informativa basada en el Saltshield histórico: mean LGL ≈ 26.35% (de resultados existentes).
- Jeffrey's prior como alternativa no informativa.

**Estimación:** MCMC (Gibbs o M-H) para obtener π(α, β | datos de liquidación).

#### 6.4 Pérdida Esperada y Prima de Riesgo

La **prima de riesgo Bayesiana** por préstamo de plazo τ es:

```
PR(τ) = E_{π(θ|y)}[q(τ,θ) · E_{π(α,β|y)}[LGL]] · Principal
```

La **prima de tarifa** (con cargas) sigue la estructura de la tesis de referencia:

```
PT(τ) = PR(τ) / (1 - (G_adm + C_adq + U))
```

#### 6.5 Estratificación por Plazo y LTV

Calcular primas para la tabla de combinaciones:

| Plazo \ LTV Inicial | 40% | 50% | 60% | 70% |
|---|---|---|---|---|
| 3 meses | PR_1 | PR_2 | PR_3 | PR_4 |
| 6 meses | PR_5 | PR_6 | PR_7 | PR_8 |
| 12 meses | PR_9 | PR_10 | PR_11 | PR_12 |

### Capítulo 7: Diseño de la Cobertura Óptima

#### 7.1 Dos Productos Complementarios

**Producto 1: Seguro de Liquidación (Saltshield Bayesiano)**
- Póliza que paga al prestatario si su préstamo se liquida.
- Prima calculada con el modelo del Capítulo 6.
- **Innovación vs. Saltshield actual:** intervalos de credibilidad en la prima, no solo un punto.

**Producto 2: Buffer de Volatilidad Óptimo Bayesiano**
- Extensión de `volatility_buffer_optimizer.py` pero usando trayectorias del modelo SV en lugar de históricas.
- Para un nivel de confianza α (e.g., 99%), el buffer óptimo bajo el modelo Bayesiano es:

```
B*(α) = inf{ b : P_{π(θ|y)}[max_{t∈[0,τ]} LTV_t(b) < L_liq] >= α }
```

- **Comparación clave:** buffer Bayesiano vs. buffer histórico (método actual). ¿El Bayesiano es más conservador? ¿Más eficiente?

#### 7.2 Análisis de Sensibilidad
- Sensibilidad a la elección de priors.
- Sensibilidad a la ventana de datos de estimación.
- Sensibilidad al parámetro de fricción.
- Impacto de incluir o no incertidumbre paramétrica (posterior completa vs. estimador puntual MAP).

### Capítulo 8: Validación

#### 8.1 Backtesting Histórico
- Dividir datos en entrenamiento (2020-2023) y prueba (2024-2025).
- Estimar modelo SV con datos de entrenamiento.
- Evaluar calibración: ¿los intervalos de predicción al X% contienen X% de las observaciones reales?
- Comparar P(liquidación) predicha vs. observada en período de prueba.
- **Comparar con línea base frecuentista** (métodos actuales) en las mismas métricas.

#### 8.2 Extensión a ETH
- Repetir todo el pipeline (estimación SV, simulación, pricing) con datos de ETH.
- Evaluar si el modelo captura correctamente el diferente perfil de volatilidad de ETH (más volátil que BTC).
- Comparar primas y buffers BTC vs. ETH — ¿son consistentes con la intuición y con resultados previos?

#### 8.3 Métricas de Evaluación
- **CRPS (Continuous Ranked Probability Score):** para evaluar calibración de las distribuciones predictivas.
- **Log-score:** para comparar modelos predictivos.
- **Coverage:** proporción de observaciones dentro de los intervalos de credibilidad.
- **Sharpe ratio del producto de cobertura:** ¿la prima cobrada es suficiente para cubrir las pérdidas en el período de prueba?

### Capítulo 9: Conclusiones
- Resumen de contribuciones.
- Comparación final: enfoque Bayesiano vs. frecuentista para crypto lending.
- Limitaciones y trabajo futuro.

---

## 4. Herramientas y Librerías

### Ya disponibles
| Librería | Uso actual |
|---|---|
| `numpy`, `pandas` | Manipulación de datos |
| `scipy` | Distribuciones, integración numérica |
| `matplotlib`, `seaborn` | Visualización |
| `KDEpy` | KDE Bootstrap |
| `tqdm` | Barras de progreso |
| `ipywidgets` | Dashboards interactivos |

### Por agregar

| Librería | Uso propuesto | Instalación |
|---|---|---|
| `pymc` (v5+) | Framework MCMC: NUTS/HMC, modelo SV | `pip install pymc` |
| `arviz` | Diagnósticos MCMC (R-hat, ESS, trace plots) | viene con pymc |
| `pytensor` | Backend de PyMC para diferenciación automática | viene con pymc |
| `statsmodels` | GARCH benchmark, tests de estacionaridad | `pip install statsmodels` |
| `arch` | Modelos GARCH como benchmark | `pip install arch` |
| `numba` | Acelerar el sampler "a mano" | `pip install numba` |
| `properscoring` | CRPS y métricas de calibración | `pip install properscoring` |
| `emcee` | Alternativa ligera de MCMC (ensemble sampler) | `pip install emcee` (opcional) |

---

## 5. Roadmap de Implementación

### Fase 1: Fundamentos (4-6 semanas)
- [ ] Análisis exploratorio formal de datos BTC (Capítulo 3).
- [ ] Implementar modelo SV "a mano" con Metropolis-within-Gibbs en Python puro.
- [ ] Verificar con datos simulados (generar datos de un SV conocido, recuperar parámetros).
- [ ] Escribir Capítulo 2 (Marco Teórico) en paralelo.

### Fase 2: Estimación Real (3-4 semanas)
- [ ] Estimar modelo SV con datos reales de BTC (retornos diarios).
- [ ] Implementar en PyMC/Stan para comparar con implementación manual.
- [ ] Diagnósticos de convergencia completos.
- [ ] Análisis de sensibilidad a priors.
- [ ] Escribir Capítulo 4.

### Fase 3: Simulación y Pricing (4-5 semanas)
- [ ] Generar trayectorias Monte Carlo con posterior del SV.
- [ ] Comparar con KDE Bootstrap existente.
- [ ] Implementar modelo frecuencia-severidad Bayesiano.
- [ ] Calcular primas por plazo y LTV.
- [ ] Diseñar cobertura óptima (buffer Bayesiano).
- [ ] Escribir Capítulos 5, 6 y 7.

### Fase 4: Validación (3-4 semanas)
- [ ] Backtesting con split temporal.
- [ ] Extensión a ETH.
- [ ] Métricas de evaluación (CRPS, coverage, log-score).
- [ ] Escribir Capítulo 8.

### Fase 5: Escritura Final (3-4 semanas)
- [ ] Capítulo 1 (Introducción) y Capítulo 9 (Conclusiones).
- [ ] Revisión completa del documento.
- [ ] Preparar presentación.

**Total estimado: 17-23 semanas** (~4-6 meses).

---

## 6. Referencias Clave a Investigar

### Volatilidad Estocástica Bayesiana
1. **Kim, S., Shephard, N., & Chib, S. (1998).** *Stochastic Volatility: Likelihood Inference and Comparison with ARCH Models.* Review of Economic Studies. — El paper fundamental del modelo SV con Gibbs sampling y mezcla de normales.
2. **Kastner, G. & Frühwirth-Schnatter, S. (2014).** *Ancillarity-Sufficiency Interweaving Strategy (ASIS) for Boosting MCMC Estimation of Stochastic Volatility Models.* Computational Statistics & Data Analysis. — Mejoras al sampler.
3. **Taylor, S.J. (1982).** *Financial Returns Modelled by the Product of Two Stochastic Processes.* — Formulación original del modelo SV.

### MCMC General
4. **Robert, C. & Casella, G. (2004).** *Monte Carlo Statistical Methods.* Springer. — Referencia completa de MCMC.
5. **Gelman, A., et al. (2013).** *Bayesian Data Analysis* (3rd ed.). CRC Press. — El libro de referencia Bayesiana.
6. **Hoffman, M. & Gelman, A. (2014).** *The No-U-Turn Sampler.* JMLR. — NUTS, el sampler de PyMC/Stan.

### Crypto y Volatilidad
7. **Catania, L., Grassi, S., & Ravazzolo, F. (2019).** *Forecasting Cryptocurrencies under Model and Parameter Instability.* International Journal of Forecasting. — Modelos de volatilidad para crypto.
8. **Chu, J., Chan, S., & Zhang, Y. (2020).** *High Frequency Momentum Trading with Cryptocurrencies.* — Análisis de microestructura cripto.

### Pricing de Seguros Bayesiano
9. **Klugman, S., Panjer, H., & Willmot, G. (2019).** *Loss Models: From Data to Decisions.* Wiley. — Modelos de frecuencia-severidad, credibilidad.
10. **Gutiérrez Salgado, A. (2025).** Tesis ITAM: *Diseño de una Cobertura Indemnizatoria de Gastos Médicos Mayores para Mujeres.* — Referencia directa para el framework Bayesiano de pricing.

### DeFi / Crypto Lending
11. **Gudgeon, L., et al. (2020).** *DeFi Protocols for Loanable Funds.* — Análisis formal de protocolos de préstamo DeFi.
12. **Qin, K., et al. (2021).** *An Empirical Study of DeFi Liquidations.* — Datos empíricos de liquidaciones.

---

## 7. Qué Se Puede Reusar del Código Existente

| Componente existente | Uso en la tesis |
|---|---|
| `data/create_data.ipynb` + CSV | Base de datos completa, sin cambios |
| `prestamos/utils/model.py` (SegmentTree + simulación) | Backtesting de la cobertura: simular si préstamos con la prima/buffer propuesto sobreviven |
| `prestamos/run_saltshield*.py` | **Línea base frecuentista** contra la cual comparar el pricing Bayesiano |
| `precios_futuros/utils/bootstrap_mc.py` | **Línea base MC** contra la cual comparar las trayectorias del modelo SV |
| `bolsa_volatilidad/utils/volatility_buffer_optimizer.py` | Reusar la lógica de suffix/window min, alimentándola con trayectorias del modelo SV |
| `bolsa_volatilidad/utils/bootstrap_mc.py` (BiasConfig) | Referencia para stress testing; comparar con trayectorias SV de colas |

---

## 8. Riesgos y Mitigación

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| MCMC no converge para SV | Media | Usar PyMC/Stan como fallback; simplificar modelo (e.g., fijar μ) |
| Datos insuficientes para estimar severidad | Baja | Ya hay ~1,600 escenarios de liquidación del Saltshield; generar más con simulación |
| Modelo SV no mejora vs. GARCH | Media | Esto es un resultado válido de la tesis; documentar cuándo cada enfoque es superior |
| Complejidad computacional excesiva | Baja | Estimar con retornos diarios (~1,800 obs), no minuto-a-minuto |
| Sinodales cuestionan relevancia práctica | Baja | Existe el producto real (X2O Shield) como motivación; la aplicación concreta es el pricing |

---

## 9. Sugerencia de Título Definitivo

Opciones (de más técnica a más aplicada):

1. **"Inferencia Bayesiana para Modelos de Volatilidad Estocástica con Aplicación al Diseño de Coberturas en Préstamos Colateralizados con Bitcoin"** (Recomendado para Mat. Aplicadas ITAM)

2. **"Modelación Bayesiana del Riesgo de Liquidación en Préstamos Respaldados por Criptomonedas: Volatilidad Estocástica, MCMC y Diseño de Cobertura"**

3. **"Métodos de Monte Carlo vía Cadenas de Markov para la Estimación de Riesgo y Pricing de Coberturas en Crypto-Backed Lending"**

> **Recomendación:** La opción 1 es la más adecuada para Mat. Aplicadas en ITAM — enfatiza la herramienta matemática (inferencia Bayesiana, SV), menciona la aplicación sin reducirse a ella, y es suficientemente específica para que un sinodal entienda exactamente qué esperar.

---

## 10. Próximos Pasos Inmediatos

1. **Leer** Kim, Shephard & Chib (1998) — es el paper fundamental para el modelo SV con Gibbs.
2. **Instalar** PyMC 5 y correr un ejemplo trivial de SV para familiarizarse con la sintaxis.
3. **Generar datos sintéticos** de un proceso SV con parámetros conocidos y verificar que se pueden recuperar con MCMC.
4. **Análisis exploratorio** formal de los log-retornos diarios de BTC: curtosis, ACF, QQ-plot, test de normalidad.
5. **Reunión con asesor** para validar el alcance y ajustar si es necesario.
