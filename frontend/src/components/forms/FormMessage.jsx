import { useEffect, useRef } from 'react';

/** Form-level feedback. Errors are announced assertively and receive focus so keyboard users notice them. */
export function FormMessage({ kind = 'error', title, children, focusOnShow = true }) {
  const ref = useRef(null);
  useEffect(() => {
    if (focusOnShow && kind === 'error') ref.current?.focus();
  }, [focusOnShow, kind, children, title]);
  if (!children && !title) return null;
  return (
    <div ref={ref} tabIndex={-1} className={`message message--${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {title ? <p className="message__title">{title}</p> : null}
      {children ? <div className="message__body">{children}</div> : null}
    </div>
  );
}
