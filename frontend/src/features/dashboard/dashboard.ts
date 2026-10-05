import * as echarts from 'echarts';
import type { ECharts, EChartsOption } from 'echarts';
import { parseAgentSignalsEnvelope, updateAgentSignals, type AgentSignalsPanelElement } from '../../lib/agent-signals/dom';

type DashboardSummary = {
  conversations: number; opportunities: number; won: number; conversion_rate: number;
  revenue_minor: number; recovered_sales: number; recovery_opportunities: number;
  recovery_rate: number; recovered_revenue_minor: number;
};
type TrendPoint = { date: string; opportunities: number; won: number; conversion_rate: number };
type RecoveryPoint = { date: string; recovery_rate: number };
type MetricDelta = { current: number; previous: number; absolute: number; percentage: number | null };
type DashboardPayload = {
  summary: DashboardSummary;
  conversion_trend: TrendPoint[];
  recovery_trend: RecoveryPoint[];
  lost_reasons: Array<{ reason: string; count: number; percentage: number }>;
  objections: { total: number; resolved: number; resolution_rate: number };
  objection_categories: Array<{ category: string; resolved: number; unresolved: number; resolution_rate: number }>;
  products: Array<{ product_id: string; product_name: string; interested_count: number; won_count: number; conversion_rate: number }>;
  comparison?: { revenue_minor: MetricDelta; won: MetricDelta; conversion_rate: MetricDelta; recovered_revenue_minor: MetricDelta; recovery_rate: MetricDelta };
  metric_trend: Array<{ date: string; conversations: number; opportunities: number; won: number; revenue_minor: number; recovered_sales: number; recovered_revenue_minor: number }>;
  funnel: Array<{ stage: string; value: number; percentage: number }>;
  objection_product_heatmap: Array<{ category: string; product_name: string; count: number; resolved: number; resolution_rate: number }>;
  agent_signals?: { sample_count: number; signals: Record<string, unknown> };
};

const C = { graphite: '#414141', amber: '#f7c974', cream: '#faeccf', paper: '#f8f8f8', white: '#f8f8f8', graphite42: 'rgba(65,65,65,.42)', graphite10: 'rgba(65,65,65,.10)' };
const money = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
const integer = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 });
const percent = (value: number): string => `${Number(value || 0).toFixed(1).replace('.', ',')}%`;
const numberValue = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) ? value : 0;
const formatDate = (value: string): string => new Date(value).toLocaleDateString('es-CO', { day: '2-digit', month: 'short' });

function parsePayload(value: unknown): DashboardPayload {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid dashboard payload');
  const root = value as Record<string, unknown>;
  const summary = root.summary;
  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) throw new Error('Invalid dashboard summary');
  const source = summary as Record<string, unknown>;
  const records = (key: string): Record<string, unknown>[] => {
    const raw = root[key];
    if (raw === undefined) return [];
    if (!Array.isArray(raw) || raw.some((item) => !item || typeof item !== 'object' || Array.isArray(item))) throw new Error(`Invalid dashboard ${key}`);
    return raw as Record<string, unknown>[];
  };
  const objections = root.objections;
  if (objections !== undefined && (!objections || typeof objections !== 'object' || Array.isArray(objections))) throw new Error('Invalid dashboard objections');
  const objectionSource = (objections ?? {}) as Record<string, unknown>;
  return {
    summary: {
      conversations: numberValue(source.conversations), opportunities: numberValue(source.opportunities), won: numberValue(source.won), conversion_rate: numberValue(source.conversion_rate), revenue_minor: numberValue(source.revenue_minor), recovered_sales: numberValue(source.recovered_sales), recovery_opportunities: numberValue(source.recovery_opportunities), recovery_rate: numberValue(source.recovery_rate), recovered_revenue_minor: numberValue(source.recovered_revenue_minor),
    },
    conversion_trend: records('conversion_trend') as TrendPoint[],
    recovery_trend: records('recovery_trend') as RecoveryPoint[],
    lost_reasons: records('lost_reasons') as DashboardPayload['lost_reasons'],
    objections: { total: numberValue(objectionSource.total), resolved: numberValue(objectionSource.resolved), resolution_rate: numberValue(objectionSource.resolution_rate) },
    objection_categories: records('objection_categories') as DashboardPayload['objection_categories'],
    products: records('products') as DashboardPayload['products'],
    comparison: root.comparison as DashboardPayload['comparison'] | undefined,
    metric_trend: records('metric_trend') as DashboardPayload['metric_trend'],
    funnel: records('funnel') as DashboardPayload['funnel'],
    objection_product_heatmap: records('objection_product_heatmap') as DashboardPayload['objection_product_heatmap'],
    agent_signals: root.agent_signals && typeof root.agent_signals === 'object' && !Array.isArray(root.agent_signals)
      ? root.agent_signals as DashboardPayload['agent_signals']
      : undefined,
  };
}

function element(id: string): HTMLElement | null { return document.getElementById(id); }
function setText(id: string, value: string): void { const node = element(id); if (node) node.textContent = value; }
function show(id: string, visible: boolean): void { const node = element(id); if (node) node.hidden = !visible; }
function setProgress(id: string, value: number): void {
  const node = element(id);
  if (node) node.style.width = `${Math.min(100, Math.max(0, numberValue(value)))}%`;
}
function setDelta(id: string, metric: MetricDelta | undefined, formatter: (value: number) => string = percent): void {
  const node = element(id);
  if (!node || !metric) return;
  const sign = metric.absolute > 0 ? '+' : '';
  node.textContent = metric.percentage === null ? 'Sin período anterior' : `${sign}${formatter(metric.percentage)} vs período anterior`;
  node.classList.toggle('is-positive', metric.absolute > 0);
  node.classList.toggle('is-negative', metric.absolute < 0);
}
function setInsight(id: string, text: string): void { setText(id, text); }

const charts = new Map<string, ECharts>();
let chartMountFrame = 0;
let chartMountGeneration = 0;

function escapeHtml(value: unknown): string { return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] ?? char)); }
function tooltip(trigger: 'axis' | 'item' = 'item'): EChartsOption['tooltip'] {
  return {
    trigger,
    confine: true,
    enterable: false,
    triggerOn: 'mousemove',
    alwaysShowContent: false,
    hideDelay: 0,
    transitionDuration: .12,
    renderMode: 'html',
    backgroundColor: C.graphite,
    borderWidth: 0,
    padding: [10, 13],
    textStyle: { color: C.paper, fontFamily: 'Urbanist', fontSize: 12, fontWeight: 600 },
    extraCssText: 'width:max-content;height:auto;min-height:0;max-height:none;max-width:min(320px,calc(100vw - 32px));border-radius:14px;box-shadow:0 8px 24px rgba(65,65,65,.16);white-space:normal;pointer-events:none;',
    position: (point, _params, _dom, _rect, size) => {
      const gap = 12;
      const [x, y] = point;
      const [width, height] = size.contentSize;
      const [viewWidth, viewHeight] = size.viewSize;
      const left = x + gap + width <= viewWidth ? x + gap : x - width - gap;
      const top = Math.max(6, Math.min(y - height / 2, viewHeight - height - 6));
      return [left, top];
    },
  };
}
function cartesian(dark = false): Pick<EChartsOption, 'grid' | 'xAxis' | 'yAxis'> {
  const text = dark ? 'rgba(248,248,248,.55)' : C.graphite42; const axis = dark ? 'rgba(248,248,248,.12)' : C.graphite10;
  return { grid: { left: 48, right: 22, top: 36, bottom: 34, containLabel: true }, xAxis: { axisLine: { lineStyle: { color: axis } }, axisTick: { show: false }, axisLabel: { color: text, fontFamily: 'Urbanist', fontSize: 11, fontWeight: 600 } }, yAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: text, fontFamily: 'Urbanist', fontSize: 11, fontWeight: 600 }, splitLine: { lineStyle: { color: axis } } } };
}
function mount(id: string, option: EChartsOption): void {
  const host = element(id); if (!host) return;
  charts.get(id)?.dispose(); charts.delete(id);
  const chart = echarts.init(host, undefined, { renderer: 'canvas' }); chart.setOption(option); charts.set(id, chart);
}

function mountGradually(entries: Array<[string, EChartsOption]>): void {
  window.cancelAnimationFrame(chartMountFrame);
  const generation = ++chartMountGeneration;
  let index = 0;

  const mountNext = (): void => {
    if (generation !== chartMountGeneration || index >= entries.length) return;
    const [id, option] = entries[index++];
    mount(id, option);
    chartMountFrame = window.requestAnimationFrame(mountNext);
  };

  chartMountFrame = window.requestAnimationFrame(mountNext);
}

function lineOption(points: Array<{ date: string; value: number }>, label: string, color = C.amber): EChartsOption {
  const base = cartesian();
  const values = points.map((point) => numberValue(point.value));
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 100;
  const range = maximum - minimum;
  const padding = Math.max(2, range * .18);
  const axisMin = values.length ? Math.max(0, Math.floor(minimum - padding)) : 0;
  const axisMax = values.length ? Math.min(100, Math.ceil((maximum + (range ? padding : 10)) / 5) * 5) : 100;
  return { animationDuration: 700, tooltip: tooltip('axis'), graphic: points.length ? undefined : { type: 'text', left: 'center', top: 'middle', style: { text: 'Sin datos para este período', fill: C.graphite42, font: '600 12px Urbanist' } }, grid: { ...base.grid, left: 38, right: 18, top: 28, bottom: 28, containLabel: true }, xAxis: { ...base.xAxis, type: 'category', boundaryGap: false, data: points.map((point) => formatDate(point.date)) }, yAxis: { ...base.yAxis, type: 'value', min: axisMin, max: axisMax, axisLabel: { ...base.yAxis?.axisLabel, formatter: '{value}%' } }, series: [{ name: label, type: 'line', smooth: true, connectNulls: true, symbol: 'circle', symbolSize: 8, lineStyle: { width: 4, color }, itemStyle: { color, borderColor: C.white, borderWidth: 3 }, data: values }] };
}
function horizontalBarOption(items: Array<{ label: string; value: number }>, label: string): EChartsOption {
  const base = cartesian();
  return { animationDuration: 650, tooltip: { ...tooltip('axis'), axisPointer: { type: 'none' }, formatter: (params) => { const rows = Array.isArray(params) ? params : [params]; const index = Number(rows[0]?.dataIndex ?? 0); const item = items[index]; return `<b>${escapeHtml(item?.label)}</b><br>Casos: ${numberValue(item?.value)}`; } }, grid: { ...base.grid, left: 8, right: 30, top: 12, bottom: 12, containLabel: true }, xAxis: { ...base.xAxis, type: 'value', splitLine: { show: false }, axisLabel: { show: false } }, yAxis: { ...base.yAxis, type: 'category', inverse: true, data: items.map((item) => item.label), splitLine: { show: false }, axisLabel: { ...base.yAxis?.axisLabel, color: C.graphite, margin: 12 } }, series: [{ name: label, type: 'bar', data: items.map((item) => item.value), barMaxWidth: 18, itemStyle: { color: C.graphite, borderRadius: 99 }, label: { show: true, position: 'right', color: C.graphite, fontFamily: 'Urbanist', fontWeight: 800 } }] };
}
function recoveryOption(summary: DashboardSummary): EChartsOption {
  const recovered = numberValue(summary.recovered_sales);
  const started = numberValue(summary.recovery_opportunities);
  const pending = Math.max(0, started - recovered);
  const hasData = started > 0;
  return {
    animationDuration: 700,
    tooltip: tooltip('item'),
    graphic: hasData ? [
      { type: 'text', left: 'center', top: '38%', style: { text: percent(summary.recovery_rate), fill: C.graphite, font: '800 30px Urbanist', textAlign: 'center' } },
      { type: 'text', left: 'center', top: '52%', style: { text: 'recuperadas', fill: C.graphite42, font: '700 11px Urbanist', textAlign: 'center' } },
    ] : { type: 'text', left: 'center', top: 'middle', style: { text: 'Sin datos para este período', fill: C.graphite42, font: '600 12px Urbanist' } },
    series: [{
      type: 'pie',
      radius: ['58%', '78%'],
      center: ['50%', '46%'],
      startAngle: 90,
      label: { show: false },
      itemStyle: { borderColor: C.paper, borderWidth: 5, borderRadius: 99 },
      data: hasData ? [
        { name: 'Recuperadas', value: recovered, itemStyle: { color: C.amber } },
        { name: 'Pendientes', value: pending, itemStyle: { color: C.cream } },
      ] : [{ name: 'Sin datos', value: 1, itemStyle: { color: C.cream } }],
    }],
  };
}
function sparklineOption(values: number[], color = C.amber): EChartsOption {
  return { animationDuration: 500, grid: { left: 2, right: 2, top: 4, bottom: 4 }, xAxis: { type: 'category', show: false, data: values.map((_, index) => index) }, yAxis: { type: 'value', show: false, scale: true }, tooltip: { show: false }, series: [{ type: 'line', data: values, smooth: true, showSymbol: false, lineStyle: { width: 2, color }, areaStyle: { color, opacity: .16 } }] };
}
function funnelOption(items: DashboardPayload['funnel']): EChartsOption {
  return { animationDuration: 700, tooltip: tooltip('item'), series: [{ type: 'funnel', left: '8%', right: '8%', top: 10, bottom: 10, min: 0, max: Math.max(1, items[0]?.value ?? 0), minSize: '12%', maxSize: '100%', sort: 'descending', gap: 4, label: { show: true, position: 'inside', color: C.graphite, fontFamily: 'Urbanist', fontWeight: 800, formatter: (params) => `${params.name}  ${params.value}` }, labelLine: { show: false }, itemStyle: { borderWidth: 0, borderRadius: 10 }, data: items.map((item, index) => ({ name: item.stage, value: item.value, itemStyle: { color: [C.graphite, C.cream, C.amber, C.cream, C.amber][index % 5] }, label: { color: index % 5 === 0 ? C.paper : C.graphite } })) }] };
}
function objectionProductHeatmapOption(items: DashboardPayload['objection_product_heatmap']): EChartsOption {
  const categories = [...new Set(items.map((item) => item.category))];
  const products = [...new Set(items.map((item) => item.product_name))];
  const maximum = Math.max(1, ...items.map((item) => item.count));
  return { animationDuration: 600, tooltip: { ...tooltip('item'), formatter: (params) => { const value = Array.isArray(params.value) ? params.value : []; return `${escapeHtml(products[Number(value[1])])}<br><b>${escapeHtml(categories[Number(value[0])])}</b>: ${numberValue(value[2])}`; } }, grid: { top: 12, right: 56, bottom: 38, left: 34, containLabel: false }, xAxis: { type: 'category', data: categories, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: C.graphite42, fontFamily: 'Urbanist', fontSize: 10, fontWeight: 700, interval: 0 } }, yAxis: { type: 'category', data: products, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: C.graphite42, fontFamily: 'Urbanist', fontSize: 10, fontWeight: 700, rotate: 25, width: 32, overflow: 'truncate', margin: 2 } }, visualMap: { min: 0, max: maximum, calculable: false, orient: 'vertical', right: 0, top: 'center', text: ['Alta', 'Baja'], textStyle: { color: C.graphite42, fontFamily: 'Urbanist', fontSize: 9, fontWeight: 700 }, inRange: { color: [C.paper, C.cream, C.amber, C.graphite] }, itemWidth: 9, itemHeight: 90 }, series: [{ type: 'heatmap', data: items.map((item) => [categories.indexOf(item.category), products.indexOf(item.product_name), item.count]), label: { show: true, color: C.graphite, fontFamily: 'Urbanist', fontSize: 10, fontWeight: 800 }, itemStyle: { borderColor: C.paper, borderWidth: 3 } }] };
}
function objectionOption(items: DashboardPayload['objection_categories']): EChartsOption {
  const base = cartesian();
  return { animationDuration: 650, tooltip: { ...tooltip('axis'), axisPointer: { type: 'shadow' }, formatter: (params) => { const rows = Array.isArray(params) ? params : [params]; const index = Number(rows[0]?.dataIndex ?? 0); const item = items[index]; return `<b>${escapeHtml(item?.category)}</b><br>Tasa resuelta: ${percent(item?.resolution_rate ?? 0)}<br>Resueltas: ${item?.resolved ?? 0}<br>Sin resolver: ${item?.unresolved ?? 0}`; } }, legend: { top: 0, right: 10, icon: 'circle', textStyle: { color: C.graphite42, fontFamily: 'Urbanist', fontSize: 10, fontWeight: 700 } }, grid: { ...base.grid, top: 36, left: 42, right: 20 }, xAxis: { ...base.xAxis, type: 'category', data: items.map((item) => item.category) }, yAxis: { ...base.yAxis, type: 'value', min: 0, max: 100, axisLabel: { ...base.yAxis?.axisLabel, formatter: '{value}%' } }, series: [{ name: 'Resueltas', type: 'bar', stack: 'resolution', data: items.map((item) => item.resolution_rate), barWidth: 28, itemStyle: { color: C.amber } }, { name: 'Sin resolver', type: 'bar', stack: 'resolution', data: items.map((item) => 100 - item.resolution_rate), itemStyle: { color: C.cream, borderRadius: [10, 10, 0, 0] } }] };
}
function productsOption(items: DashboardPayload['products']): EChartsOption {
  const base = cartesian();
  return { animationDuration: 650, tooltip: { ...tooltip('axis'), axisPointer: { type: 'shadow' }, formatter: (params) => { const rows = Array.isArray(params) ? params : [params]; const index = Number(rows[0]?.dataIndex ?? 0); const item = items[index]; return `<b>${escapeHtml(item?.product_name)}</b><br>Interesados: ${item?.interested_count ?? 0}<br>Ventas: ${item?.won_count ?? 0}<br>Conversión: ${percent(item?.conversion_rate ?? 0)}`; } }, legend: { top: 0, right: 10, icon: 'circle', textStyle: { color: C.graphite42, fontFamily: 'Urbanist', fontSize: 10, fontWeight: 700 } }, grid: { ...base.grid, top: 36, left: 18, right: 50 }, xAxis: { ...base.xAxis, type: 'value', splitLine: { show: false }, axisLabel: { show: false } }, yAxis: { ...base.yAxis, type: 'category', inverse: true, data: items.map((item) => item.product_name), splitLine: { show: false }, axisLabel: { ...base.yAxis?.axisLabel, color: C.graphite, margin: 18 } }, series: [{ name: 'Interés', type: 'bar', data: items.map((item) => item.interested_count), barWidth: 12, itemStyle: { color: C.cream, borderRadius: 99 } }, { name: 'Ventas', type: 'bar', data: items.map((item) => item.won_count), barWidth: 12, itemStyle: { color: C.amber, borderRadius: 99 }, label: { show: true, position: 'right', formatter: (params) => percent(items[Number(params.dataIndex)]?.conversion_rate ?? 0), color: C.graphite, fontFamily: 'Urbanist', fontWeight: 800 } }] };
}
function buildInsights(payload: DashboardPayload): void {
  const conversion = payload.conversion_trend.map((point) => numberValue(point.conversion_rate));
  const conversionText = conversion.length < 2
    ? 'La gráfica necesita al menos dos días con oportunidades para explicar una tendencia.'
    : `La conversión pasó de ${percent(conversion[0])} a ${percent(conversion[conversion.length - 1])}. Úsala para identificar días que merecen revisar conversaciones, no como una explicación causal por sí sola.`;
  setInsight('dashboardConversionInsight', conversionText);

  const recoveryText = payload.summary.recovery_opportunities > 0
    ? `De ${integer.format(payload.summary.recovery_opportunities)} oportunidades con seguimiento, ${integer.format(payload.summary.recovered_sales)} volvieron a cerrar. El porcentaje central resume la eficacia del seguimiento, no el volumen total del pipeline.`
    : 'Todavía no hay oportunidades con seguimiento de recuperación en este período.';
  setInsight('dashboardRecoveryInsight', recoveryText);

  const funnel = payload.funnel;
  let funnelText = 'El flujo todavía no tiene datos suficientes para señalar una caída.';
  if (funnel.length > 1 && funnel[0].value > 0) {
    const largestDrop = funnel.slice(1).reduce((best, item, index) => {
      const previous = funnel[index].value;
      const drop = previous - item.value;
      return drop > best.drop ? { from: funnel[index].stage, to: item.stage, drop } : best;
    }, { from: '', to: '', drop: -1 });
    funnelText = largestDrop.drop > 0
      ? `La mayor pérdida de volumen aparece entre ${largestDrop.from.toLowerCase()} y ${largestDrop.to.toLowerCase()}. Ese tramo indica dónde conviene investigar el proceso y las conversaciones.`
      : 'El flujo no muestra una caída dominante; revisa el detalle por producto y objeción para encontrar diferencias.';
  }
  setInsight('dashboardFunnelInsight', funnelText);

  const topLoss = payload.lost_reasons[0];
  setInsight('dashboardLossesInsight', topLoss
    ? `La razón más frecuente es “${topLoss.reason}”, con ${integer.format(topLoss.count)} casos (${percent(topLoss.percentage)} del total de pérdidas). Es el primer lugar donde buscar una intervención.`
    : 'No hay pérdidas clasificadas en este período.');

  const topObjection = [...payload.objection_categories].sort((a, b) => b.resolved + b.unresolved - (a.resolved + a.unresolved))[0];
  setInsight('dashboardObjectionsInsight', topObjection
    ? `“${topObjection.category}” concentra ${integer.format(topObjection.resolved + topObjection.unresolved)} menciones y resuelve el ${percent(topObjection.resolution_rate)}. La oportunidad está en entender qué respuestas funcionan cuando no se resuelve.`
    : 'No hay objeciones clasificadas en este período.');

  const topProduct = [...payload.products].sort((a, b) => b.conversion_rate - a.conversion_rate)[0];
  setInsight('dashboardProductsInsight', topProduct
    ? `“${topProduct.product_name}” lidera la conversión con ${percent(topProduct.conversion_rate)}. Compara esa tasa con el volumen de interés para distinguir eficiencia de popularidad.`
    : 'No hay intereses de producto registrados en este período.');

  const topHeat = [...payload.objection_product_heatmap].sort((a, b) => b.count - a.count)[0];
  setInsight('dashboardHeatmapInsight', topHeat
    ? `La combinación más frecuente es “${topHeat.category}” en ${topHeat.product_name}, con ${integer.format(topHeat.count)} menciones. Es una señal para revisar ese argumento comercial específico.`
    : 'No hay suficiente relación entre objeciones y productos para mostrar concentración.');
}

function renderDashboard(payload: DashboardPayload): void {
  const { summary } = payload;
  setText('dashboardRevenue', money.format(summary.revenue_minor)); setText('dashboardWon', integer.format(summary.won)); setText('dashboardConversion', percent(summary.conversion_rate)); setText('dashboardRecoveredRevenue', money.format(summary.recovered_revenue_minor));
  setText('dashboardConversations', integer.format(summary.conversations)); setText('dashboardRecoveredSales', integer.format(summary.recovered_sales)); setText('dashboardRecoveryRate', percent(summary.recovery_rate)); setProgress('dashboardRecoveryMeter', summary.recovery_rate);
  setDelta('dashboardRevenueDelta', payload.comparison?.revenue_minor, (value) => `${value.toFixed(1)}%`);
  setDelta('dashboardWonDelta', payload.comparison?.won, (value) => `${value.toFixed(1)}%`);
  setDelta('dashboardConversionDelta', payload.comparison?.conversion_rate, (value) => `${value.toFixed(1)} pp`);
  setDelta('dashboardRecoveredRevenueDelta', payload.comparison?.recovered_revenue_minor, (value) => `${value.toFixed(1)}%`);
  const agentSignals = parseAgentSignalsEnvelope(payload.agent_signals, true);
  const signalsSection = element('dashboardAgentSignalsSection');
  const signalsPanel = element('dashboardAgentSignals') as AgentSignalsPanelElement | null;
  if (signalsSection) signalsSection.hidden = !agentSignals;
  if (agentSignals && signalsPanel) {
    signalsPanel.resetSignalHistory?.();
    updateAgentSignals(signalsPanel, agentSignals);
    setText(
      'dashboardAgentSignalsSample',
      `${integer.format(agentSignals.sampleCount ?? 0)} ${(agentSignals.sampleCount ?? 0) === 1 ? 'llamada' : 'llamadas'}`,
    );
  }
  buildInsights(payload);
  const hasData = Boolean(agentSignals) || summary.conversations > 0 || summary.opportunities > 0 || payload.lost_reasons.length > 0 || payload.products.length > 0 || payload.objections.total > 0;
  show('dashboardData', hasData); show('dashboardEmpty', !hasData); show('dashboardLoading', false); show('dashboardError', false);
  if (!hasData) {
    window.cancelAnimationFrame(chartMountFrame);
    chartMountGeneration++;
    charts.forEach((chart) => chart.dispose());
    charts.clear();
    return;
  }
   mountGradually([
     ['dashboardConversionChart', lineOption(payload.conversion_trend.map((point) => ({ date: point.date, value: point.conversion_rate })), 'Conversión')],
     ['dashboardRecoveryChart', recoveryOption(summary)],
     ['dashboardLossesChart', horizontalBarOption(payload.lost_reasons.map((item) => ({ label: item.reason, value: item.count })), 'Pérdidas')],
     ['dashboardObjectionsChart', objectionOption(payload.objection_categories)],
     ['dashboardProductsChart', productsOption(payload.products)],
     ['dashboardRevenueSparkline', sparklineOption(payload.metric_trend.map((point) => point.revenue_minor), C.amber)],
     ['dashboardWonSparkline', sparklineOption(payload.metric_trend.map((point) => point.won), C.graphite)],
     ['dashboardConversionSparkline', sparklineOption(payload.metric_trend.map((point) => point.opportunities ? point.won * 100 / point.opportunities : 0), C.graphite)],
     ['dashboardRecoveredRevenueSparkline', sparklineOption(payload.metric_trend.map((point) => point.recovered_revenue_minor), C.amber)],
     ['dashboardFunnelChart', funnelOption(payload.funnel)],
     ['dashboardObjectionProductChart', objectionProductHeatmapOption(payload.objection_product_heatmap)],
   ]);
   /*
     The charts are intentionally mounted one per frame. ECharts initialization
     is the expensive part of this view; spreading it avoids a main-thread spike
     while the login wipe is still moving.
   */
}

export function bootDashboard(apiUrl: string, token: string): () => void {
  const fromInput = element('dashboardFrom') as HTMLInputElement | null; const toInput = element('dashboardTo') as HTMLInputElement | null; const today = new Date(); const fromDate = new Date(today); fromDate.setDate(today.getDate() - 30); const isoDate = (value: Date): string => value.toISOString().slice(0, 10);
  if (fromInput && !fromInput.value) fromInput.value = isoDate(fromDate); if (toInput && !toInput.value) toInput.value = isoDate(today);
  let disposed = false;
  let requestId = 0;
  let activeController: AbortController | null = null;
  let resizeFrame = 0;
  const load = async (): Promise<void> => {
    const currentRequest = ++requestId;
    activeController?.abort();
    const controller = new AbortController();
    activeController = controller;
    show('dashboardLoading', true); show('dashboardData', false); show('dashboardEmpty', false); show('dashboardError', false);
    const query = new URLSearchParams(); if (fromInput?.value) query.set('from', fromInput.value); if (toInput?.value) query.set('to', toInput.value);
    try {
      const response = await fetch(`${apiUrl}/analytics/dashboard?${query}`, { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal });
      if (!response.ok) throw new Error(`dashboard ${response.status}`);
      const payload = parsePayload(await response.json());
      if (disposed || currentRequest !== requestId) return;
      renderDashboard(payload);
    } catch (error) {
      if (controller.signal.aborted || disposed || currentRequest !== requestId) return;
      show('dashboardLoading', false); show('dashboardData', false); show('dashboardEmpty', false); show('dashboardError', true);
    }
  };
  const applyButton = element('dashboardApply'); const retryButton = element('dashboardRetry');
  const onApply = (): void => { void load(); }; const onRetry = (): void => { void load(); };
  applyButton?.addEventListener('click', onApply); retryButton?.addEventListener('click', onRetry);
  const resizeCharts = (): void => {
    if (resizeFrame || disposed) return;
    resizeFrame = requestAnimationFrame(() => { resizeFrame = 0; if (!disposed) charts.forEach((chart) => chart.resize()); });
  };
  const resizeObserver = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resizeCharts);
  document.querySelectorAll<HTMLElement>('.dashboard-chart-host, .dashboard-sparkline').forEach((host) => resizeObserver?.observe(host));
  window.addEventListener('resize', resizeCharts, { passive: true });
  window.addEventListener('orientationchange', resizeCharts, { passive: true });
  void load();
   return () => {
     disposed = true;
     window.cancelAnimationFrame(chartMountFrame);
     chartMountGeneration++;
     requestId++;
    activeController?.abort();
    activeController = null;
    resizeObserver?.disconnect();
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    resizeFrame = 0;
    applyButton?.removeEventListener('click', onApply);
    retryButton?.removeEventListener('click', onRetry);
    window.removeEventListener('resize', resizeCharts);
    window.removeEventListener('orientationchange', resizeCharts);
    charts.forEach((chart) => chart.dispose());
    charts.clear();
  };
}
