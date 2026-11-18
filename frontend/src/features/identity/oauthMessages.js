/** Plain-language messages for the fixed set of codes the server puts in `?oauth_error=` (never free text). */
export const PROVIDER_LABEL = { google: 'Google', github: 'GitHub' };

const MESSAGES = {
  denied: (p) => `You cancelled the ${p} sign-in, so nothing was changed.`,
  oauth_no_account: (p) => `No PredictAI account is connected to this ${p} account yet. Create an account first, or sign in with your password and connect ${p} from your Account page.`,
  oauth_email_exists: (p) => `A PredictAI account with this email already exists. Sign in with your password, then connect ${p} from your Account page. Accounts are never merged automatically.`,
  oauth_email_unverified: (p) => `${p} did not confirm an email address for this account, so we cannot create a PredictAI account from it. Verify your email with ${p}, or register with an email and password.`,
  account_not_active: () => 'This account cannot sign in right now. Contact the site owner if you think this is a mistake.',
  rate_limited: () => 'Too many attempts. Please wait a few minutes and try again.',
  provider_not_configured: (p) => `Sign-in with ${p} is not set up on this server yet.`,
  invalid_token: () => 'That sign-in link expired or was opened in a different browser. Please start again from this page.',
};

/** Message for an `oauth_error` code; unknown codes get a safe generic sentence. */
export function oauthErrorMessage(code, provider) {
  const label = PROVIDER_LABEL[provider] ?? 'the provider';
  const make = MESSAGES[String(code ?? '').toLowerCase()];
  return make ? make(label) : `We could not complete the sign-in with ${label}. Nothing was changed. Please try again.`;
}

/** Reads and returns `{message}` from the URL once, or null. */
export function readOAuthError(search) {
  const params = new URLSearchParams(search);
  const code = params.get('oauth_error');
  return code ? oauthErrorMessage(code, params.get('provider')) : null;
}
