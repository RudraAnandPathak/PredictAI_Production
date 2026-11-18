import { useState } from 'react';
import { world } from '../../api/index.js';
import { ErrorState } from '../../components/ui/ErrorState.jsx';
import { Explanation } from '../../components/ui/Explanation.jsx';
import { PageLoading } from '../../components/ui/Spinner.jsx';
import { useResource } from '../../features/identity/useResource.js';
import { describeError } from '../../features/identity/validation.js';
import { topicLabelMap, trendRows } from '../../features/research/news.js';

/** How much each topic is being written about now compared with just before. The wording is the server's; with too little data it says so and gives no direction. */
export function NewsTrends({ projectId }) {
  const [days, setDays] = useState(7);
  const trends = useResource(() => world.trends(projectId, days), [projectId, days]);
  const topics = useResource(() => world.topics(projectId), [projectId]);
  const t = trends.data;
  const rows = trendRows(t, topicLabelMap(topics.data));

  return (
    <section className="stack" aria-label="Trends">
      <div className="card stack">
        <div className="card__row">
          <h2 className="card__title">Coverage trends</h2>
          <div className="filterbar"><label className="sr-only" htmlFor="trend-window">Compare the last</label>
            <select id="trend-window" className="field__input" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 14, 30].map((d) => <option key={d} value={d}>Last {d} days vs the {d} before</option>)}</select></div>
        </div>
        {trends.loading && !t ? <PageLoading label="Counting coverage…" /> : null}
        {trends.error ? <ErrorState title="Could not load trends" message={describeError(trends.error)} onRetry={() => trends.reload()} /> : null}
        {t ? (
          <>
            <p>{t.recent_articles} article{t.recent_articles === 1 ? '' : 's'} in the last {t.window_days} days, {t.previous_articles} in the {t.window_days} before{t.undated_articles ? `; ${t.undated_articles} without a date were left out` : ''}.</p>
            {rows.length === 0 ? <p className="muted">No topics to compare yet.</p> : (
              <div className="table-wrap"><table className="table">
                <caption>Articles per topic. A count change is not a judgement about importance or truth.</caption>
                <thead><tr><th scope="col">Topic</th><th scope="col">Recent</th><th scope="col">Before</th><th scope="col">What can be said</th></tr></thead>
                <tbody>{rows.map((r) => <tr key={r.key}><th scope="row">{r.topic}</th><td>{r.recent}</td><td>{r.previous}</td><td>{r.verdict}</td></tr>)}</tbody>
              </table></div>
            )}
            {t.entities?.length ? <div><h3 className="sub">Names that appear more or less</h3><ul>{t.entities.map((e, i) => <li key={i}>{e.name ?? e.entity}: {e.recent} now, {e.previous} before ({e.label})</li>)}</ul></div> : null}
            <ul className="row__meta" aria-label="Cautions">{t.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>
            <Explanation value={t.explanation} />
          </>
        ) : null}
      </div>
      {topics.data ? (
        <div className="card stack" aria-label="Topics">
          <h2 className="card__title">Topics in the last {topics.data.days} days</h2>
          {topics.data.items.length === 0 ? <p className="muted">No articles to sort into topics yet.</p> : <ul>{topics.data.items.map((i) => <li key={i.topic}>{i.label}: {i.articles} article{i.articles === 1 ? '' : 's'}</li>)}</ul>}
          <p className="row__meta">{topics.data.note}</p>
        </div>
      ) : null}
    </section>
  );
}
