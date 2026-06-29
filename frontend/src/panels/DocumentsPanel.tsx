import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { DocumentItem } from '../types'
import { fmtBytes, fmtDateTime } from './util'

interface Props {
  showToast: (msg: string) => void
}

export function DocumentsPanel({ showToast }: Props) {
  const [docs, setDocs] = useState<DocumentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [drag, setDrag] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    try {
      setDocs(await api.documents())
    } catch (e) {
      showToast(`Lỗi tải danh sách: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => {
    load()
  }, [load])

  const upload = async (file: File) => {
    try {
      const res = await api.upload(file)
      if (res.unchanged) {
        showToast(`${file.name}: không thay đổi.`)
      } else {
        showToast(`Đã xử lý ${file.name} (${res.chunk_count ?? '?'} chunks)`)
      }
    } catch (e) {
      showToast(`Lỗi tải lên ${file.name}: ${(e as Error).message}`)
    }
  }

  // Upload one or more files sequentially. Sequential keeps UI feedback clear
  // and avoids hammering the indexer with concurrent embed calls.
  const uploadMany = async (files: File[]) => {
    if (!files.length) return
    setUploading(true)
    try {
      for (const f of files) {
        await upload(f)
      }
      await load()
    } finally {
      setUploading(false)
    }
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDrag(false)
    const files = Array.from(e.dataTransfer.files || [])
    if (files.length) uploadMany(files)
  }

  const remove = async (doc: DocumentItem) => {
    if (!window.confirm(`Xoá "${doc.filename}" và toàn bộ embedding?`)) return
    setBusyId(doc.id)
    try {
      const res = await api.deleteDocument(doc.id)
      showToast(res.verified ? 'Đã xoá và xác minh embedding sạch.' : 'Đã xoá (chưa xác minh được embedding).')
      await load()
    } catch (e) {
      showToast(`Lỗi xoá: ${(e as Error).message}`)
    } finally {
      setBusyId(null)
    }
  }

  const reindex = async (doc: DocumentItem) => {
    setBusyId(doc.id)
    try {
      const res = await api.reindexDocument(doc.id)
      if (res.unchanged) showToast('File chưa thay đổi.')
      else showToast(`Đã tạo version ${res.version}.`)
      await load()
    } catch (e) {
      showToast(`Lỗi re-index: ${(e as Error).message}`)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="panel-body">
      <div
        className={`dropzone${drag ? ' drag' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDrag(true)
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
      >
        {uploading ? (
          <>
            <span className="spin">⏳</span> Đang tải lên & lập chỉ mục…
          </>
        ) : (
          <>
            Kéo thả file vào đây hoặc bấm để chọn. Hỗ trợ TXT, PDF, MD, DOCX, XLSX.
          </>
        )}
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.pdf,.md,.docx,.xlsx"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => {
            const files = Array.from(e.target.files || [])
            if (files.length) uploadMany(files)
            e.target.value = ''
          }}
        />
      </div>

      <div className="section-title">Tài liệu ({docs.length})</div>

      {loading ? (
        <div className="empty">Đang tải…</div>
      ) : docs.length === 0 ? (
        <div className="empty">Chưa có tài liệu nào.</div>
      ) : (
        docs.map((d) => (
          <div className="list-row" key={d.id}>
            <span className="nav-icon">📄</span>
            <div className="grow">
              <div className="name">{d.filename}</div>
              <div className="faint">
                {fmtBytes(d.file_size)} · v{d.current_version} · {fmtDateTime(d.uploaded_at)}
              </div>
            </div>
            <button className="btn btn-sm" disabled={busyId === d.id} onClick={() => reindex(d)}>
              Re-index
            </button>
            <button className="btn btn-danger btn-sm" disabled={busyId === d.id} onClick={() => remove(d)}>
              Xoá
            </button>
          </div>
        ))
      )}
    </div>
  )
}
