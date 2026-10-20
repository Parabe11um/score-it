"""Redact both access/error output, including Gunicorn's own exception paths."""

from gunicorn.glogging import Logger

from config.security import RedactingFormatter


class SafeLogger(Logger):
    def setup(self, cfg):
        super().setup(cfg)
        for logger in (self.access_log, self.error_log):
            for handler in logger.handlers:
                original = handler.formatter or self.error_fmt
                if isinstance(original, str):
                    handler.setFormatter(RedactingFormatter(original))
                else:
                    handler.setFormatter(RedactingFormatter(original._style._fmt, original.datefmt))
