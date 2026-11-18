// The application's navigation model. One source of truth for the sidebar, the routes
// and the overview. Status labels follow the spec's vocabulary; phase numbers follow
// the Master Engineering Documentation roadmap (section 14).

export const STATUS = Object.freeze({
  EXISTING: 'existing',
  PROTOTYPE: 'prototype',
  PLANNED: 'planned',
  ARCHITECTURAL: 'architectural',
  PENDING: 'pending',
});

export const STATUS_LABEL = Object.freeze({
  existing: 'Existing',
  prototype: 'Prototype',
  planned: 'Planned',
  architectural: 'Architectural requirement',
  pending: 'Pending approval',
});

export const ROADMAP_PHASES = Object.freeze({
  1: 'Platform foundation',
  2: 'Identity & governance',
  3: 'Projects & data workspace',
  4: 'Core intelligence',
  5: 'Predictive & exam',
  6: 'Research & world intelligence',
  7: 'Reports & collaboration',
  8: 'Development workspace',
  9: 'Pilot & hardening',
  10: 'Research Intelligence',
  11: 'Conversations',
});

// Who the area is intended for. Phase 1 has no sign-in, so this is descriptive only;
// the backend remains the authority once entitlements exist.
export const AUDIENCE = Object.freeze({ STANDARD: 'standard', SPECIAL: 'special', OWNER: 'owner' });

// Who may OPEN an area: public (anyone), user (signed in), special, owner. This only decides what the
// interface shows; the backend enforces access on every request.
const PUBLIC_IDS = new Set(['overview', 'status']);
export const accessFor = (id, audience) => (PUBLIC_IDS.has(id) ? 'public' : audience === 'standard' ? 'user' : audience);

const item = (id, label, path, icon, status, phase, audience, summary) =>
  Object.freeze({ id, label, path, icon, status, phase, audience, summary, access: accessFor(id, audience) });

export const NAV_GROUPS = Object.freeze([
  Object.freeze({
    id: 'platform',
    label: 'Platform',
    items: Object.freeze([
      item('overview', 'Overview', '/', 'home', 'existing', 1, 'standard', 'The application shell and the state of the platform.'),
      item('status', 'System status', '/system/status', 'activity', 'existing', 1, 'standard', 'Live connection to the PredictAI API.'),
    ]),
  }),
  Object.freeze({
    id: 'workspace',
    label: 'Workspace',
    items: Object.freeze([
      item('dashboard', 'Dashboard', '/dashboard', 'chart', 'existing', 7, 'standard', 'Your work, limits and what needs attention, from your own records.'),
      item('projects', 'Projects', '/projects', 'folder', 'existing', 3, 'standard', 'Containers that scope documents, datasets, conversations, jobs and reports.'),
      item('uploads', 'Uploads', '/uploads', 'upload', 'existing', 3, 'standard', 'Upload, validate, preview and profile files with source lineage.'),
      item('conversations', 'Conversations', '/conversations', 'chat', 'existing', 11, 'standard', 'One place to ask about a dataset or a research workspace, with saved history, search, export, and sources cited where the answer uses them.'),
      item('analytics', 'Analytics', '/analytics', 'chart', 'existing', 4, 'standard', 'Descriptive analytics, relationships and statistical tests with evidence.'),
      item('predictions', 'Predictions', '/predictions', 'trend', 'existing', 5, 'standard', 'Prepare data, train and compare models automatically, evaluate and explain them, and predict single cases or whole datasets.'),
      item('exam', 'Exam tools', '/exam', 'book', 'existing', 7, 'standard', 'Syllabus and past-paper analysis (historical frequency, not prediction) and reviewed mock exams built from your own material.'),
      item('notebook', 'Notebook', '/notebook', 'book', 'existing', 10, 'standard', 'Add your sources, ask questions that cite them, and make briefings, study guides, FAQs and timelines. Add sources or let Deep Research find them, ask questions that cite them, and make written guides, flashcards, quizzes, mind maps, tables, slides, infographics and audio overviews.'),
      item('reports', 'Reports', '/reports', 'file', 'existing', 7, 'standard', 'Cited, versioned reports built from your saved results, with exports, sharing and scheduled delivery.'),
      item('schedules', 'Schedules', '/schedules', 'refresh', 'existing', 7, 'standard', 'Scheduled report delivery with history, retries and honest delivery status.'),
      item('workspaces', 'Workspaces', '/workspaces', 'user', 'existing', 7, 'standard', 'Share chosen reports, models and analyses with people you invite. Nothing is public.'),
      item('intelligence', 'Advanced analysis', '/intelligence', 'trend', 'existing', 7, 'standard', 'Possible drivers of an outcome, with what the data cannot say. Associations, never proven causes.'),
      item('models', 'Model registry', '/models', 'cube', 'existing', 7, 'standard', 'Versioned, verified copies of your trained models with a confirmed lifecycle. Records only: nothing is deployed.'),
      item('account', 'Account', '/account', 'user', 'existing', 2, 'standard', 'Profile, password, special access, signed-in devices and connected accounts.'),
    ]),
  }),
  Object.freeze({
    id: 'professional',
    label: 'Professional',
    note: 'Special access',
    items: Object.freeze([
      item('development', 'Development', '/development', 'terminal', 'existing', 8, 'special', 'Code, notebooks, environments, jobs, experiments, Git and pipelines for your own projects. Running code is gated by the installation’s isolation setting.'),
      item('compute', 'Compute', '/compute', 'cpu', 'existing', 8, 'special', 'Usage, quotas, resource status and job controls, measured from your real jobs.'),
    ]),
  }),
  Object.freeze({
    id: 'owner',
    label: 'Owner',
    note: 'Restricted',
    items: Object.freeze([
      item('owner', 'Owner controls', '/owner', 'shield', 'existing', 2, 'owner', 'Account status, special access, access codes, owners and the security audit log. Password-confirmed changes.'),
    ]),
  }),
]);

export const NAV_ITEMS = Object.freeze(NAV_GROUPS.flatMap((group) => group.items));

/** The groups (and items) a given person should see. `canOpen(access)` decides per access level. */
export function visibleGroups(canOpen, groups = NAV_GROUPS) {
  return groups
    .map((group) => ({ ...group, items: group.items.filter((entry) => canOpen(entry.access)) }))
    .filter((group) => group.items.length > 0);
}

export function findNavItem(path) {
  return NAV_ITEMS.find((entry) => entry.path === path) ?? null;
}

/** Returns a list of problems (empty when the model is consistent). */
export function validateNavigation(groups = NAV_GROUPS) {
  const problems = [];
  const ids = new Set();
  const paths = new Set();
  const statuses = new Set(Object.values(STATUS));
  const audiences = new Set(Object.values(AUDIENCE));
  for (const group of groups) {
    for (const entry of group.items) {
      if (ids.has(entry.id)) problems.push(`duplicate id: ${entry.id}`);
      if (paths.has(entry.path)) problems.push(`duplicate path: ${entry.path}`);
      ids.add(entry.id);
      paths.add(entry.path);
      if (!entry.path.startsWith('/')) problems.push(`${entry.id}: path must start with /`);
      if (!statuses.has(entry.status)) problems.push(`${entry.id}: unknown status ${entry.status}`);
      if (!audiences.has(entry.audience)) problems.push(`${entry.id}: unknown audience ${entry.audience}`);
      if (!['public', 'user', 'special', 'owner'].includes(entry.access)) problems.push(`${entry.id}: unknown access ${entry.access}`);
      if (!(entry.phase in ROADMAP_PHASES)) problems.push(`${entry.id}: unknown phase ${entry.phase}`);
      if (!entry.summary) problems.push(`${entry.id}: missing summary`);
    }
  }
  return problems;
}
