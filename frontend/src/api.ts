// Thin API client. All requests go through the Vite proxy at /api.
import type {
  AgentResponse,
  AgentSettings,
  AuditItem,
  ChatMessage,
  DocumentItem,
  EventItem,
  MemoryView,
  PendingApproval,
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
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  },

  deleteDocument(docId: string): Promise<Record<string, unknown>> {
    return req(`/api/documents/${encodeURIComponent(docId)}`, { method: 'DELETE' })
  },

  reindexDocument(docId: string): Promise<Record<string, unknown>> {
    return req(`/api/documents/${encodeURIComponent(docId)}/reindex`, { method: 'POST' })
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

  tools(): Promise<ToolInfo[]> {
    return req('/api/tools')
  },

  settings(): Promise<AgentSettings> {
    return req('/api/settings')
  },
}
