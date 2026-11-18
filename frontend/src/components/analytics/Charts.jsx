import { useMemo, useRef, useState } from 'react';
import {
  extent, heatColor, linePath, linearScale, mergePeriodSelects, mergeRangeSelects, niceTicks, overlappingIndexes, paddedDomain, sortBars, xyRangeSelect,
} from '../../features/analytics/chartMath.js';
import { formatNumber } from '../../features/analytics/format.js';
import { ChartFrame, Tooltip } from './ChartFrame.jsx';
import { useBrush, VIEW } from './useBrush.js';

const M = { l: 64, r: 20, t: 16, b: 48 };
const AREA = { x0: M.l, x1: VIEW.w - M.r, y0: M.t, y1: VIEW.h - M.b };
const PALETTE = ['var(--accent)', 'var(--warm)', 'var(--ok)', 'var(--danger)', '#5fb4e8', '#c58bd9', '#b5b550', '#8e8ea8'];

function Axes({ xs, ys, xTicks, yTicks, xLabel, yLabel, xFormat = formatNumber, yFormat = formatNumber, xText }) {
  return (
    <g className="chart__axes" aria-hidden="true">
      {yTicks.map((t) => (
        <g key={`y${t}`}>
          <line x1={AREA.x0} x2={AREA.x1} y1={ys(t)} y2={ys(t)} className="chart__grid" />
          <text x={AREA.x0 - 8} y={ys(t)} className="chart__tick" textAnchor="end" dominantBaseline="middle">{yFormat(t)}</text>
        </g>
      ))}
      {xTicks.map((t) => (
        <text key={`x${t}`} x={xs(t)} y={AREA.y1 + 18} className="chart__tick" textAnchor="middle">{xText ? xText(t) : xFormat(t)}</text>
      ))}
      <line x1={AREA.x0} x2={AREA.x1} y1={AREA.y1} y2={AREA.y1} className="chart__axis" />
      <line x1={AREA.x0} x2={AREA.x0} y1={AREA.y0} y2={AREA.y1} className="chart__axis" />
      <text x={(AREA.x0 + AREA.x1) / 2} y={VIEW.h - 6} className="chart__label" textAnchor="middle">{xLabel}</text>
      <text transform={`translate(14 ${(AREA.y0 + AREA.y1) / 2}) rotate(-90)`} className="chart__label" textAnchor="middle">{yLabel}</text>
    </g>
  );
}

function BrushRect({ rect }) {
  return rect ? <rect x={rect.x0} y={rect.y0} width={rect.x1 - rect.x0} height={rect.y1 - rect.y0} className="chart__brush" /> : null;
}

/* ------------------------------------------------------------------ bar chart (categories) */
export function BarChart({ chart, onSelect, selected }) {
  const [sort, setSort] = useState('as_sent');
  const [tip, setTip] = useState(null);
  const bars = useMemo(() => (sort === 'as_sent' ? chart.bars : sortBars(chart.bars, sort)), [chart.bars, sort]);
  const [lo, hi] = extent(bars.flatMap((b) => [b.value, b.ci?.[0], b.ci?.[1], 0]));
  const x = linearScale(paddedDomain(Math.min(lo, 0), Math.max(hi, 0), 0.06), [AREA.x0 + 70, AREA.x1]);
  const rowH = Math.min(30, (AREA.y1 - AREA.y0) / Math.max(1, bars.length));
  const height = AREA.y0 + rowH * bars.length + M.b;
  const ticks = niceTicks(x.domain[0], x.domain[1], 5);
  const svgRef = useRef(null);
  return (
    <ChartFrame title={chart.title} svgRef={svgRef} table={chart.data_table}
      subtitle={chart.categories_not_shown ? `${chart.categories_not_shown} more categories are not drawn; they are in the data table export.` : null}
      actions={(
        <label className="chart__select">Sort <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort bars">
          <option value="as_sent">Server order</option><option value="value_desc">Highest first</option><option value="value_asc">Lowest first</option><option value="label">A to Z</option>
        </select></label>
      )}>
      <svg ref={svgRef} viewBox={`0 0 ${VIEW.w} ${height}`} className="chart__svg" role="img" aria-label={`${chart.title}. ${bars.length} bars.`}>
        {ticks.map((t) => (
          <g key={t}><line x1={x(t)} x2={x(t)} y1={AREA.y0} y2={height - M.b} className="chart__grid" /><text x={x(t)} y={height - M.b + 16} textAnchor="middle" className="chart__tick">{formatNumber(t)}</text></g>
        ))}
        <line x1={x(0)} x2={x(0)} y1={AREA.y0} y2={height - M.b} className="chart__axis" />
        {bars.map((b, i) => {
          const y = AREA.y0 + i * rowH;
          const active = selected && JSON.stringify(selected) === JSON.stringify(b.select);
          const x0 = Math.min(x(0), x(b.value)); const w = Math.abs(x(b.value) - x(0));
          return (
            <g key={`${b.label}-${i}`} tabIndex={0} role="button" className={`chart__bar${active ? ' is-active' : ''}`}
              aria-label={`${b.label}: ${formatNumber(b.value)}, ${b.n} rows. Press Enter to see the rows.`}
              onClick={() => onSelect?.(b.select, `${chart.x_label} = ${b.label}`)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect?.(b.select, `${chart.x_label} = ${b.label}`); } }}
              onPointerEnter={() => setTip({ x: x(b.value), y: y + rowH / 2, b })} onPointerLeave={() => setTip(null)} onFocus={() => setTip({ x: x(b.value), y: y + rowH / 2, b })} onBlur={() => setTip(null)}>
              <text x={AREA.x0 + 64} y={y + rowH / 2} textAnchor="end" dominantBaseline="middle" className="chart__tick">{String(b.label).slice(0, 12)}</text>
              <rect x={x0} y={y + 3} width={Math.max(1, w)} height={rowH - 6} rx="3" className="chart__fill" />
              {b.ci ? <g className="chart__ci"><line x1={x(b.ci[0])} x2={x(b.ci[1])} y1={y + rowH / 2} y2={y + rowH / 2} /><line x1={x(b.ci[0])} x2={x(b.ci[0])} y1={y + rowH / 2 - 4} y2={y + rowH / 2 + 4} /><line x1={x(b.ci[1])} x2={x(b.ci[1])} y1={y + rowH / 2 - 4} y2={y + rowH / 2 + 4} /></g> : null}
            </g>
          );
        })}
        <text x={(AREA.x0 + 70 + AREA.x1) / 2} y={height - 6} textAnchor="middle" className="chart__label">{chart.y_label}</text>
      </svg>
      <Tooltip at={tip && { x: Math.min(tip.x, 560), y: Math.min(tip.y, height - 10) }}>
        {tip ? <><strong>{tip.b.label}</strong><br />{formatNumber(tip.b.value)} · n = {formatNumber(tip.b.n)}{tip.b.ci ? <><br />95% CI {formatNumber(tip.b.ci[0])} to {formatNumber(tip.b.ci[1])}</> : null}</> : null}
      </Tooltip>
      {chart.overall ? <p className="chart__caption">Overall {formatNumber(chart.overall.value)} (n = {formatNumber(chart.overall.n)}). Click a bar to see its rows.</p> : null}
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ histogram */
export function Histogram({ chart, onSelect }) {
  const [tip, setTip] = useState(null);
  const bars = chart.bars;
  const [lo, hi] = extent(bars.flatMap((b) => [b.lo, b.hi]));
  const key = chart.density ? 'density' : 'count';
  const maxV = Math.max(...bars.map((b) => b[key]), 1);
  const x = linearScale([lo, hi], [AREA.x0, AREA.x1]);
  const y = linearScale([0, maxV * 1.08], [AREA.y1, AREA.y0]);
  const spans = bars.map((b) => ({ x0: x(b.lo), x1: x(b.hi) }));
  const brush = useBrush({
    mode: 'x', area: AREA,
    onDone: (r) => {
      const hit = overlappingIndexes(spans, r.x0, r.x1);
      const sel = mergeRangeSelects(hit.map((i) => bars[i].select));
      if (sel) onSelect?.(sel, `${chart.x_label} from ${formatNumber(bars[hit[0]].lo)} to ${formatNumber(bars[hit[hit.length - 1]].hi)}`);
    },
    onMove: (p) => {
      if (!p) { setTip(null); return; }
      const i = spans.findIndex((s) => p.x >= s.x0 && p.x <= s.x1);
      setTip(i >= 0 ? { x: p.x, y: p.y, bar: bars[i] } : null);
    },
  });
  return (
    <ChartFrame title={chart.title} svgRef={brush.svgRef} table={chart.data_table} subtitle={`Bins: ${chart.binning}. Drag across bars to select a range, or click one.`}>
      <svg ref={brush.svgRef} viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} className="chart__svg" role="img" aria-label={`${chart.title}. ${bars.length} bins.`}>
        <Axes xs={x} ys={y} xTicks={niceTicks(lo, hi, 6)} yTicks={niceTicks(0, maxV, 5)} xLabel={chart.x_label} yLabel={chart.density ? 'density' : chart.y_label} />
        {bars.map((b, i) => (
          <rect key={i} x={spans[i].x0 + 0.5} y={y(b[key])} width={Math.max(1, spans[i].x1 - spans[i].x0 - 1)} height={AREA.y1 - y(b[key])} className="chart__fill"
            onClick={() => onSelect?.(b.select, `${chart.x_label} from ${formatNumber(b.lo)} to ${formatNumber(b.hi)}`)} />
        ))}
        {chart.overlays?.mean !== undefined ? <g><line x1={x(chart.overlays.mean)} x2={x(chart.overlays.mean)} y1={AREA.y0} y2={AREA.y1} className="chart__ref" /><text x={x(chart.overlays.mean) + 4} y={AREA.y0 + 10} className="chart__tick">mean</text></g> : null}
        {chart.overlays?.median !== undefined ? <g><line x1={x(chart.overlays.median)} x2={x(chart.overlays.median)} y1={AREA.y0} y2={AREA.y1} className="chart__ref chart__ref--alt" /><text x={x(chart.overlays.median) + 4} y={AREA.y0 + 24} className="chart__tick">median</text></g> : null}
        <BrushRect rect={brush.rect} />
        <rect x={AREA.x0} y={AREA.y0} width={AREA.x1 - AREA.x0} height={AREA.y1 - AREA.y0} fill="transparent" className="chart__capture" {...brush.handlers} />
      </svg>
      <Tooltip at={tip}>{tip ? <><strong>{formatNumber(tip.bar.lo)} to {formatNumber(tip.bar.hi)}</strong><br />{formatNumber(tip.bar.count)} rows ({formatNumber(tip.bar.pct, 1)}%)</> : null}</Tooltip>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ scatter */
export function ScatterChart({ chart, onSelect }) {
  const [tip, setTip] = useState(null);
  const [showLine, setShowLine] = useState(true);
  const [hidden, setHidden] = useState(() => new Set());
  const [xlo, xhi] = extent(chart.points.map((p) => p.x));
  const [ylo, yhi] = extent(chart.points.map((p) => p.y));
  const x = linearScale(paddedDomain(xlo, xhi), [AREA.x0, AREA.x1]);
  const y = linearScale(paddedDomain(ylo, yhi), [AREA.y1, AREA.y0]);
  const levels = chart.color_levels ?? [];
  const colorOf = (p) => { const i = levels.indexOf(p.color); return PALETTE[(i >= 0 ? i : PALETTE.length - 1) % PALETTE.length]; };
  const shown = chart.points.filter((p) => !hidden.has(p.color));
  const line = chart.overlays?.trend_line;
  const brush = useBrush({
    mode: 'xy', area: AREA,
    onDone: (r) => onSelect?.(xyRangeSelect(chart.select_brush, r, x, y), `${chart.x_label} and ${chart.y_label} in the selected rectangle`),
    onMove: (p) => {
      if (!p) { setTip(null); return; }
      let best = null; let d2 = 100;
      for (const pt of shown) { const dx = x(pt.x) - p.x; const dy = y(pt.y) - p.y; const d = dx * dx + dy * dy; if (d < d2) { d2 = d; best = pt; } }
      setTip(best ? { x: x(best.x), y: y(best.y), p: best } : null);
    },
  });
  return (
    <ChartFrame title={chart.title} svgRef={brush.svgRef} table={chart.data_table}
      subtitle={chart.sample ? `Showing a seeded sample of ${formatNumber(chart.points.length)} points. Drag a rectangle to see the rows in it.` : 'Drag a rectangle to see the rows inside it.'}
      actions={line ? <label className="chart__check"><input type="checkbox" checked={showLine} onChange={(e) => setShowLine(e.target.checked)} /> Trend line</label> : null}
      legend={levels.length ? (
        <ul className="chart__legend" aria-label={`Colour by ${chart.color_by}`}>
          {levels.map((l, i) => (
            <li key={l}><button type="button" aria-pressed={!hidden.has(l)} className={hidden.has(l) ? 'is-off' : ''}
              onClick={() => setHidden((s) => { const n = new Set(s); if (n.has(l)) n.delete(l); else n.add(l); return n; })}>
              <span className="swatch" style={{ background: PALETTE[i % PALETTE.length] }} />{l}</button></li>
          ))}
        </ul>
      ) : null}
      caption={line ? `${line.method}: slope ${formatNumber(line.slope)} (95% CI ${formatNumber(line.slope_ci[0])} to ${formatNumber(line.slope_ci[1])}), r = ${formatNumber(line.r)}, n = ${formatNumber(line.n)}. ${line.caution}` : null}>
      <svg ref={brush.svgRef} viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} className="chart__svg" role="img" aria-label={`${chart.title}. ${shown.length} points.`}>
        <Axes xs={x} ys={y} xTicks={niceTicks(x.domain[0], x.domain[1], 6)} yTicks={niceTicks(y.domain[0], y.domain[1], 5)} xLabel={chart.x_label} yLabel={chart.y_label} />
        {shown.map((p) => <circle key={p.row} cx={x(p.x)} cy={y(p.y)} r="2.6" fill={colorOf(p)} className="chart__dot" />)}
        {line && showLine ? <line x1={x(line.line[0].x)} y1={y(line.line[0].y)} x2={x(line.line[1].x)} y2={y(line.line[1].y)} className="chart__trend" /> : null}
        {tip ? <circle cx={tip.x} cy={tip.y} r="5" className="chart__hover" /> : null}
        <BrushRect rect={brush.rect} />
        <rect x={AREA.x0} y={AREA.y0} width={AREA.x1 - AREA.x0} height={AREA.y1 - AREA.y0} fill="transparent" className="chart__capture" {...brush.handlers} />
      </svg>
      <Tooltip at={tip}>{tip ? <><strong>Row {tip.p.row}</strong><br />{chart.x_label}: {formatNumber(tip.p.x)}<br />{chart.y_label}: {formatNumber(tip.p.y)}{tip.p.color ? <><br />{chart.color_by}: {tip.p.color}</> : null}</> : null}</Tooltip>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ heatmap */
export function Heatmap({ chart, onSelect }) {
  const [tip, setTip] = useState(null);
  const svgRef = useRef(null);
  const nx = chart.x_labels.length; const ny = chart.y_labels.length;
  const left = 110; const top = 20;
  const cw = Math.min(70, (VIEW.w - left - 20) / nx); const ch = Math.min(46, 300 / ny);
  const height = top + ch * ny + 70;
  return (
    <ChartFrame title={chart.title} svgRef={svgRef} table={chart.data_table} subtitle="Click a cell to see the rows behind it.">
      <svg ref={svgRef} viewBox={`0 0 ${VIEW.w} ${height}`} className="chart__svg" role="img" aria-label={`${chart.title}. ${nx} by ${ny} cells.`}>
        {chart.y_labels.map((l, j) => <text key={l} x={left - 8} y={top + j * ch + ch / 2} textAnchor="end" dominantBaseline="middle" className="chart__tick">{String(l).slice(0, 14)}</text>)}
        {chart.x_labels.map((l, i) => <text key={l} x={left + i * cw + cw / 2} y={top + ny * ch + 14} textAnchor="end" transform={`rotate(-35 ${left + i * cw + cw / 2} ${top + ny * ch + 14})`} className="chart__tick">{String(l).slice(0, 14)}</text>)}
        {chart.cells.map((c, k) => {
          const i = chart.x_labels.indexOf(c.x_label ?? c.x); const j = chart.y_labels.indexOf(c.y_label ?? c.y);
          if (i < 0 || j < 0) return null;
          const label = c.value === null || c.value === undefined ? '—' : formatNumber(c.value, chart.kind === 'correlation' ? 2 : 0);
          return (
            <g key={k} tabIndex={0} role="button" className="chart__cell" aria-label={`${c.y} and ${c.x}: ${label}`}
              onClick={() => onSelect?.(c.select, `${c.x} / ${c.y}`)} onKeyDown={(e) => { if (e.key === 'Enter') onSelect?.(c.select, `${c.x} / ${c.y}`); }}
              onPointerEnter={() => setTip({ x: left + i * cw + cw / 2, y: top + j * ch, c })} onPointerLeave={() => setTip(null)}>
              <rect x={left + i * cw + 1} y={top + j * ch + 1} width={cw - 2} height={ch - 2} rx="3" fill={heatColor(c.value, chart.scale)} className="chart__cellbox" />
              <text x={left + i * cw + cw / 2} y={top + j * ch + ch / 2} textAnchor="middle" dominantBaseline="middle" className="chart__cellvalue">{label}</text>
            </g>
          );
        })}
      </svg>
      <Tooltip at={tip}>{tip ? <><strong>{tip.c.y} / {tip.c.x}</strong><br />{tip.c.value === null ? 'not enough data' : formatNumber(tip.c.value, 3)}</> : null}</Tooltip>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ time series */
export function TimeSeriesChart({ chart, onSelect }) {
  const [tip, setTip] = useState(null);
  const [showTrend, setShowTrend] = useState(true);
  const [showAvg, setShowAvg] = useState(true);
  const pts = chart.points;
  const t = (s) => Date.parse(`${s}T00:00:00Z`);
  const [t0, t1] = extent(pts.flatMap((p) => [t(p.start), t(p.end)]));
  const [vlo, vhi] = extent(pts.map((p) => p.value));
  const x = linearScale([t0, t1], [AREA.x0, AREA.x1]);
  const y = linearScale(paddedDomain(vlo, vhi, 0.08, true), [AREA.y1, AREA.y0]);
  const mid = (p) => (t(p.start) + t(p.end)) / 2;
  const spans = pts.map((p) => ({ x0: x(t(p.start)), x1: x(t(p.end)) }));
  const trend = chart.overlays?.trend_line;
  const brush = useBrush({
    mode: 'x', area: AREA,
    onDone: (r) => {
      const hit = overlappingIndexes(spans, r.x0, r.x1);
      const sel = mergePeriodSelects(hit.map((i) => pts[i].select));
      if (sel) onSelect?.(sel, `${chart.x_label} from ${sel.start} to ${sel.end}`);
    },
    onMove: (p) => {
      if (!p) { setTip(null); return; }
      const i = spans.findIndex((s) => p.x >= s.x0 - 2 && p.x <= s.x1 + 2);
      setTip(i >= 0 && pts[i].value !== null ? { x: x(mid(pts[i])), y: y(pts[i].value), p: pts[i] } : null);
    },
  });
  const at = (v) => (v === null || v === undefined ? null : y(v));
  const series = pts.map((p) => ({ x: x(mid(p)), y: at(p.value) }));
  const avg = pts.map((p) => ({ x: x(mid(p)), y: at(p.moving_average) }));
  const hasAvg = pts.some((p) => p.moving_average !== null && p.moving_average !== undefined);
  const tickDates = niceTicks(t0, t1, 6);
  return (
    <ChartFrame title={chart.title} svgRef={brush.svgRef} table={chart.data_table}
      subtitle={`${chart.date_range.from} to ${chart.date_range.to}, per ${chart.freq}. Drag across periods to see their rows.`}
      actions={<>
        {hasAvg ? <label className="chart__check"><input type="checkbox" checked={showAvg} onChange={(e) => setShowAvg(e.target.checked)} /> Moving average</label> : null}
        {trend ? <label className="chart__check"><input type="checkbox" checked={showTrend} onChange={(e) => setShowTrend(e.target.checked)} /> Trend line</label> : null}
      </>}
      caption={chart.trend ? (chart.trend.conclusion === 'Not enough evidence.' ? `Trend: Not enough evidence of a trend (${chart.trend.periods_used} periods).` : `Trend: ${chart.trend.direction}, slope ${formatNumber(chart.trend.slope_per_period)} per ${chart.freq} (95% CI ${formatNumber(chart.trend.slope_ci[0])} to ${formatNumber(chart.trend.slope_ci[1])}).`) + ' A trend in past data does not predict the future.' : null}>
      <svg ref={brush.svgRef} viewBox={`0 0 ${VIEW.w} ${VIEW.h}`} className="chart__svg" role="img" aria-label={`${chart.title}. ${pts.length} periods.`}>
        <Axes xs={x} ys={y} xTicks={tickDates} yTicks={niceTicks(y.domain[0], y.domain[1], 5)} xLabel={chart.x_label} yLabel={chart.y_label} xText={(v) => new Date(v).toISOString().slice(0, 7)} />
        <path d={linePath(series, (v) => v, (v) => v)} className="chart__line" />
        {hasAvg && showAvg ? <path d={linePath(avg, (v) => v, (v) => v)} className="chart__line chart__line--alt" /> : null}
        {trend && showTrend ? <path d={linePath(pts.map((p, i) => ({ x: x(mid(p)), y: at(trend.values[i]) })), (v) => v, (v) => v)} className="chart__trend" /> : null}
        {pts.map((p, i) => (p.value === null ? null : <circle key={i} cx={x(mid(p))} cy={y(p.value)} r="2.5" className="chart__point" />))}
        {tip ? <circle cx={tip.x} cy={tip.y} r="5" className="chart__hover" /> : null}
        <BrushRect rect={brush.rect} />
        <rect x={AREA.x0} y={AREA.y0} width={AREA.x1 - AREA.x0} height={AREA.y1 - AREA.y0} fill="transparent" className="chart__capture" {...brush.handlers} />
      </svg>
      <Tooltip at={tip}>{tip ? <><strong>{tip.p.start} to {tip.p.end}</strong><br />{formatNumber(tip.p.value)} · n = {formatNumber(tip.p.n)}{tip.p.moving_average !== null && tip.p.moving_average !== undefined ? <><br />moving avg {formatNumber(tip.p.moving_average)}</> : null}</> : null}</Tooltip>
    </ChartFrame>
  );
}

export function ChartView({ chart, onSelect, selected }) {
  switch (chart?.type) {
    case 'bar': return <BarChart chart={chart} onSelect={onSelect} selected={selected} />;
    case 'histogram': return <Histogram chart={chart} onSelect={onSelect} />;
    case 'scatter': return <ScatterChart chart={chart} onSelect={onSelect} />;
    case 'heatmap': return <Heatmap chart={chart} onSelect={onSelect} />;
    case 'timeseries': return <TimeSeriesChart chart={chart} onSelect={onSelect} />;
    default: return <p className="muted">This chart type is not available.</p>;
  }
}
