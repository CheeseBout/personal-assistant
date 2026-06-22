# Test Script for MVP-A

## Prerequisites
1. OpenAI API key
2. Python dependencies installed

## Setup
```bash
cd backend
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY
pip install -r requirements.txt
```

## Run Server
```bash
python -m uvicorn app.main:app --reload --port 8000
```

## Manual Test Steps

### 1. Test Health Endpoint
```bash
curl http://localhost:8000/api/health
```
Expected: `{"status":"healthy",...}`

### 2. Upload a Document
```bash
curl -X POST "http://localhost:8000/api/upload" \
  -H "accept: application/json" \
  -F "file=@test_file.txt"
```
Expected: `{"success": true, "doc_id": "...", "chunk_count": N, ...}`

### 3. List Documents
```bash
curl http://localhost:8000/api/documents
```

### 4. Test Chat
```bash
curl -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "Nội dung câu hỏi về tài liệu", "session_id": "test123"}'
```

### 5. Debug Retrieve
```bash
curl "http://localhost:8000/api/debug/retrieve?q=Nội dung tìm kiếm"
```

### 6. Test "Không tìm thấy" scenario
Chat với câu hỏi không liên quan đến tài liệu đã upload.
Expected: "Không tìm thấy tài liệu phù hợp."

### 7. Test UI
Mở `frontend/index.html` trong trình duyệt và test:
- Upload file
- Chat
- Xem citations
- Xem history

## Automated Test (Python)
See `test_mvp_a.py` for automated tests.
