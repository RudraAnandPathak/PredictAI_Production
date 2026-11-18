// Forms and guards for AI provider configuration (owners only). Secrets are never typed here: a provider names an ENVIRONMENT VARIABLE on the server,
// and the server reads the key from there. The screen cannot enable a paid provider without the owner's explicit confirmation.

// Mirrors providers/service.py and providers/base.py. The server validates again; this only saves a round trip.
const KEY_RE = /^[a-z][a-z0-9_-]{1,31}$/;
const ENV_RE = /^PREDICTAI_AI_KEY_[A-Z0-9_]{1,40}$/;
export const LIMITS = Object.freeze({ priority: [1, 999], timeoutS: [2, 300], maxRetries: [0, 3] });
export const TASKS = Object.freeze(['plan', 'extract', 'summarize', 'classify', 'sentiment', 'entities', 'synthesize', 'embed']);

export const PROVIDER_KINDS = Object.freeze([['ollama', 'Ollama (runs on this server or your network)'], ['openai_compatible', 'OpenAI-compatible server']]);

/** Request body for creating a provider. Returns { body } or { errors }. */
export function buildProviderBody(form) {
  const errors = {};
  const key = String(form?.key ?? '').trim().toLowerCase();
  if (!KEY_RE.test(key)) errors.key = 'Use 2–32 lowercase letters, digits, "-" or "_", starting with a letter.';
  const label = String(form?.label ?? '').trim();
  if (label.length < 1 || label.length > 80) errors.label = 'Give the provider a short name (up to 80 characters).';
  const baseUrl = String(form?.baseUrl ?? '').trim();
  if (!/^https?:\/\/\S+$/i.test(baseUrl)) errors.baseUrl = 'The address must start with http:// or https://';
  if (!String(form?.model ?? '').trim()) errors.model = 'Name the model to use.';
  const secretEnv = String(form?.secretEnv ?? '').trim();
  if (secretEnv && !ENV_RE.test(secretEnv)) errors.secretEnv = 'Give the NAME of an environment variable on the server, starting with PREDICTAI_AI_KEY_ (for example PREDICTAI_AI_KEY_MYHOST) — not the key itself.';
  for (const [field, name] of [['timeoutS', 'Timeout (seconds)'], ['maxRetries', 'Retries'], ['priority', 'Priority']]) {
    const [min, max] = LIMITS[field];
    const v = form?.[field];
    if (v === '' || v === undefined || v === null) continue;
    const n = Number(v);
    if (!Number.isInteger(n) || n < min || n > max) errors[field] = `${name}: whole number from ${min} to ${max}.`;
  }
  if (Object.keys(errors).length) return { errors };
  const body = { key, kind: form.kind || 'ollama', label, base_url: baseUrl, model: String(form.model).trim(), paid: Boolean(form.paid) };
  if (form.embedModel) body.embed_model = String(form.embedModel).trim();
  if (Array.isArray(form.tasks) && form.tasks.length) body.tasks = form.tasks.filter((t) => TASKS.includes(t));
  if (secretEnv) body.secret_env = secretEnv;
  for (const [field, name] of [['timeoutS', 'timeout_s'], ['maxRetries', 'max_retries'], ['priority', 'priority']]) {
    if (form[field] !== '' && form[field] !== undefined && form[field] !== null) body[name] = Number(form[field]);
  }
  return { body };
}

/** What the person must confirm before this provider can be switched on. */
export function enableChecks(provider, listing) {
  const notes = [];
  let blocked = '';
  if (!provider) return { notes, blocked: 'Provider not found.', needsPaidConfirm: false };
  if (provider.paid && !listing?.paid_allowed) blocked = 'Paid providers are switched off for this platform. The platform owner must allow them first.';
  if (provider.data_leaves_platform && !listing?.remote_allowed) blocked = blocked || 'Providers that send data off this server are switched off for this platform.';
  if (provider.data_leaves_platform) notes.push('Text you send to this provider leaves this server.');
  if (provider.paid) notes.push('This provider may charge for use. PredictAI cannot see or limit what the provider bills.');
  if (provider.secret_env && provider.has_secret === false) notes.push(`The environment variable ${provider.secret_env} is not set on the server.`);
  return { notes, blocked, needsPaidConfirm: Boolean(provider.paid) };
}

/** Rows for the usage table, as the server counted them. Units are tokens as the provider reported them (or a rough estimate); no money amount is ever worked out here. */
export function usageRows(usage) {
  return (usage?.rows ?? []).map((r, i) => ({
    key: `${r.provider}|${r.task}|${r.outcome}|${i}`, provider: r.provider, task: r.task, outcome: r.outcome, calls: r.calls, unitsIn: r.units_in, unitsOut: r.units_out, avgMs: r.avg_latency_ms,
  }));
}

export const usageErrors = (usage) => (usage?.errors ?? []).map((e) => ({ key: `${e.provider}|${e.code}`, provider: e.provider, code: e.code, count: e.count, lastAt: e.last_at }));

/** Plain sentence about the last test of a provider. */
export function probeLine(probe) {
  if (!probe) return 'Never tested.';
  if (probe.ok) return `Last test worked${probe.checked_at ? ` (${probe.checked_at})` : ''}.`;
  return `Last test failed${probe.message ? `: ${probe.message}` : probe.error ? ` (${probe.error})` : ''}.`;
}
