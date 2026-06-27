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
import { SettingsPanel } from './panels/SettingsPanel'

type View = 'chat' | 'documents' | 'timeline' | 'memory' | 'audit' | 'tools' | 'browser' | 'google' | 'sandbox' | 'settings'

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

  useEffect(() => {
    refreshPending()
    const t = window.setInterval(refreshPending, 5000)
    return () => window.clearInterval(t)
  }, [refreshPending])

  const startNewSession = () => {
    const id = newSessionId()
    setSessionId(id)
    setView('chat')
    showToast('Đã tạo phiên mới')
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

        {view === 'chat' && (
          <ChatPanel sessionId={sessionId} onApprovalChange={refreshPending} showToast={showToast} />
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
        {view === 'settings' && <SettingsPanel sessionId={sessionId} />}
      </div>

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
