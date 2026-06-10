import streamlit as st
import os
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
from serpapi import GoogleSearch

load_dotenv()

def get_secret(key):
    """Read from Streamlit secrets (cloud) or .env (local), always at call time."""
    try:
        return st.secrets[key]
    except Exception:
        return os.getenv(key, "")

st.set_page_config(page_title="FITFXR — The Right Fit Changes Everything", page_icon="👟", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #F5F6FA; }
    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        color: #1B2F6E !important;
        font-family: 'Helvetica Neue', Helvetica, sans-serif;
    }
    .stButton > button {
        background-color: #E8A020 !important;
        color: white !important;
        border-radius: 6px;
        border: none;
        font-weight: 600;
        padding: 0.5em 1.2em;
        transition: all 0.15s ease;
    }
    .stButton > button:hover {
        background-color: #D4911A !important;
        box-shadow: 0 4px 14px rgba(232,160,32,0.40);
        transform: translateY(-1px);
    }
    .price-tag { color: #E8A020; font-weight: 800; font-size: 1.15em; }
    .hero-brand {
        font-size: 5em;
        font-weight: 900;
        color: #1B2F6E;
        letter-spacing: -3px;
        line-height: 1;
    }
    .hero-tagline {
        font-size: 1.1em;
        font-weight: 700;
        color: #E8A020;
        letter-spacing: 1px;
        text-transform: uppercase;
        margin-top: -8px;
        margin-bottom: 12px;
    }
    .value-prop {
        background: white;
        border-left: 4px solid #1B2F6E;
        border-radius: 6px;
        padding: 14px 18px;
        margin-bottom: 10px;
        font-size: 0.97em;
    }
    .founding-badge {
        background: linear-gradient(135deg, #1B2F6E, #2a4494);
        color: white;
        border-radius: 10px;
        padding: 16px 20px;
        margin-top: 20px;
    }
    .mission-bar {
        background: #1B2F6E;
        color: white;
        border-radius: 8px;
        padding: 14px 20px;
        margin-bottom: 16px;
        font-size: 0.98em;
        line-height: 1.5;
    }
    div[data-testid="stTabs"] button { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ── Activity-specific question definitions ────────────────────────────────────
# Each entry is a list of question dicts:
#   key, label, type (selectbox | multiselect | text_input), options, placeholder

ACTIVITY_QUESTIONS = {
    # ── RUNNING ──────────────────────────────────────────────────────────────
    "Road Running": [
        {"key": "goal", "label": "Primary goal", "type": "selectbox",
         "options": ["Easy / recovery miles", "Speed work / intervals", "Long distance (10K–Marathon)", "Racing / PR chasing", "General fitness"]},
        {"key": "cushion", "label": "Cushion preference", "type": "selectbox",
         "options": ["No preference", "Max cushion (Hoka-style)", "Moderate cushion", "Minimal / responsive", "Carbon-plated race shoe"]},
        {"key": "drop", "label": "Heel drop", "type": "selectbox",
         "options": ["No preference", "High (12mm+)", "Medium (8–11mm)", "Low (0–7mm)", "Zero drop"]},
        {"key": "current_shoe", "label": "Current or favorite shoe", "type": "text_input", "placeholder": "e.g. Nike Pegasus 40"},
        {"key": "loved_disco", "label": "Discontinued shoe you wish came back?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Trail Running": [
        {"key": "trail_type", "label": "Trail type", "type": "multiselect",
         "options": ["Groomed / smooth", "Technical / rocky", "Muddy / wet", "High alpine", "Mixed"]},
        {"key": "goal", "label": "Goal", "type": "selectbox",
         "options": ["Fun / fitness", "Trail races", "Ultra / fastpacking", "Skyrunning"]},
        {"key": "protection", "label": "Rock plate needed?", "type": "selectbox",
         "options": ["No preference", "Yes — rocky terrain", "No — softer trails"]},
        {"key": "current_shoe", "label": "Current or favorite trail shoe", "type": "text_input", "placeholder": "e.g. Salomon Speedcross 6"},
        {"key": "loved_disco", "label": "Discontinued trail shoe you loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Ultramarathon": [
        {"key": "distance", "label": "Typical race distance", "type": "selectbox",
         "options": ["50K", "50 Miles", "100K", "100 Miles", "200+ Miles", "Varies"]},
        {"key": "terrain", "label": "Primary terrain", "type": "selectbox",
         "options": ["Road", "Mixed road/trail", "Trail", "Technical mountain"]},
        {"key": "drop", "label": "Heel drop preference", "type": "selectbox",
         "options": ["No preference", "High (12mm+)", "Medium (8–11mm)", "Low (0–7mm)", "Zero drop"]},
        {"key": "shoe_changes", "label": "Do you change shoes mid-race?", "type": "selectbox",
         "options": ["Yes", "No", "Sometimes"]},
        {"key": "current_shoe", "label": "Go-to ultra shoe", "type": "text_input", "placeholder": "e.g. Hoka Speedgoat 5"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Cross Country Running": [
        {"key": "level", "label": "Level", "type": "selectbox",
         "options": ["High school", "Collegiate", "Open / club", "Masters", "Recreational"]},
        {"key": "spike_type", "label": "Spike preference", "type": "selectbox",
         "options": ["Spiked cross country shoe", "Spikeless / trail shoe", "Either"]},
        {"key": "current_shoe", "label": "Current or favorite XC shoe", "type": "text_input", "placeholder": "e.g. Nike Zoom XC"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Walking / Casual": [
        {"key": "use", "label": "Primary use", "type": "selectbox",
         "options": ["All-day casual", "Daily walking commute", "City / travel", "Light outdoor walks", "Post-workout / recovery"]},
        {"key": "hours", "label": "Hours per day on your feet", "type": "selectbox",
         "options": ["Under 2 hours", "2–4 hours", "4–6 hours", "6–8 hours", "8+ hours"]},
        {"key": "style", "label": "Style preference", "type": "selectbox",
         "options": ["No preference", "Athletic / sporty", "Clean / minimalist", "Fashion-forward", "Classic / retro"]},
        {"key": "current_shoe", "label": "Current or favorite everyday shoe", "type": "text_input", "placeholder": "e.g. New Balance 574"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Treadmill Running": [
        {"key": "goal", "label": "Goal", "type": "selectbox",
         "options": ["General fitness", "Speed work", "Long runs", "Race training"]},
        {"key": "cushion", "label": "Cushion preference", "type": "selectbox",
         "options": ["No preference", "Max cushion", "Moderate", "Minimal / responsive"]},
        {"key": "current_shoe", "label": "Current or favorite treadmill shoe", "type": "text_input", "placeholder": "e.g. Brooks Ghost 15"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── OUTDOOR ──────────────────────────────────────────────────────────────
    "Day Hiking": [
        {"key": "cut", "label": "Boot height preference", "type": "selectbox",
         "options": ["No preference", "Low cut (trail runner style)", "Mid cut (ankle support)", "High cut (full boot)"]},
        {"key": "terrain", "label": "Typical terrain", "type": "multiselect",
         "options": ["Groomed trails", "Rocky / technical", "Muddy / wet", "Desert / sandy", "Snow / alpine", "Mixed"]},
        {"key": "waterproof", "label": "Waterproofing", "type": "selectbox",
         "options": ["Not needed", "Preferred", "Required (Gore-Tex)"]},
        {"key": "current_boot", "label": "Current or favorite hiking shoe/boot", "type": "text_input", "placeholder": "e.g. Salomon X Ultra 4"},
        {"key": "loved_disco", "label": "Discontinued boot you loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Backpacking": [
        {"key": "pack_weight", "label": "Typical pack weight", "type": "selectbox",
         "options": ["Ultralight (< 15 lbs)", "Light (15–30 lbs)", "Moderate (30–50 lbs)", "Heavy (50+ lbs)"]},
        {"key": "cut", "label": "Boot height preference", "type": "selectbox",
         "options": ["No preference", "Low cut / trail runner", "Mid cut", "High cut (full ankle support)"]},
        {"key": "waterproof", "label": "Waterproofing", "type": "selectbox",
         "options": ["Not needed", "Preferred", "Required (Gore-Tex)"]},
        {"key": "terrain", "label": "Primary terrain", "type": "multiselect",
         "options": ["Maintained trails", "Off-trail / bushwhacking", "Rocky / technical", "Snow / alpine", "River crossings", "Mixed"]},
        {"key": "current_boot", "label": "Current or favorite backpacking boot", "type": "text_input", "placeholder": "e.g. Lowa Renegade GTX"},
        {"key": "loved_disco", "label": "Discontinued boot you loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Mountaineering / Alpine": [
        {"key": "objective", "label": "Typical objective", "type": "selectbox",
         "options": ["Non-technical peaks", "Technical scrambles", "Snow/ice climbing", "Mixed routes", "Expedition"]},
        {"key": "crampon", "label": "Crampon compatibility needed?", "type": "selectbox",
         "options": ["No", "C1 (strap-on)", "C2 (semi-rigid)", "C3 (full rigid)"]},
        {"key": "warmth", "label": "Warmth / insulation", "type": "selectbox",
         "options": ["Not a priority", "Light insulation", "Moderate insulation", "Expedition warmth"]},
        {"key": "current_boot", "label": "Current or favorite mountaineering boot", "type": "text_input", "placeholder": "e.g. La Sportiva Nepal Cube GTX"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Rock Climbing": [
        {"key": "style", "label": "Climbing style", "type": "multiselect",
         "options": ["Bouldering", "Sport climbing", "Trad climbing", "Multi-pitch", "Gym / indoor", "Crack climbing", "Slab"]},
        {"key": "fit", "label": "Shoe fit / downturn", "type": "selectbox",
         "options": ["Neutral (comfortable, long pitches)", "Moderate downturn", "Aggressive downturn (performance bouldering)", "Not sure"]},
        {"key": "closure", "label": "Closure preference", "type": "selectbox",
         "options": ["No preference", "Lace-up", "Velcro / strap", "Slip-on"]},
        {"key": "current_shoe", "label": "Current or favorite climbing shoe", "type": "text_input", "placeholder": "e.g. La Sportiva Solution"},
        {"key": "vegan", "label": "Vegan / synthetic rubber?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Hunting / Fishing": [
        {"key": "season", "label": "Primary season", "type": "multiselect",
         "options": ["Early season / warm", "Mid season / cool", "Late season / cold", "Winter / snow", "Year-round"]},
        {"key": "waterproof", "label": "Waterproofing", "type": "selectbox",
         "options": ["Preferred", "Required", "Not needed"]},
        {"key": "insulation", "label": "Insulation", "type": "selectbox",
         "options": ["No insulation", "Light (200g)", "Moderate (400g)", "Heavy (800g+)"]},
        {"key": "scent", "label": "Scent control important? (hunting)", "type": "selectbox",
         "options": ["No", "Yes"]},
        {"key": "current_boot", "label": "Current or favorite boot", "type": "text_input", "placeholder": "e.g. Irish Setter Wingshooter"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Cycling / Mountain Biking": [
        {"key": "discipline", "label": "Cycling type", "type": "selectbox",
         "options": ["Road cycling", "Mountain biking", "Gravel / adventure", "Commuting", "BMX", "Cyclocross"]},
        {"key": "cleat", "label": "Cleat / pedal system", "type": "selectbox",
         "options": ["No preference", "SPD (MTB)", "SPD-SL (road)", "Look Keo", "Flat pedals", "Not sure"]},
        {"key": "current_shoe", "label": "Current or favorite cycling shoe", "type": "text_input", "placeholder": "e.g. Shimano RX8"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── COURT SPORTS ─────────────────────────────────────────────────────────
    "Basketball": [
        {"key": "position", "label": "Primary position", "type": "selectbox",
         "options": ["Point Guard", "Shooting Guard", "Small Forward", "Power Forward", "Center", "All positions"]},
        {"key": "court", "label": "Where do you play?", "type": "selectbox",
         "options": ["Indoor hardwood", "Outdoor concrete", "Both"]},
        {"key": "cut", "label": "Ankle support preference", "type": "selectbox",
         "options": ["No preference", "Low top (mobility / speed)", "Mid top", "High top (max support)"]},
        {"key": "style", "label": "Play style", "type": "selectbox",
         "options": ["Speed / quickness first", "Explosive / jumper", "Post game / physical", "Balanced all-around"]},
        {"key": "current_shoe", "label": "Current or favorite basketball shoe", "type": "text_input", "placeholder": "e.g. Nike LeBron 21"},
        {"key": "loved_disco", "label": "Discontinued shoe you loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Tennis": [
        {"key": "surface", "label": "Court surface", "type": "multiselect",
         "options": ["Hard court", "Clay", "Grass", "Indoor carpet", "Multi-surface"]},
        {"key": "style", "label": "Play style", "type": "selectbox",
         "options": ["Baseline / rallyer", "Serve and volley", "All-court", "Recreational / social"]},
        {"key": "support", "label": "Lateral support need", "type": "selectbox",
         "options": ["Standard", "High (frequent direction changes)"]},
        {"key": "current_shoe", "label": "Current or favorite tennis shoe", "type": "text_input", "placeholder": "e.g. ASICS Gel-Resolution 9"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Pickleball": [
        {"key": "court", "label": "Court type", "type": "selectbox",
         "options": ["Outdoor hard court", "Indoor gym floor", "Both"]},
        {"key": "style", "label": "Play style", "type": "selectbox",
         "options": ["Recreational / social", "Club / competitive", "Tournament level"]},
        {"key": "support", "label": "Support priority", "type": "selectbox",
         "options": ["Standard cushion", "Extra lateral support", "Extra ankle support"]},
        {"key": "current_shoe", "label": "Current or favorite pickleball shoe", "type": "text_input", "placeholder": "e.g. New Balance 806"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Volleyball": [
        {"key": "level", "label": "Level", "type": "selectbox",
         "options": ["Recreational / gym", "Club", "High school / collegiate", "Beach volleyball"]},
        {"key": "position", "label": "Position", "type": "selectbox",
         "options": ["Setter", "Outside Hitter", "Middle Blocker", "Libero", "Multiple"]},
        {"key": "current_shoe", "label": "Current or favorite volleyball shoe", "type": "text_input", "placeholder": "e.g. ASICS Gel-Rocket 11"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── FIELD SPORTS ─────────────────────────────────────────────────────────
    "Soccer": [
        {"key": "surface", "label": "Primary playing surface", "type": "selectbox",
         "options": ["Natural grass (FG)", "Artificial turf — short (AG)", "Artificial turf — long (TF)", "Indoor / futsal (IC)", "Mixed / varies"]},
        {"key": "position", "label": "Position", "type": "selectbox",
         "options": ["Goalkeeper", "Defender", "Midfielder", "Forward / Striker", "Multiple / varies"]},
        {"key": "fit", "label": "Fit preference", "type": "selectbox",
         "options": ["No preference", "Tight / locked-down", "Regular fit", "Wider fit"]},
        {"key": "current_cleat", "label": "Current or favorite cleat", "type": "text_input", "placeholder": "e.g. Nike Mercurial Superfly 9"},
        {"key": "loved_disco", "label": "Discontinued cleat you loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan / synthetic upper?", "type": "selectbox", "options": ["No preference", "Yes — synthetic only"]},
    ],
    "Football (Cleats)": [
        {"key": "position", "label": "Position", "type": "selectbox",
         "options": ["Quarterback", "Running Back", "Wide Receiver / TE", "Offensive Line", "Defensive Line", "Linebacker", "DB / Safety", "Kicker / Punter", "Multiple"]},
        {"key": "surface", "label": "Surface", "type": "selectbox",
         "options": ["Natural grass", "Artificial turf", "Both"]},
        {"key": "cut", "label": "Cleat cut", "type": "selectbox",
         "options": ["No preference", "Low cut (skill positions — speed)", "Mid cut", "High cut (linemen — stability)"]},
        {"key": "current_cleat", "label": "Current or favorite cleat", "type": "text_input", "placeholder": "e.g. Nike Alpha Menace Elite 3"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Baseball / Softball": [
        {"key": "position", "label": "Position", "type": "selectbox",
         "options": ["Pitcher", "Catcher", "Infield", "Outfield", "Utility / multiple"]},
        {"key": "surface", "label": "Field type", "type": "selectbox",
         "options": ["Dirt / natural grass", "Turf", "Both"]},
        {"key": "cleat_type", "label": "Cleat type", "type": "selectbox",
         "options": ["No preference", "Metal cleats", "Molded rubber cleats", "Turf shoes"]},
        {"key": "current_cleat", "label": "Current or favorite cleat", "type": "text_input", "placeholder": "e.g. New Balance Fresh Foam 3000v6"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── GYM ──────────────────────────────────────────────────────────────────
    "Weightlifting / CrossFit": [
        {"key": "focus", "label": "Primary focus", "type": "multiselect",
         "options": ["Olympic lifts (snatch, clean & jerk)", "Powerlifting (squat, bench, deadlift)", "CrossFit / WODs", "General strength", "Bodybuilding"]},
        {"key": "heel", "label": "Heel elevation preference", "type": "selectbox",
         "options": ["No preference", "Flat (0mm) — deadlifts / general", "Raised heel (15–20mm) — squats / Oly lifts"]},
        {"key": "current_shoe", "label": "Current or favorite lifting shoe", "type": "text_input", "placeholder": "e.g. Nike Romaleos 4"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "HIIT Training": [
        {"key": "style", "label": "Training style", "type": "selectbox",
         "options": ["Bootcamp", "Cardio-focused", "Strength + cardio mixed", "Circuit training"]},
        {"key": "surface", "label": "Surface", "type": "selectbox",
         "options": ["Gym floor", "Turf", "Outdoor", "Mixed"]},
        {"key": "current_shoe", "label": "Current or favorite training shoe", "type": "text_input", "placeholder": "e.g. Nike Metcon 9"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Yoga / Pilates": [
        {"key": "footwear", "label": "Do you use footwear for yoga/pilates?", "type": "selectbox",
         "options": ["No — barefoot only", "Yes — grip socks", "Yes — studio shoes"]},
        {"key": "current_shoe", "label": "Current or favorite (if applicable)", "type": "text_input", "placeholder": "e.g. Toesox Grip Socks"},
    ],
    "Dance / Zumba": [
        {"key": "style", "label": "Dance style", "type": "selectbox",
         "options": ["Zumba / aerobics", "Hip hop", "Ballroom / Latin", "Ballet / contemporary", "Tap", "General fitness dance"]},
        {"key": "surface", "label": "Dance floor", "type": "selectbox",
         "options": ["Sprung hardwood", "Vinyl / marley", "Gym floor", "Outdoor / concrete"]},
        {"key": "current_shoe", "label": "Current or favorite dance shoe", "type": "text_input", "placeholder": "e.g. Bloch Boost DRT"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── SNOW ─────────────────────────────────────────────────────────────────
    "Downhill Skiing": [
        {"key": "ski_type", "label": "Type of skiing", "type": "selectbox",
         "options": ["Groomed resort runs", "Powder / off-piste", "Park / halfpipe", "Backcountry / touring", "Racing", "Mixed / all-mountain"]},
        {"key": "hours", "label": "Hours per week on snow", "type": "selectbox",
         "options": ["Under 8 hours", "8–24 hours", "24–40 hours", "40+ hours"]},
        {"key": "terrain", "label": "Terrain preference", "type": "multiselect",
         "options": ["Groomed runs", "Powder", "Park & halfpipe", "Ice / hardpack", "Backcountry", "All"]},
        {"key": "flex", "label": "Boot flex preference", "type": "selectbox",
         "options": ["Not sure", "Soft (65–80) — beginner / freestyle", "Medium (85–100) — intermediate",
                     "Stiff (105–120) — advanced", "Race stiff (125+)"]},
        {"key": "weight", "label": "Weight preference", "type": "selectbox",
         "options": ["No preference", "Lighter boot", "Heavier / stiffer boot"]},
        {"key": "current_boot", "label": "Current or favorite ski boot", "type": "text_input", "placeholder": "e.g. Lange RX 120"},
        {"key": "loved_disco", "label": "Discontinued boot you loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan / synthetic liner?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Cross-Country Skiing": [
        {"key": "xc_type", "label": "XC style", "type": "selectbox",
         "options": ["Classic", "Skate skiing", "Both / touring", "Backcountry / Nordic touring"]},
        {"key": "system", "label": "Binding system", "type": "selectbox",
         "options": ["NNN (Rottefella)", "SNS / Pilot", "NIS", "Not sure / flexible"]},
        {"key": "current_boot", "label": "Current or favorite XC boot", "type": "text_input", "placeholder": "e.g. Salomon RC8 eSkin"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Snowboarding": [
        {"key": "style", "label": "Riding style", "type": "selectbox",
         "options": ["All-mountain", "Freestyle / park", "Freeride / powder", "Splitboarding / backcountry", "Racing"]},
        {"key": "flex", "label": "Boot flex preference", "type": "selectbox",
         "options": ["No preference", "Soft — park / freestyle", "Medium — all-mountain", "Stiff — freeride / race"]},
        {"key": "lacing", "label": "Lacing system", "type": "selectbox",
         "options": ["No preference", "Traditional lace", "Boa dial(s)", "Speed lace / hybrid"]},
        {"key": "current_boot", "label": "Current or favorite snowboard boot", "type": "text_input", "placeholder": "e.g. Burton Ion"},
        {"key": "loved_disco", "label": "Discontinued boot you loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Snowshoeing": [
        {"key": "terrain", "label": "Terrain", "type": "selectbox",
         "options": ["Groomed trails", "Backcountry / off-trail", "Mountain / steep", "Flat / easy"]},
        {"key": "boot_type", "label": "Preferred boot underneath", "type": "selectbox",
         "options": ["Hiking boot", "Insulated winter boot", "Trail runner", "Whatever I own"]},
        {"key": "waterproof", "label": "Waterproofing", "type": "selectbox",
         "options": ["Preferred", "Required", "Not needed"]},
        {"key": "current_boot", "label": "Current boot", "type": "text_input", "placeholder": "e.g. Baffin Impact"},
        {"key": "vegan", "label": "Vegan / synthetic?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── SKATEBOARDING ────────────────────────────────────────────────────────
    "Skateboarding": [
        {"key": "style", "label": "What kind of skating?", "type": "multiselect",
         "options": ["Street", "Park", "Vert / halfpipe", "Freestyle", "Downhill / longboard", "Cruising / commute"]},
        {"key": "hours", "label": "Hours per week skating", "type": "selectbox",
         "options": ["Under 5 hours", "5–10 hours", "10–20 hours", "20–40 hours", "40+ hours"]},
        {"key": "surface", "label": "Surface you skate most", "type": "multiselect",
         "options": ["Concrete", "Streets / sidewalks", "Skatelite", "Masonite", "Wood", "Mixed / all"]},
        {"key": "sole", "label": "Sole preference", "type": "selectbox",
         "options": ["No preference", "Cup sole (more cushion / protection)", "Vulcanized (more board feel / flexibility)", "Hybrid"]},
        {"key": "weight", "label": "Weight preference", "type": "selectbox",
         "options": ["No preference", "Lighter shoe", "Heavier / more durable shoe"]},
        {"key": "ankle", "label": "Ankle support", "type": "selectbox",
         "options": ["No preference", "Low-top (maximum mobility)", "Mid-top", "High-top (ankle support)"]},
        {"key": "current_shoe", "label": "Current or favorite skate shoe", "type": "text_input", "placeholder": "e.g. Emerica Reynolds G6"},
        {"key": "loved_disco", "label": "Discontinued shoe you love or loved?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── OTHER ─────────────────────────────────────────────────────────────────
    "Golf": [
        {"key": "course", "label": "Course condition", "type": "selectbox",
         "options": ["Dry / firm fairways", "Wet / soft course", "Links style", "Mixed"]},
        {"key": "spikes", "label": "Spike preference", "type": "selectbox",
         "options": ["No preference", "Spiked / cleated", "Spikeless (versatile, on- and off-course)"]},
        {"key": "waterproof", "label": "Waterproofing", "type": "selectbox",
         "options": ["Not needed", "Preferred", "Required"]},
        {"key": "current_shoe", "label": "Current or favorite golf shoe", "type": "text_input", "placeholder": "e.g. FootJoy Pro SL"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Work / Standing All Day": [
        {"key": "environment", "label": "Work environment", "type": "selectbox",
         "options": ["Office / light duty", "Restaurant / kitchen", "Hospital / healthcare", "Construction / industrial", "Retail / sales floor", "Warehouse", "Other"]},
        {"key": "safety", "label": "Safety requirements", "type": "multiselect",
         "options": ["None", "Slip-resistant sole", "Steel toe / composite toe", "Electrical hazard (EH) rated", "Waterproof", "Metatarsal guard", "Puncture-resistant"]},
        {"key": "comfort", "label": "Top comfort priority", "type": "selectbox",
         "options": ["Overall cushioning", "Arch support", "Wide toe box", "Lightweight", "Breathability"]},
        {"key": "hours", "label": "Hours on feet per shift", "type": "selectbox",
         "options": ["Under 4 hours", "4–6 hours", "6–8 hours", "8–10 hours", "10+ hours"]},
        {"key": "current_shoe", "label": "Current work shoe", "type": "text_input", "placeholder": "e.g. Dansko Professional"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Casual / Streetwear": [
        {"key": "style", "label": "Style vibe", "type": "selectbox",
         "options": ["Classic / retro (Jordan, Air Max, etc.)", "Minimalist / clean", "Techwear / modern", "Skate-influenced", "Luxury / designer-adjacent", "Hypebeast / limited release", "No preference"]},
        {"key": "limited", "label": "Interested in limited releases / drops?", "type": "selectbox",
         "options": ["Yes — show me what's hot", "No — great everyday shoes", "No preference"]},
        {"key": "current_shoe", "label": "Current favorite casual shoe", "type": "text_input", "placeholder": "e.g. New Balance 550"},
        {"key": "loved_disco", "label": "Discontinued style you love?", "type": "text_input", "placeholder": "Leave blank if N/A"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Travel / Light Hiking": [
        {"key": "style", "label": "Travel style", "type": "selectbox",
         "options": ["City / urban", "Mix of city and light trails", "Mostly outdoors", "Airport / transit heavy"]},
        {"key": "weight", "label": "Pack weight priority", "type": "selectbox",
         "options": ["Ultra-packable / lightweight first", "Comfort first", "Durability first"]},
        {"key": "waterproof", "label": "Waterproofing", "type": "selectbox",
         "options": ["Not needed", "Nice to have", "Required"]},
        {"key": "current_shoe", "label": "Current or favorite travel shoe", "type": "text_input", "placeholder": "e.g. Allbirds Tree Runner"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Cowboy / Western": [
        {"key": "use", "label": "Primary use", "type": "selectbox",
         "options": ["Working ranch / farm", "Rodeo", "Fashion / everyday wear", "Horseback riding", "Country dancing"]},
        {"key": "toe", "label": "Toe style", "type": "selectbox",
         "options": ["No preference", "Classic western pointed", "Roper / square toe", "Snip toe"]},
        {"key": "shaft_height", "label": "Shaft height", "type": "selectbox",
         "options": ["No preference", "Short (roper, 8–10\")", "Traditional (11–13\")", "Tall (14\"+)"]},
        {"key": "current_boot", "label": "Current or favorite western boot", "type": "text_input", "placeholder": "e.g. Ariat Workhog"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    # ── MISSING ACTIVITIES — now with proper questions ────────────────────────
    "Race Walking": [
        {"key": "goal", "label": "Goal", "type": "selectbox",
         "options": ["Fitness / recreation", "5K–10K competition", "Half / full marathon", "Olympic / elite racewalking"]},
        {"key": "technique", "label": "Technique", "type": "selectbox",
         "options": ["Casual / recreational form", "Practicing Olympic racewalking technique"]},
        {"key": "surface", "label": "Surface", "type": "multiselect",
         "options": ["Road / pavement", "Track", "Trail / mixed", "Treadmill"]},
        {"key": "current_shoe", "label": "Current or favorite race walk shoe", "type": "text_input", "placeholder": "e.g. Asics GT-2000 12"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Water Sports / Kayaking": [
        {"key": "discipline", "label": "Water activity type", "type": "multiselect",
         "options": ["Flatwater kayaking", "Whitewater kayaking", "Sea kayaking", "Canoeing", "Stand-up paddleboarding (SUP)", "Rafting", "Surfing", "Kiteboarding / windsurfing"]},
        {"key": "environment", "label": "Primary environment", "type": "selectbox",
         "options": ["Flatwater / lake", "River / whitewater", "Ocean / coastal", "Mixed"]},
        {"key": "footwear_type", "label": "Footwear needed", "type": "selectbox",
         "options": ["Not sure", "Water shoes / amphibious", "Neoprene booties", "Sandals / watershoes", "Dry suit boots"]},
        {"key": "temp", "label": "Water temperature", "type": "selectbox",
         "options": ["Warm (65°F+)", "Cool (50–65°F)", "Cold (below 50°F)", "Varies by season"]},
        {"key": "current_shoe", "label": "Current or favorite water footwear", "type": "text_input", "placeholder": "e.g. Astral Loyak"},
        {"key": "vegan", "label": "Vegan / synthetic only?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Squash / Racquetball": [
        {"key": "sport", "label": "Sport", "type": "selectbox",
         "options": ["Squash", "Racquetball", "Both"]},
        {"key": "level", "label": "Level", "type": "selectbox",
         "options": ["Recreational / social", "Club / league", "Competitive / tournament", "Elite / professional"]},
        {"key": "court", "label": "Court surface", "type": "selectbox",
         "options": ["Hardwood / parquet", "Synthetic / rubberized", "Hybrid"]},
        {"key": "support", "label": "Lateral support need", "type": "selectbox",
         "options": ["Standard", "High — frequent hard cuts and dives"]},
        {"key": "current_shoe", "label": "Current or favorite court shoe", "type": "text_input", "placeholder": "e.g. ASICS Gel-Blade 8"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Badminton": [
        {"key": "level", "label": "Level", "type": "selectbox",
         "options": ["Recreational / social", "Club / league", "Competitive / tournament"]},
        {"key": "court", "label": "Court surface", "type": "selectbox",
         "options": ["Hardwood / gym floor", "Synthetic mat", "Outdoor / concrete"]},
        {"key": "movement", "label": "Movement priority", "type": "selectbox",
         "options": ["Lightweight / fast footwork", "Extra cushioning for long sessions", "Balanced performance"]},
        {"key": "current_shoe", "label": "Current or favorite badminton shoe", "type": "text_input", "placeholder": "e.g. Yonex Power Cushion 65Z3"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Lacrosse": [
        {"key": "program", "label": "Program type", "type": "selectbox",
         "options": ["Men's lacrosse", "Women's lacrosse", "Box lacrosse (indoor)", "Youth / recreational"]},
        {"key": "position", "label": "Position", "type": "selectbox",
         "options": ["Attack", "Midfield", "Defense", "Goalie", "Multiple / varies"]},
        {"key": "surface", "label": "Primary surface", "type": "selectbox",
         "options": ["Natural grass", "Artificial turf", "Both"]},
        {"key": "cleat_type", "label": "Cleat type", "type": "selectbox",
         "options": ["No preference", "Molded cleats", "Turf shoes", "Metal cleats (if allowed)"]},
        {"key": "current_cleat", "label": "Current or favorite cleat", "type": "text_input", "placeholder": "e.g. New Balance Burn X3"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Rugby": [
        {"key": "position", "label": "Position group", "type": "selectbox",
         "options": ["Forward (props, locks, flankers, #8)", "Back (scrum-half, fly-half, centers, wings, fullback)", "Multiple / varies"]},
        {"key": "surface", "label": "Primary surface", "type": "selectbox",
         "options": ["Natural grass", "Artificial turf / 3G", "Both"]},
        {"key": "boot_type", "label": "Boot stud preference", "type": "selectbox",
         "options": ["No preference", "Molded rubber (versatile)", "Aluminium screw-in studs (soft ground)", "Bladed (firm ground / speed)"]},
        {"key": "ankle", "label": "Ankle support", "type": "selectbox",
         "options": ["No preference", "Low cut (speed)", "Mid cut (stability)"]},
        {"key": "current_boot", "label": "Current or favorite rugby boot", "type": "text_input", "placeholder": "e.g. adidas Predator Malice Elite SG"},
        {"key": "vegan", "label": "Vegan / synthetic upper?", "type": "selectbox", "options": ["No preference", "Yes — synthetic only"]},
    ],
    "Ultimate Frisbee": [
        {"key": "surface", "label": "Primary surface", "type": "selectbox",
         "options": ["Natural grass", "Turf / artificial grass", "Beach sand", "Mixed"]},
        {"key": "level", "label": "Level", "type": "selectbox",
         "options": ["Recreational / league", "Club / competitive", "College / elite"]},
        {"key": "cleat_type", "label": "Cleat type", "type": "selectbox",
         "options": ["No preference", "Soccer-style cleats (lightweight)", "Football-style cleats (support)", "Turf shoes (turf only)", "Cleats or running shoes"]},
        {"key": "cut", "label": "Ankle height", "type": "selectbox",
         "options": ["No preference", "Low cut (speed)", "Mid cut (ankle support)"]},
        {"key": "current_cleat", "label": "Current or favorite cleat / shoe", "type": "text_input", "placeholder": "e.g. Nike Phantom GT2"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Field Hockey": [
        {"key": "position", "label": "Position", "type": "selectbox",
         "options": ["Goalkeeper", "Defender", "Midfielder", "Forward", "Multiple / varies"]},
        {"key": "surface", "label": "Primary surface", "type": "selectbox",
         "options": ["Water-based astroturf", "Sand-based turf", "Natural grass", "Mixed"]},
        {"key": "cleat_type", "label": "Footwear type", "type": "selectbox",
         "options": ["No preference", "Turf shoes (multi-stud)", "Molded cleats", "Indoor shoes (for gym/sand)"]},
        {"key": "current_cleat", "label": "Current or favorite field hockey shoe", "type": "text_input", "placeholder": "e.g. adidas Divox 1.9S"},
        {"key": "vegan", "label": "Vegan / synthetic upper?", "type": "selectbox", "options": ["No preference", "Yes — synthetic only"]},
    ],
    "Spin / Indoor Cycling": [
        {"key": "setup", "label": "Setup", "type": "selectbox",
         "options": ["Studio spin class (Peloton, SoulCycle, etc.)", "Home spin / stationary bike", "Both"]},
        {"key": "cleat", "label": "Cleat / pedal system", "type": "selectbox",
         "options": ["Not sure", "SPD (two-bolt, common on spin bikes)", "Look Delta (three-bolt, Peloton default)", "SPD-SL (three-bolt road)", "Flat pedals — no cleats"]},
        {"key": "hours", "label": "Hours per week on the bike", "type": "selectbox",
         "options": ["Under 2 hours", "2–4 hours", "4–8 hours", "8+ hours"]},
        {"key": "current_shoe", "label": "Current or favorite cycling shoe", "type": "text_input", "placeholder": "e.g. Peloton Cycling Shoes"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Rowing / Erg": [
        {"key": "discipline", "label": "Rowing type", "type": "selectbox",
         "options": ["On-water rowing (shell)", "Indoor erg / Concept2", "Both"]},
        {"key": "level", "label": "Level", "type": "selectbox",
         "options": ["Recreational / club", "High school", "Collegiate", "Masters competitive", "Elite"]},
        {"key": "footwear_need", "label": "Footwear focus", "type": "selectbox",
         "options": ["Training shoe for erg workouts", "Boat shoe / dock footwear", "Both on-water and land training"]},
        {"key": "current_shoe", "label": "Current or favorite training shoe", "type": "text_input", "placeholder": "e.g. Nike Free Metcon 5"},
        {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
    "Ice Skating": [
        {"key": "discipline", "label": "Skating type", "type": "selectbox",
         "options": ["Recreational / public skating", "Figure skating", "Ice hockey (player)", "Ice hockey (goalie)", "Speed skating", "Curling"]},
        {"key": "level", "label": "Level", "type": "selectbox",
         "options": ["Beginner", "Recreational", "Competitive / club", "Elite"]},
        {"key": "boot_stiffness", "label": "Boot stiffness preference", "type": "selectbox",
         "options": ["Not sure", "Soft (beginner / recreational)", "Medium", "Stiff (advanced / performance)"]},
        {"key": "current_boot", "label": "Current or favorite skate", "type": "text_input", "placeholder": "e.g. Bauer Supreme M3"},
        {"key": "vegan", "label": "Vegan / synthetic boot liner?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
    ],
}

GENERIC_QUESTIONS = [
    {"key": "goal", "label": "Primary goal", "type": "selectbox",
     "options": ["Beginner / learning", "Recreational / fitness", "Competitive / performance", "Professional"]},
    {"key": "hours", "label": "Hours per week", "type": "selectbox",
     "options": ["Under 2 hours", "2–5 hours", "5–10 hours", "10+ hours"]},
    {"key": "current_shoe", "label": "Current or favorite shoe/boot for this activity", "type": "text_input", "placeholder": "Leave blank if N/A"},
    {"key": "weight", "label": "Weight preference", "type": "selectbox",
     "options": ["No preference", "Lighter", "Heavier / more durable"]},
    {"key": "vegan", "label": "Vegan materials?", "type": "selectbox", "options": ["No preference", "Yes — vegan only"]},
]


def render_question(q, activity):
    """Render a single question widget, keyed by activity + question key."""
    widget_key = f"{activity}__{q['key']}"
    if q["type"] == "selectbox":
        return st.selectbox(q["label"], q["options"], key=widget_key)
    elif q["type"] == "multiselect":
        return st.multiselect(q["label"], q["options"], key=widget_key)
    elif q["type"] == "text_input":
        return st.text_input(q["label"], placeholder=q.get("placeholder", ""), key=widget_key)
    return None


def collect_activity_answers(activity):
    """Pull saved widget values for an activity into a dict."""
    questions = ACTIVITY_QUESTIONS.get(activity, GENERIC_QUESTIONS)
    answers = {}
    for q in questions:
        val = st.session_state.get(f"{activity}__{q['key']}", None)
        if val:
            answers[q["label"]] = val
    return answers


def parse_price(price_str):
    if not price_str:
        return float('inf')
    nums = re.findall(r'[\d]+\.?\d*', str(price_str).replace(',', ''))
    return float(nums[0]) if nums else float('inf')


def render_product_tiers(products, act, cat_key):
    """Show 3 products (budget / best value / premium) with a More Choices button."""
    if not products:
        st.info("No products found for this category.")
        return

    priced = sorted(
        [p for p in products if parse_price(p.get('price')) < float('inf')],
        key=lambda p: parse_price(p.get('price'))
    )
    unpriced = [p for p in products if parse_price(p.get('price')) >= float('inf')]
    sorted_products = priced + unpriced

    n = len(sorted_products)
    offset_key = f"offset_{act}_{cat_key}"
    if offset_key not in st.session_state:
        st.session_state[offset_key] = 0

    offset = st.session_state[offset_key] % n
    display = [sorted_products[(offset + i) % n] for i in range(min(3, n))]

    tier_labels = ["💰 Budget Pick", "⚖️ Best Value", "🔥 Premium"]
    cols = st.columns(len(display))
    for col, product, label in zip(cols, display, tier_labels):
        with col:
            st.caption(label)
            with st.container(border=True):
                if product.get("thumbnail"):
                    st.image(product["thumbnail"], use_container_width=True)
                st.markdown(f"**{product.get('title', 'N/A')}**")
                st.markdown(
                    f"<span class='price-tag'>{product.get('price', 'N/A')}</span>",
                    unsafe_allow_html=True
                )
                st.caption(f"From: {product.get('source', 'N/A')}")
                if product.get("rating"):
                    st.caption(f"⭐ {product['rating']}  ({product.get('reviews', 0)} reviews)")
                buy_link = product.get("link") or product.get("product_link")
                if buy_link:
                    st.link_button("View & Buy →", buy_link)

    st.caption("_When you find your shoe, more color options are likely available — click through to see all styles._")

    if n > 3:
        if st.button("🔄 Get More Choices", key=f"more_{act}_{cat_key}"):
            st.session_state[offset_key] = (st.session_state[offset_key] + 3) % n
            st.rerun()


# ── Session state ─────────────────────────────────────────────────────────────
for key, default in [
    ('step', 0), ('logged_in', False), ('username', 'Guest'), ('ai_results', None)
]:
    if key not in st.session_state:
        st.session_state[key] = default

if 'user_data' not in st.session_state:
    st.session_state.user_data = {
        'age': None, 'sex': None, 'weight': None, 'height': None,
        'shoe_size': None, 'width': None, 'arch': None,
        'injuries': [],
        'selected_activities': [],
        'priorities': [],
        'waterproof': None,
    }


def next_step(): st.session_state.step += 1
def prev_step(): st.session_state.step -= 1


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## FITFXR")
    if st.session_state.step > 0:
        if st.session_state.logged_in:
            _dname = st.session_state.username.split("@")[0] if "@" in st.session_state.username else st.session_state.username
            st.markdown(f"👋 **{_dname}**")
            st.caption("Profile active")
            if st.button("Log Out"):
                try:
                    from db import get_client as _sb
                    _sb().auth.sign_out()
                except Exception:
                    pass
                st.session_state.logged_in = False
                st.session_state.step = 0
                st.rerun()
        else:
            if st.button("Log In / Sign Up"):
                st.session_state.step = 0
                st.rerun()
        if 1 < st.session_state.step <= 5:
            st.divider()
            steps = {1: "The Basics", 2: "Foot Details", 3: "Activities", 4: "The Specifics", 5: "Results"}
            for s, name in steps.items():
                if s < st.session_state.step:
                    st.caption(f"✅ {name}")
                elif s == st.session_state.step:
                    st.caption(f"▶️ **{name}**")
                else:
                    st.caption(f"○ {name}")
        if 1 <= st.session_state.step <= 4:
            st.divider()
            has_activities = bool(st.session_state.user_data.get('selected_activities'))
            if has_activities:
                if st.button("⚡ Skip to Results →", use_container_width=True, key="sidebar_skip"):
                    st.session_state.step = 5
                    st.rerun()
            else:
                if st.button("⚡ Skip to Results →", use_container_width=True, key="sidebar_skip"):
                    st.session_state.step = 3
                    st.rerun()
                st.caption("Pick activities first to skip ahead.")
    st.divider()
    st.caption("© 2025 FITFXR Inc.")

if 1 <= st.session_state.step <= 5:
    st.progress(st.session_state.step / 5)

# ==========================================
# STEP 0: LANDING / LOGIN
# ==========================================
if st.session_state.step == 0:
    col_hero, col_auth = st.columns([3, 2], gap="large")

    with col_hero:
        st.markdown('<p class="hero-brand">FITFXR</p>', unsafe_allow_html=True)
        st.markdown('<p class="hero-tagline">— The Right Fit Changes Everything —</p>', unsafe_allow_html=True)
        st.markdown(
            '<div class="mission-bar">'
            'Welcome to FITFXR — your custom, research-driven fitting consultant. '
            'No sponsors. No ads. No hidden agendas. '
            'We help <strong>YOU</strong> find what <strong>YOU</strong> need for your best fit.'
            '</div>',
            unsafe_allow_html=True
        )
        st.markdown("---")
        for prop in [
            "💾  Save your measurements — never fill out a form twice",
            "🎯  Data-driven recommendations tailored to your body and activity",
            "🔖  Bookmark gear to your Locker",
            "📣  Early access to new brands and drops",
        ]:
            st.markdown(f'<div class="value-prop">{prop}</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="founding-badge">🎉 <strong>Founding Member Offer</strong> — '
            'First 1,000 users get lifetime Pro access free when we launch premium features.</div>',
            unsafe_allow_html=True
        )

    with col_auth:
        st.markdown("### Get Started")
        tab_signup, tab_login = st.tabs(["Create Account", "Log In"])

        with tab_signup:
            su_email = st.text_input("Email", key="su_email", placeholder="you@email.com")
            su_pw = st.text_input("Password", type="password", key="su_pw", placeholder="Create a password (min 6 chars)")
            if st.button("Create My Account →", use_container_width=True, key="btn_signup"):
                if not su_email or not su_pw:
                    st.error("Please enter your email and a password.")
                elif len(su_pw) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    try:
                        from db import get_client as _sb
                        resp = _sb().auth.sign_up({"email": su_email, "password": su_pw})
                        if resp.session:
                            st.session_state.logged_in = True
                            st.session_state.username = su_email
                            next_step()
                            st.rerun()
                        elif resp.user:
                            st.success("Account created! Check your email to confirm, then log in.")
                        else:
                            st.error("Sign up failed. Please try again.")
                    except Exception as e:
                        msg = str(e).lower()
                        if "already registered" in msg or "already exists" in msg:
                            st.error("An account with this email already exists. Try logging in.")
                        else:
                            st.error(f"Sign up failed: {e}")
            st.caption("No credit card. No spam. Ever.")
            st.divider()
            st.button("🔐 Continue with Google", use_container_width=True, disabled=True,
                      help="Google Sign-In coming soon.", key="google_btn_signup")

        with tab_login:
            li_email = st.text_input("Email", key="li_email", placeholder="you@email.com")
            li_pw = st.text_input("Password", type="password", key="li_pw")
            if st.button("Log In →", use_container_width=True, key="btn_login"):
                if not li_email or not li_pw:
                    st.error("Please enter your email and password.")
                else:
                    try:
                        from db import get_client as _sb, load_user_profile
                        resp = _sb().auth.sign_in_with_password({"email": li_email, "password": li_pw})
                        st.session_state.logged_in = True
                        st.session_state.username = li_email
                        profile = load_user_profile(li_email)
                        if profile:
                            st.session_state.user_data = profile
                            st.session_state.step = 3
                        else:
                            next_step()
                        st.rerun()
                    except Exception as e:
                        msg = str(e).lower()
                        if "email not confirmed" in msg:
                            st.error("Please confirm your email first — check your inbox.")
                        else:
                            st.error("Invalid email or password. Please try again.")
            st.divider()
            st.button("🔐 Continue with Google", use_container_width=True, disabled=True,
                      help="Google Sign-In coming soon.", key="google_btn_login")

        st.divider()
        _, col_skip, _ = st.columns([1, 2, 1])
        with col_skip:
            if st.button("Browse without account", use_container_width=True, key="btn_guest"):
                next_step()
                st.rerun()
        st.caption("*Limited features without an account. Results won't be saved.*")

        st.divider()
        st.caption(
            "By using FITFXR you agree that anonymized profile data (size, activity, gear preferences) "
            "may be used to improve recommendations and shared with gear brand partners in aggregate. "
            "No personal identifiers are sold. See our Privacy Policy for details."
        )

# ==========================================
# STEP 1: BIOMETRICS
# ==========================================
elif st.session_state.step == 1:
    st.subheader("Step 1: The Basics")
    st.caption("Helps us match gear to your body.")

    age_opts = ["--- Select ---", "Under 18", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
    sex_opts = ["--- Select ---", "Man", "Woman", "Non-Binary", "Prefer Not to Share"]
    weight_opts = [
        "--- Select ---",
        "Under 120 lbs (< 55 kg)",
        "120–140 lbs (55–63 kg)",
        "140–160 lbs (63–72 kg)",
        "160–180 lbs (72–81 kg)",
        "180–200 lbs (81–90 kg)",
        "200–225 lbs (90–102 kg)",
        "225–250 lbs (102–113 kg)",
        "250–275 lbs (113–125 kg)",
        "275–300 lbs (125–136 kg)",
        "Over 300 lbs (> 136 kg)",
    ]
    height_opts = [
        "--- Select ---",
        "Under 4'9\" (< 145 cm)",
        "4'9\" – 5'0\" (145–152 cm)",
        "5'0\" – 5'3\" (152–160 cm)",
        "5'3\" – 5'6\" (160–168 cm)",
        "5'6\" – 5'9\" (168–175 cm)",
        "5'9\" – 6'0\" (175–183 cm)",
        "6'0\" – 6'3\" (183–190 cm)",
        "6'3\" – 6'6\" (190–198 cm)",
        "Over 6'6\" (> 198 cm)",
    ]

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.user_data['age'] = st.selectbox("Age Range", age_opts)
        st.session_state.user_data['sex'] = st.selectbox("Sex", sex_opts)
    with col2:
        st.session_state.user_data['weight'] = st.selectbox("Weight Range", weight_opts)
        st.session_state.user_data['height'] = st.selectbox("Height Range", height_opts)

    st.write("")
    if st.button("Next ➡️"):
        if "--- Select ---" in [
            st.session_state.user_data['age'],
            st.session_state.user_data['weight'],
            st.session_state.user_data['sex'],
        ]:
            st.error("Please complete all fields.")
        else:
            next_step()
            st.rerun()

# ==========================================
# STEP 2: FOOT DETAILS
# ==========================================
elif st.session_state.step == 2:
    st.subheader("Step 2: Foot Details")
    st.caption("The more accurate you are here, the better your matches.")

    size_list = ["--- Select ---"]
    for i in range(4, 20):
        size_list.append(f"US {i}  |  EU {i + 33}")
        if i < 19:
            size_list.append(f"US {i}.5  |  EU {i + 33}")

    injury_options = [
        "None",
        "Overpronation / Collapsed Arches",
        "Supination / Underpronation",
        "Flat Feet",
        "High Arches",
        "Plantar Fasciitis",
        "Achilles Tendonitis",
        "Knee Pain (Runner's Knee / PFPS)",
        "IT Band Syndrome",
        "Shin Splints",
        "Stress Fractures",
        "Bunions",
        "Morton's Neuroma",
        "Heel Spurs",
        "Ankle Instability / Chronic Sprains",
        "Hip Pain",
        "Lower Back Pain",
        "Diabetic Foot",
        "Post-Surgery Recovery",
        "Neuropathy",
        "Weak Ankles (general)",
        "Other",
    ]

    col1, col2 = st.columns(2)
    with col1:
        st.session_state.user_data['shoe_size'] = st.selectbox("Shoe Size", size_list)
        st.session_state.user_data['width'] = st.selectbox(
            "Foot Width", ["--- Select ---", "Standard / Medium (D/B)", "Wide (2E)", "Extra Wide (4E+)", "Narrow"]
        )
    with col2:
        st.session_state.user_data['arch'] = st.selectbox(
            "Arch Type", ["--- Select ---", "I don't know", "Neutral", "High Arch", "Flat / Low Arch"]
        )
        st.session_state.user_data['injuries'] = st.multiselect(
            "Current Injuries / Concerns (select all that apply)",
            injury_options,
            default=st.session_state.user_data.get('injuries', [])
        )

    c1, c2 = st.columns([1, 1])
    with c1:
        st.button("⬅️ Back", on_click=prev_step)
    with c2:
        if st.button("Next ➡️", key="step2_next"):
            next_step()
            st.rerun()

# ==========================================
# STEP 3: ACTIVITIES
# ==========================================
elif st.session_state.step == 3:
    st.subheader("Step 3: Your Activities")
    st.caption("Select everything you do — we'll find the right gear for each.")

    activity_groups = {
        "🏃 Run & Walk": [
            "Road Running", "Trail Running", "Ultramarathon", "Cross Country Running",
            "Walking / Casual", "Race Walking", "Treadmill Running",
        ],
        "🥾 Outdoor & Adventure": [
            "Day Hiking", "Backpacking", "Mountaineering / Alpine",
            "Rock Climbing", "Water Sports / Kayaking", "Hunting / Fishing",
            "Cycling / Mountain Biking",
        ],
        "⛹️ Court Sports": [
            "Basketball", "Tennis", "Pickleball", "Volleyball",
            "Squash / Racquetball", "Badminton",
        ],
        "⚽ Field & Diamond Sports": [
            "Soccer", "Football (Cleats)", "Baseball / Softball",
            "Lacrosse", "Rugby", "Ultimate Frisbee", "Field Hockey",
        ],
        "💪 Gym & Studio": [
            "Weightlifting / CrossFit", "HIIT Training", "Yoga / Pilates",
            "Dance / Zumba", "Spin / Indoor Cycling", "Rowing / Erg",
        ],
        "🏔️ Snow & Ice": [
            "Downhill Skiing", "Cross-Country Skiing", "Snowboarding",
            "Ice Skating", "Snowshoeing",
        ],
        "🛹 Other": [
            "Skateboarding", "Golf", "Casual / Streetwear",
            "Work / Standing All Day", "Travel / Light Hiking",
            "Cowboy / Western",
        ],
    }

    selected = list(st.session_state.user_data.get('selected_activities', []))

    for group, acts in activity_groups.items():
        with st.expander(group, expanded=False):
            cols = st.columns(3)
            for idx, act in enumerate(acts):
                with cols[idx % 3]:
                    checked = st.checkbox(act, value=(act in selected), key=f"act_{act}")
                    if checked and act not in selected:
                        selected.append(act)
                    elif not checked and act in selected:
                        selected.remove(act)

    st.session_state.user_data['selected_activities'] = selected

    if selected:
        st.success(f"Selected: {', '.join(selected)}")

    c1, c2 = st.columns([1, 1])
    with c1:
        st.button("⬅️ Back", on_click=prev_step)
    with c2:
        if st.button("Next ➡️", key="step3_next"):
            if not selected:
                st.error("Select at least one activity.")
            else:
                next_step()
                st.rerun()

# ==========================================
# STEP 4: ACTIVITY-SPECIFIC DEEP DIVE
# ==========================================
elif st.session_state.step == 4:
    st.subheader("Step 4: The Specifics")
    st.caption("Tailored questions for each of your activities. The more you share, the better the match.")

    activities = st.session_state.user_data['selected_activities']

    for act in activities:
        occasional_key = f"{act}__occasional"
        is_occasional = st.session_state.get(occasional_key, False)

        with st.container(border=True):
            col_title, col_toggle = st.columns([3, 1])
            with col_title:
                st.markdown(f"**{act}**")
            with col_toggle:
                st.checkbox("Occasional activity", key=occasional_key,
                            help="Check this for activities you do less than once a week — we'll keep it brief.")

            if is_occasional:
                st.text_input(
                    "Any notes for this activity? (optional)",
                    placeholder="e.g. Just starting out, mostly for fun",
                    key=f"{act}__occasional_note"
                )
            else:
                questions = ACTIVITY_QUESTIONS.get(act, GENERIC_QUESTIONS)
                n_cols = 2
                rows = [questions[i:i+n_cols] for i in range(0, len(questions), n_cols)]
                for row in rows:
                    cols = st.columns(len(row))
                    for col, q in zip(cols, row):
                        with col:
                            render_question(q, act)

        st.write("")

    st.markdown("### Final Preferences")
    col_w1, col_w2 = st.columns(2)
    with col_w1:
        st.session_state.user_data['waterproof'] = st.selectbox(
            "Overall weather / waterproof need",
            ["Doesn't matter", "Nice to have", "Required for most activities"]
        )
    with col_w2:
        st.session_state.user_data['priorities'] = st.multiselect(
            "What matters most to you? (pick up to 3)",
            ["Comfort", "Price / Value", "Durability", "Style / Look",
             "Brand Name", "Lightweight", "Support / Stability", "Breathability", "Sustainability"],
            default=st.session_state.user_data.get('priorities', []),
            max_selections=3,
        )

    c1, c2 = st.columns([1, 2])
    with c1:
        st.button("⬅️ Back", on_click=prev_step)
    with c2:
        if st.button("🔍 Find My Gear"):
            next_step()
            st.rerun()

# ==========================================
# STEP 5: RESULTS
# ==========================================
elif st.session_state.step == 5:

    if st.session_state.ai_results is None:
        u = st.session_state.user_data

        # Read keys fresh at runtime
        api_key = get_secret("OPENAI_API_KEY")
        serpapi_key = get_secret("SERPAPI_KEY")

        # Injury-aware constraint notes
        injury_notes = []
        injuries = u.get('injuries', [])
        if "Overpronation / Collapsed Arches" in injuries or "Flat Feet" in injuries:
            injury_notes.append("CRITICAL: all queries MUST include 'motion control' or 'stability'. No neutral shoes.")
        if "Plantar Fasciitis" in injuries or "Heel Spurs" in injuries:
            injury_notes.append("Prioritize max cushion and arch support in every query.")
        if "Knee Pain (Runner's Knee / PFPS)" in injuries or "IT Band Syndrome" in injuries:
            injury_notes.append("Prioritize shock absorption and cushioning.")
        if "High Arches" in injuries or "Supination / Underpronation" in injuries:
            injury_notes.append("Neutral or cushioned shoes only. No motion control.")
        if "Achilles Tendonitis" in injuries:
            injury_notes.append("Prioritize lower heel drop (4–8mm) and cushioning.")
        if "Bunions" in injuries or "Morton's Neuroma" in injuries:
            injury_notes.append("All queries require wide toe box.")
        if "Ankle Instability / Chronic Sprains" in injuries or "Weak Ankles (general)" in injuries:
            injury_notes.append("Prioritize high-top or ankle-support designs.")

        width_note = ""
        if u.get('width') in ["Wide (2E)", "Extra Wide (4E+)"]:
            width_note = "Wide feet — include 'wide' or '2E'/'4E' in all queries."

        waterproof_note = ""
        if u.get('waterproof') == "Required for most activities":
            waterproof_note = "Waterproofing required — include 'waterproof' or 'Gore-Tex' in all queries."

        # Separate primary (regular) and occasional activities
        primary_activities = []
        occasional_activities = []
        activity_details = {}
        for act in u['selected_activities']:
            occasional = st.session_state.get(f"{act}__occasional", False)
            if occasional:
                note = st.session_state.get(f"{act}__occasional_note", "")
                occasional_activities.append(act)
                activity_details[act] = f"occasional — {note}" if note else "occasional"
            else:
                primary_activities.append(act)
                answers = collect_activity_answers(act)
                if answers:
                    activity_details[act] = "; ".join(f"{k}: {v}" for k, v in answers.items())
                else:
                    activity_details[act] = ""

        # Build shared context for all queries
        shared_context = "\n".join(filter(None, [
            f"User: Age {u['age']}, {u['sex']}, {u['weight']}, {u['height']}.",
            f"Shoe size: {u['shoe_size']}, Width: {u['width']}, Arch: {u['arch']}.",
            f"Injuries/Concerns: {', '.join(injuries) or 'None'}.",
            f"Waterproof need: {u.get('waterproof', 'N/A')}.",
            f"Priorities: {', '.join(u['priorities']) if u['priorities'] else 'None'}.",
            width_note,
            waterproof_note,
            *injury_notes,
        ]))

        # Build per-activity detail lines for the prompt
        activity_lines = []
        for act in primary_activities:
            detail = activity_details.get(act, "")
            activity_lines.append(f'  "{act}": details — {detail}' if detail else f'  "{act}"')

        activities_block = "\n".join(activity_lines)

        system_prompt = (
            "You are FITFXR, an expert footwear and outdoor gear AI.\n"
            "For each PRIMARY activity, generate exactly 3 Google Shopping search queries covering different product categories:\n"
            "  1. Primary footwear (shoes/boots/skates/cleats — the most important)\n"
            "  2. A closely related gear item (insoles, socks, bindings, pedals, etc.)\n"
            "  3. A second gear or apparel item that pairs well with the activity\n\n"
            "Return valid JSON only. Structure:\n"
            '{"Road Running": {"footwear": "Brooks Ghost 16 men stability running shoe", '
            '"gear_1": "Superfeet GREEN insoles arch support", '
            '"gear_2": "Balega Hidden Comfort running socks men"}, '
            '"Downhill Skiing": {"footwear": "Salomon S/Pro 120 ski boot men", '
            '"gear_1": "Superfeet Yellow ski boot insoles", '
            '"gear_2": "Smartwool ski socks men medium cushion"}}\n\n'
            "Rules:\n"
            "- Name a specific real brand and model in EVERY query\n"
            "- Footwear must match the activity exactly (boots/cleats/skates/shoes — never generic)\n"
            "- ALWAYS include the user's sex in footwear queries: 'women's' for Woman, 'men's' for Man\n"
            "- ALWAYS apply arch type to footwear queries (e.g. 'high arch support', 'neutral arch')\n"
            "- Apply ALL injury, width, and waterproof requirements to the footwear query\n"
            "- gear_1 and gear_2 must be distinct product types (not two shoe queries)\n"
            "- Every query must find a buyable product on Google Shopping\n"
            "- Return ONLY the JSON object — no markdown, no explanation"
        )

        user_message = (
            f"{shared_context}\n\n"
            f"PRIMARY activities to generate queries for:\n{activities_block}"
        )

        with st.spinner("Be patient — just taking the time to get your fit right..."):
            try:
                client = OpenAI(api_key=api_key)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ],
                    response_format={"type": "json_object"}
                )
                queries = json.loads(response.choices[0].message.content)
            except Exception as e:
                st.error(f"AI Error: {e}")
                st.stop()

        # Activity-specific fallback search terms (used when AI query returns no results)
        ACTIVITY_FALLBACK_TERMS = {
            "Road Running": "men's road running shoes",
            "Trail Running": "trail running shoes",
            "Ultramarathon": "ultra marathon trail running shoes",
            "Cross Country Running": "cross country running shoes",
            "Race Walking": "race walking shoes",
            "Treadmill Running": "treadmill running shoes",
            "Walking / Casual": "walking shoes",
            "Day Hiking": "day hiking boots",
            "Backpacking": "backpacking boots",
            "Mountaineering / Alpine": "mountaineering boots",
            "Rock Climbing": "rock climbing shoes",
            "Water Sports / Kayaking": "water shoes kayaking",
            "Hunting / Fishing": "hunting boots",
            "Cycling / Mountain Biking": "mountain bike shoes SPD",
            "Basketball": "basketball shoes",
            "Tennis": "tennis shoes",
            "Pickleball": "pickleball shoes",
            "Volleyball": "volleyball shoes",
            "Squash / Racquetball": "squash court shoes",
            "Badminton": "badminton shoes",
            "Soccer": "soccer cleats",
            "Football (Cleats)": "football cleats",
            "Baseball / Softball": "baseball cleats",
            "Lacrosse": "lacrosse cleats",
            "Rugby": "rugby boots",
            "Ultimate Frisbee": "cleats ultimate frisbee",
            "Field Hockey": "field hockey turf shoes",
            "Weightlifting / CrossFit": "weightlifting shoes",
            "HIIT Training": "cross training shoes",
            "Yoga / Pilates": "yoga grip socks",
            "Dance / Zumba": "dance aerobics shoes",
            "Spin / Indoor Cycling": "indoor cycling shoes SPD",
            "Rowing / Erg": "rowing training shoes",
            "Downhill Skiing": "downhill ski boots",
            "Cross-Country Skiing": "cross country ski boots",
            "Snowboarding": "snowboard boots",
            "Ice Skating": "ice skates",
            "Snowshoeing": "winter hiking boots waterproof",
            "Skateboarding": "skate shoes vulcanized",
            "Golf": "golf shoes spikeless",
            "Work / Standing All Day": "work shoes slip resistant",
            "Casual / Streetwear": "casual sneakers",
            "Travel / Light Hiking": "travel walking shoes",
            "Cowboy / Western": "western boots",
        }

        def serpapi_search(query):
            s = GoogleSearch({
                "engine": "google_shopping",
                "q": query,
                "api_key": serpapi_key,
                "num": 10,
                "gl": "us",
                "hl": "en",
            })
            return s.get_dict().get("shopping_results", [])[:10]

        # Build a case-insensitive lookup for AI queries in case keys don't match exactly
        queries_lower = {k.lower(): v for k, v in queries.items()}

        # Category labels shown as section headers in results
        CATEGORY_LABELS = {
            "footwear": "👟 Footwear",
            "gear_1":   "🎒 Gear & Accessories",
            "gear_2":   "⚡ Also Recommended",
        }

        def search_with_fallback(ai_query, fallback_query):
            """Try AI query, fall back to generic if no results."""
            if ai_query:
                products = serpapi_search(ai_query)
                if products:
                    return ai_query, products
            products = serpapi_search(fallback_query)
            return fallback_query, products

        results = {}
        with st.spinner("Searching live stores — your perfect fit is worth the wait..."):
            for act in primary_activities:
                ai_act = queries.get(act) or queries_lower.get(act.lower()) or {}
                # Support both old flat string format and new dict format from AI
                if isinstance(ai_act, str):
                    ai_act = {"footwear": ai_act}
                generic_footwear = ACTIVITY_FALLBACK_TERMS.get(act, f"{act} shoes")
                categories = {}
                try:
                    for cat_key, cat_label in CATEGORY_LABELS.items():
                        ai_query = ai_act.get(cat_key, "")
                        fallback = generic_footwear if cat_key == "footwear" else ""
                        if not ai_query and not fallback:
                            continue
                        used_q, products = search_with_fallback(ai_query, fallback or ai_query)
                        categories[cat_key] = {
                            "label": cat_label,
                            "query": used_q,
                            "products": products,
                        }
                    results[act] = {"categories": categories}
                except Exception as e:
                    results[act] = {"categories": {}, "error": str(e)}

        st.session_state.ai_results = results

        # ── Persist session to database ────────────────────────────────────────
        try:
            from db import save_session
            results_to_save = {
                act: {**res, "answers": collect_activity_answers(act)}
                for act, res in results.items()
            }
            save_session(
                user_data=st.session_state.user_data,
                ai_results=results_to_save,
                username=st.session_state.get("username", "Guest"),
                is_guest=not st.session_state.get("logged_in", False),
            )
        except Exception:
            pass  # Never block the user if DB is unavailable

        if st.session_state.get("logged_in"):
            try:
                from db import save_user_profile
                save_user_profile(st.session_state.get("username"), st.session_state.user_data)
            except Exception:
                pass

    # ── Render results ────────────────────────────────────────────────────────
    st.balloons()

    _uname = st.session_state.username
    _dname = _uname.split("@")[0] if "@" in _uname else _uname
    greeting = f"Your Gear, {_dname}" if st.session_state.logged_in else "Your Gear Matches"
    st.subheader(greeting)

    if st.session_state.logged_in:
        st.success("✅ Results saved to your profile. Come back any time.")
    else:
        st.warning("💡 Create a free account to save these results and build your personalized feed.")

    with st.expander("📢 Know someone who needs better gear? Share FITFXR"):
        st.write("Send them to FITFXR — you'll both unlock early access perks.")
        st.code("https://fitfxr.com", language=None)
        st.caption("Referrals help us grow and unlock exclusive partner deals for our community.")

    col_ns, _ = st.columns([1, 3])
    with col_ns:
        if st.button("🔁 Start New Search", key="new_search_top"):
            for k in list(st.session_state.keys()):
                if k.startswith("act_") or "__" in k or k.startswith("offset_") or k.startswith("more_"):
                    try:
                        del st.session_state[k]
                    except Exception:
                        pass
            st.session_state.ai_results = None
            st.session_state.user_data = {
                'age': None, 'sex': None, 'weight': None, 'height': None,
                'shoe_size': None, 'width': None, 'arch': None,
                'injuries': [],
                'selected_activities': [],
                'priorities': [],
                'waterproof': None,
            }
            st.session_state.step = 1
            st.rerun()

    st.divider()

    activity_results = st.session_state.ai_results
    if not activity_results:
        st.warning("No results were generated. Try going back and adjusting your activities.")
    else:
        acts = list(activity_results.keys())
        selected_act = st.selectbox(
            "Viewing results for:",
            acts,
            index=len(acts) - 1,
            key="results_activity_selector"
        )
        st.markdown(f"### {selected_act}")
        st.divider()

        data = activity_results[selected_act]
        if data.get("error"):
            st.warning(f"Search error: {data['error']}")
        else:
            categories = data.get("categories", {})
            if not categories:
                st.info(f"No results found for **{selected_act}**. Try the Refine button below.")
            else:
                for cat_key, cat_data in categories.items():
                    st.subheader(cat_data["label"])
                    st.caption(f"Searched: *{cat_data['query']}*")
                    render_product_tiers(cat_data.get("products", []), selected_act, cat_key)
                    st.divider()

    st.divider()
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔄 Refine My Search"):
            st.session_state.ai_results = None
            st.session_state.step = 4
            st.rerun()
    with col_b:
        if st.button("🔁 Start New Search"):
            for k in list(st.session_state.keys()):
                if k.startswith("act_") or "__" in k or k.startswith("offset_") or k.startswith("more_"):
                    try:
                        del st.session_state[k]
                    except Exception:
                        pass
            st.session_state.ai_results = None
            st.session_state.user_data = {
                'age': None, 'sex': None, 'weight': None, 'height': None,
                'shoe_size': None, 'width': None, 'arch': None,
                'injuries': [],
                'selected_activities': [],
                'priorities': [],
                'waterproof': None,
            }
            st.session_state.step = 1
            st.rerun()
