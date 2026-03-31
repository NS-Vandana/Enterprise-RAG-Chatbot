"""
RBAC middleware: validates Azure AD JWT tokens, extracts role,
maps role → allowed Qdrant collections.
"""
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from functools import lru_cache
import httpx
import os
import structlog

from auth.models import UserContext

log = structlog.get_logger()
security = HTTPBearer(auto_error=False)

# ── Role → collection mapping ──────────────────────────────────────────────

ROLE_NAMESPACES: dict[str, list[str]] = {
    "hr":        ["hr_docs"],
    "finance":   ["finance_docs", "marketing_docs"],
    "marketing": ["marketing_docs"],
    "c_suite":   ["hr_docs", "finance_docs", "marketing_docs", "all_docs"],
}

# Fallback for dev/testing — remove in production
DEV_ROLE_TOKENS: dict[str, str] = {
    "dev-hr-token":        "hr",
    "dev-finance-token":   "finance",
    "dev-marketing-token": "marketing",
    "dev-csuite-token":    "c_suite",
}

TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
APP_ENV   = os.getenv("APP_ENV", "development")


# ── JWKS fetching ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_jwks_url() -> str:
    return f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"


async def fetch_jwks() -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(_get_jwks_url(), timeout=10.0)
        response.raise_for_status()
        return response.json()


# ── Token validation ───────────────────────────────────────────────────────

async def verify_azure_token(token: str) -> dict:
    """Validate JWT against Azure AD JWKS, return claims."""
    try:
        jwks = await fetch_jwks()
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=CLIENT_ID,
            options={"verify_exp": True},
        )
        return payload
    except JWTError as e:
        log.warning("Token validation failed", error=str(e))
        raise HTTPException(status_code=401, detail=f"Invalid or expired token: {e}")


def extract_role(claims: dict) -> str:
    """
    Extract role from JWT claims.
    Azure AD custom attribute: extension_role
    Also checks 'roles' array (app roles).
    """
    # Custom extension attribute (set via Azure AD app manifest)
    role = claims.get("extension_role")
    if role and role in ROLE_NAMESPACES:
        return role

    # App roles array
    roles = claims.get("roles", [])
    for r in roles:
        if r in ROLE_NAMESPACES:
            return r

    # Default fallback
    return "marketing"


# ── FastAPI dependency ─────────────────────────────────────────────────────

async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> UserContext:
    if not credentials:
        raise HTTPException(status_code=401, detail="Authorization header required")

    token = credentials.credentials

    # Dev mode: accept simple static tokens
    if APP_ENV == "development" and token in DEV_ROLE_TOKENS:
        role = DEV_ROLE_TOKENS[token]
        return UserContext(
            user_id=f"dev-{role}-user",
            email=f"{role}@company.dev",
            role=role,
            allowed_collections=ROLE_NAMESPACES[role],
            display_name=f"Dev {role.title()} User",
        )

    # Production: validate Azure AD JWT
    claims = await verify_azure_token(token)
    role = extract_role(claims)

    return UserContext(
        user_id=claims.get("oid", "unknown"),
        email=claims.get("upn", claims.get("email", "")),
        role=role,
        allowed_collections=ROLE_NAMESPACES[role],
        display_name=claims.get("name", ""),
    )


# ── Middleware (applied at app level) ──────────────────────────────────────

async def rbac_middleware(request: Request, call_next):
    """Middleware version — skips auth endpoints."""
    PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    # Auth is handled by get_current_user dependency on each route.
    # This middleware just logs request metadata.
    response = await call_next(request)
    return response
