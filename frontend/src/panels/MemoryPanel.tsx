import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { MemoryView } from '../types'
import { fmtDateTime } from './util'

interface Props {
  sessionId: string
  showToast: (msg: string) => void
}

export function MemoryPanel({ sessionId, showToast }: Props) {
  const [data, setData] = useState<MemoryView | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      setData(await api.memory(sessionId))
    } catch (e) {
      showToast(`Lỗi tải bộ nhớ: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [sessionId, showToast])

  useEffect(() => {
    load()
  }, [load])

  const removeKey = async (key: string) => {
    if (!window.confirm(`Xoá khoá "${key}"?`)) return
    try {
      await api.deleteMemory(sessionId, key)
      showToast('Đã xoá khoá.')
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const undo = async (key?: string) => {
    try {
      await api.undoMemory(sessionId, key)
      showToast('Đã hoàn tác thay đổi gần nhất.')
      await load()
    } catch (e) {
      showToast(`Không có gì để hoàn tác: ${(e as Error).message}`)
    }
  }

  const entries = data ? Object.entries(data.entries) : []

  return (
    <div className="panel-body">
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Bộ nhớ ngắn hạn ({entries.length})</span>
        <button className="btn btn-sm" style={{ textTransform: 'none' }} onClick={() => undo()}>
          Hoàn tác gần nhất
        </button>
      </div>

      {loading ? (
        <div className="empty">Đang tải…</div>
      ) : entries.length === 0 ? (
        <div className="empty">Phiên này chưa có mục bộ nhớ nào.</div>
      ) : (
        entries.map(([key, value]) => (
          <div className="card" key={key}>
            <div className="card-head">
              <span className="card-title">{key}</span>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                <button className="btn btn-sm" onClick={() => undo(key)}>
                  Hoàn tác
                </button>
                <button className="btn btn-danger btn-sm" onClick={() => removeKey(key)}>
                  Xoá
                </button>
              </div>
            </div>
            <pre className="json">{JSON.stringify(value, null, 2)}</pre>
          </div>
        ))
      )}

      {data && data.history.length > 0 && (
        <>
          <div className="section-title">Lịch sử thay đổi</div>
          <div className="card">
            {data.history.map((h) => (
              <div className="event-row" key={h.id}>
                <span className="event-time">{fmtDateTime(h.created_at).split(' ')[1] || ''}</span>
                <span className="event-actor">{h.key}</span>
                <span className="event-action">
                  {h.operation} <span className="faint">{h.existed_before ? '(ghi đè)' : '(tạo mới)'}</span>
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
