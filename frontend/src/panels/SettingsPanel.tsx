import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AgentSettings } from '../types'

interface Props {
  sessionId: string
}

export function SettingsPanel({ sessionId }: Props) {
  const [s, setS] = useState<AgentSettings | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api
      .settings()
      .then(setS)
      .catch((e) => setErr((e as Error).message))
  }, [])

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

          <div className="section-title">Embedding & RAG</div>
          <div className="card">
            <div className="settings-grid">
              <span className="k">Embedding model</span>
              <span className="v">{s.embedding_model}</span>
              <span className="k">Local embeddings</span>
              <span className="v">{s.use_local_embeddings ? 'có' : 'không'}</span>
              <span className="k">RAG threshold</span>
              <span className="v">{s.rag_threshold}</span>
              <span className="k">Rerank</span>
              <span className="v">{s.use_rerank ? 'bật' : 'tắt'}</span>
              <span className="k">Citation tối thiểu</span>
              <span className="v">{s.citation_coverage_min}</span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
