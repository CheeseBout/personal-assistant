import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ToolInfo } from '../types'
import { riskBadge } from './util'

export function ToolsPanel() {
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api
      .tools()
      .then(setTools)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="panel-body">
      <div className="section-title">Công cụ đã đăng ký ({tools.length})</div>
      {loading ? (
        <div className="empty">Đang tải…</div>
      ) : err ? (
        <div className="empty">Lỗi: {err}</div>
      ) : tools.length === 0 ? (
        <div className="empty">Chưa có công cụ nào.</div>
      ) : (
        tools.map((t) => (
          <div className="list-row" key={t.name}>
            <span className="nav-icon">🛠️</span>
            <div className="grow">
              <div className="name">
                <code style={{ fontFamily: 'var(--mono)' }}>{t.name}</code>
              </div>
              <div className="faint">{t.description}</div>
            </div>
            {t.requires_approval && <span className="tag">cần duyệt</span>}
            {t.rollback_supported && <span className="tag">hoàn tác được</span>}
            <span className={riskBadge(t.risk_level).cls}>{riskBadge(t.risk_level).label}</span>
          </div>
        ))
      )}
    </div>
  )
}
