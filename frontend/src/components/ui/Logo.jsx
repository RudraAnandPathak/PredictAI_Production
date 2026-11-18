export function LogoMark({ size = 28 }) {
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 32 32" fill="none" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true" focusable="false">
      <path d="M10 5c0 6 12 6 12 11s-12 5-12 11" stroke="var(--accent)" />
      <path d="M22 5c0 6-12 6-12 11s12 5 12 11" stroke="var(--warm)" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="brand">
      <LogoMark />
      <span className="brand__word">
        PREDICT<span className="brand__ai">AI</span>
      </span>
    </span>
  );
}
