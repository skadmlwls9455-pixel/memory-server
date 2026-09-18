"""로그 설정. stdout은 MCP 전용이라 stderr와 runtime/server.log로만 보낸다.

로그에는 도구명, 오류 코드, 처리 시간만 남긴다. 본문과 검색어는 남기지 않는다.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

LOGGER_NAME = "memory"
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(log_file: Path | None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    formatter = logging.Formatter(_FORMAT)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
