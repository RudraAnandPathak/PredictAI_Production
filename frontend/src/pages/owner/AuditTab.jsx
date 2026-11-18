import { useCallback, useEffect, useState } from 'react';
import { owner } from '../../api/index.js';
import { Field } from '../../components/forms/Field.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { formatDateTime } from '../../features/identity/format.js';
import { describeError } from '../../features/identity/validation.js';

const OUTCOME_BADGE = { success: 'badge--ok', failure: 'badge--warn', denied: 'badge--danger' };

export function AuditTab() {
  const [filters, setFilters] = useState({ event_type: '', outcome: '' });
  const [applied, setApplied] = useState(filters);
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async (beforeId, replace) => {
    setLoading(true);
    setError(null);
    try {
      const result = await owner.audit({ ...applied, before_id: beforeId, limit: 50 });
      setItems((current) => (replace ? result.data.items : [...current, ...result.data.items]));
      setNext(result.data.next_before_id ?? null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, [applied]);

  useEffect(() => { load(undefined, true); }, [load]);

  return (
    <div className="stack">
      <form className="toolbar" role="search" aria-label="Filter security events" onSubmit={(e) => { e.preventDefault(); setApplied({ ...filters, event_type: filters.event_type.trim() }); }}>
        <Field label="Event type" name="event_type" placeholder="e.g. login.failure" value={filters.event_type} onChange={(e) => setFilters((f) => ({ ...f, event_type: e.target.value }))} />
        <Field as="select" label="Outcome" name="outcome" value={filters.outcome} onChange={(e) => setFilters((f) => ({ ...f, outcome: e.target.value }))}>
          <option value="">Any</option><option value="success">Success</option><option value="failure">Failure</option><option value="denied">Denied</option>
        </Field>
        <button type="submit" className="btn">Apply</button>
      </form>
      <p className="hint">Records hold event types, outcomes, account references and a shortened network address — never passwords, tokens or codes.</p>
      {error ? <FormMessage kind="error">{error}</FormMessage> : null}
      {loading && items.length === 0 ? <PageLoading label="Loading events…" /> : (
        <div className="table-wrap">
          <table className="table">
            <caption className="sr-only">Security and administration events, newest first</caption>
            <thead><tr><th scope="col">When</th><th scope="col">Event</th><th scope="col">Outcome</th><th scope="col">Actor</th><th scope="col">Target</th><th scope="col">Detail</th></tr></thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id}>
                  <td>{formatDateTime(e.occurred_at)}</td>
                  <td className="mono">{e.event_type}</td>
                  <td><span className={`badge ${OUTCOME_BADGE[e.outcome] ?? 'badge--muted'}`}>{e.outcome}</span></td>
                  <td>{e.actor_email || '—'}</td>
                  <td>{e.target_email || '—'}</td>
                  <td className="mono">{e.reason || ''}{e.source_ip ? ` ${e.source_ip}` : ''}</td>
                </tr>
              ))}
              {!loading && items.length === 0 ? <tr><td colSpan={6}>No events match.</td></tr> : null}
            </tbody>
          </table>
        </div>
      )}
      {next ? <div><button type="button" className="btn btn--ghost" disabled={loading} onClick={() => load(next, false)}>Load older events</button></div> : null}
    </div>
  );
}
