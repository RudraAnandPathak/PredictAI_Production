import { useEffect, useState } from 'react';
import { ri } from '../../../api/index.js';
import { Field } from '../../../components/forms/Field.jsx';
import { useResource } from '../../../features/identity/useResource.js';
import { Link } from '../../../app/router/Link.jsx';
import { Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { Paper } from '../components/Paper.jsx';
import { ResearchFrame } from '../components/ResearchNav.jsx';
import { PUB_TYPES, SORTS, buildSearchBody, providerState, resultsQuery, toggleId } from '../logic.js';

const EMPTY = { query: '', title: '', author: '', doi: '', year_from: '', year_to: '', field: '', type: '', open_access: false, sort: 'relevance', providers: [] };

/** Search academic providers and the web (opt-in on the server), filter results without searching again, save, compare. */
export function DiscoverPage() {
  const prov = useResource(() => ri.discovery.providers(), []);
  const [form, setForm] = useState(EMPTY);
  const [advanced, setAdvanced] = useState(false);
  const [search, setSearch] = useState(null);       // { search_id, providers, notes }
  const [page, setPage] = useState(null);           // items, total, pages…
  const [filters, setFilters] = useState({ page: 1, page_size: 20, sort: 'relevance' });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

  async function run(e) {
    e.preventDefault();
    const { body, errors } = buildSearchBody(form, null);
    if (errors.length) { setMsg({ kind: 'error', text: errors.join(' ') }); return; }
    setBusy(true); setMsg(null);
    const out = await attempt(() => ri.discovery.search(body), setMsg);
    setBusy(false);
    if (out) { setSearch({ search_id: out.search_id, providers: out.providers, notes: out.notes }); setPage(out); setFilters({ page: 1, page_size: 20, sort: form.sort || 'relevance' }); }
  }
  // Filtering and paging re-read the stored search; the providers are not asked again.
  useEffect(() => {
    if (!search) return undefined;
    let live = true;
    ri.discovery.results(search.search_id, resultsQuery(filters)).then((d) => live && setPage(d)).catch(() => live && setMsg({ kind: 'error', text: 'The results could not be refreshed.' }));
    return () => { live = false; };
  }, [search, filters]);

  const savedNow = async (rec) => {
    const out = await attempt(() => ri.discovery.save(search.search_id, rec.rid), setMsg, 'Saved to your library.');
    if (out) setPage((p) => ({ ...p, items: p.items.map((x) => (x.rid === rec.rid ? { ...x, saved: true } : x)) }));
  };
  const providers = prov.data?.items ?? [];
  return (
    <ResearchFrame current="discover" title="Discover Research" lead="Search scholarly databases for papers, then save the ones you want. Your search words go to the providers you leave switched on; none of your documents do.">
      <form className="card" onSubmit={run} aria-label="Search">
        <Field label="Keywords" value={form.query} onChange={set('query')} maxLength={400} />
        <button type="button" className="link-button" aria-expanded={advanced} onClick={() => setAdvanced((a) => !a)}>{advanced ? 'Hide' : 'Show'} advanced search</button>
        {advanced ? (
          <div className="ri-filters">
            <Field label="Title" value={form.title} onChange={set('title')} maxLength={300} />
            <Field label="Author" value={form.author} onChange={set('author')} maxLength={120} />
            <Field label="DOI" value={form.doi} onChange={set('doi')} maxLength={200} />
            <Field label="Field or subject" value={form.field} onChange={set('field')} maxLength={80} />
            <Field label="From year" value={form.year_from} onChange={set('year_from')} inputMode="numeric" />
            <Field label="To year" value={form.year_to} onChange={set('year_to')} inputMode="numeric" />
            <Field as="select" label="Type" value={form.type} onChange={set('type')}>{PUB_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Field>
            <Field as="select" label="Sort by" value={form.sort} onChange={set('sort')}>{SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Field>
            <label className="ri-check"><input type="checkbox" checked={form.open_access} onChange={set('open_access')} /> Only results with open full text</label>
            <fieldset className="ri-fieldset"><legend>Providers</legend>
              {providers.map((p) => (
                <label key={p.key} className="ri-check"><input type="checkbox" disabled={!p.available || !p.enabled} checked={form.providers.includes(p.key)} onChange={() => setForm((f) => ({ ...f, providers: toggleId(f.providers, p.key) }))} /> {p.label}{!p.available ? ` — ${p.reason ?? 'not available'}` : !p.enabled ? ' — switched off by the platform owner' : ''}</label>
              ))}
              <p className="field__hint">Leave all unticked to use every available provider.</p>
            </fieldset>
          </div>
        ) : null}
        {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
        <div className="card__actions"><button className="btn" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button></div>
      </form>

      {search ? (
        <section aria-label="Provider status" className="card">
          <h2 className="card__title">What each provider returned</h2>
          <ul className="list">{search.providers.map((p) => <li key={p.provider} className="row"><div className="row__main"><span className="row__title">{p.label}: {providerState(p.status)}{p.status === 'ok' ? ` (${p.count} result(s))` : ''}</span>{p.message ? <span className="row__meta">{p.message}{p.fix ? ` ${p.fix}` : ''}</span> : null}</div></li>)}</ul>
          {search.notes?.map((n) => <Notice key={n} kind="info">{n}</Notice>)}
        </section>
      ) : null}

      {page ? (
        <section aria-label="Results">
          <div className="card ri-filters">
            <h2 className="card__title">{page.total} result(s){page.total !== page.total_unfiltered ? ` of ${page.total_unfiltered}` : ''}</h2>
            <Field label="Narrow by words" value={filters.within ?? ''} onChange={(e) => setFilters((f) => ({ ...f, within: e.target.value, page: 1 }))} />
            <Field as="select" label="Sort" value={filters.sort} onChange={(e) => setFilters((f) => ({ ...f, sort: e.target.value, page: 1 }))}>{SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Field>
            <Field label="Year from" value={filters.year_from ?? ''} inputMode="numeric" onChange={(e) => setFilters((f) => ({ ...f, year_from: e.target.value, page: 1 }))} />
            <Field label="Year to" value={filters.year_to ?? ''} inputMode="numeric" onChange={(e) => setFilters((f) => ({ ...f, year_to: e.target.value, page: 1 }))} />
            <label className="ri-check"><input type="checkbox" checked={Boolean(filters.open_access)} onChange={(e) => setFilters((f) => ({ ...f, open_access: e.target.checked, page: 1 }))} /> Open full text only</label>
            <label className="ri-check"><input type="checkbox" checked={Boolean(filters.has_abstract)} onChange={(e) => setFilters((f) => ({ ...f, has_abstract: e.target.checked, page: 1 }))} /> Has an abstract</label>
          </div>
          {page.items.length === 0 ? <Notice kind="info">Nothing matches these filters.</Notice> : (
            <ul className="list">{page.items.map((rec) => (
              <Paper key={rec.rid} rec={rec}
                actions={rec.saved ? <span className="row__meta">Saved</span> : <button type="button" className="btn btn--ghost" onClick={() => savedNow(rec)}>Save to library</button>} />
            ))}</ul>
          )}
          <nav className="btn-row" aria-label="Result pages">
            <button type="button" className="btn btn--ghost" disabled={page.page <= 1} onClick={() => setFilters((f) => ({ ...f, page: page.page - 1 }))}>Previous</button>
            <span className="row__meta">Page {page.page} of {page.pages}</span>
            <button type="button" className="btn btn--ghost" disabled={page.page >= page.pages} onClick={() => setFilters((f) => ({ ...f, page: page.page + 1 }))}>Next</button>
          </nav>
          <p className="row__meta">Saved papers appear in your <Link to="/research-intelligence/library">Source Library</Link>, where you can compare them and add them to a workspace.</p>
        </section>
      ) : null}
    </ResearchFrame>
  );
}
