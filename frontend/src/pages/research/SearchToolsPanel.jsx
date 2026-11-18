import { useState } from 'react';
import { research } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError, fieldErrors } from '../../features/identity/validation.js';
import { buildSearchBody, diagnosisRows, openBody, searchableProviders, unavailableProviders } from '../../features/research/searchtools.js';
import { Badge, ExternalLink, Notice } from './shared.jsx';

function TestProviders({ projectId }) {
  const [state, setState] = useState({ busy: false, data: null, error: '' });
  async function run() {
    setState({ busy: true, data: null, error: '' });
    try { setState({ busy: false, data: await research.diagnoseProviders(projectId), error: '' }); } catch (e) { setState({ busy: false, data: null, error: describeError(e) }); }
  }
  const rows = diagnosisRows(state.data);
  return (
    <section className="card stack" aria-label="Test the search providers">
      <h2 className="card__title">Test the search providers</h2>
      <p className="row__meta">Sends one short fixed phrase to each provider you have switched on, so you can see whether it works from this server and what to change if it does not. Nothing from your project is sent.</p>
      <div className="btn-row"><button type="button" className="btn" disabled={state.busy} onClick={run}>{state.busy ? 'Testing…' : 'Test now'}</button></div>
      {state.error ? <Notice kind="error" title="Could not run the test">{state.error}</Notice> : null}
      {rows.length ? (
        <ul className="list" aria-label="Test results">
          {rows.map((r) => (
            <li key={r.provider} className="row" data-provider={r.provider}>
              <div className="row__main"><span className="row__title">{r.label}</span><span className="row__meta">{r.message}</span>{r.fix && !r.ok ? <span className="row__meta"><strong>What to do:</strong> {r.fix}</span> : null}{r.fix && r.ok ? <span className="row__meta">{r.fix}</span> : null}</div>
              <Badge tone={r.tone}>{r.statusLabel}</Badge>
            </li>
          ))}
        </ul>
      ) : null}
      {state.data?.note ? <p className="row__meta">{state.data.note}</p> : null}
    </section>
  );
}

function OpenedItem({ item }) {
  return (
    <div className="stack" aria-label="Opened item">
      <h3 className="sub">{item.title || 'Untitled'}</h3>
      <p className="row__meta"><Badge tone="muted">{item.label}</Badge> {item.retrieved_at ? `Read ${item.retrieved_at}` : ''}{item.cache_hit ? ' (from the saved copy)' : ''}</p>
      {item.warnings?.length ? <Notice kind="warn" title="Read this first"><ul>{item.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></Notice> : null}
      {item.full_text === false ? <Notice kind="warn" title="Abstract only">The full text of this paper could not be read, so only its abstract is shown. An abstract is not the paper.</Notice> : null}
      <dl className="kv">
        {item.attribution ? <div className="kv__pair"><dt>Credit</dt><dd>{item.attribution}</dd></div> : null}
        {item.permalink ? <div className="kv__pair"><dt>Exact version</dt><dd><ExternalLink url={item.permalink}>permanent link</ExternalLink></dd></div> : null}
        {item.pdf_url ? <div className="kv__pair"><dt>Paper</dt><dd><ExternalLink url={item.pdf_url}>PDF</ExternalLink>{item.html_url ? <> · <ExternalLink url={item.html_url}>HTML</ExternalLink></> : null}</dd></div> : null}
        {item.license ? <div className="kv__pair"><dt>Licence</dt><dd>{item.license}</dd></div> : null}
        {item.modified ? <div className="kv__pair"><dt>Last changed</dt><dd>{item.modified}</dd></div> : null}
      </dl>
      <ol className="list" aria-label="Sections">
        {(item.sections ?? []).map((s) => (
          <li key={s.id} className="row"><div className="row__main"><span className="row__title">{s.heading || 'Introduction'}{s.page_start ? ` (page ${s.page_start})` : ''}</span><span className="row__meta">{s.text}</span></div></li>
        ))}
      </ol>
      <p className="row__meta">{item.note}</p>
    </div>
  );
}

function SearchOne({ projectId, caps }) {
  const usable = searchableProviders(caps);
  const off = unavailableProviders(caps);
  const [provider, setProvider] = useState(usable[0]?.key ?? '');
  const [query, setQuery] = useState('');
  const [errors, setErrors] = useState({});
  const [state, setState] = useState({ busy: false, data: null, error: '' });
  const [opened, setOpened] = useState({ busy: false, data: null, error: '', key: '' });

  async function submit(event) {
    event.preventDefault();
    const built = buildSearchBody({ provider, query });
    if (built.errors) { setErrors(built.errors); return; }
    setErrors({}); setOpened({ busy: false, data: null, error: '', key: '' }); setState({ busy: true, data: null, error: '' });
    try { setState({ busy: false, data: await research.providerSearch(projectId, built.body), error: '' }); } catch (e) {
      const server = fieldErrors(e);                                                              // e.g. "That search contains something that looks private (...) and was not sent."
      if (server.query || server.provider) { setErrors(server); setState({ busy: false, data: null, error: '' }); } else setState({ busy: false, data: null, error: describeError(e) });
    }
  }
  async function open(hit) {
    const body = openBody(state.data.provider, hit);
    if (!body) return;
    setOpened({ busy: true, data: null, error: '', key: hit.url });
    try { setOpened({ busy: false, data: await research.providerOpen(projectId, body), error: '', key: hit.url }); } catch (e) { setOpened({ busy: false, data: null, error: describeError(e), key: hit.url }); }
  }

  return (
    <section className="card stack" aria-label="Search one provider">
      <h2 className="card__title">Search one provider</h2>
      <p className="row__meta">Search Wikipedia, arXiv or the web on its own, and open a result to read it section by section, as it would be cited. The words you type are sent to the provider you choose; nothing from your documents or data is. Results here are not saved as evidence.</p>
      {usable.length === 0 ? <Notice kind="info" title="No search provider is switched on for this project">{off.map((o) => <p key={o.key}><strong>{o.label}.</strong> {o.reason}</p>)}<p>Change this under “Privacy for this project”.</p></Notice> : (
        <form className="stack" onSubmit={submit} noValidate aria-label="Search one provider form">
          <Field label="Where to search" as="select" value={provider} onChange={(e) => setProvider(e.target.value)} error={errors.provider} hint={usable.find((p) => p.key === provider)?.description}>
            {usable.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </Field>
          <Field label="What to search for" type="search" value={query} maxLength={400} onChange={(e) => setQuery(e.target.value)} error={errors.query} hint="A short topic phrase. Anything that looks like private information (an email address, a long number) is refused before it is sent." />
          <div className="btn-row"><button type="submit" className="btn" disabled={state.busy}>{state.busy ? 'Searching…' : 'Search'}</button></div>
        </form>
      )}
      {state.error ? <Notice kind="error" title="Could not search">{state.error}</Notice> : null}
      {state.data ? (
        <div className="stack" role="region" aria-label="Search results">
          <h3 className="sub">{state.data.hits.length ? `${state.data.hits.length} result${state.data.hits.length === 1 ? '' : 's'}` : 'Nothing found'}</h3>
          <ul className="list">
            {state.data.hits.map((h) => (
              <li key={h.url} className="row" data-url={h.url}>
                <div className="row__main">
                  <span className="row__title"><ExternalLink url={h.url}>{h.title || h.url}</ExternalLink></span>
                  <span className="row__meta"><Badge tone="muted">{h.source_label}</Badge> {h.published || h.modified || ''}</span>
                  {h.snippet ? <span className="row__meta">{h.snippet}</span> : null}
                </div>
                {h.can_open || state.data.provider === 'searxng' ? <button type="button" className="btn btn--ghost btn--sm" onClick={() => open(h)} disabled={opened.busy}>{opened.busy && opened.key === h.url ? 'Opening…' : 'Read it'}</button> : null}
              </li>
            ))}
          </ul>
          <p className="row__meta">{state.data.note}{state.data.cache_hit ? ' This answer came from the saved copy; nothing was sent.' : ''}</p>
        </div>
      ) : null}
      {opened.error ? <Notice kind="error" title="Could not open it">{opened.error}</Notice> : null}
      {opened.data ? <OpenedItem item={opened.data} /> : null}
    </section>
  );
}

/** Test each search provider, search one on its own, and read one result section by section. Everything the person types is checked on the server before it leaves. */
export function SearchToolsPanel({ projectId }) {
  const caps = useResource(() => research.capabilities(projectId), [projectId]);
  if (caps.loading && !caps.data) return <PageLoading label="Preparing the search tools…" />;
  if (caps.error) return <ErrorState title="Could not open the search tools" message={describeError(caps.error)} onRetry={() => caps.reload()} />;
  return (
    <div className="stack" aria-label="Search tools">
      {!caps.data.web?.allowed ? <Notice kind="info" title="Web research is off for this project">{(caps.data.web?.why_not ?? []).map((w, i) => <p key={i}>{w}</p>)}<p>The tools below will refuse until it is switched on under “Privacy for this project”.</p></Notice> : null}
      <TestProviders projectId={projectId} />
      <SearchOne projectId={projectId} caps={caps.data} />
    </div>
  );
}
