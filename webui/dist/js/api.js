// =============================================================================
// api.js — centralized API client
// =============================================================================
// Every function maps 1:1 to a backend endpoint.
// Functions marked @available have a fully working backend route but are not
// yet wired to a UI element — they can be called freely from any module.
// =============================================================================

const BASE = '';

async function request(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${method} ${path} → ${res.status}: ${text}`);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res.text();
}

const get  = (path)        => request('GET',    path);
const post = (path, body)  => request('POST',   path, body);
const put  = (path, body)  => request('PUT',    path, body);
const patch= (path, body)  => request('PATCH',  path, body);
const del  = (path)        => request('DELETE', path);

function encodePathId(value) {
  return encodeURIComponent(String(value || '').trim().replace(/^\/+/, ''));
}

export const api = {
  // ── Status ──────────────────────────────────────────────────────────────
  status: ()                        => get('/status'),

  // ── Vault ───────────────────────────────────────────────────────────────
  vaultList:    (section='', limit=200) => get(`/vault/list?section=${encodeURIComponent(section)}&limit=${limit}`),
  vaultFile:    (path)              => get(`/vault/file?path=${encodeURIComponent(path)}`),
  vaultSave:    (path, content)     => patch(`/vault/file?path=${encodeURIComponent(path)}`, { content }),
  vaultDelete:  (path)              => del(`/vault/file?path=${encodeURIComponent(path)}`),
  vaultTags:    ()                  => get('/vault/tags'),
  vaultMention:      (q, limit=10)  => get(`/vault/mention?q=${encodeURIComponent(q)}&limit=${limit}`),
  vaultMentionedIn:  (path)         => get(`/vault/mentioned-in?path=${encodeURIComponent(path)}`),
  vaultMailThread: (tid)            => get(`/vault/mail-thread/${encodePathId(tid)}`),
  /** @available GET /vault/contacts — enriched contact list from vault */
  vaultContacts:(limit=100)         => get(`/vault/contacts?limit=${limit}`),
  vaultReload:  ()                  => post('/vault/reload'),
  vaultDiagnostics: ()              => get('/vault/diagnostics'),

  // ── PersonalVault v2 — threaded items ────────────────────────────────────
  /** @available GET /api/v1/vault/items — paginated item list */
  vaultItems:    (section='', limit=500) => get(`/api/v1/vault/items?${section ? `item_type=${encodeURIComponent(section)}&` : ''}limit=${limit}`),
  /** @available GET /api/v1/vault/threads — thread list */
  vaultThreads:  (limit=50)          => get(`/api/v1/vault/threads?limit=${limit}`),
  /** @available GET /api/v1/vault/threads/{tid} */
  vaultThread:   (tid)              => get(`/api/v1/vault/threads/${encodePathId(tid)}`),
  /** @available POST /api/v1/vault/context — build prompt context from vault */
  vaultContext:  (body)             => post('/api/v1/vault/context', body),
  /** @available DELETE /api/v1/vault/threads/{tid} */
  vaultDeleteThread:(tid)           => del(`/api/v1/vault/threads/${encodePathId(tid)}`),

  // ── Settings ────────────────────────────────────────────────────────────
  settingsGet:  ()                  => get('/settings'),
  settingsSave: (body)              => post('/settings', body),

  // ── Rules tab: editable AI-tool settings (config.json, applied immediately)
  /** GET /api/v1/rules/settings — current AI-tool settings + UI schema */
  rulesSettingsGet:  ()             => get('/api/v1/rules/settings'),
  /** PATCH /api/v1/rules/settings — persist a partial update, applied at once */
  rulesSettingsSave: (body)         => patch('/api/v1/rules/settings', body),

  // ── Classify ────────────────────────────────────────────────────────────
  classifyConfig:     ()            => get('/classify/config'),
  classifySave:       (yaml_text)   => put('/classify/config', { yaml_text }),
  /** @available GET /classify/labels — flat list of all classifier labels */
  classifyLabels:     ()            => get('/classify/labels'),
  classifyApply:      ()            => post('/classify/apply'),
  classifyResetTags:  ()            => del('/classify/tags'),
  // Stage 8: LLM-assisted classification
  classifyLLMBatch:   ()            => post('/classify/llm-batch'),
  classifyStats:      ()            => get('/classify/stats'),

  // ── Search ──────────────────────────────────────────────────────────────
  /** @available POST /search — basic keyword search (LLM synthesis) */
  search:       (body)              => post('/search', body),
  /** @available POST /search/hybrid — BM25 + keyword fallback (no synthesis) */
  searchHybrid: (body)              => post('/search/hybrid', body),
  searchDocs:   (body)              => post('/search/docs', body),

  // ── Sync ────────────────────────────────────────────────────────────────
  syncStart:    (body)              => post('/sync', body || {}),
  syncStatus:   ()                  => get('/sync/status'),

  // ── Schedule ────────────────────────────────────────────────────────────
  scheduleStatus: ()                => get('/schedule/status'),

  // ── Index ───────────────────────────────────────────────────────────────
  indexBuild:   ()                  => post('/index/build'),
  /** @available GET /index/status — check if index is built and up-to-date */
  indexStatus:  ()                  => get('/index/status'),

  // ── Projects ────────────────────────────────────────────────────────────
  projectsList:    ()               => get('/projects'),
  projectCreate:   (body)           => post('/projects', body),
  projectUpdate:   (id, body)       => put(`/projects/${id}`, body),
  projectDelete:   (id)             => del(`/projects/${id}`),
  /** @available GET /projects/{id}/goals — get goals list for a project */
  projectGoals:    (id)             => get(`/projects/${id}/goals`),
  projectGoalAdd:  (id, body)       => post(`/projects/${id}/goals`, body),
  /** @available PUT /projects/{id}/goals/{gid} — update a goal */
  projectGoalUpdate:(id, gid, body) => put(`/projects/${id}/goals/${gid}`, body),
  projectGoalDelete:        (id, gid)   => del(`/projects/${id}/goals/${gid}`),
  projectRelated:           (id)        => get(`/projects/${id}/related`),
  projectSuggestGoal:       (id)        => post(`/projects/${id}/suggest-goal`, {}),
  projectAssistantSuggests: (id)        => get(`/projects/${id}/assistant-suggests`),
  projectLinkVault:    (id, vault_path) => post(`/projects/${id}/link-vault`, { vault_path }),
  /** @available DELETE /projects/{id}/link-vault — unlink vault doc from project */
  projectUnlinkVault:  (id, vault_path) => del(`/projects/${id}/link-vault?vault_path=${encodeURIComponent(vault_path)}`),
  projectLinkContact:  (id, body)       => post(`/projects/${id}/link-contact`, body),
  /** @available DELETE /projects/{id}/link-contact — unlink contact from project */
  projectUnlinkContact:(id, email)      => del(`/projects/${id}/link-contact?email=${encodeURIComponent(email)}`),

  // ── Profile & Assistant Config ──────────────────────────────────────────
  profileGet:       ()              => get('/api/v1/profile'),
  profileSave:      (body)          => put('/api/v1/profile', body),
  assistantConfigGet:  ()           => get('/api/v1/assistant-config'),
  assistantConfigSave: (body)       => put('/api/v1/assistant-config', body),
  /** @available GET /persona — legacy persona data (name/role/style fields) */
  personaGet:  ()                   => get('/persona'),
  /** @available PUT /persona — save legacy persona fields */
  personaSave: (body)               => put('/persona', body),

  // ── Chat v2 ─────────────────────────────────────────────────────────────
  chatThreads:   ()                 => get('/api/chat/threads'),
  chatHistory:   (tid)              => get(`/api/chat/history/${encodeURIComponent(tid)}`),
  /** @available POST /api/chat/send — non-streaming chat send (use streamText() for streaming) */
  chatSend:      (body)             => post('/api/chat/send', body),
  chatClear:     (tid)              => post(`/api/chat/clear/${encodeURIComponent(tid)}`),
  chatDelete:    (tid)              => del(`/api/chat/${encodeURIComponent(tid)}`),
  chatDeleteAll: ()                 => del('/api/chat/threads/all'),

  // ── Souls.md ────────────────────────────────────────────────────────────
  soulsGet:  ()                     => get('/souls'),
  soulsSave: (content)              => put('/souls', { content }),

  // ── Tools ───────────────────────────────────────────────────────────────
  toolsList:      ()                   => get('/tools'),
  toolToggle:     (id, enabled)        => put(`/tools/${id}`, { enabled }),

  // ── Tool Prompts ─────────────────────────────────────────────────────────
  toolPromptsGet:  ()                  => get('/tool-prompts'),
  toolPromptsSave: (body)              => post('/tool-prompts', body),

  // ── GTD Rules ───────────────────────────────────────────────────────────
  gtdRulesGet:  ()                  => get('/gtd-rules'),
  gtdRulesSave: (rules)             => put('/gtd-rules', { rules }),

  // ── Eisenhower ──────────────────────────────────────────────────────────
  eisenhowerGet:  ()                => get('/eisenhower'),
  eisenhowerSave: (tasks)           => put('/eisenhower', { tasks }),

  // ── Structured Rules ────────────────────────────────────────────────────
  rulesList:    ()                  => get('/rules'),
  rulesCreate:  (body)              => post('/rules', body),
  rulesUpdate:  (id, body)          => put(`/rules/${id}`, body),
  rulesDelete:  (id)                => del(`/rules/${id}`),
  /** @available POST /rules/classify — test a rule against sample text */
  rulesClassify:(body)              => post('/rules/classify', body),

  // ── Tag History ─────────────────────────────────────────────────────────
  /** @available GET /tag-history — audit trail of all classification changes */
  tagHistoryList:   (params={})     => get('/tag-history?' + new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([,v])=>v!=null))).toString()),
  /** @available POST /tag-history — record a manual tag change */
  tagHistoryRecord: (body)          => post('/tag-history', body),
  /** @available DELETE /tag-history/{id} — delete one history entry */
  tagHistoryDelete: (id)            => del(`/tag-history/${id}`),
  /** @available DELETE /tag-history[?item_id=] — bulk clear history */
  tagHistoryClear:  (item_id)       => del(item_id ? `/tag-history?item_id=${encodeURIComponent(item_id)}` : '/tag-history'),

  // ── Today ───────────────────────────────────────────────────────────────
  today: () => get('/api/v1/today'),

  // ── Daily Brief (Stage 6) ───────────────────────────────────────────────
  /**
   * GET /api/v1/brief/daily — get cached brief (pass refresh=true to regenerate via GET).
   * For a full LLM-regenerated brief use briefGenerate() instead.
   */
  briefDaily:    (refresh = false) => get(`/api/v1/brief/daily${refresh ? '?refresh=true' : ''}`),
  /**
   * POST /api/v1/brief/daily/generate — force LLM re-generation of the daily brief.
   * Used by the "Regenerate" button in the Today panel.
   */
  briefGenerate: ()                => post('/api/v1/brief/daily/generate', {}),

  // ── Inbox ───────────────────────────────────────────────────────────────
  inboxList:          (filter='all', limit=200, offset=0, sortBy='date') => get(`/api/v1/inbox?filter=${encodeURIComponent(filter)}&limit=${limit}&offset=${offset}&sort_by=${encodeURIComponent(sortBy)}`),
  /** @available GET /api/v1/inbox/followup-needed — items needing a reply */
  inboxFollowup:      (thresholdDays=2)         => get(`/api/v1/inbox/followup-needed?threshold_days=${thresholdDays}`),
  /** @available GET /api/v1/inbox/{id} — single inbox item details */
  inboxItem:          (id)                      => get(`/api/v1/inbox/${encodeURIComponent(id)}`),
  inboxSummarize:     (body)                    => post('/api/v1/inbox/summarize', body),
  inboxMarkRead:        (id)                      => post(`/api/v1/inbox/${encodeURIComponent(id)}/read`),
  inboxMarkUnread:      (id)                      => post(`/api/v1/inbox/${encodeURIComponent(id)}/unread`),
  inboxSetTags:         (id, tags, mode='set')    => post(`/api/v1/inbox/${encodeURIComponent(id)}/tags`, { tags, mode }),
  inboxAssignProject:   (id, project_id, project_name) => post(`/api/v1/inbox/${encodeURIComponent(id)}/assign-project`, { project_id, project_name }),
  inboxSuggestions:     (id)                      => get(`/api/v1/inbox/${encodeURIComponent(id)}/suggestions`),
  inboxExtract:         (id, body, force=false)   => post(`/api/v1/inbox/${encodeURIComponent(id)}/extract`, { body: body || null, force }),
  /** @available GET /api/v1/inbox/{id}/extraction — retrieve cached extraction result */
  inboxGetExtraction:   (id)                      => get(`/api/v1/inbox/${encodeURIComponent(id)}/extraction`),
  /** @available DELETE /api/v1/inbox/extraction-cache — clear all extraction caches */
  inboxClearExtrCache:  ()                         => del('/api/v1/inbox/extraction-cache'),
  inboxDraftContext:    (id)                       => post(`/api/v1/inbox/${encodeURIComponent(id)}/draft-context`, {}),
  /** @available GET /api/v1/inbox/thread/{thread_id}/graph — participant graph */
  inboxThreadGraph:     (threadId)                 => get(`/api/v1/inbox/thread/${encodeURIComponent(threadId)}/graph`),

  // ── Calendar / Meeting Prep ──────────────────────────────────────────────
  calendarUpcoming:     (days = 7)                 => get(`/api/v1/calendar/upcoming?days=${days}`),
  calendarPrep:         (eventId)                  => get(`/api/v1/calendar/${encodeURIComponent(eventId)}/prep`),
  calendarParseIntent:  (text, referenceDate)      => post('/api/v1/calendar/parse-intent', { text, reference_date: referenceDate || null }),
  calendarCreateFromText: (text, opts = {})        => post('/api/v1/calendar/create-from-text', {
    text,
    reference_date: opts.referenceDate || null,
    dry_run: opts.dryRun || false,
    confirmed: opts.confirmed || false,
    calendar_name: opts.calendarName || null,
  }),
  /** @available GET /api/v1/calendar/calendars — list all Apple Calendar accounts */
  calendarListCalendars: ()                        => get('/api/v1/calendar/calendars'),

  // ── MLX Model downloader ─────────────────────────────────────────────────
  /**
   * Model management — used by Settings → Model panel.
   * modelCatalogue: lists available HuggingFace models with download size.
   * modelPull: starts async download.
   * modelPullStatus: polls download progress (requires repo param).
   * modelActivate: set active inference model after download.
   * modelDeleteLocal: free disk space by removing a downloaded model.
   */
  modelCatalogue:      ()               => get('/model/catalogue'),
  modelPull:           (repo)           => post('/model/pull', { repo }),
  modelPullStatus:     (repo)           => get(`/model/pull-status?repo=${encodeURIComponent(repo)}`),
  modelActivate:       (repo)           => post('/model/activate', { repo }),
  modelDeleteLocal:    (repo)           => del(`/model/local?repo=${encodeURIComponent(repo)}`),

  // ── Test-data generator ──────────────────────────────────────────────────
  generateTestData:    (body)       => post('/testdata/generate', body),
  getSnapshots:        ()           => get('/testdata/snapshots'),
  rollbackSnapshot:    (snapId)     => post('/testdata/rollback', { snap_id: snapId }),
  deleteSnapshot:      (snapId)     => del(`/testdata/snapshots/${snapId}`),
  deleteGeneratedData: ()           => del('/testdata/generated'),
};

/**
 * streamText — fetch a streaming endpoint and call onChunk for each text chunk.
 * @param {string} path
 * @param {object} body
 * @param {function(string): void} onChunk
 * @returns {Promise<{text: string, threadId: string|null}>}
 */
export async function streamText(path, body, onChunk) {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Stream ${path} → ${res.status}: ${text}`);
  }
  // Capture X-Thread-ID before consuming body (headers arrive with initial response)
  const threadId = res.headers.get('x-thread-id') || res.headers.get('X-Thread-ID') || null;
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let full = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    full += chunk;
    onChunk(chunk);
  }
  return { text: full, threadId };
}
