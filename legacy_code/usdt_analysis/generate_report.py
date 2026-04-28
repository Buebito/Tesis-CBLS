"""Genera el reporte markdown del analisis de inversion en USDT."""
import pandas as pd


def _md_table(dataframe):
    try:
        return dataframe.to_markdown()
    except Exception:
        cols = [str(dataframe.index.name or "")] + [str(c) for c in dataframe.columns]
        rows = [[str(idx)] + [str(v) for v in row] for idx, row in dataframe.iterrows()]
        sep = "|" + "|".join("---" for _ in cols) + "|"
        header = "| " + " | ".join(cols) + " |"
        body = ["| " + " | ".join(r) + " |" for r in rows]
        return "\n".join([header, sep] + body)


def _fmt_money(x, currency="USD"):
    if x is None:
        return "—"
    sign = "-" if x < 0 else ""
    sym = "$" if currency in ("USD", "MXN") else ""
    suffix = f" {currency}" if currency == "MXN" else ""
    return f"{sign}{sym}{abs(x):,.0f}{suffix}"


def _fmt_pct(x, decimals=1):
    return f"{x*100:.{decimals}f}%"


def write_report(historic_stats, mc_horizons, yield_scenarios,
                 fx_summary, common_period, capital_usd, img_dir,
                 yield_recommended_label, yield_recommended_apy):
    """Genera reporte_usdt_analysis.md.

    Args:
        historic_stats: DataFrame con stats por activo (CAGR, vol, MaxDD, etc.)
        mc_horizons: dict {years: {usdmxn_p5/p50/p95, prob_dev_5/10/20, prob_apr_5/10}}
        yield_scenarios: DataFrame parametrico de retorno MXN-equivalente
        fx_summary: dict {start_date, end_date, start_fx, end_fx, total_appreciation}
        common_period: dict {start, end} para BTC/ETH overlap
        capital_usd: float capital de referencia para la narrativa
        img_dir: str
        yield_recommended_label: str (e.g., "Stablecoin lending — DeFi (Aave/Compound)")
        yield_recommended_apy: float APY en USD del escenario recomendado
    """
    L = []
    a = L.append

    # ── Header ──
    a("# Análisis de inversión en USDT")
    a("")
    a("> **Perspectiva del prestamista mexicano:** ¿conviene tener capital en USDT en lugar de ")
    a(f"> MXN? ¿Qué retorno esperar a 1, 3 y 5 años? Análisis sobre datos históricos del tipo ")
    a(f"> de cambio peso-dólar (Banxico, 2001–2026) y precios de BTC/ETH (Binance, 1-min).")
    a("")
    a(f"> Fecha de análisis: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}  ")
    a(f"> Periodo común BTC/ETH/USDMXN: {common_period['start']} a {common_period['end']}  ")
    a(f"> Tipo de cambio: {fx_summary['start_date']} = ${fx_summary['start_fx']:.4f} → "
      f"{fx_summary['end_date']} = ${fx_summary['end_fx']:.4f} "
      f"(apreciación USD acumulada: {_fmt_pct(fx_summary['total_appreciation'])})")
    a("")
    a("---")
    a("")

    # ── Resumen ejecutivo ──
    a("## Resumen ejecutivo")
    a("")
    yr1 = mc_horizons[1]
    yr3 = mc_horizons[3]
    yr5 = mc_horizons[5]
    cap = capital_usd
    apy_str = _fmt_pct(yield_recommended_apy)

    a(f"> **Pregunta:** si tienes **${cap:,.0f} USD disponibles**, ¿conviene mantenerlos en ")
    a(f"> USDT (con yield) o convertirlos a MXN? ¿Cuánto valor en pesos esperarás en 1, 3 y 5 años?")
    a("")
    a(f"**Respuesta** (USDT con yield {apy_str} APY — *{yield_recommended_label}*):")
    a("")
    a("| Horizonte | USDMXN esperado (mediana) | USDT con yield: valor en MXN (mediana) | Rango P5–P95 (MXN) | P(MXN se devalúa ≥10%) |")
    a("|---|---:|---:|---:|---:|")
    for years, h in [(1, yr1), (3, yr3), (5, yr5)]:
        usd_grown = cap * ((1 + yield_recommended_apy) ** years)
        median_mxn = usd_grown * h["usdmxn_p50"]
        p5_mxn = usd_grown * h["usdmxn_p5"]
        p95_mxn = usd_grown * h["usdmxn_p95"]
        a(f"| {years} año{'s' if years > 1 else ''} "
          f"| ${h['usdmxn_p50']:.2f} "
          f"| **${median_mxn:,.0f} MXN** "
          f"| ${p5_mxn:,.0f} – ${p95_mxn:,.0f} "
          f"| {_fmt_pct(h['prob_dev_10'])} |")
    a("")
    a("> **Lectura rápida:** el USDT te protege contra la devaluación del peso (la palanca más ")
    a("> grande del retorno en MXN); el yield USD añade un colchón adicional. La sección 3 ")
    a("> compara estrategias específicas de yield (Aave, Compound, lending privado, x2o ")
    a("> stablecoin) y su retorno en términos MXN.")
    a("")
    a("---")
    a("")

    # ── 1. Comparativa historica ──
    a("## 1. USDT vs BTC vs ETH vs MXN — comparativa histórica")
    a("")
    a(f"Periodo: **{common_period['start']} a {common_period['end']}**. Métricas anualizadas ")
    a("calculadas sobre retornos diarios. *MXN-cash* = mantener pesos en efectivo (sin yield ")
    a("ni inflación). *USDT (USD)* = mantener USDT en wallet, sin yield (preserva valor en USD ")
    a("pero pierde poder adquisitivo en MXN si el dólar se debilita). *USDT (MXN)* = USDT ")
    a("medido en pesos = curva del USDMXN exactamente.")
    a("")
    a(_md_table(historic_stats))
    a("")
    a(f"![Crecimiento acumulado — $1 invertido en cada activo]({img_dir}/01_crecimiento_acumulado.png)")
    a("")
    a(f"![Drawdowns históricos]({img_dir}/02_drawdowns.png)")
    a("")
    a("> **Lectura:** BTC y ETH dominan en retorno, pero con drawdowns superiores al 70%. ")
    a("> USDT en términos MXN entrega un retorno menor pero con volatilidad mucho más baja — ")
    a("> es la opción de **preservación de valor** para un inversor que vive en pesos. MXN-cash ")
    a("> es lo que pierdes por no diversificar fuera del peso.")
    a("")
    a("---")
    a("")

    # ── 2. Monte Carlo USDMXN ──
    a("## 2. Predicción Monte Carlo del tipo de cambio USD/MXN")
    a("")
    a("Simulación Bootstrap-KDE sobre retornos diarios históricos del USDMXN (Banxico, ")
    a("2001–2026), 5,000 trayectorias por horizonte. Esto te da la distribución completa de ")
    a("dónde puede estar el peso a futuro, no un solo número.")
    a("")
    a("| Horizonte | USDMXN actual | Mediana esperada | P5 | P95 | P(devalúa ≥5%) | P(devalúa ≥10%) | P(devalúa ≥20%) | P(se aprecia ≥5%) |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    fx_now = fx_summary["end_fx"]
    for years in (1, 2, 3, 5):
        h = mc_horizons[years]
        a(f"| {years} año{'s' if years > 1 else ''} "
          f"| ${fx_now:.2f} "
          f"| **${h['usdmxn_p50']:.2f}** "
          f"| ${h['usdmxn_p5']:.2f} "
          f"| ${h['usdmxn_p95']:.2f} "
          f"| {_fmt_pct(h['prob_dev_5'])} "
          f"| {_fmt_pct(h['prob_dev_10'])} "
          f"| {_fmt_pct(h['prob_dev_20'])} "
          f"| {_fmt_pct(h['prob_apr_5'])} |")
    a("")
    a(f"![Trayectorias simuladas USDMXN]({img_dir}/03_mc_trayectorias.png)")
    a("")
    a(f"![Banda de percentiles USDMXN]({img_dir}/04_mc_percentiles.png)")
    a("")
    a(f"![Distribución terminal del USDMXN a 1/3/5 años]({img_dir}/05_mc_terminal.png)")
    a("")
    a("> **Cómo leer P5/P95:** en el 5% peor de los escenarios el dólar termina por debajo del P5; ")
    a("> en el 5% mejor termina por encima del P95. La mediana (P50) es el camino más probable. ")
    a("> El bootstrap captura toda la cola de la distribución histórica de retornos, sin asumir ")
    a("> normalidad.")
    a("")
    a("---")
    a("")

    # ── 3. Estrategias de yield ──
    a("## 3. Estrategias de yield sobre USDT")
    a("")
    a("Cinco estrategias posibles para generar yield sobre tu USDT, con su yield USD anual ")
    a("típico y el retorno MXN-equivalente proyectado a 1 año (combinando yield USD con ")
    a("apreciación esperada del USDMXN, mediana / P5 / P95).")
    a("")
    a(_md_table(yield_scenarios))
    a("")
    a("> **Lectura:** el retorno MXN-equivalente combina dos efectos: el **yield USD** que ganas ")
    a("> por prestar tus USDT, y la **apreciación del USDMXN** mientras los tienes. Históricamente ")
    a("> el segundo efecto ha sido el dominante en horizontes largos para inversores en pesos.")
    a("")
    a(f"![Comparación de retorno MXN por estrategia (1 año)]({img_dir}/06_yield_strategies.png)")
    a("")
    a("---")
    a("")

    # ── 4. Conclusiones ──
    a("## 4. Conclusiones")
    a("")
    a("1. **USDT como cobertura de devaluación:** para un inversor mexicano, mantener capital ")
    a("   en USDT es principalmente una apuesta a la depreciación del peso. Históricamente el ")
    a(f"   USDMXN se ha apreciado {_fmt_pct(fx_summary['total_appreciation'])} acumulado desde "
      f"{fx_summary['start_date']}; eso es retorno puro en pesos sin necesidad de yield.")
    a("2. **Yield añade pero no domina:** un yield USD de 5–15% añade valor, pero el grueso del ")
    a("   retorno MXN viene del FX. La elección de protocolo importa menos que la decisión de ")
    a("   dolarizarse.")
    a("3. **vs BTC/ETH:** USDT entrega menor retorno que las cripto volátiles pero **sin los ")
    a("   drawdowns >70%** que destruyen capital. Para el prestamista que ya tiene exposición ")
    a("   a BTC/ETH vía colateral, USDT es la pata estable del balance.")
    a("4. **vs MXN-cash:** mantener pesos en efectivo es la peor opción histórica; pierdes ")
    a("   retorno tanto por inflación como por devaluación. CETES da yield en pesos pero no ")
    a("   te protege contra devaluación.")
    a("5. **Recomendación operativa:** asignar una porción significativa del balance ")
    a("   operativo a USDT con yield (Aave / Compound / lending privado), reservando MXN ")
    a("   solo para necesidades operativas inmediatas.")
    a("")
    a("---")
    a("")

    # ── Anexo ──
    a("## Anexo A — Metodología")
    a("")
    a("- **Datos:** USDMXN diario de Banxico (serie SF63528, 2001-01-03 a hoy). BTC/ETH ")
    a("  minutales de Binance, resampleados a frecuencia diaria para fair comparison.")
    a("- **Periodo común:** se usa la intersección de los tres datasets para las métricas ")
    a("  comparativas (sección 1).")
    a("- **Retornos:** logarítmicos diarios, anualizados con factor √252.")
    a("- **Max Drawdown:** máxima caída desde un peak hasta el siguiente trough en el periodo.")
    a("- **Monte Carlo USDMXN:** Bootstrap-KDE sobre retornos diarios. Por cada horizonte ")
    a("  (1/2/3/5 años) se simulan 5,000 trayectorias de longitud H días, donde cada paso ")
    a("  diario se construye sampleando un retorno histórico al azar y añadiendo ruido KDE ")
    a("  (Silverman). Captura colas pesadas y eventos extremos sin asumir distribución normal.")
    a("- **Yields USD:** rangos típicos observados en protocolos DeFi (Aave, Compound) y ")
    a("  mercados de lending centralizado para USDT/USDC. Los valores son de referencia y ")
    a("  varían con la demanda.")
    a("")
    a("---")
    a(f"*Generado automáticamente por usdt_analysis.ipynb — {pd.Timestamp.now().strftime('%Y-%m-%d')}*")

    report_path = "reporte_usdt_analysis.md"
    with open(report_path, "w") as f:
        f.write("\n".join(L))

    print(f"Reporte generado: {report_path} ({len(L)} líneas)")
    return report_path
