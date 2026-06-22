# Local RAG Agent — Phase 1 MVP-A

Hệ thống trợ lý ảo local-first với RAG.

## Quick Start

### 1. Setup

```bash
cd backend
pip install -r requirements.txt
```

### 2. Cấu hình

Tạo file `.env`:
```env
OPENAI_API_KEY=your_key_here
```

### 3. Chạy

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

### 4. Mở UI

Mở `frontend/index.html` trong trình duyệt.

## API Endpoints

- `POST /api/upload` — Upload file (TXT, PDF, MD)
- `GET /api/documents` — List documents
- `DELETE /api/documents/{id}` — Delete document
- `POST /api/chat` — Chat với RAG
- `GET /api/chat/history/{session_id}` — Chat history

## Debug

- `GET /api/debug/retrieve?q=...` — Xem chunks được retrieve

## Project Structure

```
pa-agent/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app
│   │   ├── models/          # SQLite models
│   │   ├── services/        # RAG, LLM, embedding
│   │   └── api/             # Endpoints
│   └── requirements.txt
├── frontend/
│   └── index.html           # Simple chat UI
└── data/
    ├── uploads/             # Stored files
    ├── db/                  # SQLite database
    └── embeddings/          # ChromaDB vector store
```
