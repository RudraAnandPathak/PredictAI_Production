import { useState } from 'react';
import { predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { Tabs } from '../../components/ui/Tabs.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { PROBLEM_SHORT, headline, modelTitle } from '../../features/predictive/format.js';
import { BatchPanel } from './BatchPanel.jsx';
import { ComparisonPanel } from './ComparisonPanel.jsx';
import { DetailsPanel } from './DetailsPanel.jsx';
import { EvaluationPanel } from './EvaluationPanel.jsx';
import { ExplainPanel } from './ExplainPanel.jsx';
import { PredictPanel } from './PredictPanel.jsx';
import { WhatIfPanel } from './WhatIfPanel.jsx';
import { Badge } from './shared.jsx';

const TAB_IDS = ['evaluation', 'comparison', 'explain', 'predict', 'batch', 'details'];

function WithReport({ report, children }) {
  if (report.loading && !report.data) return <PageLoading label="Loading the training report…" />;
  if (report.error) return <ErrorState title="Could not load the training report" message={describeError(report.error)} onRetry={() => report.reload()} />;
  return children(report.data);
}

export function ModelPage() {
  const { modelId } = useParams('/predictions/models/:modelId');
  const initial = new URLSearchParams(window.location.search).get('tab');
  const [tab, setTab] = useState(TAB_IDS.includes(initial) ? initial : 'evaluation');
  const model = useResource(() => predictive.model(modelId), [modelId]);
  const report = useResource(() => predictive.report(modelId), [modelId]);

  if (model.loading && !model.data) return <PageLoading label="Opening model…" />;
  if (model.error) {
    const missing = model.error.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Model not found' : 'Could not open the model'} message={missing ? 'This model does not exist, or it is not yours.' : describeError(model.error)} onRetry={missing ? null : () => model.reload()} />
        <p><Link to="/predictions/models" className="btn btn--ghost">Back to models</Link></p>
      </article>
    );
  }
  const m = model.data;
  const tabs = [
    { id: 'evaluation', label: 'Evaluation', panel: <WithReport key="e" report={report}>{(r) => <EvaluationPanel report={r} model={m} />}</WithReport> },
    { id: 'comparison', label: 'Model comparison', panel: <WithReport key="c" report={report}>{(r) => <ComparisonPanel report={r} />}</WithReport> },
    { id: 'explain', label: 'Explainable AI', panel: <WithReport key="x" report={report}>{(r) => <ExplainPanel report={r} model={m} />}</WithReport> },
    { id: 'predict', label: 'Predict one case', panel: <PredictPanel key="p" model={m} /> },
    { id: 'whatif', label: 'What if…', panel: <WhatIfPanel key="w" model={m} /> },
    { id: 'batch', label: 'Predict a dataset', panel: <BatchPanel key="b" model={m} /> },
    { id: 'details', label: 'Details and history', panel: <DetailsPanel key="d" model={m} report={report.data} onChanged={() => model.reload({ quiet: true })} /> },
  ];
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/predictions">Predictions</Link> / <Link to="/predictions/models">Models</Link></p>
        <h1 className="page-title">{modelTitle(m)}</h1>
        <div className="pill-row" aria-label="Model summary">
          <Badge>{m.algorithm_label}{m.tuned ? ' (tuned)' : ''}</Badge>
          <Badge>{PROBLEM_SHORT[m.problem_type] ?? m.problem_type}</Badge>
          <Badge>Predicts “{m.target}”</Badge>
          <Badge tone="ok">{headline(m)}</Badge>
          <Badge>Version {m.model_version}</Badge>
        </div>
        <p className="muted">These scores describe how the model did on past data. They do not guarantee how it will do on new cases, and they do not show what causes the outcome.</p>
      </header>
      <Tabs label="Model sections" tabs={tabs} active={tab} onChange={setTab} />
    </article>
  );
}
