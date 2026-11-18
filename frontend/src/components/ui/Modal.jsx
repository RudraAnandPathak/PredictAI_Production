import { useEffect, useId, useRef } from 'react';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Accessible modal dialog: focus moves in, is trapped, Escape closes, focus returns to the opener. */
export function Modal({ title, onClose, children, dismissible = true }) {
  const titleId = useId();
  const panel = useRef(null);
  const opener = useRef(null);

  useEffect(() => {
    opener.current = document.activeElement;
    const first = panel.current?.querySelector('[data-autofocus]') ?? panel.current?.querySelector(FOCUSABLE);
    first?.focus();
    return () => {
      if (opener.current && document.contains(opener.current)) opener.current.focus();
    };
  }, []);

  function onKeyDown(event) {
    if (event.key === 'Escape' && dismissible) {
      event.stopPropagation();
      onClose?.();
      return;
    }
    if (event.key !== 'Tab') return;
    const items = [...panel.current.querySelectorAll(FOCUSABLE)];
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div className="modal" onKeyDown={onKeyDown}>
      <div className="modal__scrim" onClick={dismissible ? onClose : undefined} aria-hidden="true" />
      <div ref={panel} className="modal__panel" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId} className="modal__title">{title}</h2>
        {children}
      </div>
    </div>
  );
}
