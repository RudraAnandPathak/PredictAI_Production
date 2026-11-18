export function formatBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = n / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

export const KIND_LABEL = { dataset: 'Dataset', document: 'Document' };
export const PROJECT_STATUS_LABEL = { active: 'Active', archived: 'Archived' };

export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

export const NAME_MAX = 120;
export const DESCRIPTION_MAX = 2000;

export function checkProjectName(value) {
  const v = String(value ?? '').trim();
  if (!v) return 'Give the project a name.';
  if (v.length > NAME_MAX) return `Use ${NAME_MAX} characters or fewer.`;
  return null;
}

export function checkDescription(value) {
  return String(value ?? '').length > DESCRIPTION_MAX ? `Use ${DESCRIPTION_MAX} characters or fewer.` : null;
}

const EVENT_TEXT = {
  'project.created': 'Project created',
  'project.updated': 'Project details changed',
  'project.archived': 'Project archived',
  'project.restored': 'Project restored',
  'asset.created': 'File added',
  'asset.duplicate': 'Duplicate upload recognised',
  'asset.failed': 'A file could not be processed',
  'asset.deleted': 'File deleted',
  'asset.profiled': 'Data profile generated',
  'asset.rolled_back': 'Rolled back to an earlier version',
  'transformation.previewed': 'Cleaning previewed',
  'transformation.applied': 'Cleaning saved as a new version',
  'transformation.discarded': 'Cleaning preview discarded',
};

export function describeEvent(type) {
  return EVENT_TEXT[type] ?? type.replace(/[._]/g, ' ');
}
