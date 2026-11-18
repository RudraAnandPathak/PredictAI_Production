// Plain-language labels for the words the research and news services send. They only NAME a value the server chose; nothing here decides what is true,
// how reliable a source is, or whether a statement is a fact. An unknown key is shown as itself (made readable), never mapped to a friendlier guess.

export const humanize = (key) => (typeof key === 'string' && key ? key.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '—');
export const labelOf = (map, key) => (key && Object.prototype.hasOwnProperty.call(map, key) ? map[key] : humanize(key));

/** Where a piece of material comes from. A prediction is never shown as a fact, and anything from outside says so. */
export const ORIGIN_LABEL = Object.freeze({
  user_provided: 'Your material',
  calculated: 'Calculated by PredictAI',
  model_prediction: 'Model prediction — not a fact',
  external_research: 'From outside sources',
});
export const ORIGIN_TONE = Object.freeze({ user_provided: 'info', calculated: 'ok', model_prediction: 'warn', external_research: 'muted' });
export const ORIGIN_HINT = Object.freeze({
  user_provided: 'Something you uploaded. PredictAI has not checked that it is accurate.',
  calculated: 'A figure PredictAI calculated from your data by a fixed method.',
  model_prediction: 'An estimate from a trained model. It is a forecast, not something that has happened.',
  external_research: 'Read from the web or a news feed with your permission. Not verified by PredictAI.',
});

/** What kind of statement a sentence is (set by the wording rules on the server, not by a judgment of truth). */
export const STATEMENT_LABEL = Object.freeze({
  stated: 'Stated as fact by the source',
  uncertain: 'Hedged by the source',
  opinion: 'Opinion or recommendation',
  prediction: 'Forecast or estimate',
  computed: 'Calculated by PredictAI',
});
export const STATEMENT_TONE = Object.freeze({ stated: 'muted', uncertain: 'warn', opinion: 'warn', prediction: 'warn', computed: 'ok' });

export const CONFIDENCE_LABEL = Object.freeze({
  well_supported: 'Several sources say this',
  single_source: 'One source says this',
  contested: 'Sources may disagree',
  uncertain_wording: 'The source itself is unsure',
  prediction: 'A forecast, not an established fact',
  calculated: 'Calculated from your data',
});
export const CONFIDENCE_TONE = Object.freeze({ well_supported: 'ok', single_source: 'warn', contested: 'danger', uncertain_wording: 'warn', prediction: 'warn', calculated: 'ok' });

export const QUALITY_VERDICT_LABEL = Object.freeze({ pass: 'Checks passed', caution: 'Use with care', fail: 'Problems found' });
export const QUALITY_VERDICT_TONE = Object.freeze({ pass: 'ok', caution: 'warn', fail: 'danger' });
export const CHECK_STATUS_LABEL = Object.freeze({ pass: 'Passed', warn: 'Warning', fail: 'Failed', info: 'For information' });
export const CHECK_STATUS_TONE = Object.freeze({ pass: 'ok', warn: 'warn', fail: 'danger', info: 'muted' });

export const SESSION_STATUS_LABEL = Object.freeze({ draft: 'Plan ready', running: 'Researching', completed: 'Finished', failed: 'Did not finish', cancelled: 'Cancelled', partial: 'Finished with gaps' });
export const SESSION_STATUS_TONE = Object.freeze({ draft: 'info', running: 'warn', completed: 'ok', failed: 'danger', cancelled: 'muted', partial: 'warn' });
export const BUNDLE_STATUS_LABEL = Object.freeze({ complete: 'Complete', partial: 'Partial — some parts found nothing', empty: 'Nothing found' });
export const BUNDLE_STATUS_TONE = Object.freeze({ complete: 'ok', partial: 'warn', empty: 'danger' });

export const JOB_STATUS_LABEL = Object.freeze({ queued: 'Waiting to start', running: 'Working', completed: 'Finished', failed: 'Did not finish', cancelled: 'Cancelled' });
export const JOB_STATUS_TONE = Object.freeze({ queued: 'muted', running: 'warn', completed: 'ok', failed: 'danger', cancelled: 'muted' });
export const FINAL_JOB_STATUSES = Object.freeze(['completed', 'failed', 'cancelled']);

export const PURPOSE_LABEL = Object.freeze({ general: 'General research', education: 'Study support' });

/** A news event is shown with how it is known: confirmed means several outlets agree, which is not the same as verified. */
export const EVENT_STATUS_LABEL = Object.freeze({ confirmed: 'Confirmed by several outlets', reported: 'Reported', disputed: 'Disputed — outlets disagree', unverified: 'Unverified' });
export const EVENT_STATUS_TONE = Object.freeze({ confirmed: 'ok', reported: 'info', disputed: 'danger', unverified: 'muted' });
export const EVENT_STATUS_HINT = Object.freeze({
  confirmed: 'At least three outlets report matching facts and none disagrees. Outlets often repeat one wire story, so this is agreement, not proof.',
  reported: 'Reported by one or two outlets.',
  disputed: 'Outlets give different facts or numbers. Read them side by side.',
  unverified: 'Not enough to say. Treat as a claim.',
});

export const ARTICLE_STATUS_LABEL = Object.freeze({ active: 'Stored', quarantined: 'Held back', expired: 'Expired', removed: 'Removed' });
export const TONE_LABEL = Object.freeze({ negative: 'Worded negatively', positive: 'Worded positively', neutral: 'Worded neutrally', mixed: 'Mixed wording' });
export const TONE_NOTE = 'Tone describes how the text is worded. It does not say whether the text is true or important.';

export const FRESHNESS_LABEL = Object.freeze({ fresh: 'Fresh', aging: 'Getting old', stale: 'Stale', never: 'Never read' });
export const FRESHNESS_TONE = Object.freeze({ fresh: 'ok', aging: 'warn', stale: 'danger', never: 'muted' });

export const SOURCE_STATUS_LABEL = Object.freeze({ active: 'Active', paused: 'Paused', failing: 'Failing', ok: 'Read', error: 'Could not be read', blocked: 'Blocked' });

export const PROVIDER_KIND_LABEL = Object.freeze({ builtin: 'Built-in (no model)', ollama: 'Ollama', openai_compatible: 'OpenAI-compatible server' });
export const PRIVACY_CLASS_LABEL = Object.freeze({ local: 'Stays on this server', private_network: 'Your own network (data leaves this server)', external: 'Outside service (data leaves your network)' });
export const PRIVACY_CLASS_TONE = Object.freeze({ local: 'ok', private_network: 'warn', external: 'danger' });
export const AI_TASK_LABEL = Object.freeze({ plan: 'Planning', extract: 'Extracting evidence', summarize: 'Summaries', classify: 'Classifying', sentiment: 'Tone', entities: 'Names and places', synthesize: 'Writing conclusions', embed: 'Embeddings (search by meaning)' });

/** What kind of source something is, in the server's own words (the server sets `source_label`; these are only used when an older result has none). */
export const SOURCE_CLASS_LABEL = Object.freeze({
  document: 'Your document',
  calculated: 'Calculated from your data',
  model: 'Model estimate (a prediction, not a fact)',
  earlier_research: 'Earlier research in this project',
  wikipedia: 'Wikipedia (encyclopedia: a tertiary source, edited by volunteers)',
  arxiv_preprint: 'arXiv preprint (not peer reviewed)',
  web: 'General web page',
});
export const SOURCE_CLASS_TONE = Object.freeze({ document: 'info', calculated: 'ok', model: 'warn', earlier_research: 'muted', wikipedia: 'muted', arxiv_preprint: 'warn', web: 'muted' });
/** Short names for the groups in the report's "outside sources" list. */
export const SOURCE_CLASS_SHORT = Object.freeze({ wikipedia: 'Wikipedia', arxiv_preprint: 'arXiv preprints', web: 'Web pages', document: 'Your documents', model: 'Models', earlier_research: 'Earlier research', calculated: 'Calculations' });

/** How the report places an outside passage next to a calculated result. It is judged from wording only, and the labels say so. */
export const ALIGNMENT_LABEL = Object.freeze({ similar_wording: 'Worded the same direction as your data', different_wording: 'Worded the other direction from your data' });
export const SECTION_STATUS_LABEL = Object.freeze({ answered: 'Answered', not_found: 'Nothing found' });
export const SECTION_SCOPE_LABEL = Object.freeze({ dataset: 'Your data', general: 'Documents and outside sources' });

/** Result of a search-provider test on the Search tools tab. */
export const DIAGNOSIS_LABEL = Object.freeze({
  ok: 'Working', not_allowed: 'Switched off for this project', not_configured: 'Not set up', unreachable: 'Could not be reached', limited: 'Limiting requests', refused: 'Refused the request',
  bad_answer: 'Answered unexpectedly', json_disabled: 'JSON search is switched off', engines_failed: 'Its search engines failed',
});
export const DIAGNOSIS_TONE = Object.freeze({ ok: 'ok', not_allowed: 'muted', not_configured: 'muted', unreachable: 'danger', limited: 'warn', refused: 'danger', bad_answer: 'warn', json_disabled: 'danger', engines_failed: 'danger' });
