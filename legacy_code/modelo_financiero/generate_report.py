"""Genera el reporte markdown del modelo financiero de crypto-backed loans."""
import math
import pandas as pd


def _md_table(dataframe):
    """DataFrame a markdown table."""
    try:
        return dataframe.to_markdown()
    except Exception:
        cols = [str(dataframe.index.name or "")] + [str(c) for c in dataframe.columns]
        rows = [[str(idx)] + [str(v) for v in row] for idx, row in dataframe.iterrows()]
        sep = "|" + "|".join("---" for _ in cols) + "|"
        header = "| " + " | ".join(cols) + " |"
        body = ["| " + " | ".join(r) + " |" for r in rows]
        return "\n".join([header, sep] + body)


def write_report(results, config, summary_df, pnl_df, cost_rows,
                 sensitivity_rows, capital, fc1, fc2, price_df, img_dir,
                 pred_summaries=None, vol_data=None, buffer_results=None,
                 reinvest_data=None, spread_data=None):
    """Genera reporte_modelo_financiero.md con tablas y graficas.

    Args:
        results: dict {ltv: DataFrame} con resultados por escenario
        config: dict CONFIG del modelo
        summary_df: DataFrame resumen por LTV
        pnl_df: DataFrame P&L por LTV
        cost_rows: list[dict] costos variables por LTV
        sensitivity_rows: list[dict] sensibilidad cuota apertura
        capital: float capital inyectado
        fc1: float costos fijos anio 1
        fc2: float costos fijos anio 2+
        price_df: DataFrame de precios historicos
        img_dir: str directorio de imagenes
        pred_summaries: dict {coin: dict} resumen de prediccion por activo (opcional)
        vol_data: dict {coin: dict} resumen de volatilidad por activo (opcional)
        buffer_results: dict {ltv: {coin: {q: pct}}} buffer de volatilidad (opcional)
        reinvest_data: dict {ltv: {'selffund_month': int, 'pnl_60': float}} reinversion (opcional)
        spread_data: list[dict] datos de intermediacion BitGo (opcional)
    """
    L = []
    a = L.append

    # ── Header ──
    a("# Reporte: Modelo Financiero - Crypto-Backed Loans")
    a("")
    fmt = "%Y-%m-%d %H:%M"
    a(f"> **Fecha:** {pd.Timestamp.now().strftime(fmt)}  ")
    a(f"> **Datos historicos:** {price_df.index[0].date()} a {price_df.index[-1].date()}  ")
    a(f"> **Prestamos simulados por escenario:** {config['N_LOANS']:,}  ")
    a(f"> **Capital:** ${capital:,.0f} USD")
    a("")
    a("---")
    a("")

    # ── 1. Parametros ──
    a("## 1. Parametros del modelo")
    a("")
    a("| Parametro | Valor |")
    a("|---|---|")
    ltv_str = ", ".join(f"{x*100:.0f}%" for x in config["INIT_LTV_SCENARIOS"])
    a(f"| Escenarios LTV | {ltv_str} |")
    a(f"| Margin call / Liquidacion LTV | {config['MC_LTV']*100:.0f}% / {config['LIQ_LTV']*100:.0f}% |")
    a(f"| Tasa anual BTC / ETH | {config['INT_BTC']*100:.0f}% / {config['INT_ETH']*100:.0f}% |")
    a(f"| Cuota apertura | {config['OPENING_FEE_RATE']*100:.0f}% |")
    a(f"| Fee liquidacion | {config['LIQUIDATION_FEE_RATE']*100:.0f}% |")
    a(f"| P(recapitalizar) base | {config['P_RECAP_INIT']*100:.0f}% (decae por recapitalizacion) |")
    a(f"| P(pagar al vencimiento) | {config['P_REPAY_MATURITY']*100:.0f}% |")
    a(f"| Montos prestamo | ${config['LOAN_SIZES_USD'][0]:,} - ${config['LOAN_SIZES_USD'][-1]:,} USD |")
    a("")

    # ── Bolsa de volatilidad ──
    if vol_data:
        a("### Bolsa de volatilidad")
        a("")
        a("Volatilidad anualizada historica (ventana rodante 30d) sobre datos horarios.")
        a("")
        a("| Metrica | BTC (%) | ETH (%) |")
        a("|---|---|---|")
        btc_v = vol_data.get("BTC", {})
        eth_v = vol_data.get("ETH", {})
        a(f"| Vol actual (30d) | {btc_v.get('current_30', 0)} | {eth_v.get('current_30', 0)} |")
        a(f"| Vol actual (90d) | {btc_v.get('current_90', 0)} | {eth_v.get('current_90', 0)} |")
        a(f"| Vol actual (365d) | {btc_v.get('current_365', 0)} | {eth_v.get('current_365', 0)} |")
        a(f"| Media historica (30d) | {btc_v.get('mean_30', 0)} | {eth_v.get('mean_30', 0)} |")
        a(f"| P25 (30d) | {btc_v.get('p25_30', 0)} | {eth_v.get('p25_30', 0)} |")
        a(f"| P75 (30d) | {btc_v.get('p75_30', 0)} | {eth_v.get('p75_30', 0)} |")
        a(f"| Min (30d) | {btc_v.get('min_30', 0)} | {eth_v.get('min_30', 0)} |")
        a(f"| Max (30d) | {btc_v.get('max_30', 0)} | {eth_v.get('max_30', 0)} |")
        a("")
        # Regimen
        for coin in ("BTC", "ETH"):
            vd = vol_data.get(coin, {})
            curr = vd.get("current_30", 0)
            p25 = vd.get("p25_30", 0)
            p75 = vd.get("p75_30", 0)
            if curr < p25:
                regime = "BAJA"
            elif curr > p75:
                regime = "ALTA"
            else:
                regime = "NORMAL"
            a(f"> **{coin}:** Regimen de volatilidad **{regime}** "
              f"(actual {curr}%, rango historico {vd.get('min_30', 0)}-{vd.get('max_30', 0)}%)  ")
        a("")
        a(f"![Volatilidad historica]({img_dir}/00_volatilidad.png)")
        a("")

    # ── Buffer de volatilidad ──
    if buffer_results:
        a("### Analisis de buffer (bolsa de volatilidad)")
        a("")
        a("Buffer minimo de colateral extra (% del colateral inicial) que el cliente debe")
        a("provisionar desde el inicio para evitar liquidacion en un horizonte de 1 anio.")
        a("")
        a("| LTV | Moneda | Buffer P90 (%) | Buffer P95 (%) | Buffer P99 (%) |")
        a("|---|---|---|---|---|")
        for ltv_val in sorted(buffer_results.keys()):
            for coin in ("BTC", "ETH"):
                cb = buffer_results[ltv_val].get(coin, {})
                a(f"| {ltv_val*100:.0f}% | {coin} | {cb.get(0.90, 0)} | {cb.get(0.95, 0)} | {cb.get(0.99, 0)} |")
        a("")
        a("> **Interpretacion:** si el cliente provisiona al menos el buffer P90, evita liquidacion en >=90% de los escenarios historicos.")
        a("")

    if vol_data or buffer_results:
        a("---")
        a("")

    # ── 2. Flujo ──
    a("## 2. Flujo del prestamo")
    a("")
    a("```")
    a("Inicio -> [Devengar interes] -> Margin Call?")
    a("  |                               |")
    a("  |                     Si: P(agregar colateral) -> Continua")
    a("  |                         P(amortizar)         -> Continua")
    a("  |                         P(ignorar)           -> Busca liquidacion")
    a("  |")
    a("  +-> Vencimiento?")
    a("        |")
    a("        Si: P(recapitalizar) -> Paga intereses, extiende N meses -> Continua")
    a("            Ninguno:")
    a("              P(pagar prestamo)  -> Cierre con repago (alta prob.)")
    a("              P(usar colateral)  -> Cierre con colateral (baja prob.)")
    a("```")
    a("")
    a("---")
    a("")

    # ── 3. Resultados por LTV ──
    a("## 3. Resultados por escenario de LTV")
    a("")
    a(_md_table(summary_df.T))
    a("")
    a("### Comparacion grafica")
    a("")
    a(f"![Comparacion de escenarios por LTV]({img_dir}/01_comparacion_ltv.png)")
    a("")
    a("---")
    a("")

    # ── 4. Costos ──
    a("## 4. Estructura de costos")
    a("")
    a("### Costos variables promedio por prestamo")
    a("")
    cost_df = pd.DataFrame(cost_rows).set_index("LTV")
    a(_md_table(cost_df))
    a("")
    a("### Costos fijos anuales")
    a("")
    a("| Concepto | Anio 1 | Anio 2+ |")
    a("|---|---|---|")
    a(f"| Complif Setup | ${config['COMPLIF_SETUP']:,} | $0 |")
    a(f"| Complif Mensual (x12) | ${config['COMPLIF_MONTHLY']*12:,} | ${config['COMPLIF_MONTHLY']*12:,} |")
    a(f"| Desarrollo | ${config['DEV_COST']:,} | $0 |")
    a(f"| **Total** | **${fc1:,}** | **${fc2:,}** |")
    a("")
    a("---")
    a("")

    # ── 5. P&L ──
    a("## 5. Modelo financiero: P&L con $20M USD")
    a("")
    a(_md_table(pnl_df.T))
    a("")
    a("### Visualizacion P&L y ROI")
    a("")
    a(f"![P&L y ROI]({img_dir}/02_pnl_roi.png)")
    a("")
    a("### Proyeccion a 24 meses")
    a("")
    a(f"![Proyeccion mensual]({img_dir}/03_proyeccion_mensual.png)")
    a("")
    a("---")
    a("")

    # ── 6. Sensibilidad ──
    a("## 6. Sensibilidad: Cuota de apertura (LTV 50%)")
    a("")
    sens_df = pd.DataFrame(sensitivity_rows).set_index("Cuota_apertura")
    a(_md_table(sens_df))
    a("")
    a("---")
    a("")

    # ── 6.1 Reinversion ──
    if reinvest_data:
        a("### 6.1 Reinversion de ingresos y auto-fondeo")
        a("")
        a("Si se reinvierten **todos** los ingresos netos en nuevos prestamos (en lugar de extraerlos),")
        a(f"el negocio alcanza auto-fondeo cuando las utilidades acumuladas igualan el capital inicial (${capital:,.0f}).")
        a("")
        a("| LTV | Auto-fondeo (mes) | P&L acumulado mes 60 |")
        a("|---|---|---|")
        for ltv_val in sorted(reinvest_data.keys()):
            rd = reinvest_data[ltv_val]
            sf = f"Mes {rd['selffund_month']}" if rd['selffund_month'] else ">60"
            a(f"| {ltv_val*100:.0f}% | {sf} | ${rd['pnl_60']:,.0f} |")
        a("")
        a("> **Interpretacion:** una vez alcanzado el auto-fondeo, el capital original puede retirarse ")
        a("> o reasignarse; la operacion se sostiene con sus propios ingresos.")
        a("")
        a(f"![Reinversion]({img_dir}/05_reinversion.png)")
        a("")
        a("---")
        a("")

    # ── 6.2 Intermediacion BitGo ──
    if spread_data:
        a("### 6.2 Modelo de intermediacion con BitGo")
        a("")
        a("Escenario alternativo: BitGo fondea los prestamos al **11%** anual; x2o presta al **18-20%** y retiene el spread.")
        a("No se requiere capital propio para el fondeo — solo para costos operativos.")
        a("")
        a("| Concepto | Valor |")
        a("|---|---|")
        a("| Tasa BitGo (costo de fondeo) | 11% anual |")
        a(f"| Tasa x2o BTC / ETH | {config['INT_BTC']*100:.0f}% / {config['INT_ETH']*100:.0f}% |")
        a("| LTV liquidacion BitGo | 85% |")
        a(f"| LTV liquidacion x2o | {config['LIQ_LTV']*100:.0f}% (margen de seguridad: 5pp) |")
        a(f"| Capital propio requerido | $0 (fondeo externo) |")
        a("")
        sp_df = pd.DataFrame(spread_data).set_index("LTV")
        a(_md_table(sp_df))
        a("")
        a("> **Ventaja:** ROI teoricamente infinito (sin capital propio comprometido). ")
        a("> **Trade-off:** menor ingreso neto por prestamo vs modelo con capital propio, ")
        a("> y dependencia de la relacion comercial con BitGo.")
        a("")
        a("---")
        a("")

    # ── 7. Prediccion de precios ──
    if pred_summaries:
        a("## 7. Prediccion de precios futuros (Monte Carlo Bootstrap)")
        a("")
        a("Simulacion Monte Carlo con Bootstrap-KDE sobre retornos historicos,")
        a("horizonte de 365 dias, 2,000 trayectorias por activo.")
        a("")
        a("| Metrica | BTC | ETH |")
        a("|---|---|---|")
        btc = pred_summaries.get("BTC", {})
        eth = pred_summaries.get("ETH", {})
        a(f"| Precio inicial | ${btc.get('start_price', 0):,.2f} | ${eth.get('start_price', 0):,.2f} |")
        a(f"| Precio mediano final | ${btc.get('median_final_price', 0):,.2f} | ${eth.get('median_final_price', 0):,.2f} |")
        a(f"| Percentil 5% final | ${btc.get('p05_final_price', 0):,.2f} | ${eth.get('p05_final_price', 0):,.2f} |")
        a(f"| P(caida >=20%) | {btc.get('prob_drop_20pct_or_more', 0)*100:.1f}% | {eth.get('prob_drop_20pct_or_more', 0)*100:.1f}% |")
        a(f"| P(caida >=40%) | {btc.get('prob_drop_40pct_or_more', 0)*100:.1f}% | {eth.get('prob_drop_40pct_or_more', 0)*100:.1f}% |")
        a("")
        a("### Graficas de prediccion")
        a("")
        a(f"![Prediccion BTC]({img_dir}/04_prediccion_btc.png)")
        a("")
        a(f"![Prediccion ETH]({img_dir}/04_prediccion_eth.png)")
        a("")
        a("---")
        a("")

    # ── 8. Conclusiones ──
    a("## 8. Conclusiones")
    a("")
    a("1. **Recapitalizacion** es la herramienta clave para extender la vida del prestamo y generar ingresos recurrentes.")
    a("2. **Cierre al vencimiento:** alta probabilidad de repago (cliente protege su colateral); uso de colateral para cubrir deuda es poco probable.")
    a("3. **LTV 40-50%** ofrece el mejor balance entre riesgo controlado y costos de custodia.")
    a("4. **Break-even** se alcanza con muy pocos prestamos dada la escala de montos ($100K-$20M).")
    a("5. **Cuota de apertura** del 2% genera ingreso inmediato significativo en prestamos grandes.")
    if pred_summaries:
        a("6. **Prediccion de precios:** Las simulaciones Monte Carlo Bootstrap-KDE permiten estimar probabilidades de caida del colateral, complementando el analisis de riesgo historico.")
    a("")
    a("---")
    a(f"*Generado automaticamente por modelo_financiero.ipynb — {pd.Timestamp.now().strftime('%Y-%m-%d')}*")

    # ── Escribir archivo ──
    report_path = "reporte_modelo_financiero.md"
    with open(report_path, "w") as f:
        f.write("\n".join(L))

    print(f"Reporte generado: {report_path} ({len(L)} lineas)")
    return report_path
