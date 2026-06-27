import { useCallback, useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from '../api'
import type { SandboxRun } from '../types'
import { fmtTime } from './util'

interface Props {
  sessionId: string
  showToast: (msg: string) => void
}

const STATUS_BADGE: Record<string, string> = {
  success: 'risk-0',
  error: 'risk-2',
  killed: 'risk-3',
  denied: 'risk-3',
}

export function SandboxPanel({ sessionId, showToast }: Props) {
  const [runs, setRuns] = useState<SandboxRun[]>([])
  const [loading, setLoading] = useState(true)
  const [auto, setAuto] = useState(true)
  const [artifact, setArtifact] = useState<{ name: string; content: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await api.sandboxRuns(sessionId)
      setRuns(res.runs)
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

  const viewArtifact = async (name: string) => {
    try {
      const a = await api.sandboxArtifact(sessionId, name)
      setArtifact({ name: a.name, content: a.content })
    } catch (e) {
      showToast(`Lỗi: ${(e as Error).message}`)
    }
  }

  // PLACEHOLDER_RENDER
  return (
    <div className="panel-body">
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span>Sandbox ({runs.length})</span>
        <label className="faint" style={{ display: 'flex', alignItems: 'center', gap: 5, textTransform: 'none' }}>
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Tự làm mới
        </label>
      </div>

      {runs.length === 0 ? (
        <div className="empty">
          {loading ? 'Đang tải…' : 'Chưa có lần chạy sandbox nào. Yêu cầu agent chạy code Python hoặc lệnh shell.'}
        </div>
      ) : (
        runs.map((r) => (
          <div className="card" key={r.id}>
            <div className="card-head">
              <span className="card-title">
                sandbox.{r.kind} <span className="faint">· Mode {r.mode}</span>
              </span>
              <span className={`badge ${STATUS_BADGE[r.status] || ''}`}>
                {r.status}
                {r.killed_reason ? ` (${r.killed_reason})` : ''}
                {r.exit_code != null ? ` · exit ${r.exit_code}` : ''}
              </span>
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              {fmtTime(r.timestamp)}
              {r.duration_ms != null ? ` · ${r.duration_ms} ms` : ''}
            </div>

            <div className="faint" style={{ marginTop: 8, textTransform: 'none' }}>Code / lệnh</div>
            <pre className="code-block" style={preStyle}>{r.code || '(trống)'}</pre>

            {r.stdout && (
              <>
                <div className="faint" style={{ marginTop: 8, textTransform: 'none' }}>stdout</div>
                <pre className="code-block" style={preStyle}>{r.stdout}</pre>
              </>
            )}
            {r.stderr && (
              <>
                <div className="faint" style={{ marginTop: 8, textTransform: 'none' }}>stderr</div>
                <pre className="code-block" style={{ ...preStyle, color: 'var(--danger, #c0392b)' }}>{r.stderr}</pre>
              </>
            )}

            {r.artifacts.length > 0 && (
              <>
                <div className="faint" style={{ marginTop: 8, textTransform: 'none' }}>
                  Artifacts ({r.artifacts.length})
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
                  {r.artifacts.map((a) => (
                    <button key={a.name} className="btn btn-sm" onClick={() => viewArtifact(a.name)}>
                      {a.name} <span className="faint">({a.size}B)</span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        ))
      )}

      {artifact && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="card-head">
            <span className="card-title">{artifact.name}</span>
            <button className="btn btn-sm" onClick={() => setArtifact(null)}>Đóng</button>
          </div>
          <pre className="code-block" style={preStyle}>{artifact.content}</pre>
        </div>
      )}
    </div>
  )
}

const preStyle: CSSProperties = {
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  maxHeight: 280,
  overflow: 'auto',
  fontFamily: 'monospace',
  fontSize: 12,
  background: 'var(--code-bg, #f5f5f5)',
  padding: 8,
  borderRadius: 6,
  margin: '4px 0 0',
}
