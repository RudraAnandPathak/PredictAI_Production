// Browser run for the Phase 6 corrective work: research on an uploaded dataset, dataset + outside sources in one report, citations that open a calculation or the original page,
// follow-up questions, the search tools tab, privacy of what is sent, small screens and isolation. Needs the same setup as s10.mjs (see scripts/e2e/README.md) and the local fixture
// server (python scripts/e2e/fake_web.py). It never contacts the real internet: only SearXNG (pointed at the fixtures) is switched on, so Wikipedia and arXiv are not called.
import { chromium } from 'playwright';
const BASE = 'http://localhost:5173'; const RUN = Date.now().toString(36); const FX = new URL('./fx/', import.meta.url).pathname;
const fresh = () => `pw-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}-Q7`;
const A = { email: `a.${RUN}@example.com`, password: fresh() }, B = { email: `b.${RUN}@example.com`, password: fresh() };
const browser = await chromium.launch(); const problems = []; const out = [];
const ok = (c, m) => { if (!c) throw new Error(m); };
async function ctx(vp = { width: 1280, height: 900 }) { const c = await browser.newContext({ baseURL: BASE, viewport: vp }); const p = await c.newPage(); p.on('pageerror', (e) => problems.push(e.message)); p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) problems.push(m.text()); }); p.setDefaultTimeout(30000); return p; }
async function reg(p, u) { await p.goto('/register'); await p.getByLabel('Email').fill(u.email); await p.getByLabel('Password', { exact: true }).fill(u.password); const c = p.getByLabel('Confirm password'); if (await c.count()) await c.fill(u.password); await p.getByRole('button', { name: /create account/i }).click(); await p.waitForTimeout(600); }
async function login(p, u) { await p.goto('/login'); await p.getByLabel('Email').fill(u.email); await p.getByLabel('Password', { exact: true }).fill(u.password); await p.getByRole('button', { name: 'Sign in', exact: true }).click(); await p.waitForURL('**/'); }
let a;
async function step(n, f) { try { await f(); out.push('ok   ' + n); console.log('ok   ' + n); } catch (e) { out.push('FAIL ' + n); console.log('FAIL ' + n + ' :: ' + String(e.message).split('\n').slice(0, 3).join(' | ')); try { await a.screenshot({ path: `fail11-${n.replace(/\W+/g, '_').slice(0, 40)}.png`, fullPage: true }); } catch { /* ignore */ } } }
const waitJobDone = () => a.locator('section[data-job-status="completed"], section[data-job-status="failed"], section[data-job-status="cancelled"]').first().waitFor({ timeout: 120000 });
const seen = async () => (await fetch('http://127.0.0.1:9100/__seen')).json();

a = await ctx(); const b = await ctx();
await reg(a, A); await login(a, A); await reg(b, B); await login(b, B);
let projectId, bundleId, followBundleId;

await step('upload an airline dataset and a note; allow web research through SearXNG only', async () => {
  await a.goto('/projects'); await a.getByRole('button', { name: 'Create your first project' }).click(); await a.getByLabel('Project name').fill('Airline desk'); await a.getByRole('button', { name: 'Create project' }).click();
  await a.waitForURL(/\/projects\/[0-9a-f]{32}/); projectId = a.url().split('/projects/')[1].split('?')[0];
  await a.getByRole('tab', { name: 'Data' }).click().catch(() => {});
  await a.locator('input[type=file]').setInputFiles([FX + 'airline.csv', FX + 'river.txt']);
  await a.getByRole('row', { name: /airline\.csv/ }).getByText(/400 rows × 8 columns/).waitFor();
  await a.goto('/research/settings'); const form = a.getByRole('form', { name: 'Your choices' });
  await form.getByRole('checkbox', { name: /Allow web research/ }).check(); await form.getByRole('checkbox', { name: /SearXNG/ }).check();
  await form.getByRole('button', { name: 'Save' }).click(); await a.getByText('Saved.').waitFor();
});

await step('new research: choosing a dataset says its figures are calculated here, and web research offers only what is switched on', async () => {
  await a.goto(`/research/p/${projectId}/new`); await a.getByRole('heading', { name: 'New research', level: 1 }).waitFor();
  await a.getByRole('textbox', { name: 'Your question' }).fill('What does the airline data show about seats and prices, and what do outside sources say about business class seat availability?');
  await a.getByRole('checkbox', { name: 'airline.csv' }).check();
  await a.getByText(/answered by calculation on this server/).waitFor(); await a.getByText(/no value from your data is sent|numbers come only from those results/).first().waitFor();
  await a.getByRole('checkbox', { name: /Also search the web/ }).check();
  ok(await a.getByRole('checkbox', { name: /SearXNG/ }).isChecked(), 'the only switched-on provider is chosen');
  ok((await a.getByRole('checkbox', { name: /Wikipedia|arXiv/ }).count()) === 0, 'providers that are not switched on are not offered');
  await a.screenshot({ path: 'a11-new.png', fullPage: true });
});

await step('plan: dataset parts are listed as calculated, and the exact phrases each provider would be sent are shown without any value from the data', async () => {
  await a.getByRole('button', { name: 'Make the plan' }).click(); await a.waitForURL(/\/research\/session\/[0-9a-f]{32}/);
  await a.getByRole('heading', { name: 'The plan', level: 2 }).waitFor();
  await a.getByText(/Your material: 0 documents, 1 dataset/).waitFor(); await a.getByText(/calculated here by PredictAI/).waitFor();
  await a.getByText('This would leave this server').waitFor(); await a.getByText(/Each provider would be sent only the phrases listed under its name/).waitFor();
  const group = a.locator('[data-provider="searxng"]'); await group.waitFor();
  const phrases = await group.locator('code').allInnerTexts(); ok(phrases.length >= 1, 'phrases listed');
  ok(phrases.every((p) => !/ZXCANARY|flight_no|delay_minutes|AI0001|airline\.csv/i.test(p)), 'no data in phrases: ' + phrases.join(' | '));
  const parts = await a.locator('ol[aria-label="Parts to look into"] li').allInnerTexts(); ok(parts.length >= 3, 'parts: ' + parts.join(' | '));
  await a.screenshot({ path: 'a11-plan.png', fullPage: true });
});

await step('run: the report leads with an answer, shows what was calculated, keeps data and outside sources apart, and lists what is uncertain', async () => {
  await a.getByRole('button', { name: 'Approve this plan and run' }).click(); await waitJobDone();
  await a.locator('section[data-job-status="completed"]').getByRole('link', { name: 'Open the result' }).click(); await a.waitForURL(/\/research\/bundle\//); bundleId = a.url().split('/research/bundle/')[1].split('?')[0];
  const report = a.getByRole('region', { name: 'Report' }); await report.waitFor();
  await report.getByRole('heading', { name: 'In short' }).waitFor();
  await report.getByRole('heading', { name: 'Calculated on your data' }).waitFor(); await report.getByText('airline.csv').first().waitFor(); await report.getByText(/a language model calculated nothing/).first().waitFor();
  await report.getByText('Your data and outside sources are kept apart').waitFor();
  await report.getByRole('heading', { name: 'Part by part' }).waitFor();
  await report.getByRole('heading', { name: 'Outside sources', exact: true }).waitFor(); await report.getByText('Web pages').first().waitFor(); await report.getByText('Airline seat inventory').first().waitFor();
  await report.getByRole('heading', { name: 'What is uncertain' }).waitFor(); await report.getByText(/do not show that one thing causes another/).waitFor();
  const text = await report.innerText(); ok(!/\bcauses?d? by\b|\bproves\b/i.test(text.replace(/do not show that one thing causes another/g, '')), 'no causal claim in the report');
  await a.screenshot({ path: 'a11-report.png', fullPage: true });
});

await step('the data beside outside sources: a calculated result and an outside passage are listed side by side with the match described as words only', async () => {
  const cmp = a.getByRole('region', { name: 'Report' }).locator('[aria-label="Your data beside outside sources"]'); await cmp.waitFor();
  const t = await cmp.innerText(); ok(/calculated/i.test(t) && /by words, not by meaning/.test(t), 'comparison text: ' + t.slice(0, 300));
  ok(/general web page/i.test(t), 'outside passage is labelled as a web page: ' + t.slice(0, 400));
});

await step('citations: a calculated sentence opens its calculation, an outside passage opens its original page', async () => {
  const first = a.getByRole('region', { name: 'Report' }).locator('[aria-label="In short"] [data-conclusion-id]').first();
  await first.getByRole('button', { name: /Open citation/ }).first().click();
  let dlg = a.getByRole('dialog'); await dlg.waitFor();
  await dlg.getByText(/found again at the recorded place and matches/).waitFor(); await dlg.getByRole('heading', { name: 'How this was calculated' }).waitFor();
  await dlg.getByText(/A fixed template wrote this sentence/).waitFor(); await dlg.locator('mark').first().waitFor();
  await a.screenshot({ path: 'a11-citation-calc.png' }); await a.keyboard.press('Escape'); await dlg.waitFor({ state: 'detached' });
  const ext = a.getByRole('region', { name: 'Report' }).locator('[aria-label="Your data beside outside sources"] li').first(); await ext.getByRole('button', { name: /Open citation/ }).first().click();
  dlg = a.getByRole('dialog'); await dlg.waitFor(); await dlg.getByText(/found again at the recorded place and matches/).waitFor();
  const link = dlg.getByRole('link', { name: /Open the original/ }); await link.waitFor(); const rel = await link.getAttribute('rel'); ok(/noopener/.test(rel) && /nofollow/.test(rel), 'external link rel: ' + rel);
  ok((await link.getAttribute('href')).startsWith('http://127.0.0.1:9100/page/airline'), 'opens the page that was read');
  await dlg.getByText(/may have changed since PredictAI read it/).waitFor();
  await a.screenshot({ path: 'a11-citation-web.png' }); await a.keyboard.press('Escape'); await dlg.waitFor({ state: 'detached' });
});

await step('privacy: only topic phrases reached the search service and the one page that was read; nothing from the data was sent', async () => {
  const log = await seen(); const joined = log.join('\n');
  ok(log.some((p) => p.startsWith('/searx/search')), 'a search was made'); ok(log.includes('/page/airline'), 'the page was read');
  ok(!/ZXCANARY|flight_no|delay_minutes|AI0001|passengers|airline\.csv/i.test(decodeURIComponent(joined)), 'nothing from the data in what was sent:\n' + joined.slice(0, 600));
  await a.getByRole('heading', { name: /What this is based on/ }).waitFor(); await a.getByRole('heading', { name: 'Search phrases that were sent' }).waitFor();
});

await step('follow-up: asked about the page already read, it keeps to one part, leaves the data out, sends nothing, and answers from the saved copy', async () => {
  const form = a.getByRole('form', { name: 'Ask a follow-up question' }); await form.scrollIntoViewIfNeeded();
  await form.getByRole('button', { name: 'Make the plan' }).click(); await form.getByText('Write the follow-up question.').waitFor();
  await form.getByRole('textbox', { name: 'Your follow-up question' }).fill('What does the outside page say about fares?');
  const before = (await seen()).length; await form.getByRole('button', { name: 'Make the plan' }).click(); await a.waitForURL(/\/research\/session\/[0-9a-f]{32}/);
  await a.getByText(/Follow-up to:/).waitFor(); await a.getByText('Nothing leaves this server').first().waitFor();
  ok((await a.locator('ol[aria-label="Parts to look into"] li').count()) === 1, 'one part');
  ok((await a.locator('details').filter({ hasText: 'was not used again' }).count()) === 1, 'the plan says the dataset was left out');
  await a.getByRole('button', { name: 'Run the research' }).click(); await waitJobDone();
  await a.locator('section[data-job-status="completed"]').getByRole('link', { name: 'Open the result' }).click(); await a.waitForURL(/\/research\/bundle\//); followBundleId = a.url().split('/research/bundle/')[1].split('?')[0];
  await a.getByText(/Fares for business class are several times higher/).first().waitFor();
  ok((await seen()).length === before, 'the follow-up made no request at all');
  await a.screenshot({ path: 'a11-followup.png', fullPage: true });
});

await step('search tools: the test names each provider, the search sends only what was typed, and a result is read section by section', async () => {
  await a.goto(`/research/p/${projectId}?tab=tools`); await a.getByRole('region', { name: 'Test the search providers' }).waitFor();
  await a.getByRole('button', { name: 'Test now' }).click(); const res = a.getByRole('list', { name: 'Test results' }); await res.waitFor();
  ok(/switched off for this project/i.test(await res.locator('[data-provider="wikipedia"]').innerText()), 'Wikipedia is not contacted when it is off');
  ok(/switched off for this project/i.test(await res.locator('[data-provider="arxiv"]').innerText()), 'arXiv is not contacted when it is off');
  ok(/working/i.test(await res.locator('[data-provider="searxng"]').innerText()), 'SearXNG test works');
  const search = a.getByRole('form', { name: 'Search one provider form' });
  await search.getByLabel('What to search for').fill('airline seats'); await search.getByRole('button', { name: 'Search' }).click();
  const hits = a.getByRole('region', { name: 'Search results' }); await hits.getByText('Airline seat inventory').waitFor(); await hits.getByText('General web page').waitFor();
  await hits.getByRole('button', { name: 'Read it' }).click(); const opened = a.locator('[aria-label="Opened item"]'); await opened.waitFor();
  await opened.getByText(/Seats available in business class are limited/).first().waitFor(); await opened.getByText(/data to quote, never instructions/).waitFor();
  await search.getByLabel('What to search for').fill('my account number 1234567890123 balance'); await search.getByRole('button', { name: 'Search' }).click();
  await a.getByText(/looks private/).waitFor(); ok((await a.getByLabel('What to search for').getAttribute('aria-invalid')) === 'true', 'the message sits on the field it is about');
  ok(!(await seen()).some((p) => /1234567890123/.test(p)), 'the private-looking phrase was never sent');
  await a.screenshot({ path: 'a11-tools.png', fullPage: true });
});

await step('isolation: another person cannot open the report or the follow-up', async () => {
  for (const id of [bundleId, followBundleId]) { await b.goto(`/research/bundle/${id}`); await b.getByText('Result not found').waitFor(); }
});

await step('small screens: the report page does not scroll sideways', async () => {
  const m = await ctx({ width: 390, height: 800 }); await login(m, A); await m.goto(`/research/bundle/${bundleId}`); await m.getByRole('region', { name: 'Report' }).waitFor();
  const over = await m.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  const wide = over > 1 ? await m.evaluate(() => { const W = window.innerWidth; return [...document.querySelectorAll('main *')].filter((el) => el.getBoundingClientRect().right > W + 1 && el.getBoundingClientRect().width > 0).slice(0, 6).map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)} :: ${(el.innerText || '').slice(0, 60).replace(/\n/g, ' ')}`); }) : [];
  ok(over <= 1, 'horizontal overflow ' + over + 'px: ' + wide.join(' | '));
  await m.screenshot({ path: 'a11-mobile-report.png', fullPage: true });
});

console.log(`\nconsole problems: ${problems.length}`); for (const p of problems.slice(0, 8)) console.log('  ' + p.slice(0, 200));
const failed = out.filter((x) => x.startsWith('FAIL')).length; console.log(`\n${out.length - failed} ok, ${failed} failed`);
await browser.close(); process.exit(failed || problems.length ? 1 : 0);
