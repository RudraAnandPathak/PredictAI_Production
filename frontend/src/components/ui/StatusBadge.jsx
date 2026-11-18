import { STATUS_LABEL } from '../../app/navigation.js';

export function StatusBadge({ status }) {
  return <span className={`badge badge--${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}
