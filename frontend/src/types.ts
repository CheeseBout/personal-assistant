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

export type LtmType = 'semantic' | 'procedural' | 'episodic'

export interface LtmItem {
  id: string
  type: LtmType
  content: string
  source: string | null
  confidence: number | null
  tags: string[]
  enabled: boolean
  created_at: string | null
  updated_at: string | null
  last_used_at: string | null
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
  desktop_control_enabled?: boolean
}

export interface BrowserActionItem {
  id: string
  action: string
  target: string | null
  status: string
  timestamp: string | null
}

export interface BrowserState {
  session_id: string
  current_url: string | null
  title: string | null
  is_active: boolean
  screenshot: string | null // base64 PNG
  actions: BrowserActionItem[]
}

export interface GoogleStatus {
  connected: boolean
  email: string | null
  error?: string
}

export interface GoogleActionItem {
  id: string
  service: string
  action: string
  target: string | null
  status: string
  timestamp: string | null
}

export interface SandboxArtifact {
  name: string
  size: number
}

export interface SandboxRun {
  id: string
  kind: string // python | shell | install
  mode: string // A | B | C | D | E
  code: string // code or command (redacted/truncated)
  status: string // success | error | killed | denied
  exit_code: number | null
  killed_reason: string | null
  stdout: string
  stderr: string
  artifacts: SandboxArtifact[]
  duration_ms: number | null
  timestamp: string | null
}

export interface SandboxArtifactContent {
  name: string
  content: string
  size: number
}

export interface NewsSource {
  title: string
  url: string
  snippet: string
  published: string | null
}

export interface NewsReport {
  id: string
  task_id: string | null
  query: string
  summary: string
  sources: NewsSource[]
  created_at: string | null
}

export interface ScheduledTask {
  id: string
  name: string
  kind: string
  schedule: string
  params: Record<string, unknown>
  enabled: boolean
  last_run_at: string | null
  last_status: string | null
  created_at: string | null
}

export interface UiElement {
  type: string
  name: string
  auto_id: string
  rect: { left: number; top: number; right: number; bottom: number } | null
  enabled: boolean
}

export interface DesktopWindow {
  title: string
  visible: boolean
  minimized: boolean
  maximized: boolean
  active: boolean
}

export interface DesktopObservation {
  id: string
  active_window: string | null
  ocr_text: string | null
  summary: string | null
  ui_elements: UiElement[] | null
  masked: boolean
  created_at: string | null
}

export interface DesktopObserveResult {
  status: string
  active_window: string | null
  ocr_text: string | null
  ui_elements: UiElement[] | null
  summary: string | null
  masked: boolean
  error: string | null
  id?: string
}

// Chat session metadata (sidebar list)
export interface ChatSessionItem {
  id: string
  title: string | null
  created_at: string | null
  updated_at: string | null
  message_count: number
  last_message_at: string | null
}

// RAG settings editable from the Settings panel
export interface RagSettings {
  threshold: number
  min_results: number
  max_results: number
  use_rerank: boolean
  citation_coverage_min: number
  hybrid_candidates: number
  rerank_threshold: number
}

// SSE events from POST /api/chat/stream
export type ChatStreamEvent =
  | { type: 'retrieval'; sources: Citation[] }
  | { type: 'delta'; content: string }
  | { type: 'verdict'; accepted: boolean; refusal?: boolean; grounding?: number; coverage?: unknown }
  | { type: 'done'; session_id: string }
  | { type: 'error'; message: string }
