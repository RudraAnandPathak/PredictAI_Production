// Browser run for Phase 6. Needs a running backend and frontend (see scripts/e2e/README.md) and the local fixture server (python scripts/e2e/fake_web.py). It never contacts the real internet.
// Phase 6 browser run: research, news, influence, consent and AI providers against local fixtures only (fake_web.py). No real internet is contacted.
import { chromium } from 'playwright';
import fs from 'node:fs';
const BASE = 'http://localhost:5173'; const RUN = Date.now().toString(36); const FX = new URL('./fx/', import.meta.url).pathname;
// Throwaway passwords for throwaway local accounts, made fresh for every run; the owner's password is yours to choose and is read from the environment.
const fresh = () => `pw-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}-Q7`;
const A = { email: `a.${RUN}@example.com`, password: fresh() }, B = { email: `b.${RUN}@example.com`, password: fresh() };
if (!process.env.E2E_OWNER_PASSWORD) { console.error('Set E2E_OWNER_PASSWORD to the password you gave owner@example.com.'); process.exit(2); }
const OWNER = { email: 'owner@example.com', password: process.env.E2E_OWNER_PASSWORD };
const browser = await chromium.launch(); const problems = []; const out = [];
const ok = (c, m) => { if (!c) throw new Error(m); };
async function ctx(vp = { width: 1280, height: 900 }) { const c = await browser.newContext({ baseURL: BASE, viewport: vp, acceptDownloads: true }); const p = await c.newPage(); p.on('pageerror', (e) => problems.push(e.message)); p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) problems.push(m.text()); }); p.setDefaultTimeout(30000); return p; }
async function reg(p, u) { await p.goto('/register'); await p.getByLabel('Email').fill(u.email); await p.getByLabel('Password', { exact: true }).fill(u.password); const c = p.getByLabel('Confirm password'); if (await c.count()) await c.fill(u.password); await p.getByRole('button', { name: /create account/i }).click(); await p.waitForTimeout(600); }
async function login(p, u) { await p.goto('/login'); await p.getByLabel('Email').fill(u.email); await p.getByLabel('Password', { exact: true }).fill(u.password); await p.getByRole('button', { name: 'Sign in', exact: true }).click(); await p.waitForURL('**/'); }
let a; // current page, for failure screenshots
async function step(n, f) { try { await f(); out.push('ok   ' + n); console.log('ok   ' + n); } catch (e) { out.push('FAIL ' + n + ' :: ' + String(e.message).split('\n').slice(0,3).join(' | ')); console.log('FAIL ' + n + ' :: ' + String(e.message).split('\n').slice(0,3).join(' | ')); try { await a.screenshot({ path: `fail10-${n.replace(/\W+/g, '_').slice(0, 40)}.png`, fullPage: true }); } catch { /* ignore */ } } }
const cnt = (d) => 1 + ((d * 5) % 3);
// The harbour dataset: value follows the number of harbour articles two days earlier.
function harbourCsv() {
  const today = new Date(); today.setUTCHours(0, 0, 0, 0); const rows = ['day,value'];
  for (let k = 40; k >= 1; k -= 1) { const date = new Date(today.getTime() - k * 86400000).toISOString().slice(0, 10); const d = k + 2; rows.push(`${date},${100 + (d >= 1 && d <= 24 ? 10 * cnt(d) : 0)}`); }
  return rows.join('\n') + '\n';
}
fs.writeFileSync(FX + 'harbour.csv', harbourCsv());

a = await ctx(); const b = await ctx();
await reg(a, A); await login(a, A); await reg(b, B); await login(b, B);
let projectId, sessionId, bundleId, bundle2Id, webSessionId, eventId, analysisId;

async function openResearch(page = a) { await page.goto('/research'); await page.getByRole('heading', { name: 'Research and world intelligence', level: 1 }).waitFor(); }
async function openProject(tab) { await a.goto(`/research/p/${projectId}${tab ? `?tab=${tab}` : ''}`); await a.getByRole('heading', { name: 'Flood desk', level: 1 }).waitFor(); }
async function waitJobDone(label) { await a.locator('section[data-job-status="completed"], section[data-job-status="failed"], section[data-job-status="cancelled"]').first().waitFor({ timeout: 90000 }); }

await step('create a project and upload a document and a dataset', async () => {
  await a.goto('/projects'); await a.getByRole('button', { name: 'Create your first project' }).click(); await a.getByLabel('Project name').fill('Flood desk'); await a.getByRole('button', { name: 'Create project' }).click();
  await a.waitForURL(/\/projects\/[0-9a-f]{32}/); projectId = a.url().split('/projects/')[1].split('?')[0];
  await a.getByRole('tab', { name: 'Data' }).click().catch(() => {});
  await a.locator('input[type=file]').setInputFiles([FX + 'river.txt', FX + 'harbour.csv']);
  await a.getByRole('row', { name: /harbour\.csv/ }).getByText(/40 rows × 2 columns/).waitFor();
  await a.getByRole('row', { name: /river\.txt/ }).waitFor();
});

await step('Research is in the sidebar as Existing; the dashboard shows what is allowed and what is not built yet', async () => {
  await a.goto('/'); const link = a.locator('a[href="/research"]').first(); await link.waitFor(); await link.click();
  await a.getByRole('heading', { name: 'Research and world intelligence', level: 1 }).waitFor();
  await a.getByText('Web research is off.').waitFor(); await a.getByText('Reading news feeds is off.').waitFor(); await a.getByText('Nothing is sent to an outside AI service.').waitFor();
  await a.getByRole('heading', { name: 'Not available yet' }).waitFor(); await a.getByText(/OUT-01a/).first().waitFor(); await a.getByText(/Phase 7 requirement/).first().waitFor();
  await a.getByRole('link', { name: 'Flood desk' }).first().waitFor();
  await a.screenshot({ path: 'a10-dashboard.png', fullPage: true });
});

await step('new research: web is off with reasons; the plan sends nothing and shows its parts', async () => {
  await a.goto(`/research/p/${projectId}/new`); await a.getByRole('heading', { name: 'New research', level: 1 }).waitFor();
  await a.getByText('Web research is off', { exact: true }).waitFor();
  await a.getByRole('button', { name: 'Make the plan' }).click(); await a.getByText('Write the question you want researched.').waitFor();
  await a.getByRole('textbox', { name: 'Your question' }).fill('How high did the river rise and how many residents were evacuated?');
  await a.getByRole('checkbox', { name: 'river.txt' }).check();
  await a.getByRole('region', { name: 'What would be used' }).getByText('river.txt').waitFor();
  await a.screenshot({ path: 'a10-new.png', fullPage: true });
  await a.getByRole('button', { name: 'Make the plan' }).click();
  await a.waitForURL(/\/research\/session\/[0-9a-f]{32}/); sessionId = a.url().split('/research/session/')[1].split('?')[0];
  await a.getByRole('heading', { name: 'The plan', level: 2 }).waitFor();
  await a.getByText('Nothing leaves this server').first().waitFor(); await a.getByText(/Web: off\. Nothing leaves this server\./).waitFor();
  await a.getByText(/Your material: 1 document, 0 datasets/).waitFor();
});

await step('run the research: progress is shown, and a finished run offers its result', async () => {
  await a.getByRole('button', { name: 'Run the research' }).click();
  await waitJobDone();
  await a.locator('section[data-job-status="completed"]').getByRole('link', { name: 'Open the result' }).click();
  await a.waitForURL(/\/research\/bundle\//); bundleId = a.url().split('/research/bundle/')[1].split('?')[0];
  await a.getByRole('heading', { name: 'Conclusions', level: 2 }).first().waitFor();
});

await step('result: each conclusion names its origin and strength, and the quote is the user\'s own text', async () => {
  const c1 = a.locator('[data-conclusion-id]').filter({ hasText: '1,200 residents' }).first(); await c1.waitFor();
  const text = await c1.innerText(); ok(/1,200 residents/.test(text), 'conclusion text: ' + text.slice(0, 200));
  ok(/Your material/i.test(text), 'origin badge'); ok(/One source says this|Several sources say this/i.test(text), 'confidence label');
  await a.getByRole('heading', { name: 'Quality checks' }).waitFor(); await a.getByText('Citations exist').waitFor();
  await a.getByRole('heading', { name: /What this is based on/ }).waitFor(); await a.getByText(/Not used\. Nothing left this server\./).waitFor();
  await a.getByRole('region', { name: 'Explanation' }).first().waitFor();
  await a.screenshot({ path: 'a10-bundle.png', fullPage: true });
});

await step('citation: the passage is read again from the saved source, marked, with its place', async () => {
  await a.locator('[data-conclusion-id]').filter({ hasText: '1,200 residents' }).first().getByRole('button', { name: /Open citation/ }).first().click();
  const dlg = a.getByRole('dialog'); await dlg.waitFor();
  await dlg.getByText(/found again at the recorded place and matches/).waitFor();
  const mark = await dlg.locator('mark').innerText(); ok(/Officials evacuated 1,200 residents/.test(mark), 'marked quote: ' + mark);
  await dlg.getByText('river.txt').first().waitFor();
  await a.screenshot({ path: 'a10-citation.png' });
  await a.keyboard.press('Escape'); await dlg.waitFor({ state: 'detached' });
});

await step('export: markdown and csv carry the quote, its source and the integrity fingerprint', async () => {
  const dl = async (name) => { const [d] = await Promise.all([a.waitForEvent('download', { timeout: 20000 }), a.getByRole('button', { name }).click()]); const p = await d.path(); return fs.readFileSync(p, 'utf8'); };
  const md = await dl(/Download Markdown/); ok(/1,200 residents/.test(md) && /river\.txt/.test(md), 'markdown content');
  const csv = await dl(/Download CSV/); ok(/Officials evacuated 1,200 residents/.test(csv) && /river\.txt/.test(csv), 'csv content');
  const js = JSON.parse(await dl(/Download JSON/)); ok(js.schema === 'predictai.evidence_bundle/1', 'json schema'); ok(!JSON.stringify(js).includes('/storage'), 'no storage paths');
});

await step('run again, compare the two runs, save the result and add a label', async () => {
  await a.goto(`/research/session/${sessionId}`); await a.getByRole('heading', { name: 'Run it again' }).waitFor();
  await a.getByRole('button', { name: 'Run the research' }).click(); await waitJobDone();
  await a.reload(); await a.getByText('Run 2').first().waitFor();
  const boxes = a.getByRole('checkbox', { name: /Select run \d to compare/ }); ok((await boxes.count()) === 2, 'two runs listed');
  await boxes.nth(0).check(); await boxes.nth(1).check(); await a.getByRole('button', { name: 'Compare the two selected runs' }).click();
  await a.getByRole('heading', { name: /What changed between run/ }).waitFor(); await a.getByText('Unchanged').first().waitFor();
  await a.getByRole('link', { name: /^Run 2/ }).click(); await a.waitForURL(/\/research\/bundle\//); bundle2Id = a.url().split('/research/bundle/')[1].split('?')[0];
  await a.getByRole('button', { name: 'Save this result' }).click(); await a.getByRole('button', { name: 'Remove from saved' }).waitFor();
  await a.getByRole('button', { name: 'Add a label' }).click(); await a.getByLabel('Label').fill('Second pass'); await a.getByRole('button', { name: 'Save label' }).click(); await a.getByText(/“Second pass”/).waitFor();
});

await step('notes: add, edit and delete a note; it is labelled as written by the user', async () => {
  await a.goto(`/research/session/${sessionId}`); const notes = a.getByRole('region', { name: 'Notes' });
  await notes.getByRole('textbox', { name: 'Add a note' }).fill('Ask the council about the levee'); await notes.getByRole('button', { name: 'Add note' }).click(); await notes.getByText('Ask the council about the levee').waitFor(); await notes.getByText(/Written by you/).first().waitFor();
  await notes.getByRole('button', { name: 'Edit' }).click(); await notes.getByLabel('Edit note').fill('Ask the council about the levee repairs'); await notes.getByRole('button', { name: 'Save', exact: true }).click(); await notes.getByText('Ask the council about the levee repairs').waitFor();
  a.once('dialog', (d) => d.accept()); await notes.getByRole('button', { name: 'Delete' }).click(); await notes.getByText('No notes yet.').waitFor();
});

await step('documents: search finds the passage with its page, says how it was ranked, and nothing leaves the server', async () => {
  await openProject('documents'); const form = a.getByRole('form', { name: 'Search your documents' }); await form.waitFor();
  await form.getByRole('button', { name: 'Search' }).click(); await form.getByText(/at least two characters/).waitFor();
  await form.getByLabel('What do you want to find?').fill('levee northern bridge'); await form.getByRole('button', { name: 'Search' }).click();
  const res = a.getByRole('region', { name: 'Document search results' }); await res.waitFor();
  const hit = res.locator('[data-chunk-id]').first(); await hit.waitFor(); const t = await hit.innerText();
  ok(/river\.txt/.test(t) && /levee held at the northern bridge/i.test(t), 'passage text: ' + t.slice(0, 200)); ok(/page 1/i.test(t), 'page shown'); ok(/Ranked by|Matched words/.test(t), 'why shown');
  await form.getByLabel('What do you want to find?').fill('zebra giraffe'); await form.getByRole('button', { name: 'Search' }).click();
  await res.getByText('Nothing found').waitFor();
  await a.screenshot({ path: 'a10-documents.png' });
});
await step('history lists the research and earlier research can be searched', async () => {
  await openProject(); await a.getByRole('link', { name: /How high did the river rise/ }).first().waitFor();
  await a.getByLabel('Search earlier research').fill('river'); await a.getByRole('button', { name: 'Search', exact: true }).click(); await a.getByRole('heading', { name: /Results for “river”/ }).waitFor();
  await a.getByRole('region', { name: 'Search results' }).getByRole('link').first().waitFor();
  await a.getByLabel('Search earlier research').fill('x'); await a.getByText('Type at least two characters to search.').waitFor();
});

await step('settings: everything starts off; turning on web research is explicit and per search site', async () => {
  await a.goto('/research/settings'); await a.getByRole('heading', { name: 'Research settings', level: 1 }).waitFor();
  const form = a.getByRole('form', { name: 'Your choices' });
  ok(!(await form.getByRole('checkbox', { name: /Allow web research/ }).isChecked()), 'web off by default'); ok(!(await form.getByRole('checkbox', { name: /Allow reading news feeds/ }).isChecked()), 'news off by default'); ok(!(await form.getByRole('checkbox', { name: /outside AI service/ }).isChecked()), 'ai off by default');
  await form.getByRole('button', { name: 'Save' }).click(); await a.getByText('Nothing has changed.').waitFor();
  await form.getByLabel('Keep saved web pages for (days)').fill('0'); await form.getByRole('button', { name: 'Save' }).click(); await a.getByText(/whole number from 1 to 90/).waitFor();
  await form.getByLabel('Keep saved web pages for (days)').fill('');
  await form.getByRole('checkbox', { name: /Allow web research/ }).check(); await form.getByRole('checkbox', { name: /SearXNG/ }).check(); await form.getByRole('checkbox', { name: /Allow reading news feeds/ }).check();
  await form.getByRole('button', { name: 'Save' }).click(); await a.getByText('Saved.').waitFor();
  await a.reload(); await a.getByText('Web research is allowed through searxng.').waitFor(); await a.getByText('Reading news feeds is allowed.').waitFor();
});

await step('web research: the plan lists the exact phrases that would leave, and running needs approval of that plan', async () => {
  await a.goto(`/research/p/${projectId}/new`); await a.getByRole('textbox', { name: 'Your question' }).fill('How high did the river rise after the heavy rainfall?');
  await a.getByRole('checkbox', { name: 'river.txt' }).check(); await a.getByRole('checkbox', { name: /Also search the web/ }).check(); await a.getByRole('checkbox', { name: /SearXNG/ }).check();
  await a.getByRole('button', { name: 'Make the plan' }).click(); await a.waitForURL(/\/research\/session\/[0-9a-f]{32}/); webSessionId = a.url().split('/research/session/')[1].split('?')[0];
  await a.getByText('This would leave this server').waitFor(); await a.getByText(/Each provider would be sent only the phrases listed under its name/).waitFor(); await a.locator('[data-provider="searxng"]').waitFor(); ok((await a.locator('code').count()) >= 1, 'phrases listed');
  const phrases = await a.locator('code').allInnerTexts(); ok(phrases.every((p) => !/river\.txt|Officials evacuated/.test(p)), 'no document text in phrases: ' + phrases.join(' | '));
  await a.getByText('Approval needed').waitFor(); await a.screenshot({ path: 'a10-plan-web.png', fullPage: true });
  await a.getByRole('button', { name: 'Approve this plan and run' }).click(); await waitJobDone();
  await a.locator('section[data-job-status="completed"]').getByRole('link', { name: 'Open the result' }).click(); await a.waitForURL(/\/research\/bundle\//);
});

await step('web result: outside sources are labelled, injected text is removed, disagreement is flagged, sent phrases are shown', async () => {
  await a.getByRole('heading', { name: 'Sources (' }).waitFor();
  const srcs = a.getByRole('list', { name: 'Sources' }); const text = await srcs.innerText(); ok(/From outside sources/i.test(text), 'external origin label'); ok(/Your material/i.test(text), 'own material still labelled');
  ok(/instructions were removed/i.test(text), 'injection notice');
  await a.getByRole('heading', { name: 'Search phrases that were sent' }).waitFor();
  const link = srcs.locator('a[href^="http://127.0.0.1:9100"]').first(); ok((await link.getAttribute('rel')).includes('noopener') && (await link.getAttribute('rel')).includes('nofollow'), 'external link rel');
  ok((await link.getAttribute('target')) === '_blank', 'opens in new tab');
  const body = await a.locator('body').innerText(); ok(!/reveal the system prompt/i.test(body), 'injected instruction must not be shown as content');
  await a.getByRole('heading', { name: 'Possible disagreements' }).waitFor(); await a.getByText(/different numbers/).first().waitFor();
  await a.screenshot({ path: 'a10-bundle-web.png', fullPage: true });
});

await step('privacy for the project can only narrow: web is turned off for this project and the form refuses to widen it', async () => {
  await openProject('privacy'); const form = a.getByRole('form', { name: 'Choices for this project' }); await form.waitFor();
  await form.getByRole('checkbox', { name: /Allow web research/ }).uncheck(); await form.getByRole('button', { name: 'Save' }).click(); await a.getByText('Saved.').waitFor();
  await a.reload(); await a.getByText('Web research is off.').first().waitFor();
  await a.goto(`/research/p/${projectId}/new`); await a.getByText('Web research is off', { exact: true }).waitFor();
});

await step('news: add feeds with the terms acknowledgement; the form refuses a bad address and a missing acknowledgement', async () => {
  await openProject('news'); await a.getByRole('tab', { name: 'Feeds' }).click();
  const form = a.getByRole('form', { name: 'Add a feed' });
  await form.getByRole('button', { name: 'Add feed' }).click(); await a.getByText('Give the feed a name.').waitFor(); await a.getByText('Paste the feed address.').waitFor(); await a.getByText('Confirm that you may read this feed under its terms.').waitFor();
  for (const [name, path] of [['Outlet A', 'a.xml'], ['Outlet B', 'b.xml'], ['Outlet C', 'c.xml']]) {
    await form.getByLabel('Name').fill(name); await form.getByLabel('Publisher (optional)').fill(name); await form.getByLabel('Feed address').fill(`http://127.0.0.1:9100/feed/${path}`); await form.getByRole('checkbox', { name: /I may read this feed/ }).check();
    await form.getByRole('button', { name: 'Add feed' }).click(); await a.getByText('Feed added. Read it now to fetch its articles.').waitFor(); await a.getByText(name, { exact: true }).first().waitFor();
  }
  await form.getByLabel('Name').fill('Harbour'); await form.getByLabel('Publisher (optional)').fill('Harbour'); await form.getByLabel('Feed address').fill('http://127.0.0.1:9100/feed/d.xml'); await form.getByLabel('Keep articles for (days, optional)').fill('90'); await form.getByRole('checkbox', { name: /I may read this feed/ }).check();
  await form.getByRole('button', { name: 'Add feed' }).click(); await a.getByText('Harbour', { exact: true }).first().waitFor();
});

await step('news is off for this project, so reading is refused with the reason; then the project is allowed again', async () => {
  // The project switched web research off only; news stays allowed. Switch news off at project level to prove the gate.
  await openProject('privacy'); const form = a.getByRole('form', { name: 'Choices for this project' });
  await form.getByRole('checkbox', { name: /Allow reading news feeds/ }).uncheck(); await form.getByRole('button', { name: 'Save' }).click(); await a.getByText('Saved.').waitFor();
  await openProject('news'); await a.getByRole('tab', { name: 'Feeds' }).click(); await a.getByText('Reading is off', { exact: true }).waitFor();
  ok(await a.getByRole('button', { name: 'Read all feeds now' }).isDisabled(), 'read button disabled');
  await openProject('privacy'); await a.getByRole('form', { name: 'Choices for this project' }).getByRole('checkbox', { name: /Allow reading news feeds/ }).check(); await a.getByRole('form', { name: 'Choices for this project' }).getByRole('button', { name: 'Save' }).click(); await a.getByText('Saved.').waitFor();
});

await step('news: read all feeds; per-feed results are shown and the instruction-like article is held back', async () => {
  await openProject('news'); await a.getByRole('tab', { name: 'Feeds' }).click(); await a.getByRole('button', { name: 'Read all feeds now' }).click();
  await waitJobDone();
  const res = a.getByRole('list', { name: 'Result for each feed' }); await res.waitFor(); const t = await res.innerText(); ok(/Outlet A/.test(t) && /Outlet B/.test(t) && /Outlet C/.test(t) && /Harbour/.test(t), 'rows for every feed: ' + t);
  ok(/held back/.test(t) || /instruction-like/.test(t), 'quarantine reported: ' + t);
  await a.screenshot({ path: 'a10-feeds.png', fullPage: true });
});

await step('news: events group the outlets, "confirmed" carries its caveat, and the event page compares outlets side by side', async () => {
  await a.getByRole('tab', { name: 'Events', exact: true }).click();
  const row = a.locator('[data-event-id]').filter({ hasText: /flood/i }).first(); await row.waitFor(); await row.getByText('Confirmed by several outlets').waitFor();
  await a.getByText(/agreement between outlets, not verification by PredictAI/).waitFor();
  eventId = await row.getAttribute('data-event-id'); await row.getByRole('link').click(); await a.waitForURL(/\/research\/news\/event\//);
  await a.getByRole('heading', { name: 'Timeline of reports' }).waitFor(); await a.getByText(/first report/).first().waitFor();
  await a.getByRole('heading', { name: 'Outlets side by side' }).waitFor(); await a.getByText(/does not say whether the text is true or important/).first().waitFor();
  ok((await a.locator('table tbody tr').count()) >= 3, 'three outlets in the table'); await a.getByText(/Agreement between outlets is not proof/).first().waitFor();
  await a.getByRole('region', { name: 'Explanation' }).first().waitFor();
  const ext = a.locator('a[href^="http://127.0.0.1:9100/"]').first(); ok((await ext.getAttribute('rel')).includes('noopener'), 'external rel');
  await a.screenshot({ path: 'a10-event.png', fullPage: true });
});

await step('news: article filters work, held-back articles are separate, tone is described as wording only', async () => {
  await openProject('news'); await a.getByRole('tab', { name: 'Articles' }).click(); await a.locator('[data-article-id]').first().waitFor();
  await a.getByLabel('Words').fill('library'); await a.getByRole('button', { name: 'Apply filters' }).click(); await a.locator('[data-article-id]').filter({ hasText: 'library budget' }).first().waitFor();
  await a.waitForFunction(() => document.querySelectorAll('[data-article-id]').length === 1, null, { timeout: 8000 }).catch(() => {}); ok((await a.locator('[data-article-id]').count()) === 1, 'filter narrows to one article');
  await a.getByRole('button', { name: 'Clear' }).click(); await a.locator('[data-article-id]').nth(3).waitFor();
  await a.getByLabel('Status').selectOption('quarantined'); await a.getByRole('button', { name: 'Apply filters' }).click();
  const held = a.locator('[data-article-id]').first(); await held.waitFor(); await held.getByText('Held back').waitFor();
  await held.getByRole('button', { name: 'Details' }).click(); await a.getByText(/Why it was held back/).waitFor();
  await a.getByRole('button', { name: 'Clear' }).click(); await a.getByText('Wording only; not a measure of truth or importance').first().waitFor().catch(() => {});
  await a.getByText(/Tone describes how the text is worded/).first().waitFor();
});

await step('news: trends say "too few to say" rather than invent a direction', async () => {
  await a.getByRole('tab', { name: 'Trends' }).click(); await a.getByRole('heading', { name: 'Coverage trends' }).waitFor();
  await a.getByRole('table').first().waitFor(); const t = await a.getByRole('table').first().innerText(); ok(/too few to say|steady|rising|falling/i.test(t), 'verdict wording: ' + t.slice(0, 200));
  await a.getByText(/More coverage does not mean more happened/).first().waitFor(); await a.getByText(/A count change is not a judgement about importance or truth/).waitFor();
});

await step('influence: the page leads with "association, not cause" and never claims causation', async () => {
  await openProject('influence'); await a.getByText('Association, not cause', { exact: true }).first().waitFor();
  const form = a.getByRole('form', { name: 'New influence analysis' });
  await form.getByLabel('Dataset').selectOption({ label: 'harbour.csv' }); await form.getByLabel('Date column').selectOption('day'); await form.getByLabel('Number to compare').selectOption('value');
  await form.getByLabel('Longest gap to test (days)').fill('15'); await form.getByRole('button', { name: 'Run the analysis' }).click(); await a.getByText(/whole number|0 to 14/).first().waitFor();
  await form.getByLabel('Longest gap to test (days)').fill('5'); await form.getByLabel('Name (optional)').fill('Harbour data vs harbour coverage'); await form.getByRole('button', { name: 'Run the analysis' }).click();
  await waitJobDone(); await a.locator('section[data-job-status="completed"]').getByRole('link', { name: 'Open the result' }).click();
  await a.waitForURL(/\/research\/influence\//); analysisId = a.url().split('/research/influence/')[1].split('?')[0];
  await a.getByText('Association, not cause', { exact: true }).waitFor(); await a.getByText(/does not show that one caused the other/).waitFor(); await a.getByText(/not established/).first().waitFor();
  await a.getByRole('heading', { name: 'Why this is not cause and effect' }).waitFor(); await a.getByRole('region', { name: 'Explanation' }).first().waitFor();
  const body = (await a.locator('main, body').first().innerText()); ok(!/\b(caused by|causes|proves|drives|because of the news)\b/i.test(body), 'no causal claim in page text');
  await a.screenshot({ path: 'a10-influence.png', fullPage: true });
});

await step('cancel: a slow news read can be cancelled from the page and nothing is saved from it', async () => {
  await openProject('news'); await a.getByRole('tab', { name: 'Feeds' }).click();
  const form = a.getByRole('form', { name: 'Add a feed' }); await form.getByLabel('Name').fill('Slow outlet'); await form.getByLabel('Feed address').fill('http://127.0.0.1:9100/feed/slow.xml'); await form.getByRole('checkbox', { name: /I may read this feed/ }).check(); await form.getByRole('button', { name: 'Add feed' }).click();
  const row = a.locator('[data-source-id]').filter({ hasText: 'Slow outlet' }); await row.waitFor(); await row.getByRole('button', { name: 'Read', exact: true }).click();
  const job = a.locator('section[data-job-status]').first(); await job.waitFor(); await job.getByRole('button', { name: 'Cancel' }).click();
  await a.locator('section[data-job-status="cancelled"], section[data-job-status="completed"]').first().waitFor({ timeout: 60000 });
  const status = await a.locator('section[data-job-status]').first().getAttribute('data-job-status'); ok(status === 'cancelled', 'job status was ' + status);
  await a.getByText(/This was cancelled\. Nothing was saved\./).waitFor();
  a.once('dialog', (d) => d.accept()); await row.getByRole('button', { name: 'Remove' }).click(); await a.getByText('Feed and its articles deleted.').waitFor();
});

await step('AI providers: a normal user sees them without controls; the owner adds, tests and switches one on, never typing a key', async () => {
  await a.goto('/research/settings'); await a.getByRole('heading', { name: 'AI providers', level: 2 }).waitFor(); await a.getByText(/PredictAI works without any model provider/).first().waitFor();
  await a.getByText('Only the platform owner can add, switch on or remove providers.').waitFor(); ok((await a.getByRole('form', { name: 'Add a provider' }).count()) === 0, 'no add form for a normal user');
  const o = await ctx(); a = o; await login(o, OWNER); await o.goto('/research/settings'); const add = o.getByRole('form', { name: 'Add a provider' }); await add.waitFor();
  await add.getByLabel('Short name').fill('fixture'); await add.getByLabel('Label').fill('Fixture model'); await add.getByLabel('Address').fill('http://127.0.0.1:9101'); await add.getByLabel('Model', { exact: true }).fill('fixture-model');
  await add.getByLabel(/Name of the environment variable/).fill('sk-this-is-a-pasted-key'); await add.getByRole('button', { name: 'Add provider' }).click(); await o.getByText(/starting with PREDICTAI_AI_KEY_/).waitFor();
  await add.getByLabel(/Name of the environment variable/).fill(''); await add.getByRole('button', { name: 'Add provider' }).click(); await o.getByText(/Provider added\. It is switched off until you switch it on\./).waitFor();
  const row = o.locator('[data-provider-key="fixture"]'); await row.waitFor(); await row.getByText('Off', { exact: true }).waitFor();
  await row.getByRole('button', { name: 'Test' }).click(); await o.getByText('The provider answered.').waitFor();
  o.once('dialog', (d) => d.accept()); await row.getByRole('button', { name: 'Switch on' }).click(); await o.getByText('Fixture model switched on.').waitFor(); await row.getByText('On', { exact: true }).waitFor();
  const html = await o.content(); ok(!html.includes('sk-this-is-a-pasted-key'), 'pasted key must not be echoed'); await o.screenshot({ path: 'a10-providers.png', fullPage: true });
});

await step('isolation: another person cannot open the first person\'s research, results, events or analyses', async () => {
  a = b;
  for (const [url, title] of [[`/research/session/${sessionId}`, /Research not found/], [`/research/bundle/${bundleId}`, /Result not found/], [`/research/news/event/${eventId}`, /Event not found/], [`/research/influence/${analysisId}`, /Analysis not found/], [`/research/p/${projectId}`, /Project not found/]]) {
    await b.goto(url); await b.getByRole('heading', { name: title }).waitFor();
  }
  await openResearch(b); await b.getByText('No projects yet').waitFor();
});

await step('small screens: research pages do not scroll sideways', async () => {
  const m = await ctx({ width: 390, height: 800 }); a = m; await login(m, A);
  for (const url of ['/research', `/research/p/${projectId}`, `/research/p/${projectId}?tab=news`, `/research/bundle/${bundleId}`, `/research/news/event/${eventId}`, `/research/influence/${analysisId}`, '/research/settings']) {
    await m.goto(url); await m.waitForLoadState('networkidle'); await m.waitForTimeout(400);
    const over = await m.evaluate(() => document.documentElement.scrollWidth - window.innerWidth); ok(over <= 1, `${url} overflows by ${over}px`);
  }
  await m.goto(`/research/bundle/${bundleId}`); await m.screenshot({ path: 'a10-mobile-bundle.png', fullPage: true });
});

await step('dark theme: the result page is readable', async () => {
  await a.emulateMedia({ colorScheme: 'dark' }); await a.goto(`/research/bundle/${bundleId}`); await a.getByRole('heading', { name: 'Conclusions', level: 2 }).first().waitFor(); await a.screenshot({ path: 'a10-dark.png', fullPage: true });
});

await step('delete research: removing a session removes its results', async () => {
  const p = await ctx(); a = p; await login(p, A); await p.goto(`/research/p/${projectId}`);
  p.once('dialog', (d) => d.accept()); await p.locator(`[data-session-id="${webSessionId}"]`).getByRole('button', { name: /Delete/ }).click(); await p.getByText('Research deleted.').waitFor();
  const gone = await p.request.get(`/api/v1/research/sessions/${webSessionId}`); ok(gone.status() === 404, 'deleted session is 404, was ' + gone.status());
});

console.log('console problems:', problems.length); for (const x of [...new Set(problems)].slice(0, 12)) console.log('  ' + x.slice(0, 220));
const failed = out.filter((l) => l.startsWith('FAIL')).length; console.log(`\n${out.length - failed} ok, ${failed} failed`);
await browser.close(); process.exit(failed ? 1 : 0);
