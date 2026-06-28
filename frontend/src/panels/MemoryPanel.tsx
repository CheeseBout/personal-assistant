import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { LtmItem, LtmType, MemoryView } from '../types'
import { fmtDateTime } from './util'

interface Props {
  sessionId: string
  showToast: (msg: string) => void
}

const LTM_TYPES: { id: LtmType; label: string }[] = [
  { id: 'semantic', label: 'Tri thức' },
  { id: 'procedural', label: 'Quy trình' },
  { id: 'episodic', label: 'Sự kiện' },
]

function typeLabel(t: string): string {
  return LTM_TYPES.find((x) => x.id === t)?.label || t
}

export function MemoryPanel({ sessionId, showToast }: Props) {
  const [data, setData] = useState<MemoryView | null>(null)
  const [loading, setLoading] = useState(true)

  // Long-term memory state
  const [ltm, setLtm] = useState<LtmItem[]>([])
  const [ltmQuery, setLtmQuery] = useState('')
  const [ltmType, setLtmType] = useState<LtmType | ''>('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editContent, setEditContent] = useState('')
  const [newContent, setNewContent] = useState('')
  const [newType, setNewType] = useState<LtmType>('semantic')

  const load = useCallback(async () => {
    try {
      setData(await api.memory(sessionId))
    } catch (e) {
      showToast(`Lỗi tải bộ nhớ: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [sessionId, showToast])

  const loadLtm = useCallback(async () => {
    try {
      const res = await api.ltmList({
        q: ltmQuery || undefined,
        type: ltmType || undefined,
      })
      setLtm(res.items)
    } catch (e) {
      showToast(`Lỗi tải ghi nhớ dài hạn: ${(e as Error).message}`)
    }
  }, [ltmQuery, ltmType, showToast])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    loadLtm()
  }, [loadLtm])

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

  const createLtm = async () => {
    const content = newContent.trim()
    if (!content) return
    try {
      await api.ltmCreate({ content, type: newType })
      setNewContent('')
      showToast('Đã lưu ghi nhớ.')
      await loadLtm()
    } catch (e) {
      showToast(`Lỗi lưu: ${(e as Error).message}`)
    }
  }

  const saveEdit = async (id: string) => {
    try {
      await api.ltmUpdate(id, { content: editContent })
      setEditingId(null)
      showToast('Đã cập nhật ghi nhớ.')
      await loadLtm()
    } catch (e) {
      showToast(`Lỗi cập nhật: ${(e as Error).message}`)
    }
  }

  const toggleEnabled = async (m: LtmItem) => {
    try {
      await api.ltmUpdate(m.id, { enabled: !m.enabled })
      await loadLtm()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const removeLtm = async (id: string) => {
    if (!window.confirm('Xoá vĩnh viễn ghi nhớ này?')) return
    try {
      await api.ltmDelete(id)
      showToast('Đã xoá ghi nhớ.')
      await loadLtm()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const exportLtm = async () => {
    try {
      const res = await api.ltmExport()
      const blob = new Blob([JSON.stringify(res.items, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `memory-export-${res.exported_at.slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      showToast(`Đã xuất ${res.count} ghi nhớ.`)
    } catch (e) {
      showToast(`Lỗi xuất: ${(e as Error).message}`)
    }
  }

  const entries = data ? Object.entries(data.entries) : []

  return (
    <div className="panel-body">
      {/* ---- Long-term memory ---- */}
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Ghi nhớ dài hạn ({ltm.length})</span>
        <button className="btn btn-sm" style={{ textTransform: 'none', marginLeft: 'auto' }} onClick={exportLtm}>
          Xuất JSON
        </button>
      </div>

      <div className="card">
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <input
            className="input"
            placeholder="Tìm trong ghi nhớ…"
            value={ltmQuery}
            onChange={(e) => setLtmQuery(e.target.value)}
            style={{ flex: 1 }}
          />
          <select className="input" value={ltmType} onChange={(e) => setLtmType(e.target.value as LtmType | '')}>
            <option value="">Tất cả loại</option>
            {LTM_TYPES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            className="input"
            placeholder="Thêm ghi nhớ mới…"
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && createLtm()}
            style={{ flex: 1 }}
          />
          <select className="input" value={newType} onChange={(e) => setNewType(e.target.value as LtmType)}>
            {LTM_TYPES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
          <button className="btn btn-sm" onClick={createLtm}>
            Lưu
          </button>
        </div>
      </div>

      {ltm.length === 0 ? (
        <div className="empty">Chưa có ghi nhớ dài hạn nào.</div>
      ) : (
        ltm.map((m) => (
          <div className="card" key={m.id} style={{ opacity: m.enabled ? 1 : 0.55 }}>
            <div className="card-head">
              <span className="badge">{typeLabel(m.type)}</span>
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                {editingId === m.id ? (
                  <>
                    <button className="btn btn-sm" onClick={() => saveEdit(m.id)}>
                      Lưu
                    </button>
                    <button className="btn btn-sm" onClick={() => setEditingId(null)}>
                      Huỷ
                    </button>
                  </>
                ) : (
                  <>
                    <button className="btn btn-sm" onClick={() => toggleEnabled(m)}>
                      {m.enabled ? 'Tắt' : 'Bật'}
                    </button>
                    <button
                      className="btn btn-sm"
                      onClick={() => {
                        setEditingId(m.id)
                        setEditContent(m.content)
                      }}
                    >
                      Sửa
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={() => removeLtm(m.id)}>
                      Xoá
                    </button>
                  </>
                )}
              </div>
            </div>
            {editingId === m.id ? (
              <textarea
                className="input"
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                rows={3}
                style={{ width: '100%' }}
              />
            ) : (
              <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
            )}
            <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>
              {m.source ? `Nguồn: ${m.source}` : 'Nguồn: —'}
              {m.last_used_at ? ` · Dùng lần cuối: ${fmtDateTime(m.last_used_at)}` : ''}
            </div>
          </div>
        ))
      )}

      {/* ---- Short-term memory ---- */}
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 18 }}>
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
