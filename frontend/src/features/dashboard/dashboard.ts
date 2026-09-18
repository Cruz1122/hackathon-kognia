import * as echarts from 'echarts';
import type { ECharts, EChartsOption } from 'echarts';

type DashboardSummary = {
  conversations: number; opportunities: number; won: number; conversion_rate: number;
  revenue_minor: number; recovered_sales: number; recovery_opportunities: number;
  recovery_rate: number; recovered_revenue_minor: number;
};
type TrendPoint = { date: string; opportunities: number; won: number; conversion_rate: number };
type RecoveryPoint = { date: string; recovery_rate: number };
type DashboardPayload = {
  summary: DashboardSummary;
  conversion_trend: TrendPoint[];
  recovery_trend: RecoveryPoint[];
  lost_reasons: Array<{ reason: string; count: number; percentage: number }>;
  objections: { total: number; resolved: number; resolution_rate: number };
  objection_categories: Array<{ category: string; resolved: number; unresolved: number; resolution_rate: number }>;
  products: Array<{ product_id: string; product_name: string; interested_count: number; won_count: number; conversion_rate: number }>;
};

const C = { graphite: '#414141', amber: '#f7c974', cream: '#faeccf', paper: '#f8f8f8', white: '#fff', graphite42: 'rgba(65,65,65,.42)', graphite10: 'rgba(65,65,65,.10)' };
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
  return {
    summary: {
      conversations: numberValue(source.conversations), opportunities: numberValue(source.opportunities), won: numberValue(source.won), conversion_rate: numberValue(source.conversion_rate), revenue_minor: numberValue(source.revenue_minor), recovered_sales: numberValue(source.recovered_sales), recovery_opportunities: numberValue(source.recovery_opportunities), recovery_rate: numberValue(source.recovery_rate), recovered_revenue_minor: numberValue(source.recovered_revenue_minor),
    },
    conversion_trend: Array.isArray(root.conversion_trend) ? root.conversion_trend as TrendPoint[] : [],
    recovery_trend: Array.isArray(root.recovery_trend) ? root.recovery_trend as RecoveryPoint[] : [],
    lost_reasons: Array.isArray(root.lost_reasons) ? root.lost_reasons as DashboardPayload['lost_reasons'] : [],
    objections: (root.objections && typeof root.objections === 'object' ? root.objections : {}) as DashboardPayload['objections'],
    objection_categories: Array.isArray(root.objection_categories) ? root.objection_categories as DashboardPayload['objection_categories'] : [],
    products: Array.isArray(root.products) ? root.products as DashboardPayload['products'] : [],
  };
}

function element(id: string): HTMLElement | null { return document.getElementById(id); }
function setText(id: string, value: string): void { const node = element(id); if (node) node.textContent = value; }
function show(id: string, visible: boolean): void { const node = element(id); if (node) node.hidden = !visible; }

const charts = new Map<string, ECharts>();
function tooltip(trigger: 'axis' | 'item' = 'item'): EChartsOption['tooltip'] { return { trigger, backgroundColor: C.graphite, borderWidth: 0, padding: [10, 13], textStyle: { color: C.paper, fontFamily: 'Urbanist', fontSize: 12, fontWeight: 600 }, extraCssText: 'border-radius:14px;box-shadow:none;' }; }
function cartesian(dark = false): Pick<EChartsOption, 'grid' | 'xAxis' | 'yAxis'> {
  const text = dark ? 'rgba(248,248,248,.55)' : C.graphite42; const axis = dark ? 'rgba(248,248,248,.12)' : C.graphite10;
  return { grid: { left: 48, right: 22, top: 36, bottom: 34, containLabel: true }, xAxis: { axisLine: { lineStyle: { color: axis } }, axisTick: { show: false }, axisLabel: { color: text, fontFamily: 'Urbanist', fontSize: 11, fontWeight: 600 } }, yAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: text, fontFamily: 'Urbanist', fontSize: 11, fontWeight: 600 }, splitLine: { lineStyle: { color: axis } } } };
}
function mount(id: string, option: EChartsOption): void {
  const host = element(id); if (!host) return;
  charts.get(id)?.dispose();
  const chart = echarts.init(host, undefined, { renderer: 'canvas' }); chart.setOption(option); charts.set(id, chart);
}

function lineOption(points: Array<{ date: string; value: number }>, label: string, color = C.amber): EChartsOption {
  const base = cartesian();
  return { animationDuration: 700, tooltip: tooltip('axis'), graphic: points.length ? undefined : { type: 'text', left: 'center', top: 'middle', style: { text: 'Sin datos para este período', fill: C.graphite42, font: '600 12px Urbanist' } }, grid: { ...base.grid, left: 42, right: 26, top: 36, bottom: 28, containLabel: true }, xAxis: { ...base.xAxis, type: 'category', boundaryGap: false, data: points.map((point) => formatDate(point.date)) }, yAxis: { ...base.yAxis, type: 'value', min: 0, max: 100, axisLabel: { ...base.yAxis?.axisLabel, formatter: '{value}%' } }, series: [{ name: label, type: 'line', smooth: true, symbol: 'circle', symbolSize: 8, lineStyle: { width: 4, color }, itemStyle: { color, borderColor: C.white, borderWidth: 3 }, data: points.map((point) => point.value) }] };
}
function horizontalBarOption(items: Array<{ label: string; value: number }>, label: string): EChartsOption {
  const base = cartesian();
  return { animationDuration: 650, tooltip: { ...tooltip('axis'), axisPointer: { type: 'none' } }, grid: { ...base.grid, left: 18, right: 40, top: 18, bottom: 18 }, xAxis: { ...base.xAxis, type: 'value', splitLine: { show: false }, axisLabel: { show: false } }, yAxis: { ...base.yAxis, type: 'category', inverse: true, data: items.map((item) => item.label), splitLine: { show: false }, axisLabel: { ...base.yAxis?.axisLabel, color: C.graphite, margin: 18 } }, series: [{ name: label, type: 'bar', data: items.map((item) => item.value), barWidth: 16, itemStyle: { color: C.graphite, borderRadius: 99 }, label: { show: true, position: 'right', color: C.graphite, fontFamily: 'Urbanist', fontWeight: 800 } }] };
}
function objectionOption(items: DashboardPayload['objection_categories']): EChartsOption {
  const base = cartesian();
  return { animationDuration: 650, tooltip: { ...tooltip('axis'), axisPointer: { type: 'shadow' }, formatter: (params) => { const rows = Array.isArray(params) ? params : [params]; const index = Number(rows[0]?.dataIndex ?? 0); const item = items[index]; return `<b>${item?.category ?? ''}</b><br>Resueltas: ${percent(item?.resolution_rate ?? 0)}<br>Resueltas: ${item?.resolved ?? 0}<br>Sin resolver: ${item?.unresolved ?? 0}`; } }, legend: { top: 0, right: 10, icon: 'circle', textStyle: { color: C.graphite42, fontFamily: 'Urbanist', fontSize: 10, fontWeight: 700 } }, grid: { ...base.grid, top: 36, left: 42, right: 20 }, xAxis: { ...base.xAxis, type: 'category', data: items.map((item) => item.category) }, yAxis: { ...base.yAxis, type: 'value', min: 0, max: 100, axisLabel: { ...base.yAxis?.axisLabel, formatter: '{value}%' } }, series: [{ name: 'Resueltas', type: 'bar', stack: 'resolution', data: items.map((item) => item.resolution_rate), barWidth: 28, itemStyle: { color: C.amber } }, { name: 'Sin resolver', type: 'bar', stack: 'resolution', data: items.map((item) => 100 - item.resolution_rate), itemStyle: { color: C.cream, borderRadius: [10, 10, 0, 0] } }] };
}
function productsOption(items: DashboardPayload['products']): EChartsOption {
  const base = cartesian();
  return { animationDuration: 650, tooltip: { ...tooltip('axis'), axisPointer: { type: 'shadow' }, formatter: (params) => { const rows = Array.isArray(params) ? params : [params]; const index = Number(rows[0]?.dataIndex ?? 0); const item = items[index]; return `<b>${item?.product_name ?? ''}</b><br>Interesados: ${item?.interested_count ?? 0}<br>Ventas: ${item?.won_count ?? 0}<br>Conversión: ${percent(item?.conversion_rate ?? 0)}`; } }, legend: { top: 0, right: 10, icon: 'circle', textStyle: { color: C.graphite42, fontFamily: 'Urbanist', fontSize: 10, fontWeight: 700 } }, grid: { ...base.grid, top: 36, left: 18, right: 50 }, xAxis: { ...base.xAxis, type: 'value', splitLine: { show: false }, axisLabel: { show: false } }, yAxis: { ...base.yAxis, type: 'category', inverse: true, data: items.map((item) => item.product_name), splitLine: { show: false }, axisLabel: { ...base.yAxis?.axisLabel, color: C.graphite, margin: 18 } }, series: [{ name: 'Interés', type: 'bar', data: items.map((item) => item.interested_count), barWidth: 12, itemStyle: { color: C.cream, borderRadius: 99 } }, { name: 'Ventas', type: 'bar', data: items.map((item) => item.won_count), barWidth: 12, itemStyle: { color: C.amber, borderRadius: 99 }, label: { show: true, position: 'right', formatter: (params) => percent(items[Number(params.dataIndex)]?.conversion_rate ?? 0), color: C.graphite, fontFamily: 'Urbanist', fontWeight: 800 } }] };
}

function renderDashboard(payload: DashboardPayload): void {
  const { summary } = payload;
  setText('dashboardRevenue', money.format(summary.revenue_minor)); setText('dashboardWon', integer.format(summary.won)); setText('dashboardConversion', percent(summary.conversion_rate)); setText('dashboardRecoveredRevenue', money.format(summary.recovered_revenue_minor));
  setText('dashboardConversations', integer.format(summary.conversations)); setText('dashboardRecoveredSales', integer.format(summary.recovered_sales)); setText('dashboardRecoveryRate', percent(summary.recovery_rate));
  const hasData = summary.conversations > 0 || summary.opportunities > 0 || payload.lost_reasons.length > 0 || payload.products.length > 0 || payload.objections.total > 0;
  show('dashboardData', hasData); show('dashboardEmpty', !hasData); show('dashboardLoading', false); show('dashboardError', false);
  mount('dashboardConversionChart', lineOption(payload.conversion_trend.map((point) => ({ date: point.date, value: point.conversion_rate })), 'Conversión'));
  mount('dashboardRecoveryChart', lineOption(payload.recovery_trend.map((point) => ({ date: point.date, value: point.recovery_rate })), 'Recuperación'));
  mount('dashboardLossesChart', horizontalBarOption(payload.lost_reasons.map((item) => ({ label: item.reason, value: item.count })), 'Pérdidas'));
  mount('dashboardObjectionsChart', objectionOption(payload.objection_categories));
  mount('dashboardProductsChart', productsOption(payload.products));
  requestAnimationFrame(() => charts.forEach((chart) => chart.resize()));
}

export function bootDashboard(apiUrl: string, token: string): void {
  const fromInput = element('dashboardFrom') as HTMLInputElement | null; const toInput = element('dashboardTo') as HTMLInputElement | null; const today = new Date(); const fromDate = new Date(today); fromDate.setDate(today.getDate() - 30); const isoDate = (value: Date): string => value.toISOString().slice(0, 10);
  if (fromInput && !fromInput.value) fromInput.value = isoDate(fromDate); if (toInput && !toInput.value) toInput.value = isoDate(today);
  const load = async (): Promise<void> => { show('dashboardLoading', true); show('dashboardData', false); show('dashboardEmpty', false); show('dashboardError', false); const query = new URLSearchParams(); if (fromInput?.value) query.set('from', fromInput.value); if (toInput?.value) query.set('to', toInput.value); try { const response = await fetch(`${apiUrl}/analytics/dashboard?${query}`, { headers: { Authorization: `Bearer ${token}` } }); if (!response.ok) throw new Error(`dashboard ${response.status}`); renderDashboard(parsePayload(await response.json())); } catch { show('dashboardLoading', false); show('dashboardData', false); show('dashboardEmpty', false); show('dashboardError', true); } };
  element('dashboardApply')?.addEventListener('click', () => void load()); element('dashboardRetry')?.addEventListener('click', () => void load()); window.addEventListener('resize', () => charts.forEach((chart) => chart.resize()), { passive: true }); void load();
}
