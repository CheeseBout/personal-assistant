import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { BrowserState } from '../types'
import { fmtTime } from './util'

interface Props {
  sessionId: string
  onApprovalChange: () => void
  showToast: (msg: string) => void
}

export function BrowserPanel({ sessionId, showToast }: Props) {
  const [state, setState] = useState<BrowserState | null>(null)
  const [loading, setLoading] = useState(true)
  const [auto, setAuto] = useState(true)

  const load = useCallback(async () => {
    try {
      setState(await api.browserState(sessionId))
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

  const close = async () => {
    try {
      await api.browserClose(sessionId)
      showToast('Đã đóng tab trình duyệt.')
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const active = state?.is_active
  const actions = state?.actions || []

  return (
    <div className="panel-body">
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Phiên trình duyệt</span>
        <label className="faint" style={{ display: 'flex', alignItems: 'center', gap: 5, textTransform: 'none' }}>
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Tự làm mới
        </label>
        {active && (
          <button className="btn btn-danger btn-sm" style={{ marginLeft: 'auto' }} onClick={close}>
            Đóng tab
          </button>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">{state?.title || (active ? 'Trang chưa có tiêu đề' : 'Chưa mở trang')}</span>
          <span className={`badge ${active ? 'risk-0' : ''}`}>{active ? 'Đang hoạt động' : 'Không hoạt động'}</span>
        </div>
        {state?.current_url && (
          <div className="muted" style={{ wordBreak: 'break-all' }}>{state.current_url}</div>
        )}
        {state?.screenshot ? (
          <img
            src={`data:image/png;base64,${state.screenshot}`}
            alt="Ảnh chụp trang"
            style={{ width: '100%', marginTop: 10, border: '1px solid var(--border, #ddd)', borderRadius: 6 }}
          />
        ) : (
          <div className="empty" style={{ marginTop: 10 }}>
            {loading ? 'Đang tải…' : 'Chưa có ảnh chụp. Yêu cầu agent mở một trang web để bắt đầu.'}
          </div>
        )}
      </div>

      <div className="section-title">Dòng hành động ({actions.length})</div>
      {actions.length === 0 ? (
        <div className="empty">Chưa có hành động trình duyệt nào trong phiên này.</div>
      ) : (
        <div className="card">
          {actions.map((a) => (
            <div className="event-row" key={a.id}>
              <span className="event-time">{fmtTime(a.timestamp)}</span>
              <span className="event-actor">browser.{a.action}</span>
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
