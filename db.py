import os
import json
import datetime
import time
from supabase import create_client, Client


def _get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")


def get_client() -> Client:
    """Anon-key client — use for auth operations (sign up, sign in, sign out)."""
    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_KEY")
    return create_client(url, key)


def get_service_client() -> Client:
    """Service-role client — bypasses RLS for server-side writes."""
    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_SERVICE_KEY") or _get_secret("SUPABASE_KEY")
    return create_client(url, key)


_cache_client = None


def get_cache_client() -> Client:
    """Service-role client for the search_cache table: built once per process, short timeout
    so a slow database can't hold up a product search (the cache fails open)."""
    global _cache_client
    if _cache_client is None:
        from supabase import ClientOptions
        url = _get_secret("SUPABASE_URL")
        key = _get_secret("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        _cache_client = create_client(url, key, options=ClientOptions(postgrest_client_timeout=4))
    return _cache_client


# ── Auth helpers ──────────────────────────────────────────────────────────────
# Retry transient network failures, and turn Supabase/httpx errors into messages a
# user can act on. The raw error is logged (Render logs), never shown.

_NETWORK_HINTS = (
    "nodename nor servname", "name or service not known", "temporary failure in name resolution",
    "getaddrinfo", "connection refused", "connection reset", "connecterror", "timed out",
    "timeout", "network is unreachable", "remoteprotocolerror", "server disconnected",
    "502", "503", "504",
)


def _is_network_error(e: Exception) -> bool:
    try:
        import httpx
        if isinstance(e, (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError)):
            return True
    except ImportError:
        pass
    msg = f"{type(e).__name__} {e}".lower()
    return any(h in msg for h in _NETWORK_HINTS)


def auth_call(fn, retries: int = 2):
    """Run fn(auth) against a fresh Supabase auth client, retrying network failures with backoff."""
    for attempt in range(retries + 1):
        try:
            return fn(get_client().auth)
        except Exception as e:
            if _is_network_error(e) and attempt < retries:
                time.sleep(0.75 * (attempt + 1))
                continue
            raise


def friendly_auth_error(e: Exception, action: str) -> str:
    """Map an auth exception to a user-facing message. action: 'sign_up' | 'log_in'."""
    msg = str(e).lower()
    print(f"[FITFXR AUTH] {action} failed: {type(e).__name__}: {e}", flush=True)
    if _is_network_error(e):
        return ("We can't reach our account service right now. Please try again in a minute, "
                "or browse without an account. Your results still work.")
    if "rate limit" in msg or "429" in msg or "too many" in msg:
        return "Too many attempts right now. Please wait a minute and try again."
    if "already registered" in msg or "already exists" in msg:
        return "An account with this email already exists. Try logging in."
    if "email not confirmed" in msg:
        return "Please confirm your email first. Check your inbox (and spam)."
    if "invalid login credentials" in msg or "invalid credentials" in msg:
        return "Invalid email or password. Please try again."
    if "password" in msg and ("weak" in msg or "at least" in msg or "should be" in msg):
        return "That password is too weak. Use at least 8 characters, including a number."
    if "email" in msg and ("invalid" in msg or "valid email" in msg):
        return "That email address doesn't look right. Please check it."
    if "signups not allowed" in msg or "signup is disabled" in msg:
        return "New sign-ups are paused right now. Please browse without an account for the moment."
    return ("Something went wrong creating your account. Please try again."
            if action == "sign_up" else "Something went wrong logging in. Please try again.")


def auth_health(timeout: float = 3.0) -> tuple[bool, str]:
    """(ok, reason) for the Supabase auth service — DNS first (catches a paused/deleted project),
    then the /auth/v1/health endpoint."""
    import socket
    from urllib.parse import urlparse
    url = (_get_secret("SUPABASE_URL") or "").strip()
    host = urlparse(url).hostname
    if not host:
        return False, "SUPABASE_URL is missing or malformed"
    try:
        socket.getaddrinfo(host, 443)
    except OSError:
        return False, f"{host} does not resolve — Supabase project paused or deleted?"
    try:
        import httpx
        r = httpx.get(f"{url.rstrip('/')}/auth/v1/health",
                      headers={"apikey": _get_secret("SUPABASE_KEY")}, timeout=timeout)
        if r.status_code >= 500:
            return False, f"auth health returned HTTP {r.status_code}"
        return True, "ok"
    except Exception as e:
        return False, f"auth health check failed: {e}"


def save_session(user_data: dict, ai_results: dict,
                 username: str, is_guest: bool) -> str | None:
    """
    Persist one completed recommendation session to Supabase.
    ai_results shape: {activity: {"query": str, "products": [...], "answers": {...}}}
    Returns session UUID on success, None on failure (never raises).
    """
    try:
        sb = get_service_client()

        session_row = {
            "username":   username,
            "is_guest":   is_guest,
            "age":        user_data.get("age"),
            "sex":        user_data.get("sex"),
            "weight":     user_data.get("weight"),
            "height":     user_data.get("height"),
            "shoe_size":  user_data.get("shoe_size"),
            "width":      user_data.get("width"),
            "arch":       user_data.get("arch"),
            "injuries":   json.dumps(user_data.get("injuries", [])),
            "waterproof": user_data.get("waterproof"),
            "priorities": json.dumps(user_data.get("priorities", [])),
        }
        session_resp = sb.table("sessions").insert(session_row).execute()
        session_id = session_resp.data[0]["id"]

        for activity, result in ai_results.items():
            act_row = {
                "session_id":    session_id,
                "activity":      activity,
                "is_occasional": result.get("is_occasional", False),
                "answers":       json.dumps(result.get("answers", {})),
                "search_query":  result.get("query"),
            }
            act_resp = sb.table("session_activities").insert(act_row).execute()
            act_id = act_resp.data[0]["id"]

            products = result.get("products", [])
            product_rows = []
            for rank, p in enumerate(products, start=1):
                product_rows.append({
                    "session_activity_id": act_id,
                    "rank":          rank,
                    "title":         p.get("title"),
                    "price":         p.get("price"),
                    "source":        p.get("source"),
                    "rating":        p.get("rating"),
                    "reviews":       p.get("reviews"),
                    "product_link":  p.get("link") or p.get("product_link"),
                    "thumbnail_url": p.get("thumbnail"),
                })
            if product_rows:
                sb.table("products_shown").insert(product_rows).execute()

        return session_id

    except Exception as e:
        print(f"[FITFXR DB] Session save failed: {e}")
        return None


def load_user_profile(username: str) -> dict | None:
    """Load saved biometric profile for a returning user. Returns None if not found."""
    try:
        sb = get_service_client()
        resp = sb.table("user_profiles").select("*").eq("username", username).execute()
        if resp.data:
            row = resp.data[0]
            return {
                "age":                row.get("age"),
                "sex":                row.get("sex"),
                "weight":             row.get("weight"),
                "height":             row.get("height"),
                "shoe_size":          row.get("shoe_size"),
                "width":              row.get("width"),
                "arch":               row.get("arch"),
                "injuries":           row.get("injuries") or [],
                "waterproof":         row.get("waterproof"),
                "priorities":         row.get("priorities") or [],
                "selected_activities": [],
            }
        return None
    except Exception as e:
        print(f"[FITFXR DB] Profile load failed: {e}")
        return None


def save_user_profile(username: str, user_data: dict) -> None:
    """Upsert biometric profile so it's pre-filled on next login."""
    try:
        sb = get_service_client()
        sb.table("user_profiles").upsert({
            "username":   username,
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "age":        user_data.get("age"),
            "sex":        user_data.get("sex"),
            "weight":     user_data.get("weight"),
            "height":     user_data.get("height"),
            "shoe_size":  user_data.get("shoe_size"),
            "width":      user_data.get("width"),
            "arch":       user_data.get("arch"),
            "injuries":   user_data.get("injuries", []),
            "waterproof": user_data.get("waterproof"),
            "priorities": user_data.get("priorities", []),
        }).execute()
    except Exception as e:
        print(f"[FITFXR DB] Profile save failed: {e}")
