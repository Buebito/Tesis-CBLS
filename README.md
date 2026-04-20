# Tesis-CBLS: Crypto-Backed Loans — Research Repository

Este repositorio reúne el código de investigación y el código heredado de simulaciones de préstamos colateralizados con criptomonedas (CBLs), usando precios históricos de BTC y ETH.

## Estructura

```
Tesis-CBLS/
├── legacy_code/          # Código original del proyecto de simulaciones
│   ├── bolsa_volatilidad/    # Optimización de bolsa de volatilidad
│   ├── precios_futuros/      # Modelos de precios futuros
│   ├── prestamos/            # Simulaciones de préstamos (Segment Tree + Markov)
│   ├── modelo_financiero/    # Modelo financiero del negocio (P&L, break-even, $20M)
│   ├── real_time_api/        # API de liquidación en tiempo real
│   ├── X2O shield/           # Análisis de cobertura Salt Shield
│   └── requirements.txt
│
└── tesis/                # Trabajo de investigación (tesis de maestría)
    ├── baseline/             # Modelos de referencia: simulador, Salt Shield, buffer optimizer
    ├── bayesian/             # Modelos bayesianos: volatilidad estocástica, buffer, pricing
    ├── data/                 # Cargadores de datos
    ├── notebooks/            # Análisis exploratorio y validación
    ├── VF Tesis AGS.pdf      # Tesis de referencia (PDF)
    ├── VF_Tesis_AGS.md       # Tesis de referencia (Markdown)
    └── requirements.txt
```

## Legacy Code

Simulaciones de CBLs usando precios históricos por minuto de Binance (2020–2025). El módulo `prestamos` implementa un Segment Tree para eficiencia O(log n) en la búsqueda de Margin Call, y Cadenas de Markov para modelar el comportamiento del cliente.

## Tesis

Investigación activa sobre modelos bayesianos de volatilidad estocástica aplicados a la gestión de colateral y pricing de préstamos crypto.
