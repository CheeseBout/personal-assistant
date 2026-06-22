import logging
import sys
from pathlib import Path

def setup_logging():
    """Configure logging for the application"""
    log_dir = Path(__file__).parent.parent.parent.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_dir / 'app.log', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Reduce noise from third-party libraries
    logging.getLogger('chromadb').setLevel(logging.WARNING)
    logging.getLogger('sentence_transformers').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
