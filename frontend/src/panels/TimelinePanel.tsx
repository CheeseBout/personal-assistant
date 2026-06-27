import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { EventItem, PendingApproval } from '../types'
import { fmtTime, riskBadge } from './util'

interface Props {
  sessionId: string
  onApprovalChange: () => void
  showToast: (msg: string) => void
}

export function TimelinePanel({ sessionId, onApprovalChange, showToast }: Props) {
  const [events, setEvents] = useState<EventItem[]>([])
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [loading, setLoading] = useState(true)
  const [auto, setAuto] = useState(true)

  const load = useCallback(async () => {
    try {
      const [ev, ap] = await Promise.all([api.events(sessionId, 80), api.approvals(sessionId)])
      setEvents(ev)
      setApprovals(ap)
    } catch {
      // keep last good state
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!auto) return
    const t = window.setInterval(load, 4000)
    return () => window.clearInterval(t)
  }, [auto, load])

  const decide = async (ap: PendingApproval, approve: boolean) => {
    try {
      await api.decideApproval(ap.id, approve ? 'approve' : 'deny')
      showToast(approve ? 'Đã duyệt — quay lại tab Trò chuyện để tiếp tục.' : 'Đã từ chối.')
      onApprovalChange()
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  return (
    <div className="panel-body">
      {approvals.length > 0 && (
        <>
          <div className="section-title">Chờ phê duyệt ({approvals.length})</div>
          {approvals.map((a) => (
            <div className="card" key={a.id}>
              <div className="card-head">
                <span className="card-title">{a.tool_name}</span>
                <span className={riskBadge(a.risk_level).cls}>{riskBadge(a.risk_level).label}</span>
              </div>
              <div className="muted">{a.reason}</div>
              <pre className="json">{JSON.stringify(a.arguments, null, 2)}</pre>
              <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                <button className="btn btn-primary btn-sm" onClick={() => decide(a, true)}>
                  Duyệt
                </button>
                <button className="btn btn-danger btn-sm" onClick={() => decide(a, false)}>
                  Từ chối
                </button>
              </div>
            </div>
          ))}
        </>
      )}

      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Dòng hoạt động</span>
        <label className="faint" style={{ display: 'flex', alignItems: 'center', gap: 5, textTransform: 'none' }}>
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Tự làm mới
        </label>
      </div>

      {loading ? (
        <div className="empty">Đang tải…</div>
      ) : events.length === 0 ? (
        <div className="empty">Chưa có hoạt động nào trong phiên này.</div>
      ) : (
        <div className="card">
          {events.map((e) => (
            <div className="event-row" key={e.id}>
              <span className="event-time">{fmtTime(e.timestamp)}</span>
              <span className="event-actor">{e.actor}</span>
              <span className="event-action">
                {e.action}
                {e.details && Object.keys(e.details).length > 0 && (
                  <span className="faint">
                    {' '}
                    {summarize(e.details)}
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function summarize(details: Record<string, unknown>): string {
  const keys = ['tool', 'approval_id', 'risk_level', 'iterations', 'error']
  const parts: string[] = []
  for (const k of keys) {
    if (details[k] != null) parts.push(`${k}=${String(details[k])}`)
  }
  return parts.length ? `· ${parts.join(' ')}` : ''
}
