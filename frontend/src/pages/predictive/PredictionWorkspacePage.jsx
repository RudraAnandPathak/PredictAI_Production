import { useCallback, useEffect, useMemo, useState } from 'react';
import { predictive } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useParams } from '../../app/router/useParams.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { Tabs } from '../../components/ui/Tabs.jsx';
import { formatNumber } from '../../features/analytics/format.js';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { buildConfig, checkForm, defaultForm, formFromConfig } from '../../features/predictive/config.js';
import { DatasetModelsPanel } from './DatasetModelsPanel.jsx';
import { ObjectivePanel } from './ObjectivePanel.jsx';
import { PreparationPanel } from './PreparationPanel.jsx';
import { TrainingPanel } from './TrainingPanel.jsx';
import { useJobTracker } from './useJob.js';

const TAB_IDS = ['objective', 'prepare', 'train', 'models'];

/** One dataset's modelling workspace. The settings and the latest check live here so they carry across the tabs. */
export function PredictionWorkspacePage() {
  const { assetId } = useParams('/predictions/data/:assetId');
  const initial = new URLSearchParams(window.location.search).get('tab');
  const [tab, setTab] = useState(TAB_IDS.includes(initial) ? initial : 'objective');
  const schema = useResource(() => predictive.schema(assetId), [assetId]);
  const catalog = useResource(() => predictive.catalog(), []);
  const [form, setForm] = useState(defaultForm);
  const [assessment, setAssessment] = useState({ busy: false, data: null, error: '', key: null });
  const tracker = useJobTracker();

  // A finished run adds a model: refresh the dataset summary (the model count in the header) without blanking the page.
  const jobStatus = tracker.job?.status;
  const refreshSchema = schema.reload;
  useEffect(() => { if (jobStatus === 'completed') refreshSchema({ quiet: true }); }, [jobStatus, refreshSchema]);

  const config = useMemo(() => buildConfig(form), [form]);
  const configKey = useMemo(() => JSON.stringify(config), [config]);
  const errors = useMemo(() => checkForm(form, { schema: schema.data, limits: catalog.data?.limits }), [form, schema.data, catalog.data]);

  const update = useCallback((patch) => setForm((f) => ({ ...f, ...patch })), []);
  const setPre = useCallback((patch) => setForm((f) => ({ ...f, preprocessing: { ...f.preprocessing, ...patch } })), []);
  const setRole = useCallback((column, role) => setForm((f) => {
    const roles = { ...f.roles };
    if (role === 'default') delete roles[column]; else roles[column] = role;
    return { ...f, roles };
  }), []);
  const applyConfig = useCallback((proposed) => setForm(formFromConfig(proposed)), []);

  const runAssess = useCallback(async () => {
    setAssessment((a) => ({ ...a, busy: true, error: '' }));
    try {
      const data = await predictive.assess(assetId, config, schema.data?.version_id);
      setAssessment({ busy: false, data, error: '', key: JSON.stringify(config) });
    } catch (e) { setAssessment((a) => ({ ...a, busy: false, error: describeError(e) })); }
  }, [assetId, config, schema.data?.version_id]);

  if ((schema.loading && !schema.data) || (catalog.loading && !catalog.data)) return <PageLoading label="Opening dataset…" />;
  const failed = schema.error || catalog.error;
  if (failed) {
    const missing = schema.error?.status === 404;
    return (
      <article className="page">
        <ErrorState title={missing ? 'Dataset not found' : 'Could not open the dataset for modelling'}
          message={missing ? 'This dataset does not exist, or it is not yours.' : describeError(failed)} onRetry={missing ? null : () => { schema.reload(); catalog.reload(); }} />
        <p><Link to="/predictions" className="btn btn--ghost">Back to predictions</Link></p>
      </article>
    );
  }

  const s = schema.data;
  const ws = {
    schema: s, catalog: catalog.data, form, update, setPre, setRole, applyConfig, errors, config, setTab, tracker,
    assessment: { ...assessment, stale: assessment.data !== null && assessment.key !== configKey }, runAssess,
  };
  const tabs = [
    { id: 'objective', label: '1. Objective', panel: <ObjectivePanel key="o" ws={ws} /> },
    { id: 'prepare', label: '2. Data preparation', panel: <PreparationPanel key="p" ws={ws} /> },
    { id: 'train', label: '3. Training', panel: <TrainingPanel key="t" ws={ws} /> },
    { id: 'models', label: 'Models', panel: <DatasetModelsPanel key="m" ws={ws} /> },
  ];
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to="/predictions">Predictions</Link> / Dataset</p>
        <h1 className="page-title">{s.name}</h1>
        <div className="pill-row" aria-label="Dataset summary">
          <span className="badge badge--muted">{formatNumber(s.rows, 0)} rows</span>
          <span className="badge badge--muted">{s.columns.length} columns</span>
          <span className="badge badge--muted">Version {s.version_number}</span>
          <span className="badge badge--muted">{s.models} {s.models === 1 ? 'model' : 'models'}</span>
        </div>
        <p><Link to={`/analytics/${encodeURIComponent(assetId)}`} className="btn btn--ghost btn--sm">Explore this dataset first</Link></p>
      </header>
      <Tabs label="Prediction steps" tabs={tabs} active={tab} onChange={setTab} />
    </article>
  );
}
