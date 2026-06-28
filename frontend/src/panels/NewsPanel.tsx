import { useCallback, useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from '../api'
import type { NewsReport, ScheduledTask } from '../types'
import { fmtDateTime } from './util'

interface Props {
  showToast: (msg: string) => void
}

const STATUS_BADGE: Record<string, string> = {
  success: 'risk-0',
  error: 'risk-2',
  running: 'risk-1',
  no_results: 'risk-1',
}

export function NewsPanel({ showToast }: Props) {
  const [reports, setReports] = useState<NewsReport[]>([])
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)

  // New scheduled task form
  const [taskName, setTaskName] = useState('')
  const [taskQuery, setTaskQuery] = useState('')
  const [taskInterval, setTaskInterval] = useState('3600')

  const load = useCallback(async () => {
    try {
      const [r, t] = await Promise.all([api.newsReports(), api.schedulerTasks()])
      setReports(r.items)
      setTasks(t.items)
    } catch (e) {
      showToast(`Lỗi tải tin tức: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => {
    load()
  }, [load])

  const summarize = async () => {
    const q = query.trim()
    if (!q) return
    setBusy(true)
    try {
      const res = await api.newsSummarize({ query: q })
      if (res.status === 'success') {
        showToast('Đã tạo báo cáo.')
        setQuery('')
        await load()
      } else {
        showToast(res.error || 'Không tìm thấy nguồn phù hợp.')
      }
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const createTask = async () => {
    const q = taskQuery.trim()
    if (!q) {
      showToast('Cần nhập chủ đề cho tác vụ.')
      return
    }
    const seconds = parseInt(taskInterval, 10)
    if (isNaN(seconds) || seconds < 300) {
      showToast('Chu kỳ tối thiểu là 300 giây.')
      return
    }
    try {
      await api.createSchedulerTask({
        name: taskName.trim() || `Tin: ${q}`,
        schedule: `interval:${seconds}`,
        params: { query: q },
        kind: 'news_summary',
      })
      showToast('Đã tạo tác vụ định kỳ.')
      setTaskName('')
      setTaskQuery('')
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const toggleTask = async (t: ScheduledTask) => {
    try {
      await api.updateSchedulerTask(t.id, !t.enabled)
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const runTask = async (t: ScheduledTask) => {
    try {
      await api.runSchedulerTask(t.id)
      showToast('Đã chạy tác vụ.')
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  const removeTask = async (t: ScheduledTask) => {
    if (!window.confirm(`Xoá tác vụ "${t.name}"?`)) return
    try {
      await api.deleteSchedulerTask(t.id)
      showToast('Đã xoá tác vụ.')
      await load()
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  return (
    <div className="panel-body">
      {/* ---- On-demand summary ---- */}
      <div className="section-title">Tóm tắt tin tức</div>
      <div className="card">
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            className="input"
            placeholder="Chủ đề cần tóm tắt (vd: tin công nghệ hôm nay)…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !busy && summarize()}
            style={{ flex: 1 }}
          />
          <button className="btn btn-sm" onClick={summarize} disabled={busy}>
            {busy ? 'Đang tạo…' : 'Tóm tắt'}
          </button>
        </div>
      </div>

      {/* ---- Scheduled tasks ---- */}
      <div className="section-title" style={{ marginTop: 16 }}>Tác vụ định kỳ ({tasks.length})</div>
      <div className="card">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          <input
            className="input"
            placeholder="Tên (tuỳ chọn)"
            value={taskName}
            onChange={(e) => setTaskName(e.target.value)}
            style={{ flex: '1 1 120px' }}
          />
          <input
            className="input"
            placeholder="Chủ đề"
            value={taskQuery}
            onChange={(e) => setTaskQuery(e.target.value)}
            style={{ flex: '1 1 160px' }}
          />
          <select className="input" value={taskInterval} onChange={(e) => setTaskInterval(e.target.value)}>
            <option value="3600">Mỗi giờ</option>
            <option value="21600">Mỗi 6 giờ</option>
            <option value="43200">Mỗi 12 giờ</option>
            <option value="86400">Mỗi ngày</option>
          </select>
          <button className="btn btn-sm" onClick={createTask}>
            Thêm
          </button>
        </div>
      </div>

      {tasks.map((t) => (
        <div className="card" key={t.id} style={{ opacity: t.enabled ? 1 : 0.55 }}>
          <div className="card-head">
            <span className="card-title">{t.name}</span>
            {t.last_status && (
              <span className={`badge ${STATUS_BADGE[t.last_status] || ''}`}>{t.last_status}</span>
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <button className="btn btn-sm" onClick={() => runTask(t)}>
                Chạy ngay
              </button>
              <button className="btn btn-sm" onClick={() => toggleTask(t)}>
                {t.enabled ? 'Tắt' : 'Bật'}
              </button>
              <button className="btn btn-danger btn-sm" onClick={() => removeTask(t)}>
                Xoá
              </button>
            </div>
          </div>
          <div className="faint" style={{ fontSize: 12 }}>
            {t.schedule} · {String((t.params as { query?: string }).query || '')}
            {t.last_run_at ? ` · chạy lần cuối ${fmtDateTime(t.last_run_at)}` : ''}
          </div>
        </div>
      ))}

      {/* ---- Reports ---- */}
      <div className="section-title" style={{ marginTop: 16 }}>Báo cáo ({reports.length})</div>
      {loading ? (
        <div className="empty">Đang tải…</div>
      ) : reports.length === 0 ? (
        <div className="empty">Chưa có báo cáo nào.</div>
      ) : (
        reports.map((r) => (
          <div className="card" key={r.id}>
            <div className="card-head">
              <span className="card-title">{r.query}</span>
              <span className="faint" style={{ marginLeft: 'auto', fontSize: 12 }}>
                {fmtDateTime(r.created_at)}
              </span>
            </div>
            <div style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>{r.summary}</div>
            {r.sources.length > 0 && (
              <>
                <div className="faint" style={{ marginTop: 8, textTransform: 'none' }}>
                  Nguồn ({r.sources.length})
                </div>
                <ol style={olStyle}>
                  {r.sources.map((s, i) => (
                    <li key={i} style={{ marginBottom: 4 }}>
                      <a href={s.url} target="_blank" rel="noreferrer noopener">
                        {s.title || s.url}
                      </a>
                      {s.published ? <span className="faint"> · {s.published}</span> : null}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </div>
        ))
      )}
    </div>
  )
}

const olStyle: CSSProperties = {
  margin: '4px 0 0',
  paddingLeft: 20,
  fontSize: 13,
}
