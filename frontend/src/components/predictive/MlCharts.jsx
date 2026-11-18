import { useMemo, useRef, useState } from 'react';
import { ChartFrame, Tooltip } from '../analytics/ChartFrame.jsx';
import { heatColor } from '../../features/analytics/chartMath.js';
import { formatNumber } from '../../features/analytics/format.js';
import {
  AREA, PLOT, confusionCells, distributionBars, effectBars, histogramBars, importanceBars, learningGeometry, pdpGeometry, prGeometry, probabilityBars,
  rankingBars, residualGeometry, rocGeometry, scatterGeometry,
} from '../../features/predictive/charts.js';
import { formatScore, formatShare } from '../../features/predictive/format.js';

const PALETTE = ['var(--accent)', 'var(--warm)', 'var(--ok)', 'var(--danger)', '#5fb4e8', '#c58bd9', '#b5b550', '#8e8ea8'];
const colour = (i) => PALETTE[i % PALETTE.length];
const f3 = (v) => formatScore(v, 3);

function Legend({ items }) {
  if (!items || items.length < 2) return null;
  return (
    <div className="chart__legend" aria-label="Legend">
      {items.map((it, i) => (
        <span key={`${it.label}-${i}`} className="chart__check"><span className="swatch" style={{ background: it.colour ?? colour(i) }} aria-hidden="true" /> {it.label}{it.note ? <span className="muted"> {it.note}</span> : null}</span>
      ))}
    </div>
  );
}

function Axes({ xs, ys, xTicks, yTicks, xLabel, yLabel, xText, yFormat = f3, xFormat = f3 }) {
  return (
    <g className="chart__axes" aria-hidden="true">
      {yTicks.map((t) => (
        <g key={`y${t}`}>
          <line x1={AREA.x0} x2={AREA.x1} y1={ys(t)} y2={ys(t)} className="chart__grid" />
          <text x={AREA.x0 - 8} y={ys(t)} className="chart__tick" textAnchor="end" dominantBaseline="middle">{yFormat(t)}</text>
        </g>
      ))}
      {xTicks.map((t, i) => (
        <text key={`x${i}`} x={xs(typeof t === 'object' ? t.i : t)} y={AREA.y1 + 18} className="chart__tick" textAnchor="middle">{xText ? xText(t) : xFormat(t)}</text>
      ))}
      <line x1={AREA.x0} x2={AREA.x1} y1={AREA.y1} y2={AREA.y1} className="chart__axis" />
      <line x1={AREA.x0} x2={AREA.x0} y1={AREA.y0} y2={AREA.y1} className="chart__axis" />
      <text x={(AREA.x0 + AREA.x1) / 2} y={PLOT.h - 6} className="chart__label" textAnchor="middle">{xLabel}</text>
      <text transform={`translate(14 ${(AREA.y0 + AREA.y1) / 2}) rotate(-90)`} className="chart__label" textAnchor="middle">{yLabel}</text>
    </g>
  );
}

const Svg = ({ svgRef, label, height = PLOT.h, children }) => (
  <svg ref={svgRef} viewBox={`0 0 ${PLOT.w} ${height}`} className="chart__svg" role="img" aria-label={label}>{children}</svg>
);

/* ------------------------------------------------------------------ ROC */
export function RocChart({ curves, basisNote }) {
  const g = useMemo(() => rocGeometry(curves), [curves]);
  const svgRef = useRef(null);
  if (!curves?.length) return null;
  return (
    <ChartFrame title="ROC curve" svgRef={svgRef} table={g.table} subtitle="How well the model separates the outcomes at every possible cut-off"
      legend={<Legend items={g.series.map((s) => ({ label: s.label, note: `AUC ${f3(s.auc)}` }))} />}
      caption={`The dashed line is a model that guesses. The higher a curve sits above it, the better the separation.${basisNote ? ` ${basisNote}` : ''}`}>
      <Svg svgRef={svgRef} label={`ROC curve. ${g.series.map((s) => `${s.label}: area ${f3(s.auc)}`).join('; ')}.`}>
        <Axes xs={g.x} ys={g.y} xTicks={g.ticks} yTicks={g.ticks} xLabel="False positive rate" yLabel="True positive rate" />
        <line x1={g.diagonal.x1} y1={g.diagonal.y1} x2={g.diagonal.x2} y2={g.diagonal.y2} className="chart__ref" />
        {g.series.map((s, i) => <path key={s.label} d={s.d} className="chart__line" style={{ stroke: colour(i) }} />)}
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ precision-recall */
export function PrChart({ curves }) {
  const g = useMemo(() => prGeometry(curves), [curves]);
  const svgRef = useRef(null);
  if (!curves?.length) return null;
  return (
    <ChartFrame title="Precision-recall curve" svgRef={svgRef} table={g.table} subtitle="Of the cases the model flags, how many are real, as it flags more of them"
      legend={<Legend items={g.series.map((s) => ({ label: s.label, note: `average precision ${f3(s.ap)}` }))} />}
      caption="The dashed line is the share of real cases: what a model with no skill would score. Useful when one outcome is rare.">
      <Svg svgRef={svgRef} label={`Precision-recall curve. ${g.series.map((s) => `${s.label}: average precision ${f3(s.ap)}`).join('; ')}.`}>
        <Axes xs={g.x} ys={g.y} xTicks={g.ticks} yTicks={g.ticks} xLabel="Recall" yLabel="Precision" />
        {g.series.map((s, i) => (s.baselineY === null ? null : <line key={`b${s.label}`} x1={AREA.x0} x2={AREA.x1} y1={s.baselineY} y2={s.baselineY} className="chart__ref" style={{ stroke: colour(i), opacity: 0.6 }} />))}
        {g.series.map((s, i) => <path key={s.label} d={s.d} className="chart__line" style={{ stroke: colour(i) }} />)}
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ confusion matrix */
export function ConfusionMatrix({ matrix }) {
  const m = useMemo(() => confusionCells(matrix), [matrix]);
  const svgRef = useRef(null);
  const [tip, setTip] = useState(null);
  if (!m) return null;
  const left = 96; const top = 20; const bottom = 70;
  const cell = Math.max(14, Math.min(64, Math.floor((PLOT.w - left - 20) / m.size), Math.floor(300 / m.size)));
  const height = top + cell * m.size + bottom;
  const showText = cell >= 30;
  const table = { columns: ['Actual', 'Predicted', 'Rows', 'Share of the actual group'], rows: m.cells.map((c) => [c.actual, c.predicted, c.count, c.share]) };
  const short = (s) => (s.length > 12 ? `${s.slice(0, 11)}…` : s);
  return (
    <ChartFrame title="Confusion matrix" svgRef={svgRef} table={table} subtitle="Rows are what was true; columns are what the model said. The diagonal is correct."
      caption="Shading is the share of each true group (a row), so a rare outcome is not drowned out by a common one.">
      <Svg svgRef={svgRef} height={height} label={`Confusion matrix for ${m.size} outcomes. ${m.cells.filter((c) => c.correct).reduce((a, c) => a + (c.count ?? 0), 0)} rows on the diagonal.`}>
        <text x={left + (cell * m.size) / 2} y={height - 6} className="chart__label" textAnchor="middle">Predicted</text>
        <text transform={`translate(12 ${top + (cell * m.size) / 2}) rotate(-90)`} className="chart__label" textAnchor="middle">Actual</text>
        {m.labels.map((l, i) => (
          <g key={`l${i}`}>
            <text x={left - 6} y={top + i * cell + cell / 2} className="chart__tick" textAnchor="end" dominantBaseline="middle">{short(l)}</text>
            <text x={left + i * cell + cell / 2} y={top + cell * m.size + 14} className="chart__tick" textAnchor={m.size > 6 ? 'end' : 'middle'}
              transform={m.size > 6 ? `rotate(-35 ${left + i * cell + cell / 2} ${top + cell * m.size + 14})` : undefined}>{short(l)}</text>
          </g>
        ))}
        {m.cells.map((c) => (
          <g key={`${c.row}-${c.col}`} tabIndex={0} role="img" className="chart__cell" aria-label={`Actual ${c.actual}, predicted ${c.predicted}: ${c.count} rows${c.share === null ? '' : `, ${formatShare(c.share)} of ${c.actual}`}`}
            onPointerEnter={() => setTip({ x: left + c.col * cell + cell / 2, y: top + c.row * cell, c })} onPointerLeave={() => setTip(null)} onFocus={() => setTip({ x: left + c.col * cell + cell / 2, y: top + c.row * cell, c })} onBlur={() => setTip(null)}>
            <rect x={left + c.col * cell + 1} y={top + c.row * cell + 1} width={cell - 2} height={cell - 2} rx="3" fill={heatColor(c.share, { min: 0, max: 1 })} className="chart__cellbox" stroke={c.correct ? 'var(--ok)' : 'var(--border)'} />
            {showText ? <text x={left + c.col * cell + cell / 2} y={top + c.row * cell + cell / 2} textAnchor="middle" dominantBaseline="middle" className="chart__cellvalue">{formatNumber(c.count, 0)}</text> : null}
          </g>
        ))}
      </Svg>
      <Tooltip at={tip && { x: Math.min(tip.x, 600), y: Math.min(tip.y, height - 20) }}>
        {tip ? <><strong>Actual {tip.c.actual}, predicted {tip.c.predicted}</strong><br />{formatNumber(tip.c.count, 0)} rows{tip.c.share === null ? '' : ` · ${formatShare(tip.c.share, 1)} of ${tip.c.actual}`}</> : null}
      </Tooltip>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ importance */
export function ImportanceBars({ permutation, metricLabel, title = 'Which inputs the model relies on', top = 12 }) {
  const bars = useMemo(() => importanceBars(permutation?.features, { top }), [permutation, top]);
  const svgRef = useRef(null);
  if (!permutation?.available || !bars.length) return null;
  const rowH = 28; const left = 150; const height = AREA.y0 + rowH * bars.length + 40;
  const max = Math.max(...bars.map((b) => Math.max(b.importance + (b.std ?? 0), 0)), 1e-12);
  const x = (v) => left + (Math.max(0, v) / max) * (PLOT.w - left - 30);
  const table = { columns: ['Input', 'Importance', 'Spread between repeats', 'Share of total', 'Clearly above zero'], rows: bars.map((b) => [b.feature, b.importance, b.std, b.share, b.reliable === null ? null : (b.reliable ? 'yes' : 'no')]) };
  return (
    <ChartFrame title={title} svgRef={svgRef} table={table}
      subtitle={`Measured by shuffling one input at a time on held-out rows and seeing how much the ${metricLabel ?? 'score'} changes`}
      caption="A large drop means the model leans on that input. It shows what the model uses, not what causes the outcome. Inputs that overlap can share credit.">
      <Svg svgRef={svgRef} height={height} label={`Importance of ${bars.length} inputs. Largest: ${bars[0].feature}.`}>
        <line x1={left} x2={left} y1={AREA.y0} y2={AREA.y0 + rowH * bars.length} className="chart__axis" />
        {bars.map((b, i) => {
          const y = AREA.y0 + i * rowH;
          return (
            <g key={b.feature} className="chart__bar">
              <text x={left - 8} y={y + rowH / 2} textAnchor="end" dominantBaseline="middle" className="chart__tick">{b.feature.length > 22 ? `${b.feature.slice(0, 21)}…` : b.feature}</text>
              <rect x={left} y={y + 4} width={Math.max(1, x(b.importance) - left)} height={rowH - 8} rx="3" className="chart__fill" style={b.reliable === false ? { opacity: 0.45 } : undefined} />
              {b.std ? <g className="chart__ci"><line x1={x(b.importance - b.std)} x2={x(b.importance + b.std)} y1={y + rowH / 2} y2={y + rowH / 2} /></g> : null}
              <text x={x(b.importance + (b.std ?? 0)) + 6} y={y + rowH / 2} dominantBaseline="middle" className="chart__tick">{f3(b.importance)}{b.negative ? ' (below zero)' : ''}</text>
            </g>
          );
        })}
        <text x={left + (PLOT.w - left - 30) / 2} y={height - 6} textAnchor="middle" className="chart__label">Worsening of {metricLabel ?? 'the score'} when the input is shuffled</text>
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ regression plots */
export function ActualVsPredicted({ scatter, targetName }) {
  const g = useMemo(() => scatterGeometry(scatter), [scatter]);
  const svgRef = useRef(null);
  const [tip, setTip] = useState(null);
  if (!g) return null;
  return (
    <ChartFrame title="Actual against predicted" svgRef={svgRef} table={g.table}
      subtitle={g.shown < g.of ? `${formatNumber(g.shown, 0)} of ${formatNumber(g.of, 0)} held-out rows are drawn` : `${formatNumber(g.of, 0)} held-out rows`}
      caption="Points on the dashed line were predicted exactly. Points above it were over-predicted; points below it were under-predicted.">
      <Svg svgRef={svgRef} label={`Actual against predicted ${targetName ?? 'values'}, ${g.points.length} points.`}>
        <Axes xs={g.x} ys={g.y} xTicks={g.ticks} yTicks={g.ticks} xLabel={`Actual${targetName ? ` ${targetName}` : ''}`} yLabel="Predicted" />
        <line x1={g.diagonal.x1} y1={g.diagonal.y1} x2={g.diagonal.x2} y2={g.diagonal.y2} className="chart__ref" />
        {g.points.map((p, i) => (
          <circle key={i} cx={p.cx} cy={p.cy} r="3" className="chart__dot" onPointerEnter={() => setTip({ x: p.cx, y: p.cy, p })} onPointerLeave={() => setTip(null)} />
        ))}
      </Svg>
      <Tooltip at={tip}>{tip ? <>Actual {formatNumber(tip.p.actual, 3)}<br />Predicted {formatNumber(tip.p.predicted, 3)}</> : null}</Tooltip>
    </ChartFrame>
  );
}

export function ResidualPlot({ scatter }) {
  const g = useMemo(() => residualGeometry(scatter), [scatter]);
  const svgRef = useRef(null);
  if (!g) return null;
  const table = { columns: ['Predicted', 'Residual (actual - predicted)'], rows: g.points.map((p) => [p.predicted, p.residual]) };
  return (
    <ChartFrame title="Residuals against predicted" svgRef={svgRef} table={table} subtitle="The miss on each held-out row (actual minus predicted)"
      caption="Look for a pattern. A flat, even band around zero is healthy; a curve or a widening funnel means the model misses in a systematic way.">
      <Svg svgRef={svgRef} label={`Residuals against predicted values, ${g.points.length} points.`}>
        <Axes xs={g.x} ys={g.y} xTicks={g.xticks} yTicks={g.yticks} xLabel="Predicted" yLabel="Residual" />
        <line x1={AREA.x0} x2={AREA.x1} y1={g.zeroY} y2={g.zeroY} className="chart__ref" />
        {g.points.map((p, i) => <circle key={i} cx={p.cx} cy={p.cy} r="3" className="chart__dot" />)}
      </Svg>
    </ChartFrame>
  );
}

export function ResidualHistogram({ residuals }) {
  const bars = useMemo(() => histogramBars(residuals?.histogram), [residuals]);
  const svgRef = useRef(null);
  if (!bars) return null;
  const lo = bars[0].lo; const hi = bars[bars.length - 1].hi;
  const x = (v) => AREA.x0 + ((v - lo) / (hi - lo || 1)) * (AREA.x1 - AREA.x0);
  const y = (h) => AREA.y1 - h * (AREA.y1 - AREA.y0);
  const table = { columns: ['From', 'To', 'Rows'], rows: bars.map((b) => [b.lo, b.hi, b.count]) };
  const ticks = [lo, (lo + hi) / 2, hi];
  return (
    <ChartFrame title="Distribution of residuals" svgRef={svgRef} table={table}
      subtitle={`Mean miss ${f3(residuals.mean)}, spread (standard deviation) ${f3(residuals.std)}`}
      caption="Residuals centred on zero mean the model is not biased up or down overall.">
      <Svg svgRef={svgRef} label={`Histogram of residuals in ${bars.length} bins.`}>
        <g aria-hidden="true">
          <line x1={AREA.x0} x2={AREA.x1} y1={AREA.y1} y2={AREA.y1} className="chart__axis" />
          {ticks.map((t) => <text key={t} x={x(t)} y={AREA.y1 + 18} textAnchor="middle" className="chart__tick">{f3(t)}</text>)}
          {lo < 0 && hi > 0 ? <line x1={x(0)} x2={x(0)} y1={AREA.y0} y2={AREA.y1} className="chart__ref" /> : null}
          <text x={(AREA.x0 + AREA.x1) / 2} y={PLOT.h - 6} className="chart__label" textAnchor="middle">Residual (actual - predicted)</text>
        </g>
        {bars.map((b, i) => <rect key={i} x={x(b.lo) + 1} y={y(b.height)} width={Math.max(1, x(b.hi) - x(b.lo) - 2)} height={AREA.y1 - y(b.height)} className="chart__fill"><title>{`${f3(b.lo)} to ${f3(b.hi)}: ${b.count} rows`}</title></rect>)}
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ learning curve */
export function LearningCurve({ curve, metricLabel, lowerIsBetter = false }) {
  const g = useMemo(() => learningGeometry(curve, { flip: lowerIsBetter }), [curve, lowerIsBetter]);
  const svgRef = useRef(null);
  if (!g) return null;
  return (
    <ChartFrame title="Learning curve" svgRef={svgRef} table={g.table} subtitle={`${metricLabel ?? curve.metric ?? 'Score'}${lowerIsBetter ? ' (lower is better)' : ''} as the model sees more training rows`}
      legend={<Legend items={[{ label: 'Training score' }, { label: 'Validation score', note: 'with its spread across folds' }]} />}
      caption="If the two lines are far apart the model has memorised its training rows. If the validation line is still rising, more data would help.">
      <Svg svgRef={svgRef} label={`Learning curve with ${g.points.length} training sizes.`}>
        <Axes xs={g.x} ys={g.y} xTicks={g.xticks} yTicks={g.yticks} xLabel="Training rows" yLabel={metricLabel ?? curve.metric ?? 'Score'} xFormat={(v) => formatNumber(v, 0)} />
        <path d={g.band} className="chart__band" />
        <path d={g.trainPath} className="chart__line" style={{ stroke: colour(0) }} />
        <path d={g.validationPath} className="chart__line" style={{ stroke: colour(1) }} />
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ partial dependence */
export function PdpChart({ pdp }) {
  const g = useMemo(() => pdpGeometry(pdp), [pdp]);
  const svgRef = useRef(null);
  if (!g) return null;
  return (
    <ChartFrame title={`Effect of ${pdp.feature} on the prediction`} svgRef={svgRef} table={g.table} subtitle={pdp.y_label}
      legend={<Legend items={g.series.map((s) => ({ label: s.name }))} />}
      caption={pdp.note || 'The average prediction when only this input is changed and everything else is left as it is in the data. It describes the model, not the real world.'}>
      <Svg svgRef={svgRef} label={`Partial dependence of the prediction on ${pdp.feature}.`}>
        <Axes xs={g.x} ys={g.y} xTicks={g.xticks} yTicks={g.yticks} xLabel={pdp.feature} yLabel={pdp.y_label ?? 'Average prediction'} xText={g.numeric ? undefined : (t) => String(t.label).slice(0, 10)} />
        {g.band ? <path d={g.band} className="chart__band" /> : null}
        {g.series.map((s, i) => (
          <g key={s.name}>
            {g.numeric ? <path d={s.d} className="chart__line" style={{ stroke: colour(i) }} /> : null}
            {s.points.map((p, k) => <circle key={k} cx={p.cx} cy={p.cy} r="4" style={{ fill: colour(i) }}><title>{`${p.at}: ${f3(p.value)}`}</title></circle>)}
          </g>
        ))}
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ model comparison */
export function ModelComparisonBars({ ranking, metricLabel, lowerIsBetter }) {
  const bars = useMemo(() => rankingBars(ranking), [ranking]);
  const svgRef = useRef(null);
  if (!bars.length) return null;
  const rowH = 32; const left = 170; const height = AREA.y0 + rowH * bars.length + 40;
  const w = PLOT.w - left - 40;
  const table = { columns: ['Rank', 'Model', `${metricLabel} (cross-validation)`, 'Spread between folds', `${metricLabel} (held-out)`, 'Overfitting', 'Training time (s)'],
                  rows: bars.map((b) => [b.rank, b.label, b.cv, b.std, b.test, b.overfitting, b.fitSeconds]) };
  return (
    <ChartFrame title="Models compared" svgRef={svgRef} table={table}
      subtitle={`${metricLabel}${lowerIsBetter ? ' (lower is better)' : ' (higher is better)'}. Bars are cross-validation on the training rows; the marker is the held-out score.`}
      caption="Models are ranked on cross-validation, so the held-out rows are never used to choose. When bars overlap within their spread, the models are effectively tied.">
      <Svg svgRef={svgRef} height={height} label={`Comparison of ${bars.length} models by ${metricLabel}.`}>
        <line x1={left} x2={left} y1={AREA.y0} y2={AREA.y0 + rowH * bars.length} className="chart__axis" />
        {bars.map((b, i) => {
          const y = AREA.y0 + i * rowH;
          return (
            <g key={b.id} className="chart__bar">
              <text x={left - 8} y={y + rowH / 2} textAnchor="end" dominantBaseline="middle" className="chart__tick">{b.rank}. {b.label.length > 22 ? `${b.label.slice(0, 21)}…` : b.label}</text>
              <rect x={left} y={y + 5} width={Math.max(1, b.cvWidth * w)} height={rowH - 10} rx="3" className="chart__fill" style={i === 0 ? undefined : { opacity: 0.6 }} />
              {b.std ? <g className="chart__ci"><line x1={left + (b.cvWidth - b.stdWidth) * w} x2={left + (b.cvWidth + b.stdWidth) * w} y1={y + rowH / 2} y2={y + rowH / 2} /></g> : null}
              {b.testAt === null ? null : <line x1={left + b.testAt * w} x2={left + b.testAt * w} y1={y + 3} y2={y + rowH - 3} className="chart__ref" />}
              <text x={left + (b.cvWidth + b.stdWidth) * w + 6} y={y + rowH / 2} dominantBaseline="middle" className="chart__tick">{f3(b.cv)}</text>
            </g>
          );
        })}
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ prediction distribution (batch) */
export function DistributionBars({ summary }) {
  const bars = useMemo(() => distributionBars(summary), [summary]);
  const svgRef = useRef(null);
  if (!bars) return null;
  const rowH = 28; const left = 130; const height = AREA.y0 + rowH * bars.length + 20;
  const w = PLOT.w - left - 110;
  const table = { columns: ['Predicted', 'Rows', 'Share of rows'], rows: bars.map((b) => [b.label, b.rows, b.share]) };
  return (
    <ChartFrame title="What the model predicted" svgRef={svgRef} table={table} subtitle="Number of rows given each prediction" caption="A sudden shift from the training data's mix can be a sign the new rows differ from what the model learned.">
      <Svg svgRef={svgRef} height={height} label={`Predictions by class: ${bars.map((b) => `${b.label} ${b.rows}`).join(', ')}.`}>
        {bars.map((b, i) => {
          const y = AREA.y0 + i * rowH;
          return (
            <g key={b.label} className="chart__bar">
              <text x={left - 8} y={y + rowH / 2} textAnchor="end" dominantBaseline="middle" className="chart__tick">{b.label.length > 18 ? `${b.label.slice(0, 17)}…` : b.label}</text>
              <rect x={left} y={y + 4} width={Math.max(1, b.width * w)} height={rowH - 8} rx="3" className="chart__fill" />
              <text x={left + b.width * w + 6} y={y + rowH / 2} dominantBaseline="middle" className="chart__tick">{formatNumber(b.rows, 0)} ({formatShare(b.share, 1)})</text>
            </g>
          );
        })}
      </Svg>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ small inline bars for a single prediction (the numbers are always written out) */
export function ProbabilityBars({ probabilities, positive }) {
  const bars = probabilityBars(probabilities);
  if (!bars.length) return null;
  return (
    <ul className="meter-list" aria-label="Probability of each outcome">
      {bars.map((b) => (
        <li key={b.label} className="meter-list__row">
          <span className="meter-list__label">{b.label}{positive !== undefined && positive !== null && String(positive) === b.label ? <span className="muted"> (positive class)</span> : null}</span>
          <span className="meter" role="presentation"><span className="meter__fill" style={{ width: `${b.width * 100}%` }} /></span>
          <span className="meter-list__value">{formatShare(b.probability, 1)}</span>
        </li>
      ))}
    </ul>
  );
}

export function EffectBars({ local }) {
  const bars = effectBars(local?.effects);
  if (!bars.length) return null;
  return (
    <ul className="meter-list" aria-label="How each input moved this prediction">
      {bars.map((b) => (
        <li key={b.feature} className="meter-list__row">
          <span className="meter-list__label">{b.feature} <span className="muted">= {String(b.value)}</span></span>
          <span className={`meter meter--${b.direction}`} role="presentation"><span className="meter__fill" style={{ width: `${b.width * 100}%` }} /></span>
          <span className="meter-list__value">{b.effect > 0 ? '+' : ''}{f3(b.effect)} <span className="muted">{b.direction}</span></span>
        </li>
      ))}
    </ul>
  );
}
