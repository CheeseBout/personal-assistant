// Thin API client. All requests go through the Vite proxy at /api.
import type {
  AgentResponse,
  AgentSettings,
  AuditItem,
  BrowserState,
  ChatMessage,
  ChatSessionItem,
  ChatStreamEvent,
  DesktopObservation,
  DesktopObserveResult,
  DesktopWindow,
  DocumentItem,
  DocumentVersionsResult,
  EventItem,
  GoogleActionItem,
  GoogleStatus,
  LtmItem,
  LtmType,
  MemoryView,
  NewsReport,
  PendingApproval,
  RagSettings,
  SandboxArtifactContent,
  SandboxRun,
  ScheduledTask,
  ToolInfo,
} from './types'

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // ignore JSON parse error
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  // --- Agent / chat ---
  agent(body: {
    message: string
    session_id: string
    intent_confirmed?: boolean
    suggested_route?: string
  }): Promise<AgentResponse> {
    return req('/api/agent', { method: 'POST', body: JSON.stringify(body) })
  },

  continueAfterApproval(body: {
    session_id: string
    approval_id: string
    approved: boolean
  }): Promise<AgentResponse> {
    return req('/api/agent/continue', { method: 'POST', body: JSON.stringify(body) })
  },

  history(sessionId: string): Promise<ChatMessage[]> {
    return req(`/api/chat/history/${encodeURIComponent(sessionId)}`)
  },

  clearHistory(sessionId: string): Promise<{ success: boolean }> {
    return req(`/api/chat/history/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
  },

  // --- Chat sessions (sidebar list) ---
  sessions(limit = 20): Promise<ChatSessionItem[]> {
    return req(`/api/chat/sessions?limit=${limit}`)
  },

  renameSession(id: string, title: string): Promise<{ success: boolean; id: string; title: string }> {
    return req(`/api/chat/sessions/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    })
  },

  deleteSession(id: string): Promise<{ success: boolean; id: string }> {
    return req(`/api/chat/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },

  // --- Streaming chat (SSE). Calls onEvent for each parsed event. Returns
  //     when the stream ends or aborts. Throws on HTTP error.
  async chatStream(
    body: { message: string; session_id: string; doc_ids?: string[] },
    onEvent: (e: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    })
    if (!res.ok || !res.body) {
      let detail = `${res.status} ${res.statusText}`
      try {
        const j = await res.json()
        if (j?.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
      } catch {
        /* ignore */
      }
      throw new Error(detail)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const rawEvent = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        // Each SSE event is one or more "data: <line>" lines
        const lines = rawEvent.split('\n').filter((l) => l.startsWith('data:'))
        if (!lines.length) continue
        const payload = lines.map((l) => l.slice(5).trimStart()).join('\n')
        try {
          onEvent(JSON.parse(payload) as ChatStreamEvent)
        } catch {
          // ignore malformed event
        }
      }
    }
  },

  // --- Approvals (HITL) ---
  approvals(sessionId: string): Promise<PendingApproval[]> {
    return req(`/api/approvals?session_id=${encodeURIComponent(sessionId)}`)
  },

  decideApproval(approvalId: string, decision: 'approve' | 'deny'): Promise<{ success: boolean }> {
    return req(`/api/approvals/${encodeURIComponent(approvalId)}/decide`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    })
  },

  // --- Documents / RAG ---
  documents(): Promise<DocumentItem[]> {
    return req('/api/documents')
  },

  async upload(file: File): Promise<Record<string, unknown>> {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/upload', { method: 'POST', body: form })
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`
      try {
        const body = await res.json()
        if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch {
        // ignore parse error
      }
      throw new Error(detail)
    }
    return res.json()
  },

  deleteDocument(docId: string): Promise<Record<string, unknown>> {
    return req(`/api/documents/${encodeURIComponent(docId)}`, { method: 'DELETE' })
  },

  reindexDocument(docId: string): Promise<Record<string, unknown>> {
    return req(`/api/documents/${encodeURIComponent(docId)}/reindex`, { method: 'POST' })
  },

  documentVersions(docId: string): Promise<DocumentVersionsResult> {
    return req(`/api/documents/${encodeURIComponent(docId)}/versions`)
  },

  // --- Observability ---
  events(sessionId: string, limit = 50): Promise<EventItem[]> {
    return req(`/api/events?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`)
  },

  audit(sessionId?: string, limit = 100): Promise<AuditItem[]> {
    const q = sessionId ? `session_id=${encodeURIComponent(sessionId)}&limit=${limit}` : `limit=${limit}`
    return req(`/api/audit?${q}`)
  },

  memory(sessionId: string): Promise<MemoryView> {
    return req(`/api/memory?session_id=${encodeURIComponent(sessionId)}`)
  },

  deleteMemory(sessionId: string, key: string): Promise<{ success: boolean }> {
    return req(`/api/memory?session_id=${encodeURIComponent(sessionId)}&key=${encodeURIComponent(key)}`, {
      method: 'DELETE',
    })
  },

  undoMemory(sessionId: string, key?: string): Promise<Record<string, unknown>> {
    return req('/api/memory/undo', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, key }),
    })
  },

  // --- Long-term memory (Phase 6): cross-session ---
  ltmList(opts?: { type?: LtmType; q?: string; includeDisabled?: boolean }): Promise<{ items: LtmItem[]; count: number }> {
    const p = new URLSearchParams()
    if (opts?.type) p.set('type', opts.type)
    if (opts?.q) p.set('q', opts.q)
    if (opts?.includeDisabled !== undefined) p.set('include_disabled', String(opts.includeDisabled))
    const qs = p.toString()
    return req(`/api/ltm${qs ? `?${qs}` : ''}`)
  },

  ltmCreate(body: { content: string; type?: LtmType; tags?: string[] }): Promise<Record<string, unknown>> {
    return req('/api/ltm', { method: 'POST', body: JSON.stringify(body) })
  },

  ltmUpdate(
    id: string,
    body: { content?: string; type?: LtmType; tags?: string[]; enabled?: boolean },
  ): Promise<Record<string, unknown>> {
    return req(`/api/ltm/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) })
  },

  ltmDelete(id: string): Promise<Record<string, unknown>> {
    return req(`/api/ltm/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },

  ltmExport(): Promise<{ items: LtmItem[]; count: number; exported_at: string }> {
    return req('/api/ltm/export')
  },

  tools(): Promise<ToolInfo[]> {
    return req('/api/tools')
  },

  // --- Browser automation ---
  browserState(sessionId: string, limit = 30): Promise<BrowserState> {
    return req(`/api/browser/state?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`)
  },

  browserClose(sessionId: string): Promise<Record<string, unknown>> {
    return req('/api/browser/close', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    })
  },

  // --- Google integrations (Gmail) ---
  googleStatus(): Promise<GoogleStatus> {
    return req('/api/google/status')
  },

  googleConnect(): Promise<GoogleStatus> {
    return req('/api/google/connect', { method: 'POST' })
  },

  googleDisconnect(): Promise<GoogleStatus> {
    return req('/api/google/disconnect', { method: 'POST' })
  },

  googleActions(sessionId: string, limit = 30): Promise<{ session_id: string; actions: GoogleActionItem[] }> {
    return req(`/api/google/actions?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`)
  },

  // --- Sandbox (Phase 7) ---
  sandboxRuns(sessionId: string, limit = 30): Promise<{ session_id: string; runs: SandboxRun[] }> {
    return req(`/api/sandbox/runs?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`)
  },

  sandboxArtifact(sessionId: string, name: string): Promise<SandboxArtifactContent> {
    return req(`/api/sandbox/artifact?session_id=${encodeURIComponent(sessionId)}&name=${encodeURIComponent(name)}`)
  },

  settings(): Promise<AgentSettings> {
    return req('/api/settings')
  },

  ragSettings(): Promise<RagSettings> {
    return req('/api/rag/settings')
  },

  updateRagSettings(patch: Partial<RagSettings>): Promise<RagSettings> {
    return req('/api/rag/settings', { method: 'PATCH', body: JSON.stringify(patch) })
  },

  // --- News + Scheduler (Phase 8) ---
  newsSummarize(body: { query: string; max_sources?: number }): Promise<{ status: string; report?: NewsReport; error?: string }> {
    return req('/api/news/summarize', { method: 'POST', body: JSON.stringify(body) })
  },

  newsReports(limit = 30): Promise<{ items: NewsReport[]; count: number }> {
    return req(`/api/news/reports?limit=${limit}`)
  },

  schedulerTasks(): Promise<{ items: ScheduledTask[]; count: number }> {
    return req('/api/scheduler/tasks')
  },

  createSchedulerTask(body: {
    name: string
    schedule: string
    params?: Record<string, unknown>
    kind?: string
    enabled?: boolean
  }): Promise<Record<string, unknown>> {
    return req('/api/scheduler/tasks', { method: 'POST', body: JSON.stringify(body) })
  },

  updateSchedulerTask(id: string, enabled: boolean): Promise<Record<string, unknown>> {
    return req(`/api/scheduler/tasks/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    })
  },

  runSchedulerTask(id: string): Promise<Record<string, unknown>> {
    return req(`/api/scheduler/tasks/${encodeURIComponent(id)}/run`, { method: 'POST' })
  },

  deleteSchedulerTask(id: string): Promise<Record<string, unknown>> {
    return req(`/api/scheduler/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },

  // --- Desktop perception (Phase 9): read-only ---
  desktopObserve(sessionId: string, includeSummary = true): Promise<DesktopObserveResult> {
    return req('/api/desktop/observe', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, include_summary: includeSummary }),
    })
  },

  desktopObservations(sessionId: string, limit = 30): Promise<{ items: DesktopObservation[]; count: number }> {
    return req(`/api/desktop/observations?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`)
  },

  desktopWindows(): Promise<{ windows: DesktopWindow[]; count: number }> {
    return req('/api/desktop/windows')
  },
}
