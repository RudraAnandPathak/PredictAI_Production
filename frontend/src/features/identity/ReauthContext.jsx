import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { identity } from '../../api/index.js';
import { Modal } from '../../components/ui/Modal.jsx';
import { Spinner } from '../../components/ui/Spinner.jsx';
import { FormMessage } from '../../components/forms/FormMessage.jsx';
import { PasswordField } from '../../components/forms/Field.jsx';
import { ProviderReauth } from '../../components/auth/ProviderReauth.jsx';
import { describeError } from './validation.js';
import { useAuth } from './AuthContext.jsx';

const ReauthContext = createContext(null);

/**
 * Sensitive actions (owner changes, creating access codes…) need a recent password confirmation.
 * `withReauth(action)` runs the action; if the server answers REAUTH_REQUIRED it asks for the
 * password once, then retries the action. The server enforces the rule either way.
 */
export function ReauthProvider({ children, service = identity }) {
  const { user, setCsrf } = useAuth();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const pending = useRef(null);

  const ask = useCallback(() => new Promise((resolve, reject) => {
    pending.current = { resolve, reject };
    setPassword('');
    setError(null);
    setOpen(true);
  }), []);

  const withReauth = useCallback(async (action) => {
    try {
      return await action();
    } catch (error_) {
      if (error_?.code !== 'REAUTH_REQUIRED') throw error_;
      await ask();
      return action();
    }
  }, [ask]);

  function cancel() {
    setOpen(false);
    pending.current?.reject(Object.assign(new Error('Password confirmation was cancelled.'), { code: 'REAUTH_CANCELLED' }));
    pending.current = null;
  }

  async function submit(event) {
    event.preventDefault();
    if (!password) return setError('Enter your password.');
    setBusy(true);
    setError(null);
    try {
      const result = await service.reauth(password);
      setCsrf(result.data?.csrf_token);
      setOpen(false);
      setPassword('');
      pending.current?.resolve();
      pending.current = null;
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const value = useMemo(() => ({ withReauth }), [withReauth]);
  return (
    <ReauthContext.Provider value={value}>
      {children}
      {open ? (
        <Modal title="Confirm your password" onClose={cancel}>
          {user && !user.hasPassword ? (
            <div className="form">
              <ProviderReauth providers={user.connectedProviders} reason="confirm this action" />
              <div className="form__actions"><button type="button" className="btn btn--ghost" onClick={cancel}>Cancel</button></div>
            </div>
          ) : (
          <form onSubmit={submit} noValidate className="form">
            <p className="muted">For your security, please confirm your password before continuing. You will not be asked again for a few minutes.</p>
            {error ? <FormMessage kind="error">{error}</FormMessage> : null}
            <PasswordField label="Password" name="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} data-autofocus required />
            <div className="form__actions">
              <button type="submit" className="btn" disabled={busy}>{busy ? <Spinner label="Confirming" /> : null} Confirm</button>
              <button type="button" className="btn btn--ghost" onClick={cancel}>Cancel</button>
            </div>
          </form>
          )}
        </Modal>
      ) : null}
    </ReauthContext.Provider>
  );
}

export function useReauth() {
  const context = useContext(ReauthContext);
  if (!context) throw new Error('useReauth must be used inside <ReauthProvider>.');
  return context;
}
