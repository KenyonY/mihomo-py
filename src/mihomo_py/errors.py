class Message(str):
    """Keep formatting parameters so the TUI can translate before interpolation."""

    def __new__(cls, template, **values):
        message = super().__new__(cls, template.format(**values))
        message.template = template
        message.values = values
        return message


class AppError(Exception):
    def __init__(self, kind, message, code=1, suggestion=None, retryable=False):
        super().__init__(message)
        self.message = message
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
