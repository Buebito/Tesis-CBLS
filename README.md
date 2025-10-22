# Simulaciones-Crypto-Backed-Loans
Simulaciones de Crypto Backed Loans

Se utilizan precios históricos de BTC y ETH para simular el desempeño de un préstamo a lo largo del tiempo.

Tenemos 2 folders principales: Simulacion de Préstamos a lo Largo del tiempo y Bolsa de Volatilidad, uno diseñado para simular muchísimos préstamos a lo largo de tiempo utilizando segment tree, para ver su desempeño y ganancia para el prestamista, y el otro diseñado para encontrar el préstamo óptimo de crypto que permita una diración indefinida de este, respectivamente. 

# ¿Cómo Correr Simulaciones? 

En La carpeta simulaciones

1. Primero corremos el notebook create_data.ipynb

Este notebook utiliza los datos históricos de ETH y BTC, proporcionados por binance desde 2020 a mediados de 2025 (cada año es un csv), y genera un csv "resultados.csv" con una unica tabla con los precios históricos por minuto de estas criptomonedas.

2. Importamos de utils el model.py
Este es el modelo para simular los préstamos

Para evitar que las simulaciones sean secuenciales y por ende orde $$O(n)$$, se utilizó un Segment Tree para encontrar el valor mínimo en el cual el préstamo llega a Margin Call, así lograr una eficiencia de orden $$O(log(n))$$

Para simular las decisiones del cliente se utilizaron Cadenas de Markov.

Guarda precio inicial de la moneda y realiza un análisis de si es mejor tomar el préstamo o hab er vendido. 

También tiene la opción de rehipoteca, para ver si con estrategias de trading podemos rehipotecar el colateral y generar mayor ganancia (aun no funciona correctamente)

Hay un log_model que utiliza la logvolatilidad de las cryptomonedas 

# ¿Cómo correr Bolsa Volatilidad?