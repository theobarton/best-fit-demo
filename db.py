import os
import json
from supabase import create_client, Client


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def save_session(user_data: dict, ai_results: dict,
                 username: str, is_guest: bool) -> str | None:
    """
    Persist one completed recommendation session to Supabase.
    ai_results shape: {activity: {"query": str, "products": [...], "answers": {...}}}
    Returns session UUID on success, None on failure (never raises).
    """
    try:
        sb = get_client()

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
