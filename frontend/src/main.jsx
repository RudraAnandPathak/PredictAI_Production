import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './design/index.css';
import { initTheme } from './app/theme.js';

initTheme();
const root = createRoot(document.getElementById('root'));

function StartupError({ error }) {
  return (
    <main className="startup-error" role="alert">
      <h1>PredictAI could not start</h1>
      <p>The application hit a problem while loading its configuration.</p>
      <pre>{error instanceof Error ? error.message : String(error)}</pre>
    </main>
  );
}

// Loaded dynamically so a configuration error is shown on screen instead of a blank page.
import('./app/App.jsx')
  .then(({ App }) => root.render(<StrictMode><App /></StrictMode>))
  .catch((error) => {
    console.error(error);
    root.render(<StartupError error={error} />);
  });
