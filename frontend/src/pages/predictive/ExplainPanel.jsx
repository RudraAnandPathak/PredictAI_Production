import { useEffect, useState } from 'react';
import { predictive } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { ImportanceBars, PdpChart } from '../../components/predictive/MlCharts.jsx';
import { describeError } from '../../features/identity/validation.js';
import { formatScore, formatShare } from '../../features/predictive/format.js';
import { Notice } from './shared.jsx';

function NativeImportance({ native }) {
  if (!native?.available) return native ? <p className="muted">Built-in importance is not available for this kind of model{native.reason ? `: ${native.reason}` : ''}.</p> : null;
  return (
    <div className="card stack" aria-label="Importance from the model itself">
      <h3 className="card__title">What the model itself reports</h3>
      <p>{native.basis}</p>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Built-in importance">
        <table className="table table--compact">
          <thead><tr><th scope="col">Input</th><th scope="col">Size</th><th scope="col">Share</th></tr></thead>
          <tbody>{native.features.map((f) => <tr key={f.feature}><td>{f.feature}</td><td>{formatScore(f.importance, 4)}</td><td>{formatShare(f.share, 1)}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

/** Interface 7: Explainable AI. What the model relies on, and how its answer changes as one input changes. It never claims an input causes the outcome. */
export function ExplainPanel({ report, model }) {
  const imp = report.importance ?? {};
  const ex = report.explanations ?? {};
  const label = report.best?.primary_label;
  const features = (model.features?.used ?? []).map((f) => (typeof f === 'string' ? f : f.name));
  const ready = (imp.permutation?.features ?? []).map((f) => f.feature).filter((f) => features.includes(f));
  const [feature, setFeature] = useState('');
  const [pdp, setPdp] = useState({ busy: false, data: null, error: '' });

  useEffect(() => { if (!feature && ready.length) setFeature(ready[0]); }, [ready.join('|')]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function load(name) {
    setPdp({ busy: true, data: null, error: '' });
    try { setPdp({ busy: false, data: await predictive.partialDependence(model.id, name), error: '' }); } catch (e) { setPdp({ busy: false, data: null, error: describeError(e) }); }
  }
  const stored = (imp.partial_dependence ?? []).find((p) => p.feature === feature);
  const shown = pdp.data && pdp.data.feature === feature ? pdp.data : stored;

  return (
    <section className="stack" aria-label="Explainable AI">
      <Notice kind="info" title="What these explanations can and cannot say">
        They describe how <em>this model</em> behaves. A strong link between an input and the prediction does not mean that input causes the outcome in the real world.
      </Notice>
      <ImportanceBars permutation={imp.permutation} metricLabel={label} />
      {imp.permutation && !imp.permutation.available ? <p className="muted">Permutation importance is not available: {imp.permutation.reason ?? 'not enough held-out rows.'}</p> : null}
      {imp.permutation?.reading ? <p>{imp.permutation.reading}</p> : null}
      <Explanation value={ex.importance} />
      <NativeImportance native={imp.native} />

      <div className="card stack" aria-label="Partial dependence">
        <h3 className="card__title">How the prediction changes with one input</h3>
        <p className="muted">Pick an input. The chart shows the model’s average answer as only that input changes and the others stay as they are in the data.</p>
        <div className="toolbar">
          <Field as="select" label="Input" value={feature} onChange={(e) => { setFeature(e.target.value); setPdp({ busy: false, data: null, error: '' }); }}>
            {features.map((f) => <option key={f} value={f}>{f}</option>)}
          </Field>
          <button type="button" className="btn" disabled={!feature || pdp.busy} onClick={() => load(feature)}>{pdp.busy ? <><Spinner label="Calculating" /> Calculating…</> : 'Calculate for this input'}</button>
        </div>
        {pdp.error ? <Notice kind="error">{pdp.error}</Notice> : null}
        {shown && shown.available ? (
          <>
            <PdpChart pdp={shown} />
            <Explanation value={shown.explanation} />
          </>
        ) : null}
        {shown && !shown.available ? <Notice kind="warn" title="Not available for this input">{shown.note || shown.reason || 'There is not enough varied data to draw this reliably.'}</Notice> : null}
        {!shown && !pdp.busy ? <p className="muted">Charts for the most influential inputs are included when the model is trained; others are calculated when you ask.</p> : null}
      </div>

      <div className="card stack" aria-label="SHAP">
        <h3 className="card__title">SHAP values</h3>
        <p className="muted">{imp.shap?.reason ?? 'SHAP values are not available in this build.'}</p>
      </div>
    </section>
  );
}
