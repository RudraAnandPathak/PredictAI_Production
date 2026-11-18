import { predictive } from '../../api/index.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { ModelsList } from './ModelsList.jsx';

export function DatasetModelsPanel({ ws }) {
  const list = useResource(() => predictive.models({ asset_id: ws.schema.asset_id }), [ws.schema.asset_id, ws.tracker.job?.status]);
  if (list.loading && !list.data) return <PageLoading label="Loading models…" />;
  if (list.error) return <ErrorState title="Could not load the models" message={describeError(list.error)} onRetry={() => list.reload()} />;
  const items = list.data?.items ?? [];
  return (
    <section className="stack" aria-label="Models for this dataset">
      {items.length === 0 ? (
        <div className="state" role="status"><div className="state__body"><h2 className="state__title">No models yet</h2><p>Models you train on this dataset appear here.</p>
          <button type="button" className="btn" onClick={() => ws.setTab(ws.form.target ? 'train' : 'objective')}>{ws.form.target ? 'Go to training' : 'Choose what to predict'}</button></div></div>
      ) : <ModelsList items={items} onChanged={() => list.reload({ quiet: true })} />}
    </section>
  );
}
