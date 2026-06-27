import logging
import sys
from pathlib import Path


class _RedactionFilter(logging.Filter):
    """Strip secret-shaped substrings from every log record before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Import locally to avoid a circular import at module load time.
        from .redaction import redact_text
        try:
            record.msg = redact_text(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging():
    """Configure logging for the application"""
    log_dir = Path(__file__).parent.parent.parent.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_dir / 'app.log', encoding='utf-8')
    stream_handler = logging.StreamHandler(sys.stdout)
    redaction_filter = _RedactionFilter()
    file_handler.addFilter(redaction_filter)
    stream_handler.addFilter(redaction_filter)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[file_handler, stream_handler],
    )

    # Reduce noise from third-party libraries
    logging.getLogger('chromadb').setLevel(logging.WARNING)
    logging.getLogger('sentence_transformers').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)


# Shared application logger
logger = logging.getLogger("pa_agent")
