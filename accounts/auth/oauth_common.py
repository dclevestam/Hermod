"""Shared OAuth credential error types."""


class OAuthTokenAcquisitionError(RuntimeError):
    def __init__(self, detail, *, stage='', retryable=False, source='oauth', original=None):
        text = str(detail or '').strip() or 'OAuth token unavailable'
        super().__init__(text)
        self.detail = text
        self.stage = str(stage or '').strip()
        self.retryable = bool(retryable)
        self.source = str(source or 'oauth').strip().lower() or 'oauth'
        self.original = original
