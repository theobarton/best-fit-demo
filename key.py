import os
import sys
from dotenv import load_dotenv
from openai import OpenAI

# 1. Setup & Load Key
load_dotenv()
api_key = os.environ.get("OPENAI_API_KEY")

if not api_key:
    print("❌ Error: OPENAI_API_KEY not found in .env file.")
    sys.exit(1)

client = OpenAI(api_key=api_key)

def get_input(prompt_text, choices=None):
    """Helper to ask questions and optionally show valid choices."""
    if choices:
        print(f"\nOptions: {', '.join(choices)}")
    return input(f"{prompt_text}: ").strip()

def main():
    # --- INTRO ---
    print("\n" + "="*60)
    print("Welcome to BEST FIT! 👟🏔️")
    print("We use AI to match you with the best gear for your pursuits.")
    print("="*60 + "\n")
    
    print("Let's get started. Please enter the following information.\n")

    # --- DATA COLLECTION (Merged from your prompt + Rob's notes) ---
    user_data = {}
    
    # Biometrics
    user_data['age'] = get_input("AGE")
    user_data['sex'] = get_input("SEX (Man/Woman/Unisex/Prefer Not to Share)")
    user_data['weight'] = get_input("WEIGHT (lbs)")
    user_data['height'] = get_input("HEIGHT")
    user_data['shoe_size'] = get_input("SHOE SIZE")
    user_data['foot_shape'] = get_input("FOOT WIDTH (Wide/Narrow/Standard)")
    user_data['arch'] = get_input("ARCH TYPE (High/Neutral/Flat/Don't know)")
    user_data['injury'] = get_input("CURRENT INJURY/PAIN (None/Knees/Shins/Heels/Other)")

    # Activity & Usage
    activities = [
        "Hiking", "Running", "Trail Running", "Walking", "Backpacking", 
        "Soccer", "Basketball", "Football", "Tennis", "Pickleball", 
        "Baseball", "Skateboarding", "Skiing", "Snowboarding", "Training"
    ]
    user_data['activity'] = get_input("SPECIFIC ACTIVITY", choices=activities)
    user_data['frequency'] = get_input("FREQUENCY (How often will you use this?)")
    user_data['experience'] = get_input("EXPERIENCE LEVEL (Beginner/Experienced/Expert)")
    
    # Environment
    user_data['terrain'] = get_input("TERRAIN (Road/Trail/Gym/Mud/Snow/Rocky)")
    user_data['weather'] = get_input("WATERPROOF NEEDED? (Yes/No)")

    # Preferences
    user_data['priorities'] = get_input("WHAT MATTERS MOST? (Cost/Comfort/Durability/Look/Brand)")

    print("\n" + "-"*60)
    print("PLEASE BE PATIENT WHILE WE GATHER YOUR RESULTS...")
    print("-"*60 + "\n")

    # --- CONSTRUCTING THE AI PROMPT ---
    system_prompt = (
        "You are 'BEST FIT', an expert gear consultant AI. "
        "Your goal is to recommend footwear/gear based on user biometrics and specific needs. "
        "You must provide exactly 3 recommendations categorized by price: "
        "1. High End / Premium "
        "2. Moderate / Best Value "
        "3. Low / Budget Friendly "
        "For each item, explain WHY it fits their specific foot shape, injury history, and terrain."
    )

    user_message = (
        f"User Profile:\n"
        f"- Age/Sex: {user_data['age']}, {user_data['sex']}\n"
        f"- Size: {user_data['shoe_size']} ({user_data['foot_shape']}), Arch: {user_data['arch']}\n"
        f"- Weight/Height: {user_data['weight']}, {user_data['height']}\n"
        f"- Injury History: {user_data['injury']}\n"
        f"- Activity: {user_data['activity']} ({user_data['frequency']})\n"
        f"- Terrain/Conditions: {user_data['terrain']}, Waterproof: {user_data['weather']}\n"
        f"- Experience: {user_data['experience']}\n"
        f"- Top Priority: {user_data['priorities']}\n\n"
        "Please provide the 3 recommendations now."
    )

    # --- CALLING OPENAI ---
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini", # Using a smart model for better reasoning
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        )
        
        # --- DISPLAY RESULTS ---
        print("\nHERE’S WHAT WE THINK ARE YOUR BEST CHOICES:\n")
        print(completion.choices[0].message.content)
        
        print("\n" + "="*60)
        print("WOULD YOU LIKE US TO SEND YOU LINKS TO PURCHASE THESE? (Simulated)")
        print("PLEASE LET US KNOW WHAT YOU THINK BY ANSWERING A SHORT SURVEY.")
        print("="*60 + "\n")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()