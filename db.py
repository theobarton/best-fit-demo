import os
import json
import datetime
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
