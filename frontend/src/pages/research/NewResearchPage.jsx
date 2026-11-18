import { useEffect, useMemo, useState } from 'react';
import { research, workspace } from '../../api/index.js';
import { Link } from '../../app/router/Link.jsx';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { useParams } from '../../app/router/useParams.js';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { ARXIV_MODE_LABEL, buildSessionBody, pickableAssets, webOffReasons } from '../../features/research/plan.js';
import { Notice } from './shared.jsx';

const INITIAL = { question: '', title: '', level: 'topic', purpose: 'general', documents: [], datasets: [], web: false, providers: [], freshnessDays: '', aiSummary: false, aiPlan: false, arxivMode: 'auto', reuseEarlier: false };

function AssetChoice({ label, assets, chosen, onToggle, empty }) {
  return (
    <fieldset className="card stack" aria-label={label}>
      <legend className="card__title">{label}</legend>
      {assets.length === 0 ? <p className="muted">{empty}</p> : (
        <ul className="list">
          {assets.map((a) => (
            <li key={a.id} className="row">
              <label className="check-field"><input type="checkbox" checked={chosen.includes(a.id)} onChange={() => onToggle(a.id)} /><span className="check-field__label">{a.name}</span></label>
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}

/** Interface: new research workspace. Making the plan sends nothing anywhere; the person approves it on the next screen before anything runs. */
export function NewResearchPage() {
  const { projectId } = useParams('/research/p/:projectId/new');
  const { navigate } = useRouter();
  const [form, setForm] = useState(INITIAL);
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const caps = useResource(() => research.capabilities(projectId), [projectId]);
  const docs = useResource(() => workspace.assets.list(projectId, { kind: 'document', limit: 100 }), [projectId]);
  const sets = useResource(() => workspace.assets.list(projectId, { kind: 'dataset', limit: 100 }), [projectId]);
  const update = (patch) => setForm((f) => ({ ...f, ...patch }));
  const toggle = (field) => (id) => setForm((f) => ({ ...f, [field]: f[field].includes(id) ? f[field].filter((x) => x !== id) : [...f[field], id] }));

  const webAllowed = Boolean(caps.data?.web?.allowed);
  const providers = useMemo(() => (caps.data?.search_providers ?? []).filter((p) => p.available && (caps.data?.web?.providers ?? []).includes(p.key)), [caps.data]);
  useEffect(() => { if (providers.length && form.providers.length === 0) setForm((f) => ({ ...f, providers: providers.map((p) => p.key) })); }, [providers]); // eslint-disable-line react-hooks/exhaustive-deps

  // A short preview of which of the person's own material would be used (names and counts only), refreshed when the question or the choices change.
  const previewKey = JSON.stringify([form.question.trim().length >= 3, form.level, form.documents, form.datasets, form.reuseEarlier]);
  const preview = useResource(() => (form.question.trim().length >= 3 ? research.context(projectId, { question: form.question.trim(), level: form.level, options: { documents: form.documents, datasets: form.datasets, reuse_earlier: form.reuseEarlier, web: false } }) : Promise.resolve(null)), [projectId, previewKey]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(event) {
    event.preventDefault();
    setFailure('');
    const built = buildSessionBody({ ...form, web: form.web && webAllowed });
    if (built.errors) { setErrors(built.errors); return; }
    setErrors({});
    setBusy(true);
    try {
      const session = await research.createSession(projectId, built.body);
      navigate(`/research/session/${encodeURIComponent(session.id)}`);
    } catch (e) {
      setFailure(describeError(e));
      setBusy(false);
    }
  }

  if (caps.loading && !caps.data) return <PageLoading label="Preparing the research workspace…" />;
  if (caps.error) return <article className="page"><ErrorState title="Could not open the research workspace" message={describeError(caps.error)} onRetry={() => caps.reload()} /></article>;
  const levels = caps.data.levels;
  const level = levels.find((l) => l.key === form.level);
  const why = webOffReasons(null, caps.data);

  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow"><Link to={`/research/p/${encodeURIComponent(projectId)}`}>Research</Link></p>
        <h1 className="page-title">New research</h1>
        <p className="lead">Write the question and choose what to use. You will see the plan, and exactly what would leave this server, before anything runs.</p>
      </header>
      <form className="stack" onSubmit={submit} noValidate aria-label="New research">
        <section className="card stack">
          <Field label="Your question" as="textarea" rows={3} value={form.question} onChange={(e) => update({ question: e.target.value })} error={errors.question} maxLength={2100} hint="Ask one thing at a time. For example: How high did the river rise, and how many people were affected?" required />
          <Field label="Title (optional)" value={form.title} onChange={(e) => update({ title: e.target.value })} error={errors.title} maxLength={320} />
          <Field label="Depth" as="select" value={form.level} onChange={(e) => update({ level: e.target.value })} hint={level ? `${level.about} Up to ${level.subtopics} part${level.subtopics === 1 ? '' : 's'}, at least ${level.min_sources} source${level.min_sources === 1 ? '' : 's'}.` : undefined}>
            {levels.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
          </Field>
          <Field label="What is this for?" as="select" value={form.purpose} onChange={(e) => update({ purpose: e.target.value })}>
            <option value="general">General research</option>
            <option value="education">Study support (course documents)</option>
          </Field>
        </section>

        <AssetChoice label="Your documents" assets={pickableAssets(docs.data?.items, 'document')} chosen={form.documents} onToggle={toggle('documents')} empty={docs.loading ? 'Loading…' : 'No documents in this project. Upload some under Projects, or research with the web if you allow it.'} />
        <AssetChoice label="Your datasets" assets={pickableAssets(sets.data?.items, 'dataset')} chosen={form.datasets} onToggle={toggle('datasets')} empty={sets.loading ? 'Loading…' : 'No datasets in this project.'} />
        {form.datasets.length ? <p className="row__meta" role="note">Questions about a dataset are answered by calculation on this server with PredictAI’s analysis and model tools, and the numbers come only from those results. Up to {caps.data?.dataset_research?.max_datasets ?? 3} datasets can be used at once. Add a question like “compare this with outside sources” and switch on web research to see what outside sources say beside your data.</p> : null}

        <fieldset className="card stack" aria-label="Outside sources">
          <legend className="card__title">Outside sources</legend>
          {webAllowed ? (
            <>
              <label className="check-field"><input type="checkbox" checked={form.web} onChange={(e) => update({ web: e.target.checked })} /><span><span className="check-field__label">Also search the web for this question</span><span className="field__hint">Only short search phrases made from your question would be sent. No text from your documents or datasets is ever sent.</span></span></label>
              {form.web ? (
                <>
                  <div className="stack" role="group" aria-label="Where to search">
                    {providers.map((p) => (
                      <label key={p.key} className="check-field"><input type="checkbox" checked={form.providers.includes(p.key)} onChange={() => toggle('providers')(p.key)} /><span><span className="check-field__label">{p.label}</span><span className="field__hint">{p.description}</span></span></label>
                    ))}
                  </div>
                  <label className="field"><span className="field__label">arXiv (research papers)</span>
                    <select value={form.arxivMode} onChange={(e) => update({ arxivMode: e.target.value })}>{Object.entries(ARXIV_MODE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                    <span className="field__hint">arXiv holds papers in science, mathematics, computing and economics. It is skipped for history and similar questions unless you choose “Always”, and the plan says when it was skipped.</span>
                  </label>
                  <Field label="Only use pages newer than (days, optional)" inputMode="numeric" value={form.freshnessDays} onChange={(e) => update({ freshnessDays: e.target.value })} error={errors.freshnessDays} hint="Older pages are still shown if used, but marked as outdated." />
                </>
              ) : null}
            </>
          ) : (
            <Notice kind="info" title="Web research is off">
              <ul>{why.map((w, i) => <li key={i}>{w}</li>)}</ul>
              <p><Link to={`/research/p/${encodeURIComponent(projectId)}?tab=privacy`}>Change this for the project</Link> or <Link to="/research/settings">in your settings</Link>.</p>
            </Notice>
          )}
        </fieldset>

        <fieldset className="card stack" aria-label="More options">
          <legend className="card__title">More options</legend>
          <label className="check-field"><input type="checkbox" checked={form.reuseEarlier} onChange={(e) => update({ reuseEarlier: e.target.checked })} /><span><span className="check-field__label">Also use this project’s earlier saved research</span><span className="field__hint">Earlier findings are brought in as labelled material, never as new facts.</span></span></label>
          <label className="check-field"><input type="checkbox" checked={form.aiPlan} onChange={(e) => update({ aiPlan: e.target.checked })} /><span><span className="check-field__label">Let an AI model suggest extra parts of the plan</span><span className="field__hint">Optional. It can only add parts you can see and approve; it never adds searches. Only the question’s words are sent, never your files, and only to a provider your settings allow.</span></span></label>
          <label className="check-field"><input type="checkbox" checked={form.aiSummary} onChange={(e) => update({ aiSummary: e.target.checked })} /><span><span className="check-field__label">Let an AI model word the summary</span><span className="field__hint">Optional. Uses only a provider the platform owner allows; the quoted evidence is unchanged, and the summary is labelled as AI-written. Works without it.</span></span></label>
        </fieldset>

        {preview.data ? (
          <section className="card stack" aria-label="What would be used">
            <h2 className="card__title">What would be used from your project</h2>
            {preview.data.included.length === 0 ? <p className="muted">Nothing from your own files. {webAllowed && form.web ? 'Only outside sources would be used.' : 'Choose a document or dataset, or allow web research.'}</p> : <ul>{preview.data.included.map((i, n) => <li key={n}>{i.name} <span className="row__meta">({i.kind}{i.passages ? `, ${i.passages} passage${i.passages === 1 ? '' : 's'}` : ''})</span></li>)}</ul>}
            <p className="row__meta">{preview.data.note}</p>
          </section>
        ) : null}

        {failure ? <Notice kind="error" title="Could not make the plan">{failure}</Notice> : null}
        <div className="btn-row">
          <button type="submit" className="btn" disabled={busy}>{busy ? 'Making the plan…' : 'Make the plan'}</button>
          <Link to={`/research/p/${encodeURIComponent(projectId)}`} className="btn btn--ghost">Cancel</Link>
        </div>
      </form>
    </article>
  );
}
