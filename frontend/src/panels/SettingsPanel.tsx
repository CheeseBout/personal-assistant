import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AgentSettings, RagSettings } from '../types'

interface Props {
  sessionId: string
}

export function SettingsPanel({ sessionId }: Props) {
  const [s, setS] = useState<AgentSettings | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [rag, setRag] = useState<RagSettings | null>(null)
  const [draft, setDraft] = useState<RagSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<string | null>(null)

  useEffect(() => {
    api
      .settings()
      .then(setS)
      .catch((e) => setErr((e as Error).message))
    api
      .ragSettings()
      .then((r) => {
        setRag(r)
        setDraft(r)
      })
      .catch(() => {
        // ignore — panel still works without editable settings
      })
  }, [])

  const dirty = rag && draft && (
    rag.threshold !== draft.threshold ||
    rag.min_results !== draft.min_results ||
    rag.max_results !== draft.max_results ||
    rag.use_rerank !== draft.use_rerank ||
    rag.citation_coverage_min !== draft.citation_coverage_min ||
    rag.hybrid_candidates !== draft.hybrid_candidates ||
    rag.rerank_threshold !== draft.rerank_threshold ||
    rag.min_grounding !== draft.min_grounding
  )

  const saveRag = async () => {
    if (!draft) return
    setSaving(true)
    try {
      const updated = await api.updateRagSettings(draft)
      setRag(updated)
      setDraft(updated)
      setSavedAt(new Date().toLocaleTimeString())
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const resetDraft = () => {
    if (rag) setDraft(rag)
  }

  return (
    <div className="panel-body">
      <div className="section-title">Phiên</div>
      <div className="card">
        <div className="settings-grid">
          <span className="k">Session ID</span>
          <span className="v">{sessionId}</span>
        </div>
      </div>

      <div className="section-title">Nhà cung cấp & mô hình</div>
      {err ? (
        <div className="empty">Lỗi tải cài đặt: {err}</div>
      ) : !s ? (
        <div className="empty">Đang tải…</div>
      ) : (
        <>
          <div className="card">
            <div className="settings-grid">
              <span className="k">Provider</span>
              <span className="v">{s.provider}</span>
              <span className="k">Base URL</span>
              <span className="v">{s.base_url}</span>
              <span className="k">Model</span>
              <span className="v">{s.model}</span>
              <span className="k">Default model</span>
              <span className="v">{s.default_model}</span>
              <span className="k">API key</span>
              <span className="v">
                {s.api_key_configured ? (
                  <span className="dot-ok">● đã cấu hình</span>
                ) : (
                  <span className="dot-no">● chưa cấu hình</span>
                )}
              </span>
            </div>
          </div>

          <div className="section-title">Embedding</div>
          <div className="card">
            <div className="settings-grid">
              <span className="k">Embedding model</span>
              <span className="v">{s.embedding_model}</span>
              <span className="k">Local embeddings</span>
              <span className="v">{s.use_local_embeddings ? 'có' : 'không'}</span>
            </div>
          </div>
        </>
      )}

      <div className="section-title">RAG (có thể chỉnh sửa)</div>
      {!draft ? (
        <div className="empty">Đang tải cài đặt RAG…</div>
      ) : (
        <div className="card">
          <div className="settings-grid">
            <span className="k">Rerank threshold</span>
            <span className="v">
              <input
                type="number"
                step="0.05"
                value={draft.rerank_threshold}
                onChange={(e) => setDraft({ ...draft, rerank_threshold: parseFloat(e.target.value) || 0 })}
              />
            </span>

            <span className="k">Min results</span>
            <span className="v">
              <input
                type="number"
                min={1}
                value={draft.min_results}
                onChange={(e) => setDraft({ ...draft, min_results: parseInt(e.target.value) || 1 })}
              />
            </span>

            <span className="k">Max results</span>
            <span className="v">
              <input
                type="number"
                min={1}
                value={draft.max_results}
                onChange={(e) => setDraft({ ...draft, max_results: parseInt(e.target.value) || 1 })}
              />
            </span>

            <span className="k">Hybrid candidates</span>
            <span className="v">
              <input
                type="number"
                min={5}
                value={draft.hybrid_candidates}
                onChange={(e) => setDraft({ ...draft, hybrid_candidates: parseInt(e.target.value) || 5 })}
              />
            </span>

            <span className="k">Citation tối thiểu</span>
            <span className="v">
              <input
                type="number"
                min={0}
                value={draft.citation_coverage_min}
                onChange={(e) => setDraft({ ...draft, citation_coverage_min: parseInt(e.target.value) || 0 })}
              />
            </span>

            <span className="k">Rerank</span>
            <span className="v">
              <label>
                <input
                  type="checkbox"
                  checked={draft.use_rerank}
                  onChange={(e) => setDraft({ ...draft, use_rerank: e.target.checked })}
                />{' '}
                Bật rerank cross-encoder
              </label>
            </span>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button
              className="btn btn-primary btn-sm"
              disabled={!dirty || saving}
              onClick={saveRag}
            >
              {saving ? 'Đang lưu…' : 'Lưu thay đổi'}
            </button>
            <button className="btn btn-sm" disabled={!dirty || saving} onClick={resetDraft}>
              Hoàn tác
            </button>
            {savedAt && !dirty && <span className="muted">Đã lưu lúc {savedAt}</span>}
          </div>
        </div>
      )}
    </div>
  )
}
