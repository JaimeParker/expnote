"""Errors shared by the research layer (mapped to envelope error codes)."""

from __future__ import annotations


class ResearchError(Exception):
    """A command-level failure with a stable code and exit status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: int = 2,
        remediation: str | None = None,
        retryable: bool = False,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.remediation = remediation
        self.retryable = retryable
        self.details = details or {}


class MissingDependency(ResearchError):
    def __init__(self, package: str = "pyyaml"):
        super().__init__(
            "ENV_MISSING_DEPENDENCY",
            f"The research layer needs {package}, which is not installed.",
            exit_code=6,
            remediation="pip install 'expnote[research]'",
            details={"package": package},
        )
