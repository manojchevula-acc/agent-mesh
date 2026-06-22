"""Authentication — API key (dev) or JWT validation (production)."""

from fastapi import Header, HTTPException, Request, status

from ..utils.logging import get_logger

logger = get_logger(__name__)


async def verify_auth(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    """Validate the request.

    - If ``jwt_secret`` is configured, a valid Bearer JWT is required.
    - Otherwise, if ``api_key`` is configured, a matching ``X-API-Key`` is required.
    - If neither is configured (open dev mode), all requests are allowed.
    """
    settings = request.app.state.settings

    if settings.jwt_secret:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
            )
        token = authorization.split(" ", 1)[1]
        _validate_jwt(token, settings.jwt_secret, settings.jwt_algorithm)
        return

    if settings.api_key:
        if x_api_key != settings.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
            )
        return

    # No auth configured — open dev mode.
    return


def _validate_jwt(token: str, secret: str, algorithm: str) -> dict:
    try:
        from jose import jwt

        return jwt.decode(token, secret, algorithms=[algorithm])
    except Exception as exc:
        logger.warning("JWT validation failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
