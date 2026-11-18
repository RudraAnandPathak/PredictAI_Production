import { useState } from 'react';
import { applyTheme, storeTheme } from '../../app/theme.js';
import { Icon } from '../ui/Icon.jsx';

export function ThemeToggle() {
  const [theme, setTheme] = useState(() => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'));
  const next = theme === 'dark' ? 'light' : 'dark';

  const toggle = () => {
    applyTheme(next);
    storeTheme(next);
    setTheme(next);
  };

  return (
    <button type="button" className="icon-btn" onClick={toggle} aria-label={`Switch to ${next} theme`}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
    </button>
  );
}
