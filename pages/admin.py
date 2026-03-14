import os
import bcrypt
import streamlit as st
import pandas as pd

st.set_page_config(page_title="FITFXR Admin", layout="wide")

# ── Auth gate ──────────────────────────────────────────────────────────────────
if "admin_authed" not in st.session_state:
    st.session_state.admin_authed = False

if not st.session_state.admin_authed:
    st.title("FITFXR Admin")
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        stored_hash = (st.secrets.get("ADMIN_PASSWORD_HASH") or os.environ.get("ADMIN_PASSWORD_HASH", "")).encode()
        if stored_hash and bcrypt.checkpw(pw.encode(), stored_hash):
            st.session_state.admin_authed = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

# ── Load data ──────────────────────────────────────────────────────────────────
try:
    from supabase import create_client
    sb = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])
except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()

st.title("FITFXR — Data Dashboard")

if st.button("Logout"):
    st.session_state.admin_authed = False
    st.rerun()

# ── Summary metrics ────────────────────────────────────────────────────────────
sessions_resp = sb.table("sessions").select("id, is_guest, created_at").execute()
sessions_df = pd.DataFrame(sessions_resp.data or [])

acts_resp = sb.table("session_activities").select("activity").execute()
acts_df = pd.DataFrame(acts_resp.data or [])

products_resp = sb.table("products_shown").select("id").execute()
products_df = pd.DataFrame(products_resp.data or [])

total = len(sessions_df)
registered = int((sessions_df["is_guest"] == False).sum()) if not sessions_df.empty else 0
guest = total - registered

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Sessions", total)
c2.metric("Registered Users", registered)
c3.metric("Guest Sessions", guest)
c4.metric("Product Records", len(products_df))

st.divider()

# ── Sessions table ─────────────────────────────────────────────────────────────
st.subheader("Sessions")

full_resp = sb.table("sessions").select("*").order("created_at", desc=True).execute()
df = pd.DataFrame(full_resp.data or [])

if not df.empty:
    df["created_at"] = pd.to_datetime(df["created_at"])

    col_date, col_guest = st.columns(2)
    with col_date:
        dates = st.date_input("Date range",
                              value=(df["created_at"].min().date(), df["created_at"].max().date()))
    with col_guest:
        guest_filter = st.selectbox("User type", ["All", "Registered", "Guest"])

    if len(dates) == 2:
        df = df[(df["created_at"].dt.date >= dates[0]) & (df["created_at"].dt.date <= dates[1])]
    if guest_filter == "Registered":
        df = df[df["is_guest"] == False]
    elif guest_filter == "Guest":
        df = df[df["is_guest"] == True]

    st.dataframe(df, use_container_width=True)
    st.download_button("Download Sessions CSV", df.to_csv(index=False),
                       file_name="fitfxr_sessions.csv", mime="text/csv")
else:
    st.info("No sessions recorded yet.")

st.divider()

# ── Activity breakdown ─────────────────────────────────────────────────────────
st.subheader("Activity Frequency")

if not acts_df.empty:
    counts = acts_df["activity"].value_counts().reset_index()
    counts.columns = ["Activity", "Count"]
    st.bar_chart(counts.set_index("Activity"))

    full_acts = sb.table("session_activities").select("*").execute()
    df_acts = pd.DataFrame(full_acts.data or [])
    if not df_acts.empty:
        st.download_button("Download Activities CSV", df_acts.to_csv(index=False),
                           file_name="fitfxr_activities.csv", mime="text/csv")
else:
    st.info("No activity data yet.")

st.divider()

# ── Injury breakdown ───────────────────────────────────────────────────────────
st.subheader("Injury / Concern Frequency")

full_s = pd.DataFrame(sb.table("sessions").select("injuries").execute().data or [])
if not full_s.empty:
    import json
    all_injuries = []
    for row in full_s["injuries"]:
        try:
            vals = json.loads(row) if isinstance(row, str) else row
            all_injuries.extend(vals or [])
        except Exception:
            pass
    if all_injuries:
        inj_counts = pd.Series(all_injuries).value_counts().reset_index()
        inj_counts.columns = ["Injury / Concern", "Count"]
        st.bar_chart(inj_counts.set_index("Injury / Concern"))

st.divider()

# ── Products table ─────────────────────────────────────────────────────────────
st.subheader("Products Shown")

full_prod = (
    sb.table("products_shown")
      .select("title, price, source, rating, reviews, product_link, created_at")
      .order("created_at", desc=True)
      .limit(1000)
      .execute()
)
df_prod = pd.DataFrame(full_prod.data or [])

if not df_prod.empty:
    sources = ["All"] + sorted(df_prod["source"].dropna().unique().tolist())
    selected_src = st.selectbox("Filter by retailer", sources)
    if selected_src != "All":
        df_prod = df_prod[df_prod["source"] == selected_src]

    st.dataframe(df_prod, use_container_width=True)
    st.download_button("Download Products CSV", df_prod.to_csv(index=False),
                       file_name="fitfxr_products.csv", mime="text/csv")
else:
    st.info("No product data yet.")
