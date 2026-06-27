import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { AuditItem } from '../types'
import { fmtDateTime } from './util'

interface Props {
  sessionId: string
}

export function AuditPanel({ sessionId }: Props) {
  const [items, setItems] = useState<AuditItem[]>([])
  const [loading, setLoading] = useState(true)
  const [scoped, setScoped] = useState(true)

  const load = useCallback(async () => {
    try {
      setItems(await api.audit(scoped ? sessionId : undefined, 150))
    } catch {
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [sessionId, scoped])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="panel-body">
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Nhật ký kiểm toán ({items.length})</span>
        <label className="faint" style={{ display: 'flex', alignItems: 'center', gap: 5, textTransform: 'none' }}>
          <input type="checkbox" checked={scoped} onChange={(e) => setScoped(e.target.checked)} />
          Chỉ phiên hiện tại
        </label>
        <button className="btn btn-sm" style={{ marginLeft: 'auto', textTransform: 'none' }} onClick={load}>
          Làm mới
        </button>
      </div>

      {loading ? (
        <div className="empty">Đang tải…</div>
      ) : items.length === 0 ? (
        <div className="empty">Chưa có bản ghi kiểm toán.</div>
      ) : (
        <div className="card">
          {items.map((a) => (
            <div className="event-row" key={a.id}>
              <span className="event-time">{fmtDateTime(a.timestamp).split(' ')[1] || ''}</span>
              <span className="event-actor">{a.actor}</span>
              <span className="event-action">
                {a.action}
                {a.details && Object.keys(a.details).length > 0 && (
                  <span className="faint"> · {compact(a.details)}</span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function compact(d: Record<string, unknown>): string {
  return Object.entries(d)
    .slice(0, 4)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' ')
}
