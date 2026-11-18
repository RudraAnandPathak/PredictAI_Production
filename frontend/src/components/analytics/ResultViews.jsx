import { formatNumber, formatP, formatPercent } from '../../features/analytics/format.js';
import { Explanation } from '../ui/Explanation.jsx';

const T = ({ head, rows, caption }) => (
  <div className="table-wrap" tabIndex={0} role="region" aria-label={caption || 'Results table'}>
    <table className="table table--compact">
      {caption ? <caption className="sr-only">{caption}</caption> : null}
      <thead><tr>{head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody>
    </table>
  </div>
);
const n = (v, p = 2) => formatNumber(v, p);

export function Assumptions({ list }) {
  if (!list?.length) return null;
  return (
    <ul className="assumptions" aria-label="Assumption checks">
      {list.map((a, i) => (
        <li key={i} className={`assumption assumption--${a.status}`}>
          <span className="badge badge--muted">{String(a.status).replace('_', ' ')}</span> <strong>{a.name}</strong>{a.detail ? <> — {a.detail}</> : null}
        </li>
      ))}
    </ul>
  );
}

function Hypothesis({ data, evidence }) {
  const h = data.hypothesis; const res = data.result;
  return (
    <div className="stack">
      <p><strong>{h.statement}</strong></p>
      <dl className="kv">
        <dt>Null hypothesis (H0)</dt><dd>{h.null}</dd>
        <dt>Alternative (H1)</dt><dd>{h.alternative_text}</dd>
        <dt>Significance level</dt><dd>{h.alpha} ({h.alternative})</dd>
        {data.selected_test ? <><dt>Test chosen</dt><dd>{data.selected_test.name} — {data.selected_test.why}</dd></> : null}
        {data.design?.groups ? <><dt>Groups</dt><dd>{data.design.groups.map((g) => `${g.name} (n = ${g.n})`).join(', ')}</dd></> : null}
      </dl>
      <h4 className="sub">Assumption checks</h4>
      <Assumptions list={data.assumption_checks} />
      {!data.executed || !res ? (
        <div className="message message--warn" role="status">
          <p className="message__title">{data.executed === false && !data.result && data.plan_only ? 'Plan only' : 'Test not run'}</p>
          <p>{data.not_run_reason || (data.executed === false ? 'Only the plan was produced. Run it to see the result.' : 'The assumptions for this test are not met, so it was not run as if they were.')}</p>
        </div>
      ) : (
        <>
          <h4 className="sub">Result</h4>
          <dl className="kv">
            <dt>Test</dt><dd>{res.test}</dd>
            <dt>Statistic</dt><dd>{res.statistic_name} = {n(res.statistic, 4)}{res.df !== undefined && res.df !== null ? `, df = ${Array.isArray(res.df) ? res.df.map((v) => n(v, 1)).join(', ') : n(res.df, 1)}` : ''}</dd>
            <dt>p-value</dt><dd>{formatP(res.p_value)} <span className="muted">({res.p_value})</span></dd>
            {res.effect_size ? <><dt>Effect size</dt><dd>{res.effect_size.name}: {n(res.effect_size.value, 3)}{res.effect_size.ci ? ` (95% CI ${n(res.effect_size.ci[0], 3)} to ${n(res.effect_size.ci[1], 3)})` : ''}</dd></> : null}
            <dt>Sample size</dt><dd>{evidence?.sample_size !== undefined ? n(evidence.sample_size) : '—'}</dd>
            <dt>Missing data</dt><dd>{evidence?.null_policy || '—'}</dd>
            <dt>Decision</dt><dd>{res.decision?.wording}</dd>
            <dt>Practical size</dt><dd>{res.decision?.practical_relevance}</dd>
          </dl>
          {res.post_hoc ? (
            <>
              <h4 className="sub">Pairwise comparisons ({res.post_hoc.adjustment}-adjusted)</h4>
              <T head={['Group', 'versus', 'Difference', 'Effect', 'Adjusted p']} rows={res.post_hoc.comparisons.map((c) => [c.group, c.versus, n(c.difference), `${c.effect_name} ${n(c.effect, 2)}`, formatP(c.p_adjusted_holm)])} />
              <p className="muted">{res.post_hoc.note}</p>
            </>
          ) : null}
        </>
      )}
      {data.p_value_note ? <p className="note">{data.p_value_note}</p> : null}
      {data.multiple_testing ? <p className="note">{data.multiple_testing.note}</p> : null}
    </div>
  );
}

const VIEWS = {
  dataset_summary: ({ data }) => (
    <dl className="kv">
      <dt>Rows in scope</dt><dd>{n(data.rows_in_scope)} of {n(data.rows_in_dataset)}</dd>
      <dt>Columns</dt><dd>{n(data.columns)} ({Object.entries(data.column_types).map(([k, v]) => `${v} ${k}`).join(', ')})</dd>
      <dt>Empty cells</dt><dd>{n(data.missing_cells.count)} ({formatPercent(data.missing_cells.pct, 2)})</dd>
      <dt>Duplicate rows</dt><dd>{n(data.duplicate_rows.count)} — {data.duplicate_rows.definition}</dd>
    </dl>
  ),
  data_quality: ({ data }) => (data.issues.length === 0 ? <p>No data-quality issues were found by the {n(data.checked_columns)} checks.</p> : (
    <ul className="issues">{data.issues.map((i, k) => (
      <li key={k}><span className={`badge ${i.severity === 'warning' ? 'badge--warn' : 'badge--muted'}`}>{i.severity}</span> {i.message}{i.row_numbers?.length ? <span className="muted"> Rows: {i.row_numbers.slice(0, 8).join(', ')}{i.row_numbers.length > 8 ? '…' : ''}</span> : null}</li>
    ))}</ul>
  )),
  missing_values: ({ data }) => (
    <T head={['Column', 'Missing', 'Share']} rows={[...data.columns].sort((a, b) => b.missing - a.missing).map((c) => [c.column, n(c.missing), formatPercent(c.pct)])} caption="Missing values by column" />
  ),
  column_stats: ({ data }) => (
    <T head={['Column', 'n', 'Missing', 'Mean', 'Median', 'Std dev', 'Min', 'Max', 'Skew', 'Outliers']}
      rows={data.columns.map((c) => [c.column, n(c.non_missing), n(c.missing), n(c.mean), n(c.median), n(c.std), n(c.min), n(c.max), n(c.skewness), c.outliers ? n(c.outliers.count) : '—'])} caption="Column statistics" />
  ),
  frequency: ({ data }) => (data.values || data.bins ? (
    <T head={['Value', 'Count', 'Share']} rows={(data.values || data.bins).map((v) => [v.value ?? `${n(v.lo)}–${n(v.hi)}`, n(v.count), formatPercent(v.pct)])} />
  ) : <pre className="json">{JSON.stringify(data, null, 2)}</pre>),
  aggregate: ({ data }) => (
    <div className="stack">
      <p>Overall {data.agg}{data.measure ? ` of ${data.measure}` : ''}: <strong>{n(data.overall.value)}</strong> (n = {n(data.overall.n)}){data.overall.ci ? `, 95% CI ${n(data.overall.ci[0])} to ${n(data.overall.ci[1])}` : ''}</p>
      {data.groups.length ? <T head={[...data.group_by, 'Value', 'n', '95% CI']} rows={data.groups.map((g) => [...data.group_by.map((k) => g.key[k]), n(g.value), n(g.n), g.ci ? `${n(g.ci[0])} to ${n(g.ci[1])}` : '—'])} /> : null}
    </div>
  ),
  group_compare: ({ data }) => (
    <T head={[data.group, 'n', 'Mean', 'Median', 'Std dev', '95% CI of mean']} rows={data.groups.map((g) => [g.group + (g.reference ? ' (reference)' : ''), n(g.n), n(g.mean), n(g.median), n(g.std), g.mean_ci ? `${n(g.mean_ci[0])} to ${n(g.mean_ci[1])}` : '—'])} caption={`${data.measure} by ${data.group}`} />
  ),
  contingency: ({ data }) => (
    <div className="stack">
      <T head={[`${data.row} \\ ${data.column}`, ...data.columns, 'Total']} rows={data.rows.map((r, i) => [r, ...data.counts[i].map((c) => n(c)), n(data.row_totals[i])])} caption="Counts" />
      {data.chi_square?.statistic !== undefined ? <p>Chi-square = {n(data.chi_square.statistic, 3)}, df = {data.chi_square.df}, {formatP(data.chi_square.p_value)}{data.chi_square.valid === false ? ' — expected counts are too small for this test to be trusted.' : ''}</p> : null}
    </div>
  ),
  correlation: ({ data }) => (
    <div className="stack">
      <T head={['Pair', 'n', 'Spearman rho', 'Pearson r', 'Strength', 'Direction']}
        rows={data.pairs.slice(0, 40).map((p) => [`${p.x} · ${p.y}`, n(p.n), p.spearman ? n(p.spearman.rho, 3) : '—', p.pearson ? n(p.pearson.r, 3) : '—', p.magnitude, p.direction])} caption="Correlations" />
      {data.pairs.some((p) => p.pearson_caution) ? <p className="note">{data.pairs.find((p) => p.pearson_caution).pearson_caution}</p> : null}
      <p className="note">{data.causation_note}</p>
    </div>
  ),
  discover: ({ data }) => (
    data.patterns.length === 0 ? <p><strong>Not enough evidence.</strong> No pair of columns shows a stable, non-trivial association after adjusting for the {n(data.pairs_examined)} pairs examined.</p> : (
      <div className="stack">
        <T head={['Columns', 'Measure', 'Strength', 'Adj. p (BH)', 'n', 'Stability', 'Score']}
          rows={data.patterns.map((p) => [`${p.x} · ${p.y}`, p.measure, `${n(p.strength, 2)} (${p.practical_relevance})`, formatP(p.p_adjusted_bh), n(p.n), p.stability === null ? '—' : n(p.stability, 2), n(p.score, 3)])} caption="Ranked patterns" />
        <p className="muted">Ranked by strength 40%, coverage 20%, stability 25% and practical relevance 15%. {data.excluded_total} pairs were excluded (too weak, unstable or not distinguishable from chance).</p>
        <p className="note">{data.causation_note}</p>
      </div>
    )
  ),
  trend: ({ data }) => (
    <div className="stack">
      <p>Per {data.freq}, {data.agg}{data.measure ? ` of ${data.measure}` : ' of rows'}: {n(data.periods)} periods{data.highest ? `; highest ${n(data.highest.value)} (${data.highest.period_start}), lowest ${n(data.lowest.value)} (${data.lowest.period_start})` : ''}.</p>
      {data.trend.conclusion === 'Not enough evidence.' ? <p><strong>Not enough evidence</strong> of a trend: the slope is not distinguishable from chance.</p>
        : <p>Trend: {data.trend.direction}, {n(data.trend.slope_per_period)} per {data.freq} (95% CI {n(data.trend.slope_ci[0])} to {n(data.trend.slope_ci[1])}, {formatP(data.trend.slope_p_value)}).</p>}
    </div>
  ),
  hypothesis: Hypothesis,
};

const DEFAULT_TITLES = {
  dataset_summary: 'Dataset summary', data_quality: 'Data quality', missing_values: 'Missing values', column_stats: 'Column statistics', frequency: 'Value counts',
  aggregate: 'Summary', group_compare: 'Group comparison', contingency: 'Category counts', correlation: 'Correlations', discover: 'Strongest relationships',
  trend: 'Trend over time', hypothesis: 'Hypothesis test', chart: 'Chart', distribution_compare: 'Distribution comparison', temporal_correlation: 'Correlation over time',
};

export function ResultBlock({ result, title, titles }) {
  const View = VIEWS[result.op];
  const ev = result.evidence || {};
  return (
    <section className="result" aria-label={title || titles?.[result.op] || DEFAULT_TITLES[result.op] || result.op}>
      <header className="result__head">
        <h3 className="result__title">{title || titles?.[result.op] || DEFAULT_TITLES[result.op] || result.op}</h3>
        <span className={`badge ${result.status === 'ok' ? 'badge--ok' : result.status === 'failed' ? 'badge--danger' : 'badge--warn'}`}>{result.status === 'ok' ? (result.evidence_status || 'ok') : result.status === 'not_run' ? 'not run' : result.status}</span>
      </header>
      {result.status === 'failed' ? <p className="message message--error" role="alert">{result.error?.message || 'This analysis could not be completed.'}</p> : null}
      {result.status === 'not_run' ? <p className="message message--warn" role="status"><strong>Not enough evidence.</strong> {result.data?.message || result.data?.not_run_reason || result.warnings?.[0] || 'There is not enough data for this analysis.'}</p> : null}
      {(result.status === 'ok' || (result.status === 'not_run' && result.op === 'hypothesis')) && result.data && View ? <View data={result.data} evidence={ev} /> : null}
      {result.status === 'ok' && result.data && !View ? <pre className="json">{JSON.stringify(result.data, null, 2).slice(0, 4000)}</pre> : null}
      <Explanation value={result.explanation} />
      {result.warnings?.length ? <ul className="warnings">{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
      {(ev.method || ev.limitations?.length) ? (
        <details className="how">
          <summary>How this was calculated</summary>
          <dl className="kv">
            {ev.method ? <><dt>Method</dt><dd>{ev.method}</dd></> : null}
            {ev.sample_size !== undefined ? <><dt>Sample size</dt><dd>{n(ev.sample_size)}</dd></> : null}
            {ev.null_policy ? <><dt>Missing data</dt><dd>{ev.null_policy}</dd></> : null}
            {ev.uncertainty ? <><dt>Uncertainty</dt><dd>{ev.uncertainty.available === false ? ev.uncertainty.reason : (ev.uncertainty.kind || 'see the result')}</dd></> : null}
          </dl>
          {ev.assumptions?.length ? <><h4 className="sub">Assumptions</h4><Assumptions list={ev.assumptions} /></> : null}
          {ev.limitations?.length ? <><h4 className="sub">Limitations</h4><ul>{ev.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></> : null}
        </details>
      ) : null}
    </section>
  );
}

export function RunResults({ run, titles }) {
  const results = run?.result?.results ?? [];
  return <div className="stack">{results.map((r, i) => <ResultBlock key={i} result={r} titles={titles} />)}</div>;
}
