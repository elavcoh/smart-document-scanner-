"""Keep the errors that the API can explain to the app.

Each error has an HTTP status, a short code, and a message for the user.
"""

class AppError(Exception):
    """Keep the HTTP status, error code, and message together."""
    def __init__(self, status: int, code: str, message: str):
        """Store the details that the API returns for this error."""
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
