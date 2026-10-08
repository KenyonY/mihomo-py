class AppError(Exception):
    def __init__(self, kind, message, code=1, suggestion=None, retryable=False):
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.suggestion = suggestion
        self.retryable = retryable

    def as_dict(self):
        return {
            "error": self.kind,
            "message": str(self),
            "suggestion": self.suggestion,
            "retryable": self.retryable,
        }
