# Backend Setup Guide

## Prerequisites

- Python 3.10+
- pip

## Installation

```bash
cd backend
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the `backend/` directory:

```env
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL=gpt-4o
EMBEDDING_MODEL=all-MiniLM-L6-v2
```

**Note:** The embedding model uses `sentence-transformers` (local) which will download on first use. No OpenAI API key needed for embeddings.

## Directory Structure

The app expects these directories (created automatically on first run):

```
data/
├── db/          # SQLite database
├── uploads/     # Uploaded files
├── embeddings/  # ChromaDB vector store
└── logs/        # Application logs
```

## Running the Server

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

The API will be available at: http://localhost:8000

API documentation (Swagger UI): http://localhost:8000/docs

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Health check |
| `GET` | `/api/health` | Detailed health check |
| `POST` | `/api/upload` | Upload document (TXT, PDF, MD, DOCX, XLSX) |
| `GET` | `/api/documents` | List all documents |
| `GET` | `/api/documents/{id}` | Get document details |
| `DELETE` | `/api/documents/{id}` | Delete document |
| `POST` | `/api/chat` | Chat with RAG |
| `GET` | `/api/chat/history/{session_id}` | Get chat history |
| `DELETE` | `/api/chat/history/{session_id}` | Clear chat history |
| `GET` | `/api/debug/retrieve?q=...` | Debug: see retrieved chunks |

## Frontend

### Option 1: Open directly (Recommended for development)

Open `frontend/index.html` directly in your browser. The frontend will connect to the backend at `http://localhost:8000`.

### Option 2: Serve via backend

The backend also serves the frontend at `/`. Visit http://localhost:8000 to access the UI.

## Testing with cURL

### Upload a file
```bash
curl -X POST "http://localhost:8000/api/upload" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@/path/to/your/file.txt"
```

### Chat
```bash
curl -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hỏi về nội dung tài liệu", "session_id": "test-session-1"}'
```

### List documents
```bash
curl "http://localhost:8000/api/documents"
```

### Debug retrieve (see what chunks are retrieved)
```bash
curl "http://localhost:8000/api/debug/retrieve?q=your+search+query&n_results=5"
```

## Troubleshooting

1. **Import errors**: Make sure you've installed all dependencies from `requirements.txt`
2. **OpenAI API errors**: Check your `.env` file has a valid `OPENAI_API_KEY`
3. **Database errors**: Ensure the `data/db/` directory exists and is writable
4. **Embedding model download**: First run will download ~100MB model (all-MiniLM-L6-v2)
5. **CORS errors**: The backend allows all origins (`*`) for development. For production, configure `CORS_ORIGINS` in `.env`.

## Logs

Application logs are stored in `data/logs/app.log`.
