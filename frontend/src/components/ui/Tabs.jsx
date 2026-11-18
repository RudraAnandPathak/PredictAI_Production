import { useRef } from 'react';

/** Accessible tab set (arrow keys, Home/End). `tabs` = [{ id, label, panel }]. */
export function Tabs({ label, tabs, active, onChange }) {
  const refs = useRef({});
  function onKeyDown(event) {
    const index = tabs.findIndex((t) => t.id === active);
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    onChange(tabs[next].id);
    refs.current[tabs[next].id]?.focus();
  }
  const current = tabs.find((t) => t.id === active) ?? tabs[0];
  return (
    <>
      <div className="tabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" className="tab" id={`tab-${t.id}`} aria-selected={current.id === t.id}
            aria-controls={`panel-${t.id}`} tabIndex={current.id === t.id ? 0 : -1} ref={(el) => { refs.current[t.id] = el; }}
            onClick={() => onChange(t.id)}>{t.label}</button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${current.id}`} aria-labelledby={`tab-${current.id}`} tabIndex={0}>{current.panel}</div>
    </>
  );
}
