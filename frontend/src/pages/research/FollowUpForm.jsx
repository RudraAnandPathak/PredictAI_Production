import { useState } from 'react';
import { research } from '../../api/index.js';
import { useRouter } from '../../app/router/RouterProvider.jsx';
import { Field } from '../../components/forms/Field.jsx';
import { describeError, fieldErrors } from '../../features/identity/validation.js';
import { FOLLOW_UP_MAX, buildFollowUpBody } from '../../features/research/report.js';
import { Notice } from './shared.jsx';

/** Ask a follow-up about a finished result. It keeps the earlier question's files and settings and starts from the pages and articles already read, so a question about a paper or an article
 *  is answered from the saved copy. A new plan is made and shown first; nothing runs, and nothing is sent anywhere, until the person approves it. */
export function FollowUpForm({ sessionId, webWasUsed }) {
  const { navigate } = useRouter();
  const [question, setQuestion] = useState('');
  const [web, setWeb] = useState(false);
  const [errors, setErrors] = useState({});
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setFailure('');
    const built = buildFollowUpBody({ question, web });
    if (built.errors) { setErrors(built.errors); return; }
    setErrors({});
    setBusy(true);
    try {
      const next = await research.followUp(sessionId, built.body);
      navigate(`/research/session/${encodeURIComponent(next.id)}`);
    } catch (e) {
      const server = fieldErrors(e);
      if (server.question) setErrors({ question: server.question });
      else setFailure(describeError(e));
      setBusy(false);
    }
  }

  return (
    <form className="card stack" onSubmit={submit} noValidate aria-label="Ask a follow-up question">
      <h2 className="card__title">Ask a follow-up</h2>
      <p className="row__meta">For example: “What results does the paper report?” or “What does the article say about fares?” It starts from what was already read.</p>
      <Field label="Your follow-up question" as="textarea" rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} error={errors.question} maxLength={FOLLOW_UP_MAX + 100} />
      {webWasUsed ? (
        <label className="check-field"><input type="checkbox" checked={web} onChange={(e) => setWeb(e.target.checked)} /><span><span className="check-field__label">Search outside sources again</span><span className="field__hint">Off by default: the follow-up uses the saved copies and sends nothing. If you switch it on you will see the new search phrases and approve them first.</span></span></label>
      ) : null}
      {failure ? <Notice kind="error" title="Could not make the follow-up">{failure}</Notice> : null}
      <div className="btn-row"><button type="submit" className="btn" disabled={busy}>{busy ? 'Making the plan…' : 'Make the plan'}</button></div>
    </form>
  );
}
