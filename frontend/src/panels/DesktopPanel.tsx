import { useCallback, useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from '../api'
import type { DesktopObservation, DesktopWindow, UiElement } from '../types'
import { fmtDateTime } from './util'

interface Props {
  sessionId: string
  showToast: (msg: string) => void
  onApprovalChange?: () => void
}

export function DesktopPanel({ sessionId, showToast }: Props) {
  const [items, setItems] = useState<DesktopObservation[]>([])
  const [windows, setWindows] = useState<DesktopWindow[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [showWindows, setShowWindows] = useState(false)
  const [expandedA11y, setExpandedA11y] = useState<Set<string>>(new Set())
  const [controlEnabled, setControlEnabled] = useState<boolean>(false)

  const load = useCallback(async () => {
    try {
      const res = await api.desktopObservations(sessionId)
      setItems(res.items)
    } catch (e) {
      showToast(`Lỗi tải quan sát: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [sessionId, showToast])

  useEffect(() => {
    load()
    api.settings()
      .then((s) => setControlEnabled(Boolean(s.desktop_control_enabled)))
      .catch(() => setControlEnabled(false))
  }, [load])

  const observe = async () => {
    setBusy(true)
    try {
      const res = await api.desktopObserve(sessionId)
      if (res.status === 'success') {
        showToast('Đã quan sát màn hình.')
        await load()
      } else {
        showToast(res.error || 'Không thể chụp màn hình trong môi trường này.')
      }
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const loadWindows = async () => {
    try {
      const res = await api.desktopWindows()
      setWindows(res.windows)
      setShowWindows(true)
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const toggleA11y = (id: string) => {
    setExpandedA11y((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const createMonitorTask = async () => {
    try {
      await api.createSchedulerTask({
        name: 'Giám sát màn hình',
        schedule: 'interval:60',
        kind: 'desktop_monitor',
        params: {},
        enabled: true,
      })
      showToast('Đã tạo tác vụ giám sát màn hình định kỳ.')
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  return (
    <div className="panel-body">
      {/* Header + actions */}
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span>Màn hình {controlEnabled ? '(quan sát + điều khiển)' : '(chỉ đọc)'}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button className="btn btn-sm" onClick={observe} disabled={busy}>
            {busy ? 'Đang quan sát…' : 'Quan sát ngay'}
          </button>
          <button className="btn btn-sm" onClick={loadWindows}>
            Cửa sổ
          </button>
          <button className="btn btn-sm" onClick={createMonitorTask}>
            Bật giám sát
          </button>
        </div>
      </div>

      {controlEnabled ? (
        <div
          className="faint"
          style={{ fontSize: 12, marginBottom: 8, textTransform: 'none', color: '#b45309' }}
        >
          ⚠ Điều khiển desktop đã BẬT. Agent có thể click, gõ phím và di chuột — nhưng mọi hành động
          thay đổi trạng thái đều cần bạn xác nhận trước khi thực thi (xem ở tab Dòng thời gian/Chat).
        </div>
      ) : (
        <div className="faint" style={{ fontSize: 12, marginBottom: 8, textTransform: 'none' }}>
          Agent chỉ xem, đọc và tóm tắt màn hình. Không điều khiển chuột/bàn phím. Để bật điều khiển, đặt
          DESKTOP_ENABLE_CONTROL=true trong .env và khởi động lại. Văn bản nhạy cảm được che, ảnh chụp lưu cục bộ.
        </div>
      )}

      {/* Window list (toggle) */}
      {showWindows && windows.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="card-head">
            <span className="card-title">Cửa sổ đang mở ({windows.length})</span>
            <button className="btn btn-sm" style={{ marginLeft: 'auto' }} onClick={() => setShowWindows(false)}>
              Ẩn
            </button>
          </div>
          <div style={{ maxHeight: 200, overflow: 'auto' }}>
            {windows.map((w, i) => (
              <div key={i} style={{ padding: '3px 0', fontSize: 13, display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ opacity: w.minimized ? 0.5 : 1 }}>
                  {w.active ? '▶ ' : ''}
                  {w.title}
                </span>
                {w.minimized && <span className="badge" style={badgeStyle}>thu nhỏ</span>}
                {w.maximized && <span className="badge" style={badgeStyle}>phóng to</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Observations */}
      {loading ? (
        <div className="empty">Đang tải…</div>
      ) : items.length === 0 ? (
        <div className="empty">Chưa có quan sát nào. Bấm "Quan sát ngay".</div>
      ) : (
        items.map((o) => (
          <div className="card" key={o.id}>
            <div className="card-head">
              <span className="card-title">{o.active_window || '(không rõ cửa sổ)'}</span>
              <span className="faint" style={{ marginLeft: 'auto', fontSize: 12 }}>
                {fmtDateTime(o.created_at)}
              </span>
            </div>
            {o.summary && <div style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>{o.summary}</div>}

            {/* UI Elements (accessibility tree) */}
            {o.ui_elements && o.ui_elements.length > 0 && (
              <>
                <div
                  className="faint"
                  style={{ marginTop: 8, textTransform: 'none', cursor: 'pointer', userSelect: 'none' }}
                  onClick={() => toggleA11y(o.id)}
                >
                  {expandedA11y.has(o.id) ? '▼' : '▶'} UI Elements ({o.ui_elements.length})
                </div>
                {expandedA11y.has(o.id) && <UiElementList elements={o.ui_elements} />}
              </>
            )}

            {o.ocr_text && (
              <>
                <div className="faint" style={{ marginTop: 8, textTransform: 'none' }}>
                  Văn bản OCR {o.masked ? '(đã che dữ liệu nhạy cảm)' : ''}
                </div>
                <pre className="code-block" style={preStyle}>
                  {o.ocr_text}
                </pre>
              </>
            )}
          </div>
        ))
      )}
    </div>
  )
}

function UiElementList({ elements }: { elements: UiElement[] }) {
  return (
    <div style={{ maxHeight: 300, overflow: 'auto', marginTop: 4 }}>
      <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--border, #ddd)' }}>
            <th style={thStyle}>Loại</th>
            <th style={thStyle}>Tên</th>
            <th style={thStyle}>Trạng thái</th>
          </tr>
        </thead>
        <tbody>
          {elements.map((el, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--border-light, #eee)' }}>
              <td style={tdStyle}>
                <span className="badge" style={{ ...badgeStyle, ...controlBadge(el.type) }}>
                  {el.type}
                </span>
              </td>
              <td style={tdStyle}>{el.name || <span className="faint">—</span>}</td>
              <td style={tdStyle}>{el.enabled ? '' : <span style={{ color: '#b00' }}>disabled</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function controlBadge(type: string): CSSProperties {
  const map: Record<string, string> = {
    Button: '#2563eb',
    Edit: '#059669',
    Text: '#6b7280',
    MenuItem: '#7c3aed',
    CheckBox: '#d97706',
    ComboBox: '#0891b2',
    ListItem: '#4f46e5',
    TabItem: '#dc2626',
  }
  const bg = map[type]
  if (!bg) return {}
  return { background: bg, color: '#fff' }
}

const preStyle: CSSProperties = {
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  maxHeight: 240,
  overflow: 'auto',
  fontFamily: 'monospace',
  fontSize: 12,
  background: 'var(--code-bg, #f5f5f5)',
  padding: 8,
  borderRadius: 6,
  margin: '4px 0 0',
}

const badgeStyle: CSSProperties = {
  fontSize: 10,
  padding: '1px 6px',
  borderRadius: 4,
  background: 'var(--badge-bg, #e5e7eb)',
  display: 'inline-block',
}

const thStyle: CSSProperties = { padding: '4px 6px', fontWeight: 600 }
const tdStyle: CSSProperties = { padding: '3px 6px' }
