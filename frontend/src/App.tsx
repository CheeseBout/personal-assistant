import { useCallback, useEffect, useState } from 'react'
import './index.css'
import { api } from './api'
import { ChatPanel } from './panels/ChatPanel'
import { DocumentsPanel } from './panels/DocumentsPanel'
import { TimelinePanel } from './panels/TimelinePanel'
import { MemoryPanel } from './panels/MemoryPanel'
import { AuditPanel } from './panels/AuditPanel'
import { ToolsPanel } from './panels/ToolsPanel'
import { BrowserPanel } from './panels/BrowserPanel'
import { GooglePanel } from './panels/GooglePanel'
import { SandboxPanel } from './panels/SandboxPanel'
import { NewsPanel } from './panels/NewsPanel'
import { DesktopPanel } from './panels/DesktopPanel'
import { SettingsPanel } from './panels/SettingsPanel'
import { ErrorBoundary } from './components/ErrorBoundary'
import type { ChatSessionItem } from './types'

type View = 'chat' | 'documents' | 'timeline' | 'memory' | 'audit' | 'tools' | 'browser' | 'google' | 'sandbox' | 'news' | 'desktop' | 'settings'

const NAV: { id: View; label: string; icon: string }[] = [
  { id: 'chat', label: 'Trò chuyện', icon: '💬' },
  { id: 'documents', label: 'Tài liệu', icon: '📄' },
  { id: 'timeline', label: 'Hoạt động', icon: '⚡' },
  { id: 'memory', label: 'Bộ nhớ', icon: '🧠' },
  { id: 'audit', label: 'Nhật ký', icon: '📋' },
  { id: 'tools', label: 'Công cụ', icon: '🛠️' },
  { id: 'browser', label: 'Trình duyệt', icon: '🌐' },
  { id: 'google', label: 'Google', icon: '✉️' },
  { id: 'sandbox', label: 'Sandbox', icon: '🧪' },
  { id: 'news', label: 'Tin tức', icon: '📰' },
  { id: 'desktop', label: 'Màn hình', icon: '🖥️' },
  { id: 'settings', label: 'Cài đặt', icon: '⚙️' },
]

function newSessionId(): string {
  return 'sess-' + Math.random().toString(36).slice(2, 10)
}

function useTheme(): [string, () => void] {
  const [theme, setTheme] = useState<string>(() => localStorage.getItem('theme') || 'light')
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])
  const toggle = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [])
  return [theme, toggle]
}

export default function App() {
  const [view, setView] = useState<View>('chat')
  const [theme, toggleTheme] = useTheme()
  const [sessionId, setSessionId] = useState<string>(() => {
    return localStorage.getItem('session_id') || newSessionId()
  })
  const [pendingCount, setPendingCount] = useState(0)
  const [toast, setToast] = useState<string | null>(null)
  const [sessions, setSessions] = useState<ChatSessionItem[]>([])
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')

  useEffect(() => {
    localStorage.setItem('session_id', sessionId)
  }, [sessionId])

  const showToast = useCallback((msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 2600)
  }, [])

  const refreshPending = useCallback(async () => {
    try {
      const list = await api.approvals(sessionId)
      setPendingCount(list.length)
    } catch {
      setPendingCount(0)
    }
  }, [sessionId])

  const refreshSessions = useCallback(async () => {
    try {
      const list = await api.sessions(15)
      setSessions(list)
    } catch {
      setSessions([])
    }
  }, [])

  useEffect(() => {
    refreshPending()
    refreshSessions()
    const t = window.setInterval(refreshPending, 5000)
    return () => window.clearInterval(t)
  }, [refreshPending, refreshSessions])

  const startNewSession = () => {
    const id = newSessionId()
    setSessionId(id)
    setView('chat')
    showToast('Đã tạo phiên mới')
  }

  const switchSession = (id: string) => {
    setSessionId(id)
    setView('chat')
  }

  const startRename = (s: ChatSessionItem, e: React.MouseEvent) => {
    e.stopPropagation()
    setRenamingId(s.id)
    setRenameDraft(s.title || '')
  }

  const commitRename = async (id: string) => {
    const newTitle = renameDraft.trim()
    setRenamingId(null)
    if (!newTitle) return
    try {
      await api.renameSession(id, newTitle)
      await refreshSessions()
    } catch (e) {
      showToast(`Đổi tên thất bại: ${(e as Error).message}`)
    }
  }

  const removeSession = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('Xoá phiên chat này?')) return
    try {
      await api.deleteSession(id)
      await refreshSessions()
      if (id === sessionId) {
        startNewSession()
      }
    } catch (e2) {
      showToast(`Xoá thất bại: ${(e2 as Error).message}`)
    }
  }

  const title = NAV.find((n) => n.id === view)?.label || ''

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-dot" />
          <span>PA Agent</span>
        </div>

        <button className="nav-item" onClick={startNewSession}>
          <span className="nav-icon">➕</span>
          <span>Phiên mới</span>
        </button>

        {sessions.length > 0 && (
          <div className="sessions-section">
            <div className="sessions-label">Phiên gần đây</div>
            {sessions.map((s) => (
              <div
                key={s.id}
                className={`session-item${s.id === sessionId ? ' active' : ''}`}
                onClick={() => switchSession(s.id)}
                title={s.title || s.id}
              >
                {renamingId === s.id ? (
                  <input
                    autoFocus
                    className="session-title"
                    value={renameDraft}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onBlur={() => commitRename(s.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename(s.id)
                      if (e.key === 'Escape') setRenamingId(null)
                    }}
                    onClick={(e) => e.stopPropagation()}
                    style={{ width: '100%', background: 'transparent', border: 'none', color: 'inherit', outline: 'none' }}
                  />
                ) : (
                  <span className="session-title">{s.title || '(không tên)'}</span>
                )}
                <span className="session-actions">
                  <button
                    className="session-action-btn"
                    title="Đổi tên"
                    onClick={(e) => startRename(s, e)}
                  >
                    ✎
                  </button>
                  <button
                    className="session-action-btn"
                    title="Xoá"
                    onClick={(e) => removeSession(s.id, e)}
                  >
                    🗑
                  </button>
                </span>
              </div>
            ))}
          </div>
        )}

        <div style={{ height: 8 }} />

        {NAV.map((n) => (
          <button
            key={n.id}
            className={`nav-item${view === n.id ? ' active' : ''}`}
            onClick={() => setView(n.id)}
          >
            <span className="nav-icon">{n.icon}</span>
            <span>{n.label}</span>
            {n.id === 'timeline' && pendingCount > 0 && (
              <span className="nav-badge">{pendingCount}</span>
            )}
          </button>
        ))}

        <div className="sidebar-spacer" />
        <div className="sidebar-footer">
          <div className="session-chip">{sessionId}</div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>{title}</h1>
          {view === 'chat' && pendingCount > 0 && (
            <span className="badge risk-2">{pendingCount} chờ duyệt</span>
          )}
          <div className="topbar-spacer" />
          <button className="icon-btn" title="Đổi giao diện" onClick={toggleTheme}>
            {theme === 'dark' ? '☀️' : '🌙'}
          </button>
        </header>

        <ErrorBoundary label={view}>
          {view === 'chat' && (
            <ChatPanel
              sessionId={sessionId}
              onApprovalChange={refreshPending}
              onSessionsChange={refreshSessions}
              showToast={showToast}
            />
          )}
          {view === 'documents' && <DocumentsPanel showToast={showToast} />}
          {view === 'timeline' && (
            <TimelinePanel sessionId={sessionId} onApprovalChange={refreshPending} showToast={showToast} />
          )}
          {view === 'memory' && <MemoryPanel sessionId={sessionId} showToast={showToast} />}
          {view === 'audit' && <AuditPanel sessionId={sessionId} />}
          {view === 'tools' && <ToolsPanel />}
          {view === 'browser' && (
            <BrowserPanel sessionId={sessionId} onApprovalChange={refreshPending} showToast={showToast} />
          )}
          {view === 'google' && (
            <GooglePanel sessionId={sessionId} onApprovalChange={refreshPending} showToast={showToast} />
          )}
          {view === 'sandbox' && <SandboxPanel sessionId={sessionId} showToast={showToast} />}
          {view === 'news' && <NewsPanel showToast={showToast} />}
          {view === 'desktop' && <DesktopPanel sessionId={sessionId} showToast={showToast} onApprovalChange={refreshPending} />}
          {view === 'settings' && <SettingsPanel sessionId={sessionId} />}
        </ErrorBoundary>
      </div>

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
