# exceptions.py
"""Custom exceptions for the Omada MCP server."""


class OmadaServerError(Exception):
    """Base exception for Omada server errors."""

    def __init__(
        self, message: str, status_code: int = None, response_body: str = None
    ):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class AuthenticationError(OmadaServerError):
    """
    Raised when authentication fails or the token is invalid/expired (HTTP 401 Unauthorized).

    The caller should invoke auth.clear_tokens() and retry with fresh credentials.
    """

    pass


class AuthorizationError(OmadaServerError):
    """
    Raised when the authenticated user lacks permission for the operation (HTTP 403 Forbidden).

    Distinct from AuthenticationError (401): the user IS authenticated but does not
    hold the required Omada role or permission to access the requested resource.
    """

    pass


class ODataQueryError(OmadaServerError):
    """Raised when an OData query is malformed or the server rejects it."""

    pass
