import requests
import time
from datetime import datetime

LTV_OBJETIVO = 0.50
LIQUIDATION_FEE = 0.02       # 2%
TASA_ANUAL = 0.18            # 18% anual
BUFFER_LIQUIDACION_MIN = 0.05  # 5% del colateral adicional inicial

def obtener_precios():
    url = 'https://api.binance.com/api/v3/ticker/price'
    res = requests.get(url)
    data = res.json()
    precios = {'BTC': None, 'ETH': None}
    for entry in data:
        if entry['symbol'] == 'BTCUSDT':
            precios['BTC'] = float(entry['price'])
        elif entry['symbol'] == 'ETHUSDT':
            precios['ETH'] = float(entry['price'])
    return precios

class Prestamo:
    def __init__(self, token, cantidad_prestamo, colateral, buffer_pct=0.0):
        self.token = token.upper()
        self.cantidad_prestamo = float(cantidad_prestamo)  # principal vivo
        self.colateral = float(colateral)                  # unidades de token
        self.buffer_pct = float(buffer_pct)

        self.precios = obtener_precios()
        self.precio_inicial = self.precios[self.token]
        self.fecha_inicio = datetime.now()

        # Colateral mínimo para un LTV del 50%
        self.colateral_minimo = self.cantidad_prestamo / (LTV_OBJETIVO * self.precio_inicial)

        # Particionamos: base + extra
        self.base_colateral = min(self.colateral, self.colateral_minimo)
        self.extra_colateral = max(0.0, self.colateral - self.base_colateral)
        self.extra_colateral_inicial = self.extra_colateral

        if self.colateral < self.colateral_minimo:
            print("⚠️ Ojo: el colateral aportado es menor al mínimo para 50% LTV.")
            print(f"Colateral mínimo requerido: {self.colateral_minimo:.6f} {self.token}")

        colateral_usd = self.colateral * self.precio_inicial
        self.LTV_actual = self.cantidad_prestamo / colateral_usd
        print(f"LTV Inicial: {self.LTV_actual:.2%}")
        print(f"Base colateral: {self.base_colateral:.6f} {self.token}, "
              f"Colateral adicional: {self.extra_colateral:.6f} {self.token}")

    def calcular_interes(self):
        dias = (datetime.now() - self.fecha_inicio).days
        interes = self.cantidad_prestamo * TASA_ANUAL * dias / 365
        return interes

    def actualizar_LTV(self):
        self.precios = obtener_precios()
        precio_actual = self.precios[self.token]
        colateral_usd = self.colateral * precio_actual
        if colateral_usd <= 0:
            self.LTV_actual = float('inf')
        else:
            self.LTV_actual = self.cantidad_prestamo / colateral_usd
        interes = self.calcular_interes()
        print(f"LTV Actualizado: {self.LTV_actual:.2%} | "
              f"Precio actual de {self.token}: ${precio_actual:,.2f} USD | "
              f"Interés acumulado: ${interes:.2f}")
        return self.LTV_actual

    def agregar_colateral(self, cantidad):
        cantidad = float(cantidad)
        self.colateral += cantidad

        # Repartimos nuevamente entre base y extra
        if self.colateral <= self.colateral_minimo:
            self.base_colateral = self.colateral
            self.extra_colateral = 0.0
        else:
            self.base_colateral = self.colateral_minimo
            self.extra_colateral = self.colateral - self.base_colateral

        # Si el usuario agrega más buffer, actualizamos el "máximo histórico"
        if self.extra_colateral > self.extra_colateral_inicial:
            self.extra_colateral_inicial = self.extra_colateral

        print(f"Nuevo colateral total: {self.colateral:.6f} {self.token}")
        print(f"Base colateral: {self.base_colateral:.6f} {self.token}, "
              f"Colateral adicional: {self.extra_colateral:.6f} {self.token}")

    def porcentaje_buffer_restante(self):
        if self.extra_colateral_inicial <= 0:
            return 0.0
        return self.extra_colateral / self.extra_colateral_inicial

    def microventa(self):
        """
        Ejecuta una microventa para intentar regresar el LTV al objetivo (50%)
        usando únicamente colateral adicional. Si no hay suficiente buffer,
        se devuelve False para que el caller decida si liquida todo.
        """
        self.precios = obtener_precios()
        P = self.precios[self.token]
        L = self.cantidad_prestamo
        C = self.colateral
        t = LTV_OBJETIVO
        f = LIQUIDATION_FEE

        # Recalcular LTV actual
        ltv_actual = L / (C * P)
        if ltv_actual <= t:
            print("LTV ya está por debajo o en el objetivo, no se requiere microventa.")
            return False

        if self.extra_colateral <= 0:
            print("No queda colateral adicional para microvender.")
            return False

        # Fórmula: encontrar x tal que
        # (L - (1-f)*xP) / ((C - x)*P) = t
        num = L - t * P * C
        den = P * (1 - f - t)

        if den <= 0:
            print("No es posible calcular una microventa estable con estos parámetros.")
            return False

        x_requerido = num / den

        if x_requerido <= 0:
            print("El cálculo indica que no se requiere microventa.")
            return False

        # Limitamos la venta al colateral adicional disponible
        x = min(x_requerido, self.extra_colateral)

        # Ejecutamos la venta
        proceeds = x * P
        fee = proceeds * f
        amortizacion = proceeds - fee

        self.cantidad_prestamo = max(0.0, self.cantidad_prestamo - amortizacion)
        self.colateral -= x
        self.extra_colateral = max(0.0, self.colateral - self.base_colateral)

        nuevo_ltv = self.cantidad_prestamo / (self.colateral * P) if self.colateral > 0 else float('inf')
        buffer_pct_restante = self.porcentaje_buffer_restante()

        print("\n🔹 Microventa ejecutada")
        print(f"Se vendieron {x:.6f} {self.token} por ${proceeds:,.2f}")
        print(f"Amortización del préstamo: ${amortizacion:,.2f} | Fee cobrada: ${fee:,.2f}")
        print(f"Colateral restante: {self.colateral:.6f} {self.token}")
        print(f"Nuevo LTV: {nuevo_ltv:.2%}")
        print(f"Buffer de colateral restante: {buffer_pct_restante:.2%} del inicial")

        return True

    def liquidacion_total(self):
        """
        Liquida todo el colateral restante, cobra fee de 2% sobre el valor
        y repaga principal + interés. El resto se devuelve al usuario.
        """
        self.precios = obtener_precios()
        P = self.precios[self.token]
        valor_colateral = self.colateral * P
        interes = self.calcular_interes()
        fee = valor_colateral * LIQUIDATION_FEE

        restante = valor_colateral - self.cantidad_prestamo - interes - fee
        restante = max(0.0, restante)
        revenue = max(0.0, interes + fee)

        print("\n💥 LIQUIDACIÓN TOTAL")
        print(f"Valor del colateral: ${valor_colateral:,.2f}")
        print(f"Fee (2%): ${fee:,.2f}")
        print(f"Interés cobrado: ${interes:,.2f}")
        print(f"Devuelto al usuario: ${restante:,.2f}")
        print(f"Revenue del prestamista: ${revenue:,.2f}")

        # Cerrar préstamo
        self.cantidad_prestamo = 0.0
        self.colateral = 0.0
        self.extra_colateral = 0.0

        return 'liquidado'

    def evaluar_riesgos(self):
        """
        Lógica principal de riesgo:
        - Actualiza LTV
        - Si buffer < 5% → liquidación total
        - Si LTV > objetivo → microventa
        - Si no, préstamo en zona segura
        """
        ltv = self.actualizar_LTV()
        buffer_pct_restante = self.porcentaje_buffer_restante()

        # 1) Si el buffer adicional está prácticamente agotado → liquidar todo
        if buffer_pct_restante <= BUFFER_LIQUIDACION_MIN and self.extra_colateral_inicial > 0:
            print("\n⚠️ Colateral adicional por debajo del 5% del buffer inicial.")
            print("Se procede a liquidar completamente el préstamo.")
            return self.liquidacion_total()

        # 2) Si el LTV se pasa del 50% → intentamos microventa
        if ltv > LTV_OBJETIVO:
            print("\n⚠️ LTV por encima del objetivo. Intentando microventa...")
            ok = self.microventa()
            if not ok:
                # Si no pudimos microvender (sin buffer / inestable), liquidamos todo
                print("No fue posible estabilizar con microventa. Se liquida todo.")
                return self.liquidacion_total()
            else:
                return 'microventa'

        # 3) Todo en orden
        print("✅ Préstamo en zona segura (LTV ≤ 50%)")
        return 'seguro'

    def cerrar_prestamo(self, metodo='stablecoin'):
        interes = self.calcular_interes()
        if metodo == 'stablecoin':
            total = self.cantidad_prestamo + interes
            print(f"\n✅ Préstamo cerrado con stablecoin. Total pagado: ${total:.2f}")
            print(f"Revenue prestamista: ${interes:.2f}")
            self.cantidad_prestamo = 0.0
        elif metodo == 'colateral':
            self.precios = obtener_precios()
            P = self.precios[self.token]
            valor_colateral = self.colateral * P
            fee = valor_colateral * LIQUIDATION_FEE
            restante = valor_colateral - self.cantidad_prestamo - interes - fee
            restante = max(0.0, restante)
            print(f"\n✅ Préstamo cerrado usando colateral.")
            print(f"Devuelto al usuario: ${restante:.2f}")
            print(f"Revenue prestamista: ${interes + fee:.2f}")
            self.cantidad_prestamo = 0.0
            self.colateral = 0.0
            self.extra_colateral = 0.0

# --- FLUJO DE USO ---
def simular_prestamo():
    while True:
        print("\n=== Simulación de Préstamo con Microventas ===")
        token = input("¿Qué token usarás como colateral? (BTC/ETH): ").strip().upper()
        cantidad = float(input("¿Cuánto deseas pedir prestado (USD)?: "))

        precios = obtener_precios()
        precio_token = precios[token]
        colateral_minimo = cantidad / (LTV_OBJETIVO * precio_token)

        buffer_pct_str = input("¿Qué % de colateral adicional quieres como buffer? (ej. 0.2 = 20%, ENTER = 0): ").strip()
        buffer_pct = float(buffer_pct_str) if buffer_pct_str else 0.0

        colateral_sugerido = colateral_minimo * (1 + buffer_pct)

        print(f"\nPara un LTV inicial de 50%:")
        print(f"- Colateral mínimo: {colateral_minimo:.6f} {token}")
        print(f"- Colateral sugerido con buffer ({buffer_pct:.0%}): {colateral_sugerido:.6f} {token}")

        colateral = float(input(f"¿Cuánto {token} aportarás como colateral?: "))

        prestamo = Prestamo(token, cantidad, colateral, buffer_pct=buffer_pct)

        while True:
            accion = input("\nENTER = actualizar | 'add' = añadir colateral | 'cerrar' = cerrar préstamo | 'salir': ").strip().lower()
            if accion == 'salir':
                return
            elif accion == 'add':
                adicional = float(input("¿Cuánto colateral adicional (en unidades de token)?: "))
                prestamo.agregar_colateral(adicional)
            elif accion == 'cerrar':
                metodo = input("¿Pagar con 'stablecoin' o 'colateral'?: ").strip().lower()
                prestamo.cerrar_prestamo(metodo)
                break
            else:
                estado = prestamo.evaluar_riesgos()
                if estado == 'liquidado':
                    break

# Ejecutar
if __name__ == "__main__":
    simular_prestamo()
