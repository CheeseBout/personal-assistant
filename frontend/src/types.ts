// Shared types mirroring the backend API responses.

export type Role = 'user' | 'assistant' | 'system'

export interface Citation {
  // Backend returns retrieval "sources"; shape is loose, so keep it permissive.
  filename?: string
  file?: string
  chunk?: number | string
  page?: number | string
  version?: number | string
  score?: number
  [key: string]: unknown
}

export interface ChatMessage {
  id: string
  role: Role
  content: string
  citations?: Citation[]
  timestamp?: string | null
  // UI-only flags
  pending?: boolean
  kind?: 'normal' | 'intent_confirmation' | 'approval' | 'error' | 'info'
}

export interface ToolCallRecord {
  tool: string
  arguments: Record<string, unknown>
  status: string
  result?: unknown
}

export interface AgentResponse {
  status:
    | 'completed'
    | 'pending_approval'
    | 'error'
    | 'intent_confirmation'
    | string
  response: string
  session_id: string
  citations?: Citation[]
  approval_id?: string
  iterations?: number
  tool_calls?: ToolCallRecord[]
  // intent_confirmation extras
  intent?: string
  confidence?: number
  suggested_route?: string
}

export interface PendingApproval {
  id: string
  tool_name: string
  arguments: Record<string, unknown>
  reason: string
  risk_level: number
  requested_at: string | null
}

export interface DocumentItem {
  id: string
  filename: string
  uploaded_at: string | null
  file_size: number
  current_version: number
}

export interface EventItem {
  id: string
  session_id: string
  actor: string
  action: string
  details: Record<string, unknown>
  timestamp: string | null
}

export interface AuditItem {
  id: string
  session_id: string | null
  actor: string
  action: string
  details: Record<string, unknown>
  timestamp: string | null
}

export interface MemoryView {
  entries: Record<string, unknown>
  history: Array<{
    id: string
    key: string
    operation: string
    existed_before: boolean
    created_at: string | null
  }>
}

export interface ToolInfo {
  name: string
  description: string
  risk_level: number
  requires_approval: boolean
  rollback_supported: boolean
  enabled: boolean
}

export interface AgentSettings {
  provider: string
  base_url: string
  model: string
  default_model: string
  api_key_configured: boolean
  embedding_model: string
  use_local_embeddings: boolean
  rag_threshold: number
  use_rerank: boolean
  citation_coverage_min: number
}
