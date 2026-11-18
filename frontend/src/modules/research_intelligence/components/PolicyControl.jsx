import { useState } from 'react';
import { ri } from '../../../api/index.js';
import { Notice, attempt } from '../../../pages/phase7/shared.jsx';
import { POLICY_TEXT } from '../labels.js';

/** Where this workspace's text may be sent. Local-only is the default; external use needs an explicit, audited confirmation. */
export function PolicyControl({ wid, policy, onChanged }) {
  const [msg, setMsg] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const external = policy === 'external_allowed';
  async function apply(next) {
    const out = await attempt(() => ri.workspaces.setPolicy(wid, next, next === 'external_allowed'), setMsg, next === 'local_only' ? 'Documents now stay on this computer.' : 'External AI use is now allowed for this workspace.');
    setConfirming(false);
    if (out) onChanged();
  }
  return (
    <section className="card" aria-label="AI data policy">
      <h2 className="card__title">Where your documents may go</h2>
      <p>{POLICY_TEXT[policy] ?? POLICY_TEXT.local_only}</p>
      {confirming ? (
        <Notice kind="warning" title="Confirm external use">
          Parts of your documents will be sent to an external AI provider whenever you ask a question. The provider’s own terms apply, and this choice is recorded. Only turn this on if your documents may leave this computer.
          <div className="btn-row"><button type="button" className="btn" onClick={() => apply('external_allowed')}>Yes, allow external use</button><button type="button" className="btn btn--ghost" onClick={() => setConfirming(false)}>Cancel</button></div>
        </Notice>
      ) : external ? <button type="button" className="btn btn--ghost" onClick={() => apply('local_only')}>Keep documents on this computer</button>
        : <button type="button" className="btn btn--ghost" onClick={() => setConfirming(true)}>Allow external AI…</button>}
      {msg ? <Notice kind={msg.kind}>{msg.text}</Notice> : null}
    </section>
  );
}
