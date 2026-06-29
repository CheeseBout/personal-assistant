"""Centralized prompt templates.

Single source of truth for system prompts used across the codebase. Keeping
them here avoids drift (and the mojibake / no-diacritic bugs from inlined
strings in earlier revisions).
"""


def build_rag_system_prompt(context: str) -> str:
    """System prompt for RAG-grounded chat.

    The context block lists retrieved chunks with their citations.
    """
    return (
        "Bạn là một trợ lý AI hữu ích. Hãy sử dụng thông tin từ các tài liệu được "
        "cung cấp dưới đây để trả lời câu hỏi của người dùng.\n\n"
        "Ngữ cảnh từ tài liệu:\n"
        f"{context}\n\n"
        "Hướng dẫn:\n"
        "1. Trả lời dựa trên ngữ cảnh được cung cấp.\n"
        "2. Nếu ngữ cảnh không chứa thông tin liên quan, hãy trả lời: "
        "\"Không tìm thấy thông tin phù hợp trong tài liệu.\"\n"
        "3. Luôn trích dẫn nguồn theo định dạng: [tên file] hoặc [tên file, chunk X].\n"
        "4. Không được bịa thông tin ngoài ngữ cảnh.\n"
        "5. Trả lời bằng cùng ngôn ngữ với câu hỏi.\n"
        "6. Nếu có nhiều nguồn, hãy tổng hợp thông tin từ tất cả các nguồn.\n"
        "7. Nếu các nguồn mâu thuẫn, hãy đề cập đến sự mâu thuẫn này.\n"
    )


# Refusal text shown when retrieval finds nothing or the grounding verifier
# rejects the model's answer. Kept here so the UI and backend agree.
RAG_NOT_FOUND = "Không tìm thấy tài liệu phù hợp để trả lời câu hỏi của bạn."
RAG_UNGROUNDED = "Không tìm thấy thông tin phù hợp trong tài liệu để trả lời đáng tin cậy."
RAG_INTERNAL_ERROR = "Xin lỗi, đã xảy ra lỗi khi xử lý câu trả lời. Vui lòng thử lại."
