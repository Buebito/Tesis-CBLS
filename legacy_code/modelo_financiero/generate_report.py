"""Genera el reporte markdown del modelo financiero de crypto-backed loans.

Estructura investor-first: el reporte responde "si invierto $20M, que retorno
esperar" como pregunta nucleo, con LTV como palanca de sensibilidad. Las
metricas operativas del prestamo individual viven en el anexo.
"""
import math
import pandas as pd


# ──────────────────────────────────────────────────────────────────────────
# Mapeos de nombres tecnicos -> nombres legibles para tablas del reporte
# ──────────────────────────────────────────────────────────────────────────
SUMMARY_NAMES = {
    "N_prestamos": "Préstamos simulados",
    "Principal_prom_USD": "Monto promedio del préstamo (USD)",
    "Duracion_prom_meses": "Duración promedio (meses)",
    "Tasa_liquidacion": "% de préstamos liquidados",
    "Tasa_shortfall": "% con pérdida para el prestamista",
    "Margin_calls_prom": "Margin calls promedio por préstamo",
    "Recapitalizaciones_prom": "Recapitalizaciones promedio",
    "Settled_collateral": "% cerrados con colateral (no repago)",
    "Interes_prom_USD": "Intereses cobrados promedio (USD)",
    "Opening_fee_prom_USD": "Cuota de apertura promedio (USD)",
    "Liq_fee_prom_USD": "Fee de liquidación promedio (USD)",
    "Shortfall_prom_USD": "Pérdida promedio cuando hay shortfall (USD)",
    "Ingreso_bruto_prom_USD": "Ingreso bruto por préstamo (USD)",
}

PNL_NAMES = {
    "Principal_prom": "Monto promedio del préstamo",
    "Duracion_prom_meses": "Duración promedio (meses)",
    "Ingreso_neto_por_prestamo": "Ingreso neto por préstamo",
    "Prestamos_concurrentes": "Préstamos concurrentes",
    "Turnover_anual": "Reciclos del capital al año",
    "Total_prestamos_año": "Préstamos totales al año",
    "P&L_Año1": "Utilidad año 1",
    "ROI_Año1": "Retorno año 1",
    "P&L_Año2": "Utilidad año 2",
    "ROI_Año2": "Retorno año 2",
    "Break_even_prestamos": "Préstamos para break-even",
}

COST_NAMES = {
    "Custodia_prom": "Custodia (BitGo)",
    "Outgoing_prom": "Movimientos (BitGo)",
    "KYC": "KYC (Complif)",
    "Total_var_prom": "Costo variable total",
    "Ingreso_bruto_prom": "Ingreso bruto",
    "Shortfall_prom": "Pérdida por liquidación",
    "Ingreso_neto_prom": "Ingreso neto",
}

SPREAD_NAMES = {
    "Interes_prom": "Intereses cobrados",
    "Costo_BitGo_prom": "Costo de fondeo (BitGo)",
    "Spread_neto_prom": "Spread neto por préstamo",
    "P&L_anual_spread": "Utilidad anual con spread",
    "Capital_propio_req": "Capital propio requerido",
}

SENSITIVITY_NAMES = {
    "Cuota_apertura": "Cuota de apertura",
    "Ingreso_neto_prom": "Ingreso neto por préstamo",
    "P&L_anual": "Utilidad anual",
    "ROI": "Retorno anual",
}


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


def _rename_index(df, mapping):
    """Renombra el index segun el mapping (para tablas transpuestas)."""
    return df.rename(index=mapping)


def _rename_cols(df, mapping):
    return df.rename(columns=mapping)


def _fmt_money(x):
    if x is None:
        return "—"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.0f}"


def _fmt_pct(x, decimals=1):
    return f"{x*100:.{decimals}f}%"


def _ltv_label(ltv):
    return f"{ltv*100:.0f}%"


# ──────────────────────────────────────────────────────────────────────────
# Bloques del reporte
# ──────────────────────────────────────────────────────────────────────────

def _hero_block(L, capital, recommended_ltv, portfolio_dist, pnl_df_raw,
                reinvest_data, results, fc1):
    """Resumen ejecutivo: la respuesta directa al accionista."""
    a = L.append
    rec = portfolio_dist[recommended_ltv]
    r = results[recommended_ltv]
    avg_net = r.net_income.mean()
    avg_principal = r.original_principal.mean()
    avg_dur = r.duration_months.mean()
    is_liq = r.status.str.startswith("liquidated")

    # Capital deployed simultaneamente (prestamos concurrentes * monto promedio)
    max_concurrent = int(capital / avg_principal)
    capital_deployed = max_concurrent * avg_principal
    capital_utilization = capital_deployed / capital

    # Capital total prestado al año (no es lo mismo que deployed: si reciclas el
    # capital, prestas mas al año del que tienes en cualquier momento)
    capital_anual = rec["total_loans_year"] * avg_principal

    sf_month = reinvest_data[recommended_ltv]["selffund_month"]
    sf_label = f"mes {sf_month}" if sf_month else ">60 meses"

    be = math.ceil(fc1 / avg_net) if avg_net > 0 else float("inf")

    a("## Resumen ejecutivo")
    a("")
    a(f"> **Pregunta:** si inviertes **${capital:,.0f} USD** en operar crypto-backed loans, ")
    a("> ¿cuánto retorno esperar y cuándo recuperas tu capital?")
    a("")
    a(f"**Respuesta** (escenario recomendado: LTV inicial {_ltv_label(recommended_ltv)}):")
    a("")
    a("| Métrica | Valor esperado |")
    a("|---|---|")
    a(f"| Monto promedio del préstamo | **{_fmt_money(avg_principal)}** |")
    a(f"| Préstamos concurrentes (con ${capital/1e6:.0f}M) | **{max_concurrent}** "
      f"({_fmt_pct(capital_utilization)} del capital deployed) |")
    a(f"| Préstamos otorgados al año | **{rec['total_loans_year']}** |")
    a(f"| Capital total prestado al año | **{_fmt_money(capital_anual)}** "
      f"(reciclando el capital ~{rec['total_loans_year']/max_concurrent:.1f}× al año) |")
    a(f"| Duración promedio del préstamo | {avg_dur:.1f} meses |")
    a(f"| Utilidad anual (mediana, año 1) | **{_fmt_money(rec['pnl_y1_p50'])}** |")
    a(f"| Rango razonable año 1 (P10–P90) | "
      f"{_fmt_money(rec['pnl_y1_p10'])} – {_fmt_money(rec['pnl_y1_p90'])} |")
    a(f"| Retorno anual mediano (año 1) | **{_fmt_pct(rec['roi_y1_p50'])}** |")
    a(f"| Rango retorno año 1 (P10–P90) | "
      f"{_fmt_pct(rec['roi_y1_p10'])} – {_fmt_pct(rec['roi_y1_p90'])} |")
    a(f"| Retorno anual mediano (año 2+) | **{_fmt_pct(rec['roi_y2_p50'])}** |")
    a(f"| Probabilidad de pérdida en año 1 | {_fmt_pct(rec['prob_loss_y1'])} |")
    a(f"| Préstamos para cubrir costos fijos (break-even) | **{be}** |")
    a(f"| Mes de recuperación del capital (reinvirtiendo todo) | **{sf_label}** |")
    a(f"| % de préstamos que terminan en liquidación | {_fmt_pct(is_liq.mean())} |")
    a("")
    a(f"> **¿Por qué LTV {_ltv_label(recommended_ltv)} y no otro?** ofrece el mejor balance ")
    a("> entre retorno y riesgo: liquidaciones controladas, baja pérdida real para el ")
    a("> prestamista (la mayoría se cubre con el colateral) y costos de custodia razonables. ")
    a("> Las otras 3 LTV se reportan en la sección **Palanca: LTV** como sensibilidad.")
    a("")
    a("---")
    a("")


def _composition_block(L, composition, recommended_ltv):
    """De donde sale el ingreso neto promedio por prestamo."""
    a = L.append
    c = composition[recommended_ltv]

    a("## 1. ¿De dónde sale el retorno?")
    a("")
    a(f"Composición del ingreso neto promedio **por préstamo** (LTV {_ltv_label(recommended_ltv)}). ")
    a("Cada componente promediado sobre todos los préstamos simulados:")
    a("")
    a("| Componente | USD por préstamo | % del ingreso bruto |")
    a("|---|---:|---:|")
    gross = c["gross_income"]
    rows = [
        ("Intereses cobrados", c["interest"], "+"),
        ("Cuota de apertura (2%)", c["opening_fee"], "+"),
        ("Fee de liquidación", c["liq_fee"], "+"),
        ("**Ingreso bruto**", gross, "="),
        ("Custodia (BitGo)", -c["custody"], "−"),
        ("Movimientos (BitGo)", -c["outgoing"], "−"),
        ("KYC (Complif)", -c["kyc"], "−"),
        ("Pérdida por shortfall", -c["shortfall"], "−"),
        ("**Ingreso neto por préstamo**", c["net_income"], "="),
    ]
    for name, val, _ in rows:
        pct = (val / gross * 100) if gross else 0
        a(f"| {name} | {_fmt_money(val)} | {pct:.1f}% |")
    a("")
    a("> **Lectura:** los **intereses** son la mayor fuente de ingreso; la **cuota de apertura** ")
    a("> aporta un cobro inmediato significativo dado el tamaño de los préstamos. La **custodia** ")
    a("> es el costo dominante porque escala con el colateral durante toda la vida del préstamo.")
    a("")
    a("---")
    a("")


def _breakeven_block(L, fc1, fc2, results, recommended_ltv, capital):
    a = L.append
    r = results[recommended_ltv]
    avg_net = r.net_income.mean()
    be = math.ceil(fc1 / avg_net) if avg_net > 0 else float("inf")
    avg_p = r.original_principal.mean()
    capital_be = be * avg_p

    a("## 2. Break-even")
    a("")
    a(f"Para cubrir los **${fc1:,.0f}** de costos fijos del año 1 ")
    a(f"(Complif setup + mensualidades + desarrollo), se necesitan:")
    a("")
    be_label = "préstamo" if be == 1 else "préstamos"
    a("| Métrica | Valor |")
    a("|---|---|")
    a(f"| Préstamos para break-even (LTV {_ltv_label(recommended_ltv)}) | **{be} {be_label}** |")
    a(f"| Capital nominal en circulación equivalente | {_fmt_money(capital_be)} |")
    a(f"| Costos fijos año 1 | {_fmt_money(fc1)} |")
    a(f"| Costos fijos año 2+ | {_fmt_money(fc2)} |")
    a(f"| Ingreso neto promedio por préstamo | {_fmt_money(avg_net)} |")
    a("")
    a("> **Lectura:** la operación cubre sus costos fijos con muy pocos préstamos dada la ")
    a("> escala de los montos. Más allá del break-even, cada préstamo adicional aporta ")
    a("> directamente al retorno del accionista.")
    a("")
    a("---")
    a("")


def _portfolio_dist_block(L, portfolio_dist, capital, img_dir):
    a = L.append
    a("## 3. Rango de retornos esperados")
    a("")
    a(f"Distribución del retorno anual generada por bootstrap de portafolio: para cada LTV ")
    a(f"se construyen 2,000 portafolios sintéticos con el número de préstamos esperados al ")
    a(f"año, sampleando con reemplazo de las 5,000 simulaciones individuales. Esto captura ")
    a(f"la variabilidad real entre escenarios (préstamos que se liquidan vs. los que pagan).")
    a("")
    a("| LTV | ROI año 1 P10 | ROI año 1 mediano | ROI año 1 P90 | P(pérdida año 1) | Utilidad mediana año 1 |")
    a("|---|---:|---:|---:|---:|---:|")
    for ltv in sorted(portfolio_dist.keys()):
        d = portfolio_dist[ltv]
        a(f"| {_ltv_label(ltv)} "
          f"| {_fmt_pct(d['roi_y1_p10'])} "
          f"| **{_fmt_pct(d['roi_y1_p50'])}** "
          f"| {_fmt_pct(d['roi_y1_p90'])} "
          f"| {_fmt_pct(d['prob_loss_y1'])} "
          f"| {_fmt_money(d['pnl_y1_p50'])} |")
    a("")
    a("> **Cómo leer P10–P90:** en el 10% peor de los escenarios obtienes a lo sumo el ROI P10. ")
    a("> En la mediana (P50) obtienes el ROI más probable. En el 10% mejor superas el P90. ")
    a("> Es el rango razonable de resultados, no el mejor ni el peor caso absoluto.")
    a("")
    a(f"![Distribución del ROI anual]({img_dir}/06_distribucion_roi.png)")
    a("")
    a("---")
    a("")


def _ltv_lever_block(L, summary_df, pnl_df, img_dir):
    """LTV como palanca de sensibilidad — los 4 escenarios resumidos."""
    a = L.append
    a("## 4. Palanca: LTV inicial")
    a("")
    a("El LTV inicial es la palanca principal del modelo: balancea retorno y riesgo. ")
    a("Más conservador (LTV 10%) → menos liquidaciones pero menor turnover; más agresivo ")
    a("(LTV 50%) → mayor turnover y retorno, pero más liquidaciones.")
    a("")

    pnl_renamed = _rename_cols(pnl_df, PNL_NAMES)
    a(_md_table(pnl_renamed.T))
    a("")
    a(f"![Comparación LTV — P&L y ROI]({img_dir}/02_pnl_roi.png)")
    a("")
    a("---")
    a("")


def _trajectory_block(L, reinvest_data, capital, img_dir):
    a = L.append
    a("## 5. Trayectoria mensual y auto-fondeo")
    a("")
    a("Si **se reinvierten todos los ingresos netos** en nuevos préstamos en lugar de ")
    a("retirarlos, el negocio alcanza auto-fondeo cuando las utilidades acumuladas igualan ")
    a(f"el capital inicial (${capital:,.0f}). En ese momento, el capital original puede ")
    a("retirarse y la operación se sostiene con sus propios ingresos.")
    a("")
    a("| LTV | Mes de auto-fondeo | Utilidad acumulada al mes 60 |")
    a("|---|---|---:|")
    for ltv_val in sorted(reinvest_data.keys()):
        rd = reinvest_data[ltv_val]
        sf = f"Mes {rd['selffund_month']}" if rd["selffund_month"] else ">60"
        a(f"| {_ltv_label(ltv_val)} | {sf} | {_fmt_money(rd['pnl_60'])} |")
    a("")
    a(f"![Reinversión y auto-fondeo]({img_dir}/05_reinversion.png)")
    a("")
    a(f"![Proyección mensual P&L]({img_dir}/03_proyeccion_mensual.png)")
    a("")
    a("---")
    a("")


def _sensitivity_block(L, sensitivity_rows):
    a = L.append
    a("## 6. Sensibilidad: cuota de apertura")
    a("")
    a("La cuota de apertura (cobrada al originar el préstamo) es la palanca comercial ")
    a(f"más directa. Resultados con LTV 50%:")
    a("")
    sens_df = pd.DataFrame(sensitivity_rows).set_index("Cuota_apertura")
    sens_df.index.name = SENSITIVITY_NAMES["Cuota_apertura"]
    sens_df = _rename_cols(sens_df, SENSITIVITY_NAMES)
    a(_md_table(sens_df))
    a("")
    a("---")
    a("")


def _risk_block(L, vol_data, buffer_results, pred_summaries, img_dir):
    a = L.append
    a("## 7. Riesgo de mercado")
    a("")

    if vol_data:
        a("### 7.1 Volatilidad histórica del colateral")
        a("")
        a("Volatilidad anualizada (ventana rodante 30 días) sobre datos horarios. Es el ")
        a("driver principal del riesgo de margin call y liquidación.")
        a("")
        a("| Métrica | BTC (%) | ETH (%) |")
        a("|---|---:|---:|")
        btc_v = vol_data.get("BTC", {})
        eth_v = vol_data.get("ETH", {})
        rows = [
            ("Volatilidad actual (30d)", "current_30"),
            ("Volatilidad actual (90d)", "current_90"),
            ("Volatilidad actual (365d)", "current_365"),
            ("Media histórica (30d)", "mean_30"),
            ("P25 histórico (30d)", "p25_30"),
            ("P75 histórico (30d)", "p75_30"),
            ("Mínimo histórico (30d)", "min_30"),
            ("Máximo histórico (30d)", "max_30"),
        ]
        for label, key in rows:
            a(f"| {label} | {btc_v.get(key, 0)} | {eth_v.get(key, 0)} |")
        a("")
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
            a(f"> **{coin}:** régimen de volatilidad **{regime}** "
              f"(actual {curr}%, rango histórico {vd.get('min_30', 0)}–{vd.get('max_30', 0)}%)  ")
        a("")
        a(f"![Volatilidad histórica]({img_dir}/00_volatilidad.png)")
        a("")

    if buffer_results:
        a("### 7.2 Buffer de protección requerido al cliente")
        a("")
        a("Buffer mínimo de colateral extra (% del colateral inicial) que el cliente debe ")
        a("provisionar al inicio para evitar liquidación en un horizonte de 1 año.")
        a("")
        a("| LTV | Moneda | Buffer P90 (%) | Buffer P95 (%) | Buffer P99 (%) |")
        a("|---|---|---:|---:|---:|")
        for ltv_val in sorted(buffer_results.keys()):
            for coin in ("BTC", "ETH"):
                cb = buffer_results[ltv_val].get(coin, {})
                a(f"| {_ltv_label(ltv_val)} | {coin} "
                  f"| {cb.get(0.90, 0)} | {cb.get(0.95, 0)} | {cb.get(0.99, 0)} |")
        a("")
        a("> **Lectura:** si el cliente provisiona al menos el buffer P90, evita la liquidación ")
        a("> en ≥90% de los escenarios históricos.")
        a("")

    if pred_summaries:
        a("### 7.3 Predicción de precios futuros (Monte Carlo)")
        a("")
        a("Simulación Monte Carlo con Bootstrap-KDE sobre retornos históricos, horizonte de ")
        a("365 días, 2,000 trayectorias por activo.")
        a("")
        a("| Métrica | BTC | ETH |")
        a("|---|---:|---:|")
        btc = pred_summaries.get("BTC", {})
        eth = pred_summaries.get("ETH", {})
        a(f"| Precio inicial | ${btc.get('start_price', 0):,.2f} "
          f"| ${eth.get('start_price', 0):,.2f} |")
        a(f"| Precio mediano final | ${btc.get('median_final_price', 0):,.2f} "
          f"| ${eth.get('median_final_price', 0):,.2f} |")
        a(f"| Percentil 5% final | ${btc.get('p05_final_price', 0):,.2f} "
          f"| ${eth.get('p05_final_price', 0):,.2f} |")
        a(f"| P(caída ≥20%) | {btc.get('prob_drop_20pct_or_more', 0)*100:.1f}% "
          f"| {eth.get('prob_drop_20pct_or_more', 0)*100:.1f}% |")
        a(f"| P(caída ≥40%) | {btc.get('prob_drop_40pct_or_more', 0)*100:.1f}% "
          f"| {eth.get('prob_drop_40pct_or_more', 0)*100:.1f}% |")
        a("")
        a(f"![Predicción BTC]({img_dir}/04_prediccion_btc.png)")
        a("")
        a(f"![Predicción ETH]({img_dir}/04_prediccion_eth.png)")
        a("")

    a("---")
    a("")


def _bitgo_block(L, spread_data, config, accountant_view=None):
    a = L.append
    a("## 8. Alternativa: intermediación con BitGo + cobertura")
    a("")
    bg_rate = (accountant_view or {}).get("bitgo_rate", 0.095)
    hedge_rate = (accountant_view or {}).get("hedge_rate", 0.045)
    weighted = (accountant_view or {}).get("weighted_rate", 0.19)
    diff = weighted - bg_rate - hedge_rate

    a(f"Escenario alternativo donde **BitGo financia los préstamos al {bg_rate*100:.1f}% anual** "
      f"y x2o cobra una **cobertura adicional de {hedge_rate*100:.1f}%** (volatility insurance / ")
    a(f"liquidation hedge). x2o presta al {config['INT_BTC']*100:.0f}–{config['INT_ETH']*100:.0f}% ")
    a(f"y retiene el **diferencial neto de ~{diff*100:.1f}%** sobre el principal. ")
    a("**No se requiere capital propio** para fondear los préstamos; solo para los costos operativos.")
    a("")
    a("| Concepto | Valor |")
    a("|---|---|")
    a(f"| Tasa BitGo (costo de fondeo) | **{bg_rate*100:.1f}% anual** |")
    a(f"| Costo de cobertura (hedge) | **{hedge_rate*100:.1f}% anual** |")
    a(f"| Tasa x2o BTC / ETH | {config['INT_BTC']*100:.0f}% / {config['INT_ETH']*100:.0f}% |")
    a(f"| Tasa promedio ponderada | ~{weighted*100:.1f}% |")
    a(f"| **Diferencial neto** | **~{diff*100:.1f}% sobre principal** |")
    a("| LTV de liquidación BitGo | 85% |")
    a(f"| LTV de liquidación x2o | {config['LIQ_LTV']*100:.0f}% (margen de seguridad: 5pp) |")
    a("| Capital propio requerido | $0 (fondeo externo) |")
    a("")
    sp_df = pd.DataFrame(spread_data).set_index("LTV")
    sp_df = _rename_cols(sp_df, SPREAD_NAMES)
    a(_md_table(sp_df))
    a("")
    a("> **Ventaja:** ROI teóricamente infinito (sin capital propio comprometido). ")
    a("> **Trade-off:** menor ingreso neto por préstamo vs. modelo con capital propio, ")
    a("> y dependencia de la relación comercial con BitGo.")
    a("")
    a("---")
    a("")


def _crossover_block(L, accountant_view, capital, img_dir):
    """Comparativa directa: capital propio vs BitGo — cuando es mas rentable cada uno."""
    a = L.append
    av = accountant_view
    a("## 9. ¿Cuándo conviene capital propio vs BitGo?")
    a("")
    a(f"Comparativa directa de utilidad acumulada mes a mes. La pregunta concreta: ")
    a(f"si tienes ${capital:,.0f} **disponibles** para invertir, ¿conviene **comprometerlos como ")
    a(f"capital propio** (otorgando los préstamos directamente) o **dejarlos en una alternativa ")
    a(f"líquida** (CETES, bonos) y operar vía intermediación BitGo?")
    a("")
    a("**Asunción clave** del análisis: si vas con BitGo, los $20M libres ganan una **tasa ")
    a(f"alternativa del {av['alt_rate_annual']*100:.0f}% anual** (referencia: CETES MX). Si esa ")
    a("tasa fuera 0%, capital propio gana siempre desde el primer mes.")
    a("")
    a("### P&L mensual de cada modelo (LTV 40%)")
    a("")
    a("| Métrica | Capital propio | BitGo + alt |")
    a("|---|---:|---:|")
    a(f"| Préstamos al año | {av['loans_per_year']} | {av['loans_per_year']} |")
    a(f"| Ingreso neto / préstamo | {_fmt_money(av['own_net_pnl']/av['loans_per_year'] if av['loans_per_year'] else 0)} "
      f"| {_fmt_money(av['bg_net_pnl']/av['loans_per_year'] if av['loans_per_year'] else 0)} |")
    a(f"| **Utilidad anual operativa** | **{_fmt_money(av['own_net_pnl'])}** "
      f"| **{_fmt_money(av['bg_net_pnl'])}** |")
    a(f"| Yield sobre $20M alt ({av['alt_rate_annual']*100:.0f}%) | "
      f"$0 (capital comprometido) | {_fmt_money(capital * av['alt_rate_annual'])} |")
    a(f"| **Total anual** | **{_fmt_money(av['own_net_pnl'])}** "
      f"| **{_fmt_money(av['bg_net_pnl'] + capital * av['alt_rate_annual'])}** |")
    a("")
    a("### Mes de crossover (cuándo capital propio supera a BitGo+alt)")
    a("")
    cm = av.get("crossover_month")
    if cm:
        a(f"Con tasa alternativa {av['alt_rate_annual']*100:.0f}% anual, capital propio supera "
          f"a BitGo+alt en **el mes {cm}**.")
    else:
        a(f"Con tasa alternativa {av['alt_rate_annual']*100:.0f}% anual, BitGo+alt domina durante "
          f"todo el horizonte de 60 meses analizado.")
    a("")
    a("### Sensibilidad a la tasa alternativa")
    a("")
    a("Si la tasa alternativa cambia, el mes de crossover también:")
    a("")
    a("| Tasa alternativa anual | Mes de crossover |")
    a("|---|---|")
    for row in av["crossover_sens"]:
        a(f"| {row['alt_rate']*100:.1f}% | {row['label']} |")
    a("")
    a(f"![Crossover capital propio vs BitGo+alt]({img_dir}/07_crossover_bitgo.png)")
    a("")
    a("> **Lectura para el accionista:** capital propio gana en absoluto pero requiere comprometer ")
    a(f"> los ${capital:,.0f}. Si esos fondos pueden ganar más del {av['alt_rate_annual']*100:.0f}% ")
    a("> en otra inversión líquida, BitGo+alt domina hasta el mes de crossover. Si no tienes una ")
    a("> alternativa con yield decente, capital propio gana siempre.")
    a("")
    a("---")
    a("")


def _accountant_block(L, accountant_view, capital):
    """Vista de contador: estado de resultados side-by-side."""
    a = L.append
    av = accountant_view
    a("## 10. Vista de contador — Estado de resultados anual")
    a("")
    a("Estado de resultados comparativo (base anual), con desglose por componente para ")
    a("trazabilidad contable. Todos los números son promedio anual operando estable, ")
    a(f"basados en **{av['loans_per_year']} préstamos al año** de **{_fmt_money(av['avg_principal'])} ")
    a(f"promedio** y duración promedio **{av['avg_duration_months']:.1f} meses**.")
    a("")
    a("### Modelo A — Capital propio ($20M comprometidos)")
    a("")
    a("| Concepto | Por préstamo | Anual ({} préstamos) |".format(av['loans_per_year']))
    a("|---|---:|---:|")
    a(f"| Intereses cobrados al cliente | {_fmt_money(av['interest_per_loan'])} "
      f"| {_fmt_money(av['interest_per_loan']*av['loans_per_year'])} |")
    a(f"| Cuota de apertura | {_fmt_money(av['opening_per_loan'])} "
      f"| {_fmt_money(av['opening_per_loan']*av['loans_per_year'])} |")
    a(f"| Fee de liquidación | {_fmt_money(av['liqfee_per_loan'])} "
      f"| {_fmt_money(av['liqfee_per_loan']*av['loans_per_year'])} |")
    a(f"| **(=) Ingresos totales** | **{_fmt_money(av['interest_per_loan']+av['opening_per_loan']+av['liqfee_per_loan'])}** "
      f"| **{_fmt_money(av['own_revenue_annual'])}** |")
    a(f"| (−) Custodia (BitGo) | -{_fmt_money(av['custody_per_loan'])} "
      f"| -{_fmt_money(av['custody_per_loan']*av['loans_per_year'])} |")
    a(f"| (−) Movimientos (BitGo) | -{_fmt_money(av['outgoing_per_loan'])} "
      f"| -{_fmt_money(av['outgoing_per_loan']*av['loans_per_year'])} |")
    a(f"| (−) KYC (Complif) | -{_fmt_money(av['kyc_per_loan'])} "
      f"| -{_fmt_money(av['kyc_per_loan']*av['loans_per_year'])} |")
    a(f"| (−) Pérdida por shortfall | -{_fmt_money(av['shortfall_per_loan'])} "
      f"| -{_fmt_money(av['shortfall_per_loan']*av['loans_per_year'])} |")
    a(f"| **(=) Margen bruto operativo** | — | **{_fmt_money(av['own_gross_margin'])}** |")
    a(f"| (−) Costos fijos (año 1: setup + mensual + dev) | — | -{_fmt_money(av['fc1'])} |")
    a(f"| **(=) Utilidad neta operativa anual** | — | **{_fmt_money(av['own_net_pnl'])}** |")
    a(f"| Capital comprometido | — | {_fmt_money(capital)} |")
    a(f"| **ROI sobre capital** | — | **{_fmt_pct(av['own_net_pnl']/capital)}** |")
    a("")
    a("### Modelo B — Intermediación BitGo + cobertura ($0 capital propio)")
    a("")
    a("| Concepto | Por préstamo | Anual ({} préstamos) |".format(av['loans_per_year']))
    a("|---|---:|---:|")
    a(f"| Intereses cobrados al cliente | {_fmt_money(av['interest_per_loan'])} "
      f"| {_fmt_money(av['interest_per_loan']*av['loans_per_year'])} |")
    a(f"| (−) Costo de fondeo BitGo ({av['bitgo_rate']*100:.1f}%) | -{_fmt_money(av['bitgo_cost_per_loan'])} "
      f"| -{_fmt_money(av['bitgo_cost_per_loan']*av['loans_per_year'])} |")
    a(f"| (−) Costo de cobertura ({av['hedge_rate']*100:.1f}%) | -{_fmt_money(av['hedge_cost_per_loan'])} "
      f"| -{_fmt_money(av['hedge_cost_per_loan']*av['loans_per_year'])} |")
    a(f"| Cuota de apertura | {_fmt_money(av['opening_per_loan'])} "
      f"| {_fmt_money(av['opening_per_loan']*av['loans_per_year'])} |")
    a(f"| Fee de liquidación | {_fmt_money(av['liqfee_per_loan'])} "
      f"| {_fmt_money(av['liqfee_per_loan']*av['loans_per_year'])} |")
    a(f"| **(=) Ingreso neto por intermediación (spread + fees)** | — | "
      f"**{_fmt_money(av['bg_revenue_annual'] - av['bg_funding_cost'])}** |")
    a(f"| (−) Custodia, movimientos, KYC | — | -{_fmt_money(av['bg_var_costs_annual']-av['shortfall_per_loan']*av['loans_per_year'])} |")
    a(f"| (−) Pérdida por shortfall | — | -{_fmt_money(av['shortfall_per_loan']*av['loans_per_year'])} |")
    a(f"| **(=) Margen bruto operativo** | — | **{_fmt_money(av['bg_gross_margin'])}** |")
    a(f"| (−) Costos fijos | — | -{_fmt_money(av['fc1'])} |")
    a(f"| **(=) Utilidad neta operativa anual** | — | **{_fmt_money(av['bg_net_pnl'])}** |")
    a(f"| Capital comprometido | — | $0 |")
    a(f"| **ROI sobre capital** | — | **infinito** |")
    a("")
    a("### Comparativa directa")
    a("")
    a("| Métrica anual | Modelo A (Capital propio) | Modelo B (BitGo + cobertura) |")
    a("|---|---:|---:|")
    a(f"| Ingresos | {_fmt_money(av['own_revenue_annual'])} | {_fmt_money(av['bg_revenue_annual'])} |")
    a(f"| (−) Costos de fondeo (BitGo + cobertura) | $0 | -{_fmt_money(av['bg_funding_cost'])} |")
    a(f"| (−) Costos variables operativos | -{_fmt_money(av['own_var_costs_annual'])} | -{_fmt_money(av['bg_var_costs_annual'])} |")
    a(f"| (−) Costos fijos | -{_fmt_money(av['fc1'])} | -{_fmt_money(av['fc1'])} |")
    a(f"| **Utilidad neta anual** | **{_fmt_money(av['own_net_pnl'])}** | **{_fmt_money(av['bg_net_pnl'])}** |")
    a(f"| Capital propio comprometido | {_fmt_money(capital)} | $0 |")
    a(f"| ROI sobre capital | {_fmt_pct(av['own_net_pnl']/capital)} | infinito |")
    a(f"| Diferencia absoluta (A − B) | — | **{_fmt_money(av['own_net_pnl']-av['bg_net_pnl'])}/año extra** |")
    a("")
    a("> **Nota contable:** los $20M comprometidos en Modelo A no son un *gasto* contable — ")
    a("> permanecen como cuentas por cobrar (préstamos otorgados) en el balance. Lo que cambia ")
    a("> es la **liquidez**: en A no puedes disponer del capital, en B sí.")
    a("")
    a("---")
    a("")


def _annex_a_loan_behavior(L, summary_df, img_dir):
    """Anexo A: comportamiento del prestamo individual (lo que antes era seccion 3)."""
    a = L.append
    a("## Anexo A — Comportamiento del préstamo individual")
    a("")
    a("Métricas operativas promedio por préstamo bajo cada escenario de LTV. Estas son las ")
    a("variables que alimentan el modelo financiero anterior:")
    a("")
    summary_renamed = _rename_cols(summary_df, SUMMARY_NAMES)
    a(_md_table(summary_renamed.T))
    a("")
    a(f"![Comparación de escenarios por LTV]({img_dir}/01_comparacion_ltv.png)")
    a("")
    a("---")
    a("")


def _annex_b_costs(L, cost_rows, config, fc1, fc2):
    a = L.append
    a("## Anexo B — Estructura de costos")
    a("")
    a("### Costos variables promedio por préstamo")
    a("")
    cost_df = pd.DataFrame(cost_rows).set_index("LTV")
    cost_df = _rename_cols(cost_df, COST_NAMES)
    a(_md_table(cost_df))
    a("")
    a("### Costos fijos anuales")
    a("")
    a("| Concepto | Año 1 | Año 2+ |")
    a("|---|---:|---:|")
    a(f"| Complif Setup | {_fmt_money(config['COMPLIF_SETUP'])} | $0 |")
    a(f"| Complif mensual (×12) | {_fmt_money(config['COMPLIF_MONTHLY']*12)} "
      f"| {_fmt_money(config['COMPLIF_MONTHLY']*12)} |")
    a(f"| Desarrollo | {_fmt_money(config['DEV_COST'])} | $0 |")
    a(f"| **Total** | **{_fmt_money(fc1)}** | **{_fmt_money(fc2)}** |")
    a("")
    a("---")
    a("")


def _annex_c_params(L, config):
    a = L.append
    a("## Anexo C — Parámetros del modelo y flujo")
    a("")
    a("### Parámetros")
    a("")
    a("| Parámetro | Valor |")
    a("|---|---|")
    ltv_str = ", ".join(_ltv_label(x) for x in config["INIT_LTV_SCENARIOS"])
    a(f"| Escenarios LTV | {ltv_str} |")
    a(f"| Margin call / Liquidación LTV | {config['MC_LTV']*100:.0f}% / {config['LIQ_LTV']*100:.0f}% |")
    a(f"| Tasa anual BTC / ETH | {config['INT_BTC']*100:.0f}% / {config['INT_ETH']*100:.0f}% |")
    a(f"| Cuota de apertura | {config['OPENING_FEE_RATE']*100:.0f}% |")
    a(f"| Fee de liquidación | {config['LIQUIDATION_FEE_RATE']*100:.0f}% |")
    a(f"| P(recapitalizar) base | {config['P_RECAP_INIT']*100:.0f}% (decae por recapitalización) |")
    a(f"| P(pagar al vencimiento) | {config['P_REPAY_MATURITY']*100:.0f}% |")
    a(f"| Montos de préstamo | ${config['LOAN_SIZES_USD'][0]:,} – ${config['LOAN_SIZES_USD'][-1]:,} USD |")
    a("")
    a("### Flujo del préstamo")
    a("")
    a("```")
    a("Inicio -> [Devengar interés] -> ¿Margin Call?")
    a("  |                                |")
    a("  |                       Sí: P(agregar colateral) -> Continúa")
    a("  |                           P(amortizar)         -> Continúa")
    a("  |                           P(ignorar)           -> Liquidación")
    a("  |")
    a("  +-> ¿Vencimiento?")
    a("        |")
    a("        Sí: P(recapitalizar) -> Paga intereses, extiende N meses -> Continúa")
    a("            Ninguno:")
    a("              P(pagar préstamo)  -> Cierre con repago (alta prob.)")
    a("              P(usar colateral)  -> Cierre con colateral (baja prob.)")
    a("```")
    a("")
    a("---")
    a("")


# ──────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────

def write_report(results, config, summary_df, pnl_df, cost_rows,
                 sensitivity_rows, capital, fc1, fc2, price_df, img_dir,
                 pred_summaries=None, vol_data=None, buffer_results=None,
                 reinvest_data=None, spread_data=None,
                 portfolio_dist=None, composition=None, recommended_ltv=0.40,
                 accountant_view=None):
    """Genera reporte_modelo_financiero.md en formato investor-first.

    Args:
        results: dict {ltv: DataFrame} con resultados por escenario
        config: dict CONFIG del modelo
        summary_df: DataFrame resumen por LTV (snake_case)
        pnl_df: DataFrame P&L por LTV (snake_case)
        cost_rows: list[dict] costos variables por LTV
        sensitivity_rows: list[dict] sensibilidad cuota apertura
        capital: float capital inyectado
        fc1, fc2: float costos fijos anio 1 y 2+
        price_df: DataFrame de precios historicos
        img_dir: str directorio de imagenes
        pred_summaries: dict {coin: dict} resumen de prediccion (opcional)
        vol_data: dict {coin: dict} resumen de volatilidad (opcional)
        buffer_results: dict {ltv: {coin: {q: pct}}} buffer (opcional)
        reinvest_data: dict {ltv: {'selffund_month': int, 'pnl_60': float}}
        spread_data: list[dict] datos de intermediacion BitGo (opcional)
        portfolio_dist: dict {ltv: {p10/p50/p90 ROI...}} bootstrap portafolio
        composition: dict {ltv: {interest, opening_fee, ...}} composicion
        recommended_ltv: float LTV recomendado para el resumen ejecutivo
    """
    L = []
    a = L.append

    # ── Header ──
    a("# Modelo Financiero — Crypto-Backed Loans")
    a("")
    a(f"> **¿Cuánto retorno esperar al invertir ${capital/1e6:.0f}M USD en operar ")
    a("> un negocio de préstamos colateralizados con BTC/ETH?**")
    a("")
    fmt = "%Y-%m-%d %H:%M"
    a(f"> Fecha: {pd.Timestamp.now().strftime(fmt)} · "
      f"Datos históricos: {price_df.index[0].date()} a {price_df.index[-1].date()} · "
      f"Préstamos simulados por escenario: {config['N_LOANS']:,}  ")
    a("")
    a("---")
    a("")

    # ── Resumen ejecutivo (hero) ──
    if portfolio_dist and reinvest_data:
        _hero_block(L, capital, recommended_ltv, portfolio_dist, pnl_df,
                    reinvest_data, results, fc1)

    # ── 1. Composicion del retorno ──
    if composition:
        _composition_block(L, composition, recommended_ltv)

    # ── 2. Break-even ──
    _breakeven_block(L, fc1, fc2, results, recommended_ltv, capital)

    # ── 3. Distribucion del retorno ──
    if portfolio_dist:
        _portfolio_dist_block(L, portfolio_dist, capital, img_dir)

    # ── 4. Palanca: LTV ──
    _ltv_lever_block(L, summary_df, pnl_df, img_dir)

    # ── 5. Trayectoria mensual ──
    if reinvest_data:
        _trajectory_block(L, reinvest_data, capital, img_dir)

    # ── 6. Sensibilidad cuota apertura ──
    _sensitivity_block(L, sensitivity_rows)

    # ── 7. Riesgo de mercado ──
    _risk_block(L, vol_data, buffer_results, pred_summaries, img_dir)

    # ── 8. Alternativa BitGo ──
    if spread_data:
        _bitgo_block(L, spread_data, config, accountant_view)

    # ── 9. Crossover capital propio vs BitGo ──
    if accountant_view:
        _crossover_block(L, accountant_view, capital, img_dir)

    # ── 10. Vista de contador ──
    if accountant_view:
        _accountant_block(L, accountant_view, capital)

    # ── Anexos ──
    _annex_a_loan_behavior(L, summary_df, img_dir)
    _annex_b_costs(L, cost_rows, config, fc1, fc2)
    _annex_c_params(L, config)

    # ── Conclusiones ──
    a("## Conclusiones")
    a("")
    a(f"1. **Retorno esperado** (LTV {_ltv_label(recommended_ltv)}, año 1): "
      f"mediana {_fmt_pct(portfolio_dist[recommended_ltv]['roi_y1_p50'])}, "
      f"rango razonable "
      f"{_fmt_pct(portfolio_dist[recommended_ltv]['roi_y1_p10'])}–"
      f"{_fmt_pct(portfolio_dist[recommended_ltv]['roi_y1_p90'])} sobre los "
      f"${capital/1e6:.0f}M.")
    a("2. **Break-even temprano**: muy pocos préstamos cubren los costos fijos dada la ")
    a("   escala de los montos ($100K–$20M).")
    a("3. **Recapitalización** es la herramienta clave para extender la vida del préstamo y ")
    a("   generar ingresos recurrentes.")
    a("4. **Cierre al vencimiento**: alta probabilidad de repago (cliente protege su colateral); ")
    a("   uso de colateral para cubrir deuda es poco probable.")
    a("5. **LTV como palanca**: subir el LTV aumenta el retorno mediano pero también la ")
    a("   probabilidad de liquidación; LTV recomendado balancea ambos.")
    a("6. **Alternativa BitGo**: permite operar sin capital propio comprometido, a cambio de ")
    a("   menor ingreso neto por préstamo.")
    a("")
    a("---")
    a(f"*Generado automáticamente por modelo_financiero.ipynb — "
      f"{pd.Timestamp.now().strftime('%Y-%m-%d')}*")

    # ── Escribir archivo ──
    report_path = "reporte_modelo_financiero.md"
    with open(report_path, "w") as f:
        f.write("\n".join(L))

    print(f"Reporte generado: {report_path} ({len(L)} líneas)")
    return report_path
