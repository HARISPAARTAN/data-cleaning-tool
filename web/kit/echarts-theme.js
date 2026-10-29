// portfolio-kit chart colours and ECharts themes. Values checked with an OKLCH palette validator (README, section "`echarts-theme.js` and the chart palette"); ECharts itself is not shipped.
const pair = (dark, light) => Object.freeze({ dark: Object.freeze(dark), light: Object.freeze(light) });

export const palette = pair(
  ['#C38102', '#058DB5', '#02AB70', '#AB6FE5', '#CA445C', '#77A304', '#028E7F', '#7A84EF'],
  ['#B45309', '#0369A1', '#15803D', '#7E22CE', '#EB4960', '#3B6201', '#00948A', '#4338CA'],
);
export const sequential = pair(
  ['#6D4200', '#8B5502', '#AB6901', '#CB7E04', '#EC940D', '#FFB257'],
  ['#F29B1D', '#D78602', '#BB7400', '#9F6202', '#845001', '#694002'],
);
export const diverging = pair(
  ['#FFA9AF', '#EE6476', '#BC364E', '#3D3D3D', '#048073', '#0EB09E', '#57DDCA'],
  ['#9D1135', '#D14A5F', '#FF7C8A', '#DEDEDE', '#36C3B0', '#039585', '#026359'],
);

const TOKENS = {
  dark: { text: '#E6EDF3', muted: '#93A1B0', border: '#1F2A36', surface: '#121820', ok: '#3DDC97', warn: '#FACC15', danger: '#F87171', info: '#5EC8F2' },
  light: { text: '#14181D', muted: '#5B6472', border: '#DDD9D0', surface: '#FFFFFF', ok: '#15803D', warn: '#A16207', danger: '#B91C1C', info: '#0369A1' },
};
const FONT = 'Inter, system-ui, sans-serif';
const DISPLAY = '"Space Grotesk", Inter, system-ui, sans-serif';

function tokensFor(mode) {
  if (mode !== 'dark' && mode !== 'light') throw new RangeError(`echarts-theme: mode must be 'dark' or 'light', got ${String(mode)}`);
  return TOKENS[mode];
}

const half = (hex) => `rgba(${[1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(',')},0.5)`;

export function statusColors(mode) {
  const t = tokensFor(mode);
  return { ok: t.ok, warn: t.warn, danger: t.danger, info: t.info };
}

export function theme(mode) {
  const t = tokensFor(mode);
  const axis = () => ({
    axisLine: { lineStyle: { color: t.border } },
    axisTick: { lineStyle: { color: t.border } },
    axisLabel: { color: t.muted },
    splitLine: { lineStyle: { color: half(t.border) } },
    nameTextStyle: { color: t.muted },
  });
  return {
    color: [...palette[mode]],
    backgroundColor: 'transparent',
    textStyle: { color: t.text, fontFamily: FONT },
    title: { textStyle: { color: t.text, fontFamily: DISPLAY }, subtextStyle: { color: t.muted } },
    legend: { textStyle: { color: t.text }, inactiveColor: t.muted },
    tooltip: { backgroundColor: t.surface, borderColor: t.border, borderWidth: 1, textStyle: { color: t.text } },
    axisPointer: { lineStyle: { color: t.muted } },
    categoryAxis: axis(),
    valueAxis: axis(),
    logAxis: axis(),
    timeAxis: axis(),
    visualMap: { inRange: { color: [...sequential[mode]] }, textStyle: { color: t.text } },
    dataZoom: { borderColor: t.border, textStyle: { color: t.muted } },
  };
}

export function registerThemes(echarts) {
  echarts.registerTheme('pf-dark', theme('dark'));
  echarts.registerTheme('pf-light', theme('light'));
  return { dark: 'pf-dark', light: 'pf-light' };
}
