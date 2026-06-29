import { memo, useEffect, useRef, useState, useCallback } from 'react'
import { api } from '../api'
import type { AgentResponse, ChatMessage, ChatStreamEvent, Citation } from '../types'
import { riskBadge } from './util'
import { Markdown } from '../components/Markdown'

interface Props {
  sessionId: string
  onApprovalChange: () => void
  onSessionsChange?: () => void
  showToast: (msg: string) => void
}

// A pending intent confirmation surfaced inline in the chat stream.
interface IntentPrompt {
  message: string
  suggested_route?: string
  intent?: string
  confidence?: number
}

// A pending tool approval surfaced inline in the chat stream.
interface ApprovalPrompt {
  approval_id: string
  tool: string
  reason: string
  risk_level: number
  tool_calls?: AgentResponse['tool_calls']
}

let idSeq = 0
const localId = () => `local-${Date.now()}-${idSeq++}`

const SCROLL_NEAR_BOTTOM_PX = 100

export function ChatPanel({ sessionId, onApprovalChange, onSessionsChange, showToast }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [streaming, setStreaming] = useState<string>('') // currently-streaming assistant text
  const [streamingCitations, setStreamingCitations] = useState<Citation[]>([])
  const [streamVerdict, setStreamVerdict] = useState<null | { accepted: boolean; grounding?: number }>(null)
  const [intentPrompt, setIntentPrompt] = useState<IntentPrompt | null>(null)
  const [approvalPrompt, setApprovalPrompt] = useState<ApprovalPrompt | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  // Track latest messages for callbacks that would otherwise capture stale state.
  const messagesRef = useRef<ChatMessage[]>([])
  messagesRef.current = messages
  // Whether the user is currently parked near the bottom — only auto-scroll then.
  const stickToBottomRef = useRef(true)

  // Load history when session changes.
  useEffect(() => {
    let cancelled = false
    setIntentPrompt(null)
    setApprovalPrompt(null)
    setStreaming('')
    setStreamingCitations([])
    setStreamVerdict(null)
    api
      .history(sessionId)
      .then((h) => {
        if (cancelled) return
        setMessages(
          h.map((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            citations: m.citations,
            timestamp: m.timestamp,
          })),
        )
      })
      .catch(() => {
        if (!cancelled) setMessages([])
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  // Track whether the user is near the bottom so we don't fight their scroll.
  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    stickToBottomRef.current = distance < SCROLL_NEAR_BOTTOM_PX
  }, [])

  // Autoscroll to bottom on new content, but only if user is already there.
  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottomRef.current) el.scrollTop = el.scrollHeight
  }, [messages, intentPrompt, approvalPrompt, busy, streaming])

  const autoGrow = () => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 180) + 'px'
  }

  const push = (m: Omit<ChatMessage, 'id'>) =>
    setMessages((prev) => [...prev, { ...m, id: localId() }])

  // Translate an AgentResponse (non-streaming /api/agent path) into chat state.
  const handleAgentResponse = (resp: AgentResponse) => {
    if (resp.status === 'intent_confirmation') {
      setIntentPrompt({
        message: resp.response,
        suggested_route: resp.suggested_route,
        intent: resp.intent,
        confidence: resp.confidence,
      })
      return
    }
    if (resp.status === 'pending_approval' && resp.approval_id) {
      const last = (resp.tool_calls || []).slice(-1)[0]
      setApprovalPrompt({
        approval_id: resp.approval_id,
        tool: last?.tool || 'unknown',
        reason: resp.response || 'Cần xác nhận hành động',
        risk_level: 2,
        tool_calls: resp.tool_calls,
      })
      onApprovalChange()
      return
    }
    if (resp.status === 'error') {
      push({ role: 'assistant', content: resp.response, kind: 'error' })
      return
    }
    push({ role: 'assistant', content: resp.response, citations: resp.citations })
  }

  const sendNonStreaming = async (text: string, opts?: { intent_confirmed?: boolean; suggested_route?: string }) => {
    setBusy(true)
    try {
      const resp = await api.agent({
        message: text,
        session_id: sessionId,
        intent_confirmed: opts?.intent_confirmed,
        suggested_route: opts?.suggested_route,
      })
      handleAgentResponse(resp)
    } catch (e) {
      push({ role: 'assistant', content: `Lỗi: ${(e as Error).message}`, kind: 'error' })
    } finally {
      setBusy(false)
    }
  }

  // Streaming send via SSE. Falls back to non-streaming on failure.
  const sendStreaming = async (text: string) => {
    setBusy(true)
    setStreaming('')
    setStreamingCitations([])
    setStreamVerdict(null)
    let buffer = ''
    let citations: Citation[] = []
    let verdict: { accepted: boolean; grounding?: number } | null = null
    let gotAnyDelta = false
    try {
      await api.chatStream({ message: text, session_id: sessionId }, (e: ChatStreamEvent) => {
        if (e.type === 'retrieval') {
          citations = e.sources || []
          setStreamingCitations(citations)
        } else if (e.type === 'delta') {
          gotAnyDelta = true
          buffer += e.content
          setStreaming(buffer)
        } else if (e.type === 'verdict') {
          verdict = { accepted: e.accepted, grounding: e.grounding }
          setStreamVerdict(verdict)
        } else if (e.type === 'error') {
          throw new Error(e.message || 'Streaming error')
        }
        // 'done' just ends the loop; we flush below.
      })
      // Flush streaming buffer into the message list once.
      if (gotAnyDelta) {
        const v = verdict as { accepted: boolean; grounding?: number } | null
        const finalCitations = v && !v.accepted ? [] : citations
        setMessages((prev) => [
          ...prev,
          {
            id: localId(),
            role: 'assistant',
            content: buffer,
            citations: finalCitations,
          },
        ])
      }
      // Refresh sidebar sessions list (might be new title)
      onSessionsChange?.()
    } catch (e) {
      // If we got partial content, keep it; otherwise show the error
      if (buffer.trim()) {
        setMessages((prev) => [
          ...prev,
          { id: localId(), role: 'assistant', content: buffer + `\n\n[Lỗi: ${(e as Error).message}]`, kind: 'error' },
        ])
      } else {
        push({ role: 'assistant', content: `Lỗi: ${(e as Error).message}`, kind: 'error' })
      }
    } finally {
      setStreaming('')
      setStreamingCitations([])
      setStreamVerdict(null)
      setBusy(false)
    }
  }

  const onSubmit = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    setIntentPrompt(null)
    setApprovalPrompt(null)
    push({ role: 'user', content: text })
    // Force scroll to bottom on user submit (they expect to see their message)
    stickToBottomRef.current = true
    // Stream by default. Non-streaming path is used for intent confirm + approvals.
    await sendStreaming(text)
  }

  const confirmIntent = async (proceed: boolean) => {
    const prompt = intentPrompt
    setIntentPrompt(null)
    if (!proceed) {
      push({ role: 'assistant', content: 'Đã huỷ. Bạn có thể nhập lại yêu cầu.', kind: 'info' })
      return
    }
    // Re-send the last user message, now confirmed (use ref to avoid stale closure).
    const lastUser = [...messagesRef.current].reverse().find((m) => m.role === 'user')
    if (!lastUser) return
    await sendNonStreaming(lastUser.content, {
      intent_confirmed: true,
      suggested_route: prompt?.suggested_route,
    })
  }

  const decideApproval = async (approve: boolean) => {
    const prompt = approvalPrompt
    if (!prompt) return
    setApprovalPrompt(null)
    setBusy(true)
    try {
      await api.decideApproval(prompt.approval_id, approve ? 'approve' : 'deny')
      const resp = await api.continueAfterApproval({
        session_id: sessionId,
        approval_id: prompt.approval_id,
        approved: approve,
      })
      onApprovalChange()
      handleAgentResponse(resp)
      showToast(approve ? 'Đã duyệt hành động' : 'Đã từ chối hành động')
    } catch (e) {
      push({ role: 'assistant', content: `Lỗi xử lý phê duyệt: ${(e as Error).message}`, kind: 'error' })
    } finally {
      setBusy(false)
    }
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSubmit()
    }
  }

  const empty = messages.length === 0 && !intentPrompt && !approvalPrompt && !busy && !streaming

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="chat-inner">
          {empty && (
            <div className="empty">
              Bắt đầu trò chuyện. Hỏi về tài liệu đã tải lên hoặc yêu cầu thao tác file trong workspace.
            </div>
          )}

          {messages.map((m) => (
            <MessageBubble key={m.id} msg={m} />
          ))}

          {/* Live streaming bubble */}
          {streaming && (
            <div className="msg-row">
              <div className="avatar assistant">PA</div>
              <div className="bubble assistant">
                <Markdown content={streaming} />
                {streamingCitations.length > 0 && (
                  <div className="citations">
                    {streamingCitations.map((c, i) => (
                      <span className="citation" key={i}>
                        {String(c.filename || c.file || 'nguồn')}
                        {c.chunk != null ? ` · ${c.chunk}` : ''}
                      </span>
                    ))}
                  </div>
                )}
                {streamVerdict && !streamVerdict.accepted && (
                  <div className="verdict-warn">
                    Cảnh báo: câu trả lời có thể không đủ bằng chứng từ tài liệu
                    {typeof streamVerdict.grounding === 'number'
                      ? ` (độ bám: ${(streamVerdict.grounding * 100).toFixed(0)}%)`
                      : ''}
                    .
                  </div>
                )}
              </div>
            </div>
          )}

          {intentPrompt && (
            <div className="msg-row">
              <div className="avatar assistant">PA</div>
              <div className="inline-card">
                <h4>Xác nhận ý định</h4>
                <div className="muted">{intentPrompt.message}</div>
                {typeof intentPrompt.confidence === 'number' && (
                  <div className="kv">
                    Ý định: <code>{intentPrompt.intent}</code> · độ tin cậy{' '}
                    {(intentPrompt.confidence * 100).toFixed(0)}%
                  </div>
                )}
                <div className="row">
                  <button className="btn btn-primary btn-sm" onClick={() => confirmIntent(true)}>
                    Tiếp tục
                  </button>
                  <button className="btn btn-sm" onClick={() => confirmIntent(false)}>
                    Huỷ
                  </button>
                </div>
              </div>
            </div>
          )}

          {approvalPrompt && (
            <div className="msg-row">
              <div className="avatar assistant">PA</div>
              <div className="inline-card">
                <h4>
                  Yêu cầu phê duyệt{' '}
                  <span className={riskBadge(approvalPrompt.risk_level).cls}>
                    {riskBadge(approvalPrompt.risk_level).label}
                  </span>
                </h4>
                <div className="kv">
                  Công cụ: <code>{approvalPrompt.tool}</code>
                </div>
                <div className="muted">{approvalPrompt.reason}</div>
                {approvalPrompt.tool_calls && approvalPrompt.tool_calls.length > 0 && (
                  <pre className="json">
                    {JSON.stringify(
                      approvalPrompt.tool_calls[approvalPrompt.tool_calls.length - 1]?.arguments,
                      null,
                      2,
                    )}
                  </pre>
                )}
                <div className="row">
                  <button
                    className="btn btn-primary btn-sm"
                    disabled={busy}
                    onClick={() => decideApproval(true)}
                  >
                    Duyệt
                  </button>
                  <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => decideApproval(false)}>
                    Từ chối
                  </button>
                </div>
              </div>
            </div>
          )}

          {busy && !streaming && (
            <div className="msg-row">
              <div className="avatar assistant">PA</div>
              <div className="bubble assistant">
                <div className="typing">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="composer">
        <div className="composer-inner">
          <textarea
            ref={taRef}
            value={input}
            placeholder="Nhập tin nhắn… (Enter để gửi, Shift+Enter xuống dòng)"
            rows={1}
            disabled={busy || !!intentPrompt || !!approvalPrompt}
            onChange={(e) => {
              setInput(e.target.value)
              autoGrow()
            }}
            onKeyDown={onKeyDown}
          />
          <button
            className="send-btn"
            disabled={busy || !input.trim() || !!intentPrompt || !!approvalPrompt}
            onClick={onSubmit}
            title="Gửi"
          >
            ➤
          </button>
        </div>
      </div>
    </div>
  )
}

const MessageBubble = memo(function MessageBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === 'system') return null
  const isUser = msg.role === 'user'
  const initial = isUser ? '👤' : 'PA'
  return (
    <div className={`msg-row${isUser ? ' user' : ''}`}>
      <div className={`avatar ${isUser ? 'user' : 'assistant'}`}>{initial}</div>
      <div className={`bubble ${isUser ? 'user' : msg.kind === 'error' ? 'error' : 'assistant'}`}>
        {isUser ? <span className="user-text">{msg.content}</span> : <Markdown content={msg.content} />}
        {msg.citations && msg.citations.length > 0 && (
          <div className="citations">
            {msg.citations.map((c, i) => (
              <span className="citation" key={i}>
                {String(c.filename || c.file || 'nguồn')}
                {c.chunk != null ? ` · ${c.chunk}` : ''}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
})
