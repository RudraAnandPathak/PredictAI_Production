"""Privacy-safe logging for everything that touches uploaded data.

Exception *messages* can quote the data being parsed (a bad cell value, a line of text), and the standard
traceback prints source lines. So records on the workspace logger never carry a traceback: the filter replaces
it with the exception type and the code location only ("ValueError at parsing/datasets.py:88 in rows")."""
from __future__ import annotations

import logging
import os
from typing import Optional

LOGGER_NAME = "predictai.workspace"


def describe_exception(exc_info) -> str:
    kind, _, tb = exc_info
    where = ""
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is not None:
        frame = tb.tb_frame.f_code
        where = f" at {os.path.basename(frame.co_filename)}:{tb.tb_lineno} in {frame.co_name}"
    return f"{getattr(kind, '__name__', 'Exception')}{where}"


class _NoSensitiveTraceback(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info and record.exc_info[0] is not None:
            record.msg = f"{record.getMessage()} [{describe_exception(record.exc_info)}]"
            record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not any(isinstance(f, _NoSensitiveTraceback) for f in logger.filters):
        logger.addFilter(_NoSensitiveTraceback())
    return logger
