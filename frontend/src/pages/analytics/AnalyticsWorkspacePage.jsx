import { useState } from 'react';
import { analytics } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { Tabs } from '../../components/ui/Tabs.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { formatNumber } from '../../features/analytics/format.js';
import { AskTab } from './AskTab.jsx';
import { ChartsTab } from './ChartsTab.jsx';
import { InsightsTab } from './InsightsTab.jsx';
import { PresetTab } from './OverviewTab.jsx';
import { SavedTab } from './SavedTab.jsx';
import { TestsTab } from './TestsTab.jsx';

const REL_TITLES = { discover: 'Strongest relationships', correlation: 'Correlation table' };
const TAB_IDS = ['overview', 'explore', 'tests', 'charts', 'insights', 'ask', 'saved'];

export function AnalyticsWorkspacePage() {
  const { assetId } = useParams('/analytics/:assetId');
  const initial = new URLSearchParams(window.location.search).get('tab');
  const [tab, setTab] = useState(TAB_IDS.includes(initial) ? initial : 'overview');
  const schema = useResource(() => analytics.schema(assetId), [assetId]);

  if (schema.loading && !schema.data) return <PageLoading label="Opening dataset…" />;
  if (schema.error) {
    const status = schema.error.status;
    const missing = status === 404; const notReady = status === 409 || status === 422;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Dataset not found' : notReady ? 'This file cannot be analysed yet' : 'Could not open the dataset'}
          message={missing ? 'This dataset does not exist, or it is not yours.' : describeError(schema.error)} onRetry={missing ? null : () => schema.reload()} />
        <p><Link to="/analytics" className="btn btn--ghost">Back to analytics</Link></p>
      </article>
    );
  }
  const s = schema.data;
  const tabs = [
    { id: 'overview', label: 'Overview', panel: <PresetTab key="o" schema={s} preset="dashboard" intro="Descriptive statistics for every column, with data-quality checks. Change the filters to analyse a subset; each result states exactly which rows were used." /> },
    { id: 'explore', label: 'Relationships', panel: <PresetTab key="r" schema={s} preset="relationships" titles={REL_TITLES} intro="Associations between columns, ranked by strength and stability and adjusted for the number of pairs examined. An association is not evidence that one thing causes another." /> },
    { id: 'tests', label: 'Tests', panel: <TestsTab schema={s} /> },
    { id: 'charts', label: 'Charts', panel: <ChartsTab schema={s} /> },
    { id: 'insights', label: 'Insights', panel: <InsightsTab schema={s} /> },
    { id: 'ask', label: 'Ask', panel: <AskTab schema={s} /> },
    { id: 'saved', label: 'Saved', panel: <SavedTab schema={s} /> },
  ];
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/analytics">Analytics</Link> / Dataset</p>
        <h1 className="page-title">{s.name}</h1>
        <div className="pill-row" aria-label="Dataset summary">
          <span className="badge badge--muted">{formatNumber(s.rows, 0)} rows</span>
          <span className="badge badge--muted">{s.columns.length} columns</span>
          <span className="badge badge--muted">Version {s.version_number}</span>
        </div>
        <p><Link to={`/projects`} className="btn btn--ghost btn--sm">Projects and cleaning</Link></p>
      </header>
      <Tabs label="Analytics sections" tabs={tabs} active={tab} onChange={setTab} />
    </article>
  );
}
