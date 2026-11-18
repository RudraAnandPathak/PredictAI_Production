import { useRef, useState } from 'react';
import { BrainTab } from './BrainTab.jsx';
import { ReviewsTab } from './ReviewsTab.jsx';
import { AuditTab } from './AuditTab.jsx';
import { CodesTab } from './CodesTab.jsx';
import { OwnersTab } from './OwnersTab.jsx';
import { MaintenanceTab, MonitoringTab, OverviewTab, OwnerEventsTab, QuotasTab } from './OwnerOpsTabs.jsx';
import { UsersTab } from './UsersTab.jsx';

const TABS = [
  { id: 'overview', label: 'Overview', panel: <OverviewTab /> },
  { id: 'monitoring', label: 'Monitoring', panel: <MonitoringTab /> },
  { id: 'brain', label: 'AI Brain', panel: <BrainTab /> },
  { id: 'reviews', label: 'Reviews', panel: <ReviewsTab /> },
  { id: 'users', label: 'Accounts', panel: <UsersTab /> },
  { id: 'codes', label: 'Access codes', panel: <CodesTab /> },
  { id: 'audit', label: 'Security events', panel: <AuditTab /> },
  { id: 'owners', label: 'Owners', panel: <OwnersTab /> },
  { id: 'quotas', label: 'Limits', panel: <QuotasTab /> },
  { id: 'maintenance', label: 'Maintenance', panel: <MaintenanceTab /> },
  { id: 'events', label: 'Owner actions', panel: <OwnerEventsTab /> },
];

export function OwnerPage() {
  const [active, setActive] = useState('overview');
  const refs = useRef({});

  function onKeyDown(event) {
    const index = TABS.findIndex((t) => t.id === active);
    let nextIndex = null;
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % TABS.length;
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + TABS.length) % TABS.length;
    if (event.key === 'Home') nextIndex = 0;
    if (event.key === 'End') nextIndex = TABS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    setActive(TABS[nextIndex].id);
    refs.current[TABS[nextIndex].id]?.focus();
  }

  const current = TABS.find((t) => t.id === active);
  return (
    <article className="page">
      <header className="page__header">
        <p className="eyebrow">Owner · restricted</p>
        <h1 className="page-title">Owner controls</h1>
        <p className="lead">Manage accounts, special access and security events. Every action here is checked again by the server and written to the audit log; changes may ask you to confirm your password.</p>
      </header>
      <div className="tabs" role="tablist" aria-label="Owner sections" onKeyDown={onKeyDown}>
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" className="tab" id={`tab-${t.id}`} aria-selected={active === t.id} aria-controls={`panel-${t.id}`}
            tabIndex={active === t.id ? 0 : -1} ref={(el) => { refs.current[t.id] = el; }} onClick={() => setActive(t.id)}>{t.label}</button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${current.id}`} aria-labelledby={`tab-${current.id}`} tabIndex={0}>{current.panel}</div>
    </article>
  );
}
