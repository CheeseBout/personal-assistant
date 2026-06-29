# Personal AI Agent — Trợ lý ảo cá nhân (local-first)

Trợ lý AI cá nhân ưu tiên chạy cục bộ, kết hợp RAG với một agent có khả năng dùng công cụ (tool-calling) theo mô hình an toàn **deny-first + Human-In-The-Loop (HITL)**. Agent có thể đọc tài liệu, duyệt web, thao tác Google Workspace, chạy code trong sandbox, quan sát và điều khiển desktop — nhưng mọi hành động rủi ro đều cần người dùng xác nhận trước khi thực thi.

## Tính năng

- **RAG**: upload tài liệu (TXT/PDF/MD/DOCX), hybrid search (BM25 + vector), rerank, trích dẫn nguồn (grounding).
- **Agent tool-calling**: tool registry, permission engine, risk classifier, vòng lặp agent có HITL.
- **Browser automation** (Playwright): mở trang, click, gõ, trích xuất, chụp ảnh, tải xuống.
- **Google Workspace**: Gmail, Drive, Docs, Sheets (OAuth).
- **Bộ nhớ dài hạn**: semantic + procedural, xuyên phiên, có provenance và undo.
- **Sandbox execution**: chạy Python/shell cô lập, phân tích lệnh, gated qua HITL.
- **Web search + tóm tắt tin tức + scheduler** (APScheduler).
- **Desktop perception** (chỉ đọc): chụp màn hình, OCR, accessibility tree, vision.
- **Desktop control**: click/gõ phím/di chuột — opt-in, gated qua HITL.

## Kiến trúc an toàn

- **Deny-first + HITL**: mọi tool call đi qua `permission_engine` + `risk_classifier`. Hành động rủi ro (`ask_strong`) phải được người dùng duyệt trước khi chạy.
- **Chống prompt injection**: kết quả trả về từ tool (RAG, file, web) được bọc là "dữ liệu không tin cậy", không bao giờ coi là chỉ thị.
- **Che secret**: API key, token, private key, mật khẩu không bao giờ lọt vào log, audit hay câu trả lời (`core/redaction.py`).
- **Audit đầy đủ**: mọi hành động được ghi lại qua episodic memory + `AuditLog`.
- **Desktop control opt-in**: tắt mặc định; phải bật `DESKTOP_ENABLE_CONTROL=true` trong `.env`.

## Tech stack

- **Backend**: FastAPI, SQLite, ChromaDB (vector store), sentence-transformers
- **Frontend**: React 19 + Vite + TypeScript
- **Tích hợp**: Playwright, Google API, pyautogui/pywinauto (desktop), APScheduler

## Quick Start

### 1. Backend

```bash
cd backend
python -m venv venv
source venv/Scripts/activate   # Windows (Git Bash); Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
```

Tạo file `.env` (xem `.env.example`):

```env
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL=gpt-4o

# Google Workspace (tùy chọn)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Desktop control (tùy chọn, mặc định tắt)
DESKTOP_ENABLE_CONTROL=false
```

Chạy server:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Mở UI tại địa chỉ Vite in ra (mặc định `http://localhost:5173`). UI gồm 12 panel: Chat, Documents, Timeline, Memory, Audit, Tools, Browser, Google, Sandbox, News, Desktop, Settings.

## API Endpoints

### RAG & tài liệu
- `POST /api/upload` — Upload file (TXT, PDF, MD, DOCX)
- `GET /api/documents` — Danh sách tài liệu
- `DELETE /api/documents/{id}` — Xóa tài liệu
- `POST /api/documents/{id}/reindex` — Re-index tài liệu
- `POST /api/chat` — Chat với RAG
- `GET /api/chat/history/{session_id}` — Lịch sử chat
- `GET /api/debug/retrieve?q=...` — Xem chunks được retrieve

### Agent & HITL
- `POST /api/agent` — Chạy agent loop (tool-calling)
- `POST /api/agent/continue` — Tiếp tục sau khi duyệt
- `GET /api/approvals` — Danh sách hành động chờ duyệt
- `POST /api/approvals/{id}/decide` — Duyệt / từ chối
- `GET /api/events`, `GET /api/audit` — Dòng thời gian & audit log
- `GET /api/tools`, `GET /api/settings` — Catalog tool & cấu hình

### Bộ nhớ
- `GET /api/memory`, `POST /api/memory/undo`, `DELETE /api/memory` — Bộ nhớ ngắn hạn
- `GET/POST /api/ltm`, `DELETE /api/ltm/{id}`, `GET /api/ltm/export` — Bộ nhớ dài hạn

### Tích hợp
- `GET /api/google/status`, `POST /api/google/connect`, `POST /api/google/disconnect`
- `GET /api/browser/state`, `POST /api/browser/close`
- `GET /api/sandbox/runs`, `GET /api/sandbox/artifact`
- `POST /api/news/summarize`, `GET /api/news/reports`
- `GET/POST /api/scheduler/tasks`, `POST /api/scheduler/tasks/{id}/run`, `DELETE /api/scheduler/tasks/{id}`
- `POST /api/desktop/observe`, `GET /api/desktop/observations`, `GET /api/desktop/windows`

## Công cụ (tools)

60 tool được đăng ký, đi qua permission engine. Các nhóm chính:

| Nhóm | Số lượng | Ví dụ |
|------|----------|-------|
| `browser.*` | 10 | open, click, type, extract, screenshot, download |
| `desktop.*` | 11 | observe, ui_elements (đọc) · click, type, key, drag (điều khiển) |
| `gmail.*` | 9 | search, read, send, draft, label, trash |
| `drive.*` | 7 | search, read, upload, download, move, trash |
| `file.*` | 5 | read, write, list, delete, undo |
| `sandbox.*` | 5 | python, shell, install, list_artifacts |
| `docs.*` / `sheets.*` | 4 + 4 | read, create, edit, export / read, update, append |
| `memory.*` | 2 | save, search |
| `rag.search`, `web.search`, `news.summarize` | 3 | — |

## Cấu trúc dự án

```
pa-agent/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app + migrations + startup
│   │   ├── api/             # Endpoints (chat, agent, google, upload, debug)
│   │   ├── core/            # config, logging, redaction
│   │   ├── models/          # SQLite models + migrations
│   │   └── services/        # RAG, agent loop, tools, permission engine...
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.tsx          # Shell + điều hướng panel
│       ├── api.ts           # API client
│       └── panels/          # 12 panel UI
└── data/
    ├── uploads/             # File đã upload
    ├── db/                  # SQLite database
    ├── embeddings/          # ChromaDB vector store
    ├── browser/             # Profile, screenshots, downloads
    └── sandbox/             # Workspace sandbox
```

## Ghi chú

- **Desktop control** cần màn hình thật và phải bật `DESKTOP_ENABLE_CONTROL=true`. pyautogui FAILSAFE bật sẵn: di chuột vào góc màn hình để hủy thao tác.
- **OCR** cần cài Tesseract binary; **vision** (`DESKTOP_ENABLE_VISION`) tắt mặc định vì quyền riêng tư.
- **Google tools** cần `GOOGLE_CLIENT_ID/SECRET` (OAuth Desktop flow).
- Giao tiếp và tài liệu mặc định bằng tiếng Việt.
