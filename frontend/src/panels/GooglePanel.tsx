import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { GoogleActionItem, GoogleStatus } from '../types'
import { fmtTime } from './util'

interface Props {
  sessionId: string
  onApprovalChange: () => void
  showToast: (msg: string) => void
}

export function GooglePanel({ sessionId, showToast }: Props) {
  const [status, setStatus] = useState<GoogleStatus | null>(null)
  const [actions, setActions] = useState<GoogleActionItem[]>([])
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState(false)
  const [auto, setAuto] = useState(true)

  const load = useCallback(async () => {
    try {
      const [st, act] = await Promise.all([
        api.googleStatus(),
        api.googleActions(sessionId),
      ])
      setStatus(st)
      setActions(act.actions || [])
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

  const connect = async () => {
    setConnecting(true)
    try {
      const res = await api.googleConnect()
      showToast(res.connected ? `Đã kết nối ${res.email || 'Google'}.` : 'Kết nối thất bại.')
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    } finally {
      setConnecting(false)
    }
  }

  const disconnect = async () => {
    try {
      await api.googleDisconnect()
      showToast('Đã ngắt kết nối Google.')
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const connected = status?.connected

  return (
    <div className="panel-body">
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Kết nối Google (Gmail · Drive · Docs · Sheets)</span>
        <label className="faint" style={{ display: 'flex', alignItems: 'center', gap: 5, textTransform: 'none' }}>
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Tự làm mới
        </label>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">{connected ? status?.email || 'Đã kết nối' : 'Chưa kết nối'}</span>
          <span className={`badge ${connected ? 'risk-0' : ''}`}>
            {connected ? 'Đã kết nối' : 'Ngoại tuyến'}
          </span>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          {connected
            ? 'Agent có thể tìm/đọc (email, file, doc, sheet) tự động; gửi/sửa/tải lên/đổi/xóa cần bạn phê duyệt.'
            : 'Đăng nhập Google một lần qua trình duyệt để bật Gmail, Drive, Docs và Sheets.'}
        </div>
        <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
          {connected ? (
            <button className="btn btn-danger btn-sm" onClick={disconnect}>
              Ngắt kết nối
            </button>
          ) : (
            <button className="btn btn-sm" onClick={connect} disabled={connecting}>
              {connecting ? 'Đang mở trình duyệt…' : 'Kết nối Google'}
            </button>
          )}
        </div>
      </div>

      <div className="section-title">Dòng hành động ({actions.length})</div>
      {actions.length === 0 ? (
        <div className="empty">
          {loading ? 'Đang tải…' : 'Chưa có hành động Google nào trong phiên này.'}
        </div>
      ) : (
        <div className="card">
          {actions.map((a) => (
            <div className="event-row" key={a.id}>
              <span className="event-time">{fmtTime(a.timestamp)}</span>
              <span className="event-actor">{a.service}.{a.action}</span>
              <span className="event-action">
                {a.target && <span className="faint">{a.target}</span>}
                <span className={`badge ${a.status === 'error' ? 'risk-3' : 'risk-0'}`} style={{ marginLeft: 8 }}>
                  {a.status}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
