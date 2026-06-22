# Yêu cầu xây dựng AI Agent trợ lý ảo cá nhân local-first

## 1. Mục tiêu sản phẩm

Xây dựng một AI Agent trợ lý ảo cá nhân chạy theo hướng **local-first**, dành cho một người dùng duy nhất trên máy cá nhân. Hệ thống có giao diện desktop dạng chat giống ChatGPT, hỗ trợ xử lý đa định dạng đầu vào, truy xuất tài liệu cá nhân bằng RAG, tích hợp Gmail/Drive/Docs/Sheets, tự động hóa trình duyệt, chạy Python/Shell trong sandbox, quản lý bộ nhớ cá nhân, tóm tắt tin tức theo yêu cầu hoặc theo lịch, và mở rộng về sau sang nhận biết/thao tác màn hình desktop.

Hệ thống không chỉ là chatbot trả lời văn bản, mà là một **agent runtime có khả năng hành động** thông qua tool, memory, RAG, browser automation, sandbox và connector bên ngoài.

Định hướng kiến trúc lấy cảm hứng từ các agent runtime như Claude Code:

* Agent loop trung tâm.
* Tool execution có kiểm soát.
* Permission/HITL gate.
* Logging append-only.
* Context management.
* Memory management.
* Sandbox execution.
* Undo/rollback khi khả thi.
* Local persistence.
* Browser-first, desktop-later.

Tuy nhiên, hệ thống này là trợ lý cá nhân tổng quát, không chỉ phục vụ coding.

---

## 2. Định nghĩa “local-first”

Local-first không có nghĩa là toàn bộ model bắt buộc chạy local. Trong phạm vi sản phẩm này, local-first được hiểu là:

* Dữ liệu gốc của người dùng được lưu local.
* Database, logs, memory, document index, embedding metadata và session history được lưu local.
* File upload được lưu trong local file store.
* Tool execution chạy trong môi trường local hoặc sandbox local.
* Chỉ gửi dữ liệu ra ngoài khi cần gọi:

  * OpenAI API.
  * OpenRouter API.
  * Web search API.
  * Google API.
  * Dịch vụ bên ngoài mà người dùng đã cấp quyền.
* Dữ liệu nhạy cảm phải được kiểm tra, redact hoặc yêu cầu xác nhận trước khi gửi ra ngoài.
* Người dùng có quyền cấu hình provider nào được phép nhận loại dữ liệu nào.

Ví dụ policy dữ liệu:

| Loại dữ liệu               | Được gửi tới model/API ngoài? | Điều kiện                                    |
| -------------------------- | ----------------------------: | -------------------------------------------- |
| Chat thông thường          |                            Có | Theo provider đã cấu hình                    |
| Tài liệu RAG               |                  Có điều kiện | Cần bật cloud reasoning hoặc xác nhận        |
| Email                      |                  Có điều kiện | Cần policy hoặc xác nhận                     |
| Screenshot màn hình        |                  Có điều kiện | Cần cảnh báo vì có thể chứa dữ liệu nhạy cảm |
| Secret/API key/private key |                         Không | Trừ override đặc biệt                        |
| Sandbox output             |                  Có điều kiện | Cần kiểm tra dữ liệu nhạy cảm                |

---

## 3. Nguyên tắc thiết kế cốt lõi

### 3.1 Human-in-the-Loop là bắt buộc

Mọi hành động có rủi ro phải được kiểm tra qua permission system và có thể yêu cầu người dùng xác nhận trước khi thực thi.

Agent không được tự ý thực hiện các hành động nguy hiểm như:

* Gửi email.
* Xóa file.
* Xóa email.
* Submit form.
* Upload dữ liệu cá nhân.
* Chạy shell command destructive.
* Đọc file nhạy cảm ngoài phạm vi được cấp quyền.
* Gửi dữ liệu nhạy cảm ra ngoài.
* Thao tác desktop có nguy cơ ảnh hưởng dữ liệu thật.

### 3.2 Deny-first safety model

Nếu một hành động khớp cả rule cho phép và rule chặn, rule chặn phải thắng.

Thứ tự ưu tiên:

```text
deny > ask_strong > ask > allow
```

### 3.3 Model không phải root user

Model chỉ được đề xuất hành động dưới dạng tool call. Model không được trực tiếp gọi API, shell, browser, file system hoặc Google service.

Mọi tool call phải đi qua:

```text
Tool schema validation
→ Risk classifier
→ Permission engine
→ HITL approval nếu cần
→ Sandbox/execution boundary
→ Tool executor
→ Verifier nếu có
→ Audit logging
→ Rollback/snapshot nếu khả thi
```

### 3.4 Dữ liệu không tin cậy không được điều khiển agent

Nội dung đến từ web, email, file upload, RAG, browser page, OCR, screenshot hoặc tài liệu bên ngoài phải được xem là **untrusted data**.

Untrusted data không được phép:

* Ghi đè system instruction.
* Thay đổi permission policy.
* Tự cấp quyền cho tool.
* Yêu cầu agent bỏ qua HITL.
* Ép agent gửi dữ liệu nhạy cảm.
* Chỉ đạo agent chạy shell/API/file operation nguy hiểm.

### 3.5 RAG không được suy đoán

Khi trả lời dựa trên tài liệu, hệ thống bắt buộc trích dẫn nguồn.

Nếu retrieval không đủ bằng chứng, hệ thống phải trả lời:

```text
Không tìm thấy tài liệu phù hợp.
```

Không được bịa nội dung hoặc suy đoán ngoài dữ liệu truy xuất.

### 3.6 Browser-first, Desktop-later

Tự động hóa trình duyệt được triển khai trước vì dễ quan sát, kiểm soát, log và verify hơn desktop automation.

Desktop automation chỉ triển khai sau khi các phần sau ổn định:

* Agent runtime.
* Permission/HITL.
* Logging.
* RAG.
* Browser automation.
* Sandbox.
* Memory.

---

## 4. Phạm vi sản phẩm theo tầng

Để tránh scope quá rộng, hệ thống được chia thành các tầng năng lực.

### 4.1 Core Foundation

Bắt buộc có từ giai đoạn đầu:

* Desktop chat UI.
* Model provider abstraction.
* Local database.
* Local file store.
* Basic event log.
* RAG có citation.
* Tool registry.
* Permission/HITL.
* Agent loop cơ bản.

### 4.2 Personal Knowledge Layer

Bao gồm:

* Document library.
* RAG theo từng file hoặc toàn bộ tài liệu.
* Hybrid search.
* Reranking.
* Versioning.
* Metadata.
* Memory CRUD.
* Memory provenance.
* Search memory.
* Delete/export memory.

### 4.3 Action Layer

Bao gồm:

* File tools.
* Browser automation.
* Gmail/Drive/Docs/Sheets connectors.
* Sandbox Python/Shell.
* Scheduler.
* News summarization.

### 4.4 Desktop Intelligence Layer

Triển khai sau:

* Screen perception.
* OCR.
* Vision model.
* Accessibility tree.
* Active window tracking.
* Desktop control.
* Mouse/keyboard automation.
* App-specific adapters.

Desktop intelligence phải tách thành hai phần:

```text
Desktop Perception:
Agent nhìn, đọc, tóm tắt màn hình nhưng chưa tự thao tác.

Desktop Control:
Agent click, gõ, kéo thả hoặc điều khiển ứng dụng.
```

---

## 5. Kiến trúc tổng thể

### 5.1 Sơ đồ mức cao

```text
Desktop App / Chat UI
        |
Local API Server
        |
Agent Orchestrator
        |
+------------------------------------------+
| Agent Runtime                            |
| - Agent Loop                             |
| - Planner / Executor                     |
| - Context Manager                        |
| - Memory Manager                         |
| - Model Provider Layer                   |
+------------------------------------------+
        |
Tool Call Proposal
        |
Tool Schema Validator
        |
Risk Classifier
        |
Policy Engine / HITL Gate
        |
Tool Router / Tool Registry
        |
+------------------------------------------------------+
| RAG Tools       | Browser Tools | Google Tools       |
| File Tools      | Web Search    | Sandbox Tools      |
| Memory Tools    | Scheduler     | Desktop Tools      |
+------------------------------------------------------+
        |
Verifier / Result Checker
        |
Audit Log / Event Store / Rollback Manager
        |
Local Database + Vector Index + Local File Store
```

### 5.2 Module đề xuất

```text
apps/
  desktop/
  api-server/

packages/
  agent-runtime/
  model-providers/
  context-manager/
  permission-engine/
  risk-classifier/
  tool-registry/
  memory-store/
  rag-engine/
  browser-automation/
  desktop-perception/
  desktop-control/
  google-connectors/
  sandbox-runner/
  scheduler/
  audit-log/
  rollback-manager/
  security/
  shared-types/
```

Hoặc với Python-heavy architecture:

```text
app/
  ui/
  api/
  agent/
  models/
  tools/
  permissions/
  risk/
  rag/
  memory/
  sandbox/
  connectors/
  browser/
  desktop/
  scheduler/
  db/
  security/
```

---

## 6. Agent Runtime

### 6.1 Vòng đời xử lý yêu cầu

```text
User input
→ Input parser
→ Context assembly
→ Model call
→ Tool call proposal
→ Tool schema validation
→ Risk classification
→ Permission check
→ Human approval nếu cần
→ Execute tool
→ Verify result nếu có
→ Log result
→ Update memory/context
→ Continue loop nếu cần
→ Final answer
```

### 6.2 Input được hỗ trợ

Hệ thống hướng tới hỗ trợ:

* Text.
* File.
* Ảnh.
* Audio.
* Video.

Tuy nhiên, để giảm scope, phase đầu nên ưu tiên:

```text
Phase đầu:
- Text.
- File.
- Ảnh cơ bản.

Phase sau:
- Audio transcription.
- Video frame sampling.
- Video/audio summarization.
```

### 6.3 Multimodal input pipeline

Mỗi loại input cần được chuẩn hóa thành context có cấu trúc:

```text
Text → message context
Image → OCR / vision description / metadata
Audio → transcription / speaker metadata nếu có
Video → sampled frames / transcript / scene summary
File → parsed content / metadata / document chunks
```

### 6.4 Model provider abstraction

Hệ thống phải hỗ trợ ít nhất:

* OpenAI API.
* OpenRouter API.

Nên thiết kế abstraction:

```ts
interface LLMProvider {
  chat(messages, tools, options): Promise<ModelResponse>
  embed?(texts): Promise<Embedding[]>
  transcribe?(audio): Promise<Text>
  vision?(image): Promise<VisionResult>
}
```

Cần normalize tool calling vì mỗi provider có format khác nhau.

### 6.5 Context Manager

Context manager chịu trách nhiệm:

* Gom message gần đây.
* Thêm short-term memory.
* Thêm relevant long-term memory.
* Thêm RAG context nếu cần.
* Thêm trạng thái browser/desktop nếu liên quan.
* Cắt hoặc nén context khi vượt giới hạn.
* Không đưa dữ liệu nhạy cảm vào model nếu chưa được phép.
* Phân biệt trusted instruction và untrusted content.
* Ước lượng chi phí token trước khi gọi model nếu task lớn.

### 6.6 Agent loop

Agent loop là deterministic harness. Model chỉ ra quyết định cục bộ.

Ví dụ tool call chuẩn hóa:

```json
{
  "tool": "browser.click",
  "args": {
    "target": "button with text 'Submit'"
  },
  "reason": "User asked to submit the completed form."
}
```

---

## 7. Permission System, Risk Classifier và HITL

### 7.1 Mục tiêu

Permission system phải phân loại mức độ rủi ro của từng hành động và quyết định:

* Cho phép.
* Cho phép nhưng log.
* Yêu cầu xác nhận.
* Yêu cầu xác nhận mạnh.
* Từ chối.

Risk classifier không được phụ thuộc hoàn toàn vào model. Nó cần kết hợp:

* Static tool metadata.
* Argument analysis.
* Resource sensitivity.
* User policy.
* Current execution context.
* Model-provided reason.
* Deny rules.

### 7.2 Các cấp độ an toàn

#### Cấp 0 — Read-only / rủi ro thấp

Có thể cho phép mặc định, vẫn phải log.

Ví dụ:

* Đọc memory.
* Tìm kiếm trong RAG.
* Đọc file trong workspace.
* Đọc metadata tài liệu.
* Đọc DOM trang web.
* Đọc trạng thái browser.
* Tóm tắt tài liệu.
* Đọc email metadata nếu người dùng đã cấp quyền.

#### Cấp 1 — Tác động nhẹ

Có thể cho phép tự động tùy setting, nhưng phải log rõ.

Ví dụ:

* Tạo draft email.
* Tạo file nháp.
* Tạo note nội bộ.
* Gắn label email.
* Chạy web search.
* Tạo bản tóm tắt tin tức.
* Tạo document local chưa gửi/chưa upload.

#### Cấp 2 — Thay đổi dữ liệu

Phải hỏi người dùng trước khi thực thi.

Ví dụ:

* Gửi email.
* Sửa file.
* Upload file lên Drive.
* Sửa Google Docs.
* Cập nhật Google Sheets.
* Submit form web.
* Đặt lịch.
* Cài package.
* Chạy shell command có ghi file.

#### Cấp 3 — Nguy hiểm / khó undo

Cần xác nhận mạnh, hiển thị rõ hậu quả.

Ví dụ:

* Xóa file.
* Xóa email.
* Xóa tài liệu Drive.
* Chạy lệnh shell destructive.
* Đọc file ngoài thư mục project.
* Gửi email cho nhiều người.
* Thay đổi dữ liệu hàng loạt.
* Truy cập credentials/secrets.
* Submit giao dịch hoặc biểu mẫu quan trọng.

#### Cấp 4 — Chặn mặc định

Bị từ chối nếu không có cơ chế override đặc biệt.

Ví dụ:

* `rm -rf` trên thư mục rộng.
* Format disk.
* Đọc private key không được phép.
* Gửi dữ liệu nhạy cảm ra ngoài.
* Tự động thanh toán.
* Tự động đăng bài công khai.
* Tự động gửi mass email.
* Cố gắng vượt sandbox.

### 7.3 Policy schema đề xuất

```yaml
rules:
  - id: deny_secrets_to_external
    when:
      data_contains: ["api_key", "private_key", "password"]
      destination: ["external_model", "web", "email"]
    decision: deny

  - id: ask_before_send_email
    when:
      tool: "gmail.send_email"
    decision: ask_strong

  - id: allow_rag_read
    when:
      tool: "rag.search"
    decision: allow

  - id: ask_before_shell_write
    when:
      tool: "sandbox.shell"
      command_effect: ["write", "network", "delete"]
    decision: ask
```

Permission result:

```json
{
  "decision": "ask_strong",
  "risk_level": 3,
  "matched_rules": ["ask_before_send_email"],
  "explanation": "This action sends an external email and cannot be fully undone."
}
```

### 7.4 HITL UI

Khi cần xác nhận, UI phải hiển thị:

* Agent muốn làm gì.
* Tool nào sẽ được gọi.
* Dữ liệu nào bị đọc/sửa/gửi/xóa.
* Lý do agent đưa ra.
* Rủi ro.
* Có rollback được không.
* Preview/diff nếu có.
* Nút: Approve, Edit, Deny, Allow once, Allow for this session.

Ví dụ:

```text
Agent muốn gửi email tới alice@example.com.

Subject:
Báo cáo tuần này

Hành động này không thể undo hoàn toàn sau khi gửi.

[Approve] [Edit Draft] [Cancel]
```

---

## 8. Logging, Audit và Rollback

### 8.1 Logging bắt buộc

Hệ thống phải log toàn bộ hành động theo kiểu append-only.

Các loại event cần log:

* User input.
* Model response.
* Tool call.
* Tool result.
* Permission decision.
* User approval/rejection.
* File diff.
* Browser action.
* Email draft/send.
* Google Drive/Docs/Sheets action.
* Memory create/update/delete.
* RAG retrieval result.
* Sandbox command.
* Error và retry.

### 8.2 Event schema đề xuất

```json
{
  "id": "evt_001",
  "session_id": "sess_123",
  "timestamp": "2026-01-01T10:00:00Z",
  "actor": "agent",
  "tool": "file.write_patch",
  "args_hash": "sha256:...",
  "risk_level": "medium",
  "permission_decision": "approved",
  "approved_by_user": true,
  "before_state_ref": "snapshot_001",
  "after_state_ref": "snapshot_002",
  "result": "success"
}
```

### 8.3 Rollback và compensating action

Không phải hành động nào cũng rollback được. Hệ thống phải phân biệt:

```text
Rollback:
Khôi phục gần đúng trạng thái trước đó.

Compensating action:
Không thể undo thật, chỉ thực hiện hành động bù.
```

| Action                  | Rollback thật? | Cách xử lý                         |
| ----------------------- | -------------: | ---------------------------------- |
| Sửa file local          |             Có | Restore snapshot/diff              |
| Update memory           |             Có | Restore previous record            |
| Rename Drive file       |         Có thể | Rename lại                         |
| Move Drive file         |         Có thể | Move lại                           |
| Gửi email               |          Không | Gửi email đính chính nếu cần       |
| Submit form             |     Không chắc | Tạo compensating request nếu có    |
| Upload dữ liệu ra ngoài |     Không chắc | Xóa upload nếu API cho phép        |
| Xóa vĩnh viễn           |          Không | Chỉ rollback nếu có trash/snapshot |

Tool metadata nên có:

```json
{
  "rollback_type": "reversible | snapshot_required | compensating_only | irreversible",
  "rollback_supported": true,
  "rollback_plan": "Restore previous file snapshot.",
  "requires_strong_confirm": false
}
```

Với hành động irreversible hoặc compensating-only, hệ thống phải yêu cầu xác nhận mạnh.

---

## 9. Memory System

### 9.1 Các loại memory

Hệ thống cần hỗ trợ:

* Short-term memory.
* Episodic memory.
* Semantic memory.
* Procedural memory.
* Long-term memory.

Trong đó, **long-term memory** là lớp tổng cho các memory lưu bền vững, bao gồm episodic, semantic và procedural memory.

### 9.2 Short-term memory

Lưu trạng thái phiên hiện tại:

* Current task.
* Recent messages.
* Active files.
* Current browser state.
* Temporary decisions.
* Tool results gần đây.

### 9.3 Episodic memory

Lưu sự kiện đã xảy ra:

* Người dùng yêu cầu gì.
* Agent đã làm gì.
* Tool nào đã được gọi.
* Kết quả ra sao.
* File/email/doc nào đã tạo hoặc chỉnh sửa.
* Session nào liên quan.

### 9.4 Semantic memory

Lưu tri thức bền vững:

* Sở thích của người dùng.
* Quy ước làm việc.
* Thông tin dự án.
* Facts đã xác nhận.
* Cấu trúc tài liệu thường gặp.

### 9.5 Procedural memory

Lưu workflow hoặc quy trình thường dùng:

* Cách người dùng muốn tóm tắt tin tức.
* Quy trình tạo báo cáo tuần.
* Cách xử lý email từ một nhóm người.
* Các bước chuẩn để phân tích file Excel.
* Template thao tác lặp lại.

### 9.6 Memory management UI

Người dùng phải có thể:

* Xem memory.
* Tìm kiếm memory.
* Sửa memory.
* Xóa memory.
* Disable memory.
* Export memory.
* Xem nguồn gốc memory.
* Xem lần cuối memory được dùng.

Mỗi memory nên có metadata:

```json
{
  "id": "mem_001",
  "type": "semantic",
  "content": "User prefers concise Vietnamese summaries.",
  "source": "conversation:sess_123",
  "confidence": 0.9,
  "created_at": "...",
  "updated_at": "...",
  "last_used_at": "..."
}
```

### 9.7 Memory safety

Memory không được tự động lưu:

* Password.
* OTP.
* API keys.
* Private keys.
* Token.
* Credit card.
* Nội dung nhạy cảm nếu user chưa cho phép.

Memory phải có provenance để người dùng biết thông tin đó đến từ đâu.

---

## 10. RAG System

### 10.1 File upload được hỗ trợ

RAG phải hỗ trợ upload:

* `.txt`
* `.pdf`
* `.docx`
* `.xlsx`
* `.md`

Phase sau có thể mở rộng:

* `.pptx`
* `.csv`
* image OCR
* scanned PDF OCR

### 10.2 Chức năng bắt buộc

RAG system phải hỗ trợ:

* Hỏi đáp riêng theo từng file.
* Hỏi đáp trên toàn bộ tài liệu.
* Bắt buộc trích dẫn nguồn.
* Một người dùng, không cần phân quyền nhiều user.
* Cập nhật index tự động khi file thay đổi.
* Xóa tài liệu.
* Xóa embedding hoàn toàn.
* Trả lời “Không tìm thấy tài liệu phù hợp” thay vì suy đoán.
* Hybrid search.
* Reranking.
* Versioning.
* Metadata.

### 10.3 RAG pipeline

```text
Upload file
→ Parse
→ Extract metadata
→ Chunk
→ Embed
→ Keyword index
→ Vector index
→ Store document version
→ Ready for retrieval
```

### 10.4 Query pipeline

```text
User question
→ Determine scope: single file / all docs
→ Hybrid search: keyword + vector
→ Rerank
→ Select evidence chunks
→ Check evidence threshold
→ If no strong evidence: "Không tìm thấy tài liệu phù hợp."
→ Generate answer with citations
→ Verify citation coverage
→ Return answer
```

### 10.5 Retrieval threshold

Cần có:

* Retrieval score threshold.
* Rerank score threshold.
* Minimum evidence count.
* Citation coverage check.
* Answer grounding verifier.

Nếu evidence yếu hoặc không đủ, hệ thống không được gọi model để suy đoán.

### 10.6 Citation requirement

Mọi câu trả lời dựa trên tài liệu phải có citation gồm:

* File name.
* Page hoặc sheet nếu có.
* Chunk hoặc section.
* Link/local reference nếu có.
* Document version nếu cần.

Ví dụ:

```text
Theo tài liệu `contract.pdf`, version 3, trang 4, điều khoản 2.1, ...
```

### 10.7 Versioning

Khi file thay đổi:

* Không ghi đè version cũ ngay lập tức.
* Tạo document version mới.
* Re-index version mới.
* Đánh dấu version hiện tại.
* Cho phép audit lịch sử index.
* Cho phép so sánh version nếu cần.

### 10.8 Deletion

Khi người dùng xóa tài liệu khỏi hệ thống:

* Xóa document record.
* Xóa chunks.
* Xóa embeddings.
* Xóa keyword index.
* Xóa file local nếu người dùng yêu cầu.
* Log sự kiện xóa.
* Cho phép verify rằng embedding đã bị xóa.

---

## 11. Browser Automation

### 11.1 Ưu tiên triển khai

Browser automation phải được triển khai trước desktop automation.

Khuyến nghị dùng Playwright.

### 11.2 Browser tools tối thiểu

```text
browser.open
browser.observe
browser.click
browser.type
browser.extract
browser.screenshot
browser.wait
browser.download
browser.upload
browser.close
```

### 11.3 Browser state

Agent có thể nhận biết:

* URL hiện tại.
* Title trang.
* DOM.
* Accessibility tree.
* Screenshot.
* Form fields.
* Download/upload state.
* Visible text.
* Errors hoặc alerts.

### 11.4 Nguyên tắc thao tác

Ưu tiên thao tác có cấu trúc:

```json
{
  "tool": "browser.click",
  "args": {
    "target": "button with text 'Submit'"
  }
}
```

Hạn chế thao tác bằng tọa độ:

```json
{
  "tool": "mouse.click",
  "args": {
    "x": 683,
    "y": 421
  }
}
```

### 11.5 Browser profile và login policy

Cần có:

* Browser profile riêng cho agent.
* Cookie/session store riêng.
* Không dùng browser profile cá nhân mặc định.
* Domain allowlist/blocklist.
* Download folder riêng.
* Upload chỉ từ file được cấp quyền.
* Credential không đi qua LLM.
* 2FA/CAPTCHA do người dùng xử lý.
* Không expose raw cookies cho model.

### 11.6 Permission cho browser

Cần HITL cho:

* Submit form.
* Gửi dữ liệu cá nhân.
* Đăng nhập.
* Thanh toán.
* Upload file.
* Download file lớn hoặc không rõ nguồn.
* Thao tác có thể thay đổi dữ liệu tài khoản.
* Gửi message/post/comment.

### 11.7 Post-action verifier

Không chỉ log “click thành công”. Mỗi action quan trọng cần verifier.

Ví dụ:

```text
Action: click "Download"
Expected result: file appears in download folder
Verifier: check download event + file exists

Action: submit form
Expected result: confirmation page
Verifier: URL/text/status changed

Action: upload file
Expected result: upload complete indicator
Verifier: DOM state + file name visible
```

---

## 12. Desktop Automation

### 12.1 Triển khai sau

Desktop automation chỉ triển khai sau khi các phần sau ổn định:

* Agent loop.
* Permission/HITL.
* Logging.
* RAG.
* Browser automation.
* Sandbox.
* Memory.

### 12.2 Tách thành Desktop Perception và Desktop Control

#### Desktop Perception

Agent có thể:

* Chụp màn hình.
* Đọc OCR.
* Tóm tắt màn hình.
* Phát hiện active window.
* Đọc accessibility tree nếu có.
* Nhận biết UI state.
* Đưa ra hướng dẫn cho người dùng.

Không tự click/gõ.

#### Desktop Control

Agent có thể:

* Điều khiển chuột.
* Điều khiển bàn phím.
* Dùng clipboard.
* Mở app.
* Chuyển cửa sổ.
* Thao tác trên app desktop.

Desktop Control phải có HITL mạnh và verifier sau mỗi bước rủi ro.

### 12.3 Real-time screen awareness

Không nên gửi toàn bộ màn hình liên tục vào model.

Nên dùng event-based pipeline:

```text
Screen monitor
→ Detect changes
→ Capture screenshot
→ Extract OCR/accessibility tree
→ Summarize state
→ Mask sensitive data
→ Send relevant summary to agent
```

### 12.4 Mức độ triển khai desktop

```text
Level 1:
Analyze current screen on demand.

Level 2:
Observe active window periodically.

Level 3:
Suggest next action but user tự thao tác.

Level 4:
Agent click/gõ từng bước có approval.

Level 5:
Agent chạy workflow dài có verifier và rollback strategy.
```

MVP desktop nên bắt đầu từ Level 1 hoặc Level 2.

### 12.5 Privacy protection

Phải mask hoặc chặn:

* Password.
* OTP.
* API keys.
* Private keys.
* Credit card.
* Token.
* Sensitive personal data.

---

## 13. Web Search và Tin tức

### 13.1 Chức năng

Hệ thống phải hỗ trợ:

* Tóm tắt tin tức theo yêu cầu.
* Tóm tắt tin tức theo lịch trình.
* Web search theo yêu cầu.
* So sánh nhiều nguồn.
* Trích link gốc.

### 13.2 News pipeline

```text
Schedule / user request
→ Search multiple sources
→ Deduplicate
→ Cluster topics
→ Compare sources
→ Summarize
→ Attach original links
→ Store report
→ Notify user nếu cần
```

### 13.3 Yêu cầu chất lượng

Khi tóm tắt tin tức:

* Ghi rõ ngày/giờ nguồn nếu có.
* So sánh nhiều nguồn.
* Phân biệt fact và nhận định.
* Nêu rõ nếu nguồn mâu thuẫn.
* Luôn có link gốc.
* Không dùng thông tin cũ cho câu hỏi “mới nhất”.
* Log nguồn đã dùng.

---

## 14. Gmail / Drive / Docs / Sheets Integration

### 14.1 Nguyên tắc chung

Agent được phép đọc, viết, sửa, gửi email, tạo file và xóa file, nhưng tất cả hành động rủi ro phải đi qua permission/HITL.

Ưu tiên dùng API chính thức trước browser/desktop automation.

```text
1. Official API
2. Browser automation
3. Desktop automation
```

### 14.2 Gmail

Chức năng bắt buộc:

* Tìm email.
* Đọc email.
* Tóm tắt thread.
* Viết nháp.
* Gửi mail.
* Phân loại email.
* Gắn label.
* Tải attachment.
* Đính kèm file.

Permission đề xuất:

```text
Đọc email: allow sau khi user cấp quyền
Tạo draft: allow hoặc ask lần đầu
Gửi email: always ask
Xóa email: strong confirm
Tải attachment: ask nếu file lớn hoặc nhạy cảm
Gửi nhiều người: strong confirm
```

### 14.3 Drive

Chức năng bắt buộc:

* Tìm file.
* Đọc file.
* Tải file.
* Upload file.
* Di chuyển file.
* Đổi tên file.
* Xóa file.

Permission đề xuất:

```text
Tìm/đọc file: allow hoặc ask lần đầu
Upload/move/rename: ask
Delete: strong confirm
```

### 14.4 Google Docs

Chức năng bắt buộc:

* Tạo tài liệu.
* Chỉnh sửa tài liệu.
* Tóm tắt tài liệu.
* Xuất file.

Permission đề xuất:

```text
Tóm tắt: allow
Tạo tài liệu: ask hoặc allow tùy setting
Chỉnh sửa: ask + preview/diff
Xuất file: allow hoặc ask tùy nơi lưu
```

### 14.5 Google Sheets

Chức năng bắt buộc:

* Đọc bảng.
* Cập nhật ô.
* Tạo báo cáo.
* Chạy phân tích.

Permission đề xuất:

```text
Đọc bảng: allow hoặc ask lần đầu
Phân tích: allow
Cập nhật ô: ask + preview diff
Thay đổi hàng loạt: strong confirm
```

---

## 15. Sandbox Execution

### 15.1 Chức năng bắt buộc

Sandbox phải hỗ trợ:

* Chạy Python.
* Chạy Shell.
* Có internet khi được phép.
* Giới hạn file system.
* Giới hạn CPU.
* Giới hạn RAM.
* Timeout.
* Cho phép cài package khi được phép.
* Đọc file ngoài thư mục project khi người dùng cho phép.
* Hiển thị code/lệnh trước khi chạy.
* Capture stdout/stderr.
* Capture file output/artifacts.

### 15.2 Sandbox modes

Nên chia sandbox thành nhiều mode:

```text
Mode A — Safe Python:
Không internet, chỉ workspace, timeout ngắn.

Mode B — Data Analysis:
Được đọc file được chọn, không shell destructive.

Mode C — Network:
Có internet, cần approval.

Mode D — Shell:
Cho phép shell command, cần risk classification.

Mode E — Elevated/Manual:
Đọc ngoài workspace hoặc thao tác hệ thống, cần strong confirm.
```

### 15.3 Sandbox boundary

Nên chạy trong container hoặc process sandbox riêng.

Yêu cầu:

* Workspace mount riêng.
* Network policy.
* CPU limit.
* RAM limit.
* Timeout.
* Package install cache.
* stdout/stderr capture.
* File diff capture.
* Artifact output.
* Không truy cập toàn bộ file system mặc định.
* Không mount secrets mặc định.
* Không cho sandbox đọc token/API key nếu không có approval.

### 15.4 Shell command risk classification

Không chỉ classify theo command name. Cần phân tích cả argument, path, redirection, script và network.

Ví dụ phân loại ban đầu:

```text
Read-only:
  ls, cat, grep, rg, pwd

Write:
  touch, mkdir, sed -i, npm install, pip install

Network:
  curl, wget, git clone, pip install, npm install

Dangerous:
  rm, chmod, chown, sudo, ssh, scp, dd, mkfs
```

Tuy nhiên, các lệnh như `python script.py` hoặc `node script.js` cũng có thể nguy hiểm, nên cần:

* Static command analysis.
* Path allowlist.
* Network policy.
* Filesystem diff.
* Timeout.
* Resource limit.
* Container isolation.
* Post-run artifact scan.

### 15.5 HITL cho sandbox

Cần hỏi người dùng khi:

* Chạy command ghi file.
* Cài package.
* Dùng network.
* Đọc file ngoài workspace.
* Xóa file.
* Chạy lệnh có quyền cao.
* Command chứa pattern nguy hiểm.
* Script chưa rõ tác động.

---

## 16. UI Desktop App

### 16.1 Nền tảng đề xuất

Có thể dùng:

* Tauri + React.
* Electron + React.
* Flutter Desktop.
* Qt/PySide.

Khuyến nghị:

* Tauri + React nếu ưu tiên nhẹ và bảo mật.
* Electron + React nếu ưu tiên tốc độ phát triển và ecosystem JavaScript/Playwright.
* Electron + React + Python/FastAPI backend là lựa chọn thực tế nếu muốn phát triển nhanh.
* Tauri + React + Python/Rust sidecar phù hợp nếu ưu tiên lâu dài, nhẹ và an toàn hơn.

### 16.2 UI chính

Desktop app phải có Chat UI giống ChatGPT.

Các panel nên có:

* Chat.
* Tool activity timeline.
* Permission approvals.
* Memory manager.
* Documents/RAG library.
* Browser session viewer.
* Logs/Audit.
* Settings.
* Sandbox output.
* Scheduler/news reports.

### 16.3 Tool activity timeline

Người dùng phải thấy agent đang làm gì:

```text
Agent đang đọc 3 file.
Agent muốn sửa report.docx.
Agent muốn gửi email.
Agent đã tạo draft.
Agent đã cập nhật 12 embeddings.
Agent đang chạy Python trong sandbox.
```

### 16.4 Approval UI

Approval UI phải hỗ trợ:

* Xem tool call.
* Xem preview.
* Xem diff.
* Edit trước khi approve.
* Approve once.
* Approve for session.
* Deny.
* Cancel task.

---

## 17. Local Database và Storage

### 17.1 Stack đề xuất

Vì chỉ có một người dùng, có thể dùng:

* SQLite cho metadata, sessions, logs, memory.
* SQLite FTS5 cho keyword search.
* LanceDB hoặc Chroma cho vector embeddings.
* Local file store cho uploaded files.
* JSONL cho append-only event/session transcript.
* DuckDB cho phân tích bảng/xlsx.

### 17.2 Dữ liệu cần lưu

* User/settings dù chỉ một user.
* Sessions.
* Messages.
* Tool calls.
* Tool results.
* Permission decisions.
* Documents.
* Document versions.
* Chunks.
* Embeddings metadata.
* Memory.
* Browser sessions.
* Sandbox runs.
* Scheduled tasks.
* Audit events.
* Rollback snapshots.
* Provider usage/cost metadata.

### 17.3 Local file store

File upload nên được lưu local với metadata:

```json
{
  "file_id": "file_001",
  "original_name": "report.pdf",
  "local_path": "...",
  "mime_type": "application/pdf",
  "sha256": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

---

## 18. Tool Registry

### 18.1 Mục tiêu

Tất cả năng lực hành động của agent phải được đóng gói thành tool có schema rõ ràng.

### 18.2 Tool metadata

Mỗi tool cần có:

```json
{
  "name": "gmail.send_email",
  "description": "Send an email through Gmail",
  "input_schema": {},
  "risk_level": "high",
  "requires_approval": true,
  "rollback_supported": false,
  "rollback_type": "irreversible",
  "logs_sensitive_args": false
}
```

### 18.3 Tool categories

Tool categories:

* File tools.
* RAG tools.
* Memory tools.
* Browser tools.
* Desktop tools.
* Gmail tools.
* Drive tools.
* Docs tools.
* Sheets tools.
* Web search tools.
* News tools.
* Sandbox tools.
* Scheduler tools.

---

## 19. Security và Privacy

### 19.1 Sensitive data detection

Hệ thống nên phát hiện và mask:

* Password.
* OTP.
* API key.
* Secret token.
* Private key.
* Credit card.
* Personal ID.
* Email nhạy cảm.
* File nhạy cảm.

### 19.2 External data policy

Trước khi gửi dữ liệu ra model provider hoặc API bên ngoài, cần kiểm tra:

* Dữ liệu có nhạy cảm không.
* User đã cấp quyền chưa.
* Có cần redact không.
* Có thể dùng local summary thay vì raw content không.

### 19.3 Credentials

Credentials phải được lưu an toàn:

* OS keychain nếu có.
* Encrypted local storage.
* Không ghi secret vào log.
* Không đưa secret vào model context.
* Không cho sandbox đọc secret mặc định.
* Không expose cookies/token cho model.

### 19.4 Threat model

Hệ thống phải xử lý các rủi ro sau:

* Prompt injection từ web/email/tài liệu.
* Tool injection qua nội dung file.
* Secret leakage vào model context.
* Sandbox escape.
* Agent tự gửi dữ liệu nhạy cảm.
* Browser thao tác nhầm domain.
* Desktop automation click nhầm.
* Memory lưu sai hoặc lưu secret.
* Audit log chứa dữ liệu nhạy cảm.
* Model hallucinate file/path/tool.

---

## 20. Scheduler và Automation

### 20.1 Chức năng

Scheduler hỗ trợ:

* Tóm tắt tin tức theo lịch.
* Tóm tắt email theo lịch.
* Kiểm tra file/document thay đổi.
* Re-index tài liệu khi file thay đổi.
* Chạy workflow định kỳ.

### 20.2 Điều kiện an toàn

Scheduled task không được tự động thực hiện hành động nguy hiểm nếu chưa có permission policy rõ.

Ví dụ:

* Được phép tạo báo cáo tin tức.
* Được phép tạo draft email.
* Không được tự gửi email nếu chưa có rule explicit.
* Không được xóa file theo lịch nếu không xác nhận mạnh.

---

## 21. Error Handling và Reliability

### 21.1 Failure modes cần xử lý

* Tool call sai schema.
* Tool execution lỗi.
* Model hallucinate file/path.
* RAG không tìm thấy tài liệu.
* Browser element không tồn tại.
* Google API lỗi quyền.
* Sandbox timeout.
* Memory conflict.
* Context quá dài.
* Provider API lỗi.
* Permission policy conflict.
* Tool verifier thất bại.

### 21.2 Recovery

Hệ thống nên có:

* Retry có giới hạn.
* Ask user khi thiếu thông tin.
* Dừng task khi vượt budget.
* Tóm tắt lỗi rõ ràng.
* Không lặp vô hạn.
* Rollback partial changes nếu có thể.
* Escalate sang HITL nếu không chắc.

### 21.3 Budget control

Mỗi task nên có giới hạn:

* Số tool calls.
* Thời gian chạy.
* Chi phí API.
* Số file đọc.
* Số lần retry.
* Số command sandbox.
* Số browser actions.

Cần có:

* Daily/monthly API budget.
* Per-task token limit.
* Provider routing policy.
* Model fallback.
* Warning khi task có thể tốn nhiều chi phí.
* Log chi phí theo session/tool.

---

## 22. Evaluation Framework

Cần có evaluation framework từ sớm để đo chất lượng và an toàn.

### 22.1 RAG eval

* Citation accuracy.
* Retrieval precision/recall.
* Answer faithfulness.
* Tỷ lệ trả lời đúng “Không tìm thấy tài liệu phù hợp”.
* Khả năng không suy đoán khi evidence yếu.

### 22.2 Tool eval

* Tool call đúng schema.
* Permission classification đúng.
* Không gọi tool nguy hiểm khi chưa approve.
* Không leak sensitive args vào log.
* Verifier phát hiện action thất bại.

### 22.3 Browser eval

* Hoàn thành task mẫu.
* Không submit khi chưa approve.
* Trace đầy đủ.
* Download/upload đúng thư mục.
* Không thao tác ngoài allowlist domain.

### 22.4 Memory eval

* Memory có nguồn gốc.
* Không lưu secret.
* Có thể xem/sửa/xóa/export.
* Không dùng memory sai ngữ cảnh.
* Memory conflict được phát hiện.

### 22.5 Sandbox eval

* Timeout hoạt động.
* CPU/RAM limit hoạt động.
* File system isolation hoạt động.
* Network policy hoạt động.
* Command nguy hiểm bị chặn hoặc yêu cầu xác nhận.

---

## 23. Data Retention và Deletion Policy

Người dùng phải có khả năng:

* Xóa session.
* Xóa audit attachment/screenshot.
* Xóa sandbox artifacts.
* Xóa browser traces.
* Xóa memory.
* Xóa document versions.
* Xóa embeddings.
* Xóa cached model responses.
* Export toàn bộ dữ liệu.
* Reset toàn bộ app.

Khi xóa dữ liệu, hệ thống phải log sự kiện xóa và có cơ chế verify nếu dữ liệu liên quan đến embedding/index.

---

## 24. MVP đề xuất

Để tránh scope quá rộng, MVP nên chia thành 3 mốc nhỏ.

### 24.1 MVP-A — Local RAG Chat

Mục tiêu: có sản phẩm dùng được cho hỏi đáp tài liệu local.

Bao gồm:

* Desktop hoặc local web chat UI.
* OpenAI/OpenRouter integration.
* SQLite local DB.
* Local file store.
* Upload TXT/PDF/MD trước.
* RAG có citation.
* Trả lời “Không tìm thấy tài liệu phù hợp” khi evidence yếu.
* Basic document library.
* Basic event log.
* Basic settings.

Chưa cần:

* Full agent loop.
* Browser automation.
* Gmail/Drive.
* Shell sandbox.
* Desktop automation.
* Full memory system.

### 24.2 MVP-B — Agent Core + Safety Foundation

Mục tiêu: biến chatbot thành agent runtime an toàn.

Bao gồm:

* Tool registry.
* Agent loop cơ bản.
* Tool schema validation.
* Risk classifier.
* Permission/HITL.
* Audit event log append-only.
* File read/write trong workspace.
* Undo file edit.
* Short-term memory.
* Episodic event log.
* Preview/diff trước write action.

### 24.3 MVP-C — Browser Automation Foundation

Mục tiêu: agent có thể thao tác web ở mức kiểm soát được.

Bao gồm:

* Playwright integration.
* browser.open / observe / extract / click / type.
* DOM/accessibility state.
* Screenshot.
* Browser trace.
* Approval trước submit/upload/download.
* Download folder riêng.
* Browser profile riêng.
* Domain allowlist/blocklist.
* Post-action verifier cơ bản.

Sau MVP-C mới nên mở rộng sang Google integrations, memory nâng cao, sandbox, scheduler/news và desktop automation.

---

## 25. Lộ trình triển khai

### Phase 1 — Local RAG Chat

* Desktop/local UI.
* Chat UI.
* OpenAI/OpenRouter integration.
* Upload TXT/PDF/MD.
* Parser cơ bản.
* Chunking.
* Embedding.
* Hybrid search cơ bản.
* Citation.
* Local DB.
* Basic logs.

### Phase 2 — RAG nâng cao

* DOCX/XLSX.
* Reranking.
* Versioning.
* Metadata.
* Auto re-index.
* Delete document + embeddings.
* Citation verifier.
* Retrieval threshold.

### Phase 3 — Agent Core + Safety

* Agent loop.
* Tool registry.
* Permission engine.
* Risk classifier.
* HITL UI.
* File read/write trong workspace.
* Undo file edit.
* Short-term memory.
* Episodic event log.

### Phase 4 — Browser Automation

* Playwright integration.
* Browser observe/click/type/extract.
* DOM/accessibility state.
* Screenshot.
* Approval trước submit form.
* Download/upload support.
* Browser profile riêng.
* Post-action verifier.

### Phase 5 — Google Integrations

* Gmail search/read/thread summary/draft/send/label/attachment.
* Drive search/read/upload/move/rename/delete.
* Docs create/edit/summarize/export.
* Sheets read/update/report/analyze.
* Strong permission cho send/delete/edit.

### Phase 6 — Memory đầy đủ

* Semantic memory.
* Procedural memory.
* Long-term memory.
* Memory provenance.
* Memory search.
* Memory edit/delete/disable/export UI.

### Phase 7 — Sandbox

* Python runner.
* Shell runner.
* Internet access khi được phép.
* Package install.
* CPU/RAM/time limit.
* Workspace isolation.
* Code display.
* stdout/stderr/artifacts.
* HITL cho command rủi ro.

### Phase 8 — News và Scheduler

* Web search.
* News summarization.
* Multi-source comparison.
* Original links.
* Scheduled reports.
* Notification.
* Budget control cho scheduled tasks.

### Phase 9 — Desktop Perception

* Screen monitor.
* OCR.
* Vision model integration.
* Accessibility API.
* Active window detection.
* Privacy masking.
* Screen summary read-only.

### Phase 10 — Desktop Control

* Mouse/keyboard control.
* Clipboard control.
* Window manager.
* App-specific adapters.
* HITL overlay.
* Post-action verifier.
* Strict risk policy.

---

## 26. Tiêu chí nghiệm thu

### 26.1 Agent Core

* Mọi tool call đều đi qua schema validation.
* Mọi tool call đều đi qua risk classifier.
* Hành động rủi ro yêu cầu HITL.
* Toàn bộ action được log.
* Có undo/rollback cho file local và memory.
* Hỗ trợ OpenAI và OpenRouter.
* Dữ liệu lưu local.
* Không expose secret vào model/log/sandbox.

### 26.2 RAG

* Upload được TXT/PDF/DOCX/XLSX/MD theo phase.
* Hỏi riêng từng file.
* Hỏi toàn bộ tài liệu.
* Câu trả lời có citation.
* Không tìm thấy thì nói không tìm thấy.
* Hybrid search hoạt động.
* Reranking hoạt động.
* File thay đổi thì index cập nhật.
* Xóa tài liệu thì embedding bị xóa hoàn toàn.
* Citation verifier hoạt động.

### 26.3 Browser Automation

* Mở website.
* Quan sát trang.
* Click/type/extract.
* Có screenshot hoặc DOM state.
* Cần approval trước submit/gửi dữ liệu.
* Log toàn bộ hành động browser.
* Browser profile riêng.
* Post-action verifier hoạt động.

### 26.4 Google Integrations

* Gmail: tìm, đọc, tóm tắt, draft, gửi, label, attachment.
* Drive: tìm, đọc, tải, upload, move, rename, delete.
* Docs: tạo, sửa, tóm tắt, export.
* Sheets: đọc, update cell, báo cáo, phân tích.
* Gửi/xóa/sửa dữ liệu thật luôn cần HITL.

### 26.5 Sandbox

* Chạy Python.
* Chạy Shell.
* Có internet khi được phép.
* Giới hạn CPU/RAM/time.
* Giới hạn file system.
* Hiển thị code/lệnh chạy.
* Log stdout/stderr.
* HITL cho lệnh rủi ro.
* Không mount secrets mặc định.

### 26.6 UI

* Desktop app hoặc local UI.
* Chat UI giống ChatGPT.
* Có tool activity timeline.
* Có approval panel.
* Có memory manager.
* Có RAG document library.
* Có logs/audit viewer.
* Có sandbox output panel.
* Có settings cho model provider, permission và data policy.

---

## 27. Rủi ro kỹ thuật chính

### 27.1 Scope quá rộng

Project bao gồm nhiều hệ thống lớn: agent runtime, RAG, browser automation, desktop automation, Google integrations, sandbox, memory, scheduler. Cần triển khai theo phase, không làm tất cả cùng lúc.

### 27.2 Model nhỏ không đủ mạnh

Nếu dùng local/small LLM hoặc model rẻ qua OpenRouter, tool design và context management phải rất tốt. Nên dùng tool nhỏ, output có cấu trúc, verification bắt buộc.

### 27.3 Permission thiếu chặt chẽ

Nếu permission system yếu, agent có thể gây hại dữ liệu thật. Permission/HITL phải làm sớm.

### 27.4 RAG hallucination

Nếu retrieval yếu nhưng model vẫn trả lời, hệ thống mất độ tin cậy. Cần threshold, citation bắt buộc và citation verifier.

### 27.5 Desktop automation nguy hiểm

Desktop automation có thể click nhầm, đọc nhầm, hoặc thao tác ngoài ý muốn. Chỉ triển khai sau browser automation và sandbox.

### 27.6 Logging chứa dữ liệu nhạy cảm

Audit log cần đầy đủ nhưng không được lưu secret/raw sensitive data không cần thiết. Cần redaction.

### 27.7 Sandbox escape hoặc command nguy hiểm

Shell/Python sandbox có internet và package install là rủi ro cao. Cần container isolation, file allowlist, network policy, resource limit và HITL.

### 27.8 Prompt injection

Web/email/tài liệu có thể chứa instruction độc hại. Cần tách trusted instruction khỏi untrusted content và không cho untrusted content điều khiển tool policy.

---

## 28. Ưu tiên triển khai

Thứ tự ưu tiên khuyến nghị:

```text
1. Model provider abstraction
2. Chat UI tối thiểu
3. Local DB + event log nền tảng
4. RAG có citation
5. Permission/HITL core
6. Tool registry
7. Risk classifier
8. Agent loop
9. File tools + undo local
10. Browser automation
11. Memory management
12. Google integrations
13. Sandbox
14. Scheduler/news
15. Desktop perception
16. Desktop control
```

---

## 29. Tóm tắt định hướng kiến trúc

Project nên được xây như một hệ thống:

```text
Claude-Code-like Agent Core
+ Local-first RAG Engine
+ Permission/HITL System
+ Risk Classifier
+ Audit/Rollback Layer
+ Browser Automation Runtime
+ Google Workspace Connectors
+ Local Memory System
+ Sandbox Execution
+ Desktop UI
+ Later: Desktop Perception and Control
```

Định hướng triển khai:

```text
single-agent first
local-first
HITL-first
RAG-with-citation-first
browser-first
desktop-perception-before-desktop-control
desktop-control-later
multi-agent-later
```

Thành công của hệ thống phụ thuộc chủ yếu vào:

* Permission system.
* Risk classification.
* Tool design.
* Context management.
* RAG quality.
* Logging/rollback reliability.
* Sandbox isolation.
* Secret handling.
* Browser verification.
* UI giúp người dùng quan sát và kiểm soát agent.
