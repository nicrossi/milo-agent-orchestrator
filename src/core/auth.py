import logging
import os
from dataclasses import dataclass
from typing import Optional

import firebase_admin
from fastapi import Header, HTTPException, WebSocket, Depends
from firebase_admin import auth, credentials
from sqlalchemy import select

from src.core.database import get_db_session
from src.core.models import User

logger = logging.getLogger("milo-orchestrator.auth")


@dataclass
class AuthenticatedUser:
    uid: str
    email: str
    claims: dict


_firebase_initialized = False


def _ensure_firebase_app() -> None:
    global _firebase_initialized
    if _firebase_initialized:
        return

    cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    use_adc = os.getenv("FIREBASE_USE_APPLICATION_DEFAULT", "false").lower() == "true"

    try:
        if firebase_admin._apps:
            _firebase_initialized = True
            return

        if cred_path:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
            _firebase_initialized = True
            logger.info("Firebase Admin initialized with service account file.")
            return

        if use_adc:
            firebase_admin.initialize_app()
            _firebase_initialized = True
            logger.info("Firebase Admin initialized with application default credentials.")
            return
    except Exception:
        logger.error("Failed to initialize Firebase Admin SDK.", exc_info=True)
        raise

    raise RuntimeError(
        "Firebase auth is enabled but not configured. "
        "Set FIREBASE_SERVICE_ACCOUNT_PATH or FIREBASE_USE_APPLICATION_DEFAULT=true."
    )


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")
    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Invalid Authorization header format.")
    return parts[1].strip()


def verify_token(token: str) -> AuthenticatedUser:
    _ensure_firebase_app()
    try:
        decoded = auth.verify_id_token(token, check_revoked=False)
        uid = str(decoded.get("uid", "")).strip()
        if not uid:
            raise HTTPException(status_code=401, detail="Invalid Firebase token (uid missing).")
        email = str(decoded.get("email", "") or "")
        return AuthenticatedUser(uid=uid, email=email, claims=decoded)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired Firebase token.")


def require_http_user(authorization: Optional[str] = Header(default=None)) -> AuthenticatedUser:
    if os.getenv("AUTH_REQUIRED", "true").lower() != "true":
        return AuthenticatedUser(uid="dev-user", email="dev@example.com", claims={})
    token = _extract_bearer_token(authorization)
    return verify_token(token)


async def require_teacher(user: AuthenticatedUser = Depends(require_http_user)) -> AuthenticatedUser:
    if os.getenv("AUTH_REQUIRED", "true").lower() != "true":
        return user
        
    async with get_db_session() as db:
        stmt = select(User.role).where(User.id == user.uid)
        result = await db.execute(stmt)
        role = result.scalar_one_or_none()
        
        if role != "teacher":
            raise HTTPException(status_code=403, detail="Requires teacher role.")
            
    return user


def require_ws_user(websocket: WebSocket) -> AuthenticatedUser:
    if os.getenv("AUTH_REQUIRED", "true").lower() != "true":
        return AuthenticatedUser(uid="dev-user", email="dev@example.com", claims={})

    token = websocket.query_params.get("token", "").strip()
    if not token:
        auth_header = websocket.headers.get("authorization")
        token = _extract_bearer_token(auth_header)
    return verify_token(token)
