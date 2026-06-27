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
    setUploading(true)
    try {
      const res = await api.upload(file)
      if (res.success === false) {
        showToast(`Tải lên thất bại: ${res.error}`)
      } else if (res.unchanged) {
        showToast('File không thay đổi, bỏ qua re-index.')
      } else {
        showToast(`Đã xử lý ${file.name} (${res.chunk_count ?? '?'} chunks)`)
      }
      await load()
    } catch (e) {
      showToast(`Lỗi tải lên: ${(e as Error).message}`)
    } finally {
      setUploading(false)
    }
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDrag(false)
    const f = e.dataTransfer.files?.[0]
    if (f) upload(f)
  }

  const remove = async (doc: DocumentItem) => {
    if (!window.confirm(`Xoá "${doc.filename}" và toàn bộ embedding?`)) return
    setBusyId(doc.id)
    try {
      const res = await api.deleteDocument(doc.id)
      if (res.success === false) showToast(`Xoá thất bại: ${res.error}`)
      else showToast(res.verified ? 'Đã xoá và xác minh embedding sạch.' : 'Đã xoá (chưa xác minh được embedding).')
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
      if (res.success === false) showToast(`Re-index thất bại: ${res.error}`)
      else if (res.unchanged) showToast('File chưa thay đổi.')
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
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload(f)
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
