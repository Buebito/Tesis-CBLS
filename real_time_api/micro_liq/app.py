import requests
import time
from datetime import datetime

LTV_OBJETIVO = 0.50
MARGIN_CALL_LTV = 0.72
LIQUIDATION_LTV = 0.80
LIQUIDATION_FEE = 0.02  # 2% sobre el valor del colateral liquidado

UPDATE_INTERVAL_SEC = 3  # cada cuánto se actualiza el préstamo en "tiempo real"


def obtener_precios():
    url = "https://api.binance.com/api/v3/ticker/price"
    res = requests.get(url, timeout=10)
    data = res.json()
    precios = {"BTC": None, "ETH": None}
    for entry in data:
        if entry["symbol"] == "BTCUSDT":
            precios["BTC"] = float(entry["price"])
        elif entry["symbol"] == "ETHUSDT":
            precios["ETH"] = float(entry["price"])
    return precios


class Prestamo:
    def __init__(self, token, cantidad_prestamo_usd, colateral_unidades, colateral_minimo_unidades):
        self.token = token.upper()
        self.cantidad_prestamo = float(cantidad_prestamo_usd)  # principal vivo en USD
        self.colateral = float(colateral_unidades)             # unidades de token (BTC/ETH)
        self.colateral_minimo = float(colateral_minimo_unidades)
        self.fecha_inicio = datetime.now()

        # estado de buffer (colateral adicional sobre el mínimo requerido)
        self.base_colateral = min(self.colateral, self.colateral_minimo)
        self.extra_colateral = max(0.0, self.colateral - self.base_colateral)
        self.extra_colateral_inicial = self.extra_colateral

        # thresholds de "buffer call" cada 10% consumido: 90%, 80%, ..., 10%
        self.siguiente_buffer_threshold = 0.9

        # primer cálculo de precios / LTV real
        self.precios = obtener_precios()
        precio_inicial = self.precios[self.token]
        colateral_usd = self.colateral * precio_inicial
        self.LTV_actual = self.cantidad_prestamo / colateral_usd if colateral_usd > 0 else float("inf")

        buffer_pct_ini = self._porcentaje_buffer_restante()

        print("\n=== Préstamo creado ===")
        print(f"Token colateral: {self.token}")
        print(f"Monto préstamo: ${self.cantidad_prestamo:,.2f} USD")
        print(f"Colateral total: {self.colateral:.6f} {self.token}")
        print(f"Colateral mínimo (50% LTV): {self.colateral_minimo:.6f} {self.token}")
        print(f"Colateral base: {self.base_colateral:.6f} {self.token}")
        print(f"Colateral adicional (buffer): {self.extra_colateral:.6f} {self.token} "
              f"({buffer_pct_ini:.2%} del buffer inicial)")
        print(f"LTV contractual: {LTV_OBJETIVO:.2%} (basado en 50%)")
        print(f"LTV efectivo inicial: {self.LTV_actual:.2%}\n")

    # -------- utilidades de buffer --------
    def _porcentaje_buffer_restante(self):
        if self.extra_colateral_inicial <= 0:
            return 0.0
        return self.extra_colateral / self.extra_colateral_inicial

    def _check_buffer_calls(self, eventos):
        """
        Dispara eventos de tipo "buffer_call" cada vez que el buffer cruza
        múltiplos de 10% (90%, 80%, ..., 10%).
        """
        if self.extra_colateral_inicial <= 0:
            return

        buffer_pct = self._porcentaje_buffer_restante()

        # Emitimos todos los thresholds que se hayan cruzado en esta iteración
        while (
            self.siguiente_buffer_threshold is not None
            and buffer_pct <= self.siguiente_buffer_threshold
            and self.siguiente_buffer_threshold > 0
        ):
            eventos.append({
                "type": "buffer_call",
                "threshold": round(self.siguiente_buffer_threshold, 1),
                "buffer_pct_remaining": buffer_pct,
            })
            print(
                f"📢 Buffer call: buffer en {buffer_pct:.2%} "
                f"(threshold {self.siguiente_buffer_threshold:.0%})"
            )
            self.siguiente_buffer_threshold -= 0.1
            # Si bajamos de 0.1, dejamos de emitir calls
            if self.siguiente_buffer_threshold <= 0:
                self.siguiente_buffer_threshold = None
                break

    # -------- lógica principal --------
    def actualizar_LTV(self):
        """
        Actualiza precios y LTV real, pero lo que se muestra al cliente
        mientras haya buffer es LTV contractual = 50%.
        Cuando el buffer llega a 0, se muestra el LTV real.
        """
        self.precios = obtener_precios()
        precio = self.precios[self.token]
        colateral_usd = self.colateral * precio

        # LTV real (económico)
        real_ltv = self.cantidad_prestamo / colateral_usd if colateral_usd > 0 else float("inf")
        self.LTV_actual = real_ltv

        buffer_pct = self._porcentaje_buffer_restante()

        # Lo que mostramos:
        # - Si todavía hay buffer (>0) → LTV contractual = 50%
        # - Si buffer = 0 → mostramos LTV real
        if buffer_pct > 0:
            ltv_display = LTV_OBJETIVO
        else:
            ltv_display = real_ltv

        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"Precio {self.token}: ${precio:,.2f} | "
            f"Colateral: {self.colateral:.6f} {self.token} | "
            f"LTV mostrado: {ltv_display:.2%} |"
            #f"(LTV efectivo: {real_ltv:.2%}) | "
            f"Buffer restante: {buffer_pct:.2%}"
        )

        return real_ltv  # devolvemos el real para la lógica de riesgo

    def microventa(self, eventos):
        """
        Intenta llevar el LTV de vuelta a 50% usando SOLO colateral adicional.
        Si no hay buffer, no hace nada.
        Loguea explícitamente cuánto buffer se vendió en BTC.
        """
        if self.extra_colateral <= 0:
            print("No hay buffer disponible, no se puede hacer microventa.")
            return False

        precio = self.precios[self.token]
        L = self.cantidad_prestamo
        C = self.colateral
        t = LTV_OBJETIVO
        f = LIQUIDATION_FEE

        real_ltv = L / (C * precio) if C * precio > 0 else float("inf")
        if real_ltv <= t:
            print("LTV ya está en o por debajo del objetivo, no se requiere microventa.")
            return False

        # Fórmula para x:
        # (L - (1 - f) * x * P) / ((C - x) * P) = t
        # x = (C*P*t - L) / (P*(f + t - 1))
        num = C * precio * t - L
        den = precio * (f + t - 1)

        if den >= 0:
            print("Parámetros no permiten una microventa estable (den>=0).")
            return False

        x_requerido = num / den
        if x_requerido <= 0:
            print("El cálculo indicó que no se requiere microventa.")
            return False

        # Solo podemos vender hasta el extra_colateral disponible
        x = min(x_requerido, self.extra_colateral)

        # Ejecutamos venta
        proceeds = x * precio
        fee = proceeds * f
        amortizacion = proceeds - fee

        # IMPORTANTE: lo que se vende viene del buffer
        buffer_vendido_btc = x

        self.cantidad_prestamo = max(0.0, self.cantidad_prestamo - amortizacion)
        self.colateral -= x

        # Recalcular base + extra (base es siempre el mínimo)
        self.base_colateral = min(self.colateral, self.colateral_minimo)
        self.extra_colateral = max(0.0, self.colateral - self.base_colateral)

        # Nuevo LTV real post microventa
        colateral_usd = self.colateral * precio
        real_ltv_nuevo = (
            self.cantidad_prestamo / colateral_usd if colateral_usd > 0 else float("inf")
        )
        self.LTV_actual = real_ltv_nuevo

        buffer_pct_restante = self._porcentaje_buffer_restante()

        print("\n🔹 Microventa ejecutada")
        print(f"Buffer vendido en esta microventa: {buffer_vendido_btc:.6f} {self.token}")
        print(f"Vendidas (total): {x:.6f} {self.token} por ${proceeds:,.2f}")
        print(f"Amortización del préstamo: ${amortizacion:,.2f} | Fee (2%): ${fee:,.2f}")
        print(f"Nuevo colateral: {self.colateral:.6f} {self.token}")
        print(f"Nuevo LTV efectivo: {real_ltv_nuevo:.2%}")
        print(
            f"Buffer restante: {self.extra_colateral:.6f} {self.token} "
            f"({buffer_pct_restante:.2%} del inicial)\n"
        )

        eventos.append({
            "type": "micro_sale",
            "sold_collateral": x,
            "buffer_sold_btc": buffer_vendido_btc,
            "amortization": amortizacion,
            "fee": fee,
            "new_ltv_effective": real_ltv_nuevo,
            "buffer_pct_remaining": buffer_pct_restante,
        })

        # Checamos si cruzamos thresholds de buffer
        self._check_buffer_calls(eventos)

        return True

    def liquidacion_total(self, motivo):
        """
        Liquida TODO el colateral restante, cobra fee del 2% y repaga el préstamo.
        (sin intereses, como pediste)
        """
        precio = self.precios[self.token]
        valor_colateral = self.colateral * precio
        fee = valor_colateral * LIQUIDATION_FEE
        repago = min(self.cantidad_prestamo, valor_colateral - fee)
        sobrante = max(0.0, valor_colateral - fee - repago)

        print("\n💥 LIQUIDACIÓN TOTAL")
        print(f"Motivo: {motivo}")
        print(f"Valor colateral: ${valor_colateral:,.2f}")
        print(f"Fee 2%: ${fee:,.2f}")
        print(f"Préstamo repagado: ${repago:,.2f}")
        print(f"Devuelto al cliente: ${sobrante:,.2f}\n")

        evento = {
            "type": "liquidation",
            "reason": motivo,
            "collateral_value": valor_colateral,
            "fee": fee,
            "repaid": repago,
            "returned_to_user": sobrante,
        }

        # Cerramos el préstamo
        self.cantidad_prestamo = 0.0
        self.colateral = 0.0
        self.base_colateral = 0.0
        self.extra_colateral = 0.0

        return evento

    def tick(self):
        """
        Un "paso de simulación":
        - Actualiza LTV efectivo (real)
        - Si hay buffer y LTV efectivo > 50%, intenta microventa
        - Luego aplica lógica de margin call / liquidación (72% / 80%)
        Devuelve una lista de eventos sucedidos en este tick.
        """
        eventos = []
        real_ltv = self.actualizar_LTV()

        # 1) Si hay buffer y LTV real > objetivo, intentamos microventa
        if self.extra_colateral > 0 and real_ltv > LTV_OBJETIVO:
            self.microventa(eventos)
            # LTV ya se recalculó dentro de microventa
            real_ltv = self.LTV_actual
        elif self.extra_colateral <= 0 and real_ltv > LTV_OBJETIVO:
            # Sin buffer, dejamos subir naturalmente (no microvendemos)
            eventos.append({
                "type": "no_buffer_high_ltv",
                "ltv_effective": real_ltv,
            })
            print("⚠️ LTV efectivo por encima del 50% pero sin buffer disponible. Se deja correr.\n")

        # 2) Lógica de margin call / liquidación sobre LTV EFECTIVO
        if real_ltv >= LIQUIDATION_LTV:
            evento_liq = self.liquidacion_total(motivo="LTV efectivo >= 80%")
            eventos.append(evento_liq)
        elif real_ltv >= MARGIN_CALL_LTV:
            print("🚨 MARGIN CALL: LTV efectivo >= 72%")
            eventos.append({
                "type": "margin_call",
                "ltv_effective": real_ltv,
            })
        else:
            eventos.append({
                "type": "safe",
                "ltv_effective": real_ltv,
            })

        return eventos


def simular_prestamo():
    print("=== Demo de simulación de préstamo en tiempo (casi) real ===\n")
    token = input("Token colateral (BTC/ETH): ").strip().upper()
    if token not in ("BTC", "ETH"):
        print("Token no soportado.")
        return

    precios = obtener_precios()
    precio_token = precios[token]
    print(f"Precio actual de {token}: ${precio_token:,.2f} USD\n")

    modo = input(
        "Selecciona modo:\n"
        "1) Solicitar un préstamo en USD (50% LTV, pides X, aportas colateral mínimo para 50% + buffer)\n"
        "2) Tengo N unidades de colateral y quiero saber cuánto me prestan (50% del valor, sin buffer extra)\n"
        "Opción (1/2): "
    ).strip()

    if modo == "1":
        monto_prestamo = float(input("¿Cuánto deseas pedir prestado (USD)?: "))
        colateral_minimo = monto_prestamo / (LTV_OBJETIVO * precio_token)

        buffer_pct_str = input(
            "¿Qué % adicional de buffer sobre el colateral mínimo quieres? "
            "(ej. 0.2 = 20% extra, ENTER = 0): "
        ).strip()
        buffer_pct = float(buffer_pct_str) if buffer_pct_str else 0.0

        colateral_total = colateral_minimo * (1 + buffer_pct)

        print(f"\nColateral mínimo para 50% LTV: {colateral_minimo:.6f} {token}")
        print(f"Colateral total con buffer ({buffer_pct:.0%}): {colateral_total:.6f} {token}\n")

        prestamo = Prestamo(
            token=token,
            cantidad_prestamo_usd=monto_prestamo,
            colateral_unidades=colateral_total,
            colateral_minimo_unidades=colateral_minimo,
        )

    elif modo == "2":
        colateral_total = float(input(f"¿Cuánto {token} quieres aportar como colateral?: "))
        valor_colateral = colateral_total * precio_token
        monto_prestamo = valor_colateral * LTV_OBJETIVO  # 50% del valor
        # En este modo, todo el colateral es el mínimo requerido para 50% LTV (sin buffer)
        colateral_minimo = colateral_total

        print(f"\nValor de tu colateral: ${valor_colateral:,.2f}")
        print(f"Monto máximo de préstamo (50% LTV): ${monto_prestamo:,.2f}\n")

        prestamo = Prestamo(
            token=token,
            cantidad_prestamo_usd=monto_prestamo,
            colateral_unidades=colateral_total,
            colateral_minimo_unidades=colateral_minimo,
        )
    else:
        print("Opción no válida.")
        return

    print("Iniciando loop de simulación. Ctrl+C para detener.\n")

    try:
        while True:
            eventos = prestamo.tick()
            # Si en este tick hubo liquidación, terminamos
            if any(ev["type"] == "liquidation" for ev in eventos):
                print("Simulación finalizada: el préstamo fue liquidado.\n")
                break
            time.sleep(UPDATE_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nSimulación interrumpida por el usuario.")


if __name__ == "__main__":
    simular_prestamo()