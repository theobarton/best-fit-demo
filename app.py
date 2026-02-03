import streamlit as st
import os
import json
from dotenv import load_dotenv
from openai import OpenAI

# 1. SETUP & LOAD KEY
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

# Page Config
st.set_page_config(page_title="BEST FIT", page_icon="👟", layout="wide")

# --- CUSTOM BRANDING & CSS ---
# This acts as your "Theme Engine"
st.markdown("""
<style>
    /* Main Background & Text */
    .stApp {
        background-color: #F2F2F0; /* Creamish Gray */
    }
    
    /* Headers - Forest Green */
    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        color: #1B4D3E !important;
        font-family: 'Helvetica', sans-serif;
    }
    
    /* Buttons - Aerospace Orange */
    .stButton > button {
        background-color: #FF4500 !important;
        color: white !important;
        border-radius: 5px;
        border: none;
    }
    
    /* Product Tiles */
    div[data-testid="stExpander"] {
        border: 1px solid #1B4D3E;
        border-radius: 8px;
        background-color: white;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    
    /* Price Tag Style */
    .price-tag {
        color: #FF4500;
        font-weight: 800;
        font-size: 1.1em;
    }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALIZATION ---
if 'step' not in st.session_state: st.session_state.step = 1
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'username' not in st.session_state: st.session_state.username = "Guest"

if 'user_data' not in st.session_state:
    st.session_state.user_data = {
        'age': None, 'sex': None, 'weight': None, 'height': None,
        'shoe_size': None, 'width': None, 'arch': None, 'injury': None,
        'selected_activities': [], 
        'activity_details': {}, 
        'priorities': []
    }
if 'ai_results' not in st.session_state:
    st.session_state.ai_results = None

# --- HELPER FUNCTIONS ---
def next_step(): st.session_state.step += 1
def prev_step(): st.session_state.step -= 1

# --- SIDEBAR (LOGIN SYSTEM MVP) ---
with st.sidebar:
    st.image("https://placehold.co/200x80/1B4D3E/FFFFFF?text=BEST+FIT", use_container_width=True) # Placeholder Logo
    
    if not st.session_state.logged_in:
        st.header("Login / Sign Up")
        auth_mode = st.radio("Access", ["Login", "Sign Up"], label_visibility="collapsed")
        
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        
        if st.button("Enter Platform"):
            # CTO NOTE: Connect this to Supabase later
            st.session_state.logged_in = True
            st.session_state.username = email.split("@")[0]
            st.success("Logged in! (Demo Mode)")
            st.rerun()
    else:
        st.write(f"👋 **Welcome back, {st.session_state.username}**")
        st.info("Your preferences are saved.")
        if st.button("Log Out"):
            st.session_state.logged_in = False
            st.rerun()
        
    st.divider()
    st.caption("© 2024 BEST FIT Inc.")

# --- APP HEADER (With Banner Logic) ---
# Only show the "Splash" header on Step 1
if st.session_state.step == 1:
    st.markdown("# Find Your Perfect Gear")
    st.markdown("##### AI-Powered recommendations tailored to your biomechanics.")
    st.divider()
else:
    c1, c2 = st.columns([1, 5])
    with c1:
        st.write("### BEST FIT") 
    with c2:
        st.progress(st.session_state.step / 5)
    st.divider()

# ==========================================
# STEP 1: BIOMETRICS
# ==========================================
if st.session_state.step == 1:
    st.subheader("Step 1: The Basics")
    
    age_opts = ["--- Select ---", "Under 18", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
    weight_opts = ["--- Select ---", "Under 100lbs (<45kg)", "100-120lbs (45-54kg)", "120-140lbs (54-63kg)", "140-160lbs (63-72kg)", "160-180lbs (72-81kg)", "180-200lbs (81-90kg)", "200-220lbs (90-100kg)", "Over 220lbs (>100kg)"]
    height_opts = ["--- Select ---", "Under 4'9\"", "4'9\" - 5'0\"", "5'0\" - 5'3\"", "5'3\" - 5'6\"", "5'6\" - 5'9\"", "5'9\" - 6'0\"", "6'0\" - 6'3\"", "Over 6'3\""]

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.user_data['age'] = st.selectbox("Age Range", age_opts)
        st.session_state.user_data['sex'] = st.selectbox("Sex", ["--- Select ---", "Man", "Woman", "Non-Binary"])
    with col2:
        st.session_state.user_data['weight'] = st.selectbox("Weight Range", weight_opts)
        st.session_state.user_data['height'] = st.selectbox("Height Range", height_opts)

    st.write("")
    if st.button("Next ➡️"):
        if "--- Select ---" in [st.session_state.user_data['age'], st.session_state.user_data['weight']]:
            st.error("Please complete all fields.")
        else:
            next_step()
            st.rerun()

# ==========================================
# STEP 2: FOOT DETAILS (Updated for Pronation)
# ==========================================
elif st.session_state.step == 2:
    st.subheader("Step 2: Foot Details")

    size_list = ["--- Select ---"]
    for i in range(10, 30):
        us_size = i / 2
        size_list.append(f"US {us_size}  |  EU {int(us_size + 33)}")
    
    # Updated Injury List per CEO Request
    injury_options = [
        "--- Select ---", 
        "None", 
        "Overpronation / Collapsed Arches",  # <-- NEW OPTION
        "Knee Pain (Runner's Knee)", 
        "Shin Splints", 
        "Plantar Fasciitis", 
        "Achilles Tendonitis"
    ]

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.user_data['shoe_size'] = st.selectbox("Shoe Size", size_list)
        st.session_state.user_data['width'] = st.selectbox("Foot Width", ["--- Select ---", "Standard", "Wide", "Narrow"])
    with col2:
        st.session_state.user_data['arch'] = st.selectbox("Arch Type", ["--- Select ---", "Don't know", "Neutral", "High", "Flat"])
        st.session_state.user_data['injury'] = st.selectbox("Current Injury/Concern", injury_options)

    c1, c2 = st.columns([1, 1])
    with c1: st.button("⬅️ Back", on_click=prev_step)
    with c2: 
        if st.button("Next ➡️"):
            next_step()
            st.rerun()

# ==========================================
# STEP 3: ACTIVITIES
# ==========================================
elif st.session_state.step == 3:
    st.subheader("Step 3: Activity Selection")
    
    activities_list = ["Running", "Walking", "Hiking", "Basketball", "Soccer", "Tennis", "Pickleball", "Gym/Training"]
    
    selected = st.multiselect("Select Activities", activities_list, default=st.session_state.user_data['selected_activities'])
    st.session_state.user_data['selected_activities'] = selected

    c1, c2 = st.columns([1, 1])
    with c1: st.button("⬅️ Back", on_click=prev_step)
    with c2: 
        if st.button("Next ➡️"):
            if not selected: st.error("Select at least one activity.")
            else: next_step()
            st.rerun()

# ==========================================
# STEP 4: DEEP DIVE
# ==========================================
elif st.session_state.step == 4:
    st.subheader("Step 4: The Specifics")
    
    for act in st.session_state.user_data['selected_activities']:
        st.markdown(f"**{act} Details**")
        with st.container(border=True):
            c1, c2 = st.columns(2)
            with c1:
                st.selectbox(f"Frequency ({act})", ["Weekly", "Daily", "Pro"], key=f"freq_{act}")
            with c2:
                st.selectbox(f"Experience ({act})", ["Beginner", "Intermediate", "Advanced"], key=f"exp_{act}")
                
    st.write("### Priorities")
    st.session_state.user_data['priorities'] = st.multiselect("Top Priorities", ["Comfort", "Price", "Durability", "Style", "Brand"], default=st.session_state.user_data['priorities'])

    c1, c2 = st.columns([1, 2])
    with c1: st.button("⬅️ Back", on_click=prev_step)
    with c2:
        if st.button("🔍 Find My Gear"):
            next_step()
            st.rerun()

# ==========================================
# STEP 5: RESULTS (Logic Update for Pronation)
# ==========================================
elif st.session_state.step == 5:
    
    if st.session_state.ai_results is None:
        # Build Profile String
        u = st.session_state.user_data
        
        # LOGIC INJECTION FOR CEO REQUEST
        special_instructions = ""
        if u['injury'] == "Overpronation / Collapsed Arches":
            special_instructions = "CRITICAL: User has Overpronation/Collapsed Arches. You MUST recommend shoes with 'Motion Control', 'GuideRails', or strong medial posts. Do NOT recommend neutral shoes."
        
        user_profile = (
            f"User Profile: Age {u['age']}, Weight {u['weight']}, Sex {u['sex']}.\n"
            f" injury History: {u['injury']}. \n"
            f"Activity List: {u['selected_activities']}.\n"
            f"{special_instructions}"
        )

        system_prompt = (
            "You are BEST FIT. Recommend exactly 6 REAL, EXISTING products (Shoes/Boots). "
            "Return valid JSON only. Format: "
            "{ \"products\": [ { \"brand\": \"...\", \"model\": \"...\", \"price\": \"...\", \"short_desc\": \"...\", \"long_desc\": \"...\" } ] }"
        )

        with st.spinner("Analyzing biomechanics & inventory..."):
            try:
                client = OpenAI(api_key=api_key)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_profile}
                    ],
                    response_format={ "type": "json_object" }
                )
                data = json.loads(response.choices[0].message.content)
                st.session_state.ai_results = data.get("products", [])
            except Exception as e:
                st.error(f"AI Error: {e}")
                st.stop()

    # --- RENDER RESULTS ---
    st.balloons()
    st.subheader(f"Top Recommendations for {st.session_state.username}")
    
    # Show Search Bar (Platform Feature)
    st.text_input("🔍 Search within results...", placeholder="e.g., 'Cheapest' or 'Waterproof'")

    results = st.session_state.ai_results
    for i in range(0, len(results), 2):
        cols = st.columns(2)
        for j in range(2):
            if i + j < len(results):
                item = results[i+j]
                with cols[j]:
                    with st.container(border=True):
                        st.markdown(f"#### {item['brand']} {item['model']}")
                        st.markdown(f"<span class='price-tag'>{item['price']}</span>", unsafe_allow_html=True)
                        st.caption(item['short_desc'])
                        with st.expander("Why this fits you"):
                            st.write(item['long_desc'])
                            st.button(f"Add to Locker", key=f"save_{i+j}") # Platform Feature

    st.divider()
    if st.button("Start Over"):
        st.session_state.step = 1
        st.session_state.ai_results = None
        st.rerun()