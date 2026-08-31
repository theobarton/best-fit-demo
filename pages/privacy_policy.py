import streamlit as st

st.set_page_config(page_title="FITFXR — Privacy Policy", page_icon="👟", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #F5F6FA; }
    h1, h2, h3 { color: #1B2F6E !important; }
</style>
""", unsafe_allow_html=True)

st.title("FITFXR Privacy Policy")
st.caption("Last updated: August 30, 2026")

st.markdown("""
FITFXR Inc. ("FITFXR," "we," "us," or "our") operates the FITFXR gear-fitting
application (the "Service"). This Privacy Policy explains what information we
collect when you use the Service, how we use it, and the choices you have.

By using FITFXR, you agree to the collection and use of information in
accordance with this policy.

---

## 1. Information We Collect

**Account information.** If you create an account, we collect your email
address and password. Passwords are never stored in plain text — authentication
is handled by our database provider, Supabase, using industry-standard
hashing.

**Fit profile information.** To generate recommendations, we ask for
self-reported details about you and your gear needs:

- Age range, sex, weight range, and height range
- Shoe size, foot width, and arch type
- Injuries or foot/body concerns you select from a list (e.g., plantar
  fasciitis, flat feet)
- Activities you do (e.g., road running, hiking, basketball) and
  activity-specific preferences (e.g., cushioning, terrain, waterproofing)
- What matters most to you when choosing gear (e.g., price, durability, style)

This information is collected as **broad ranges and categories, not precise
measurements** — for example, we ask for a weight *range* like "160–180 lbs,"
never an exact weight, and an age *range* rather than a birthdate. We do not
collect health records, medical diagnoses, government ID numbers, or other
information that would let us or anyone else identify you as an individual
from the fit data alone.

**Usage and results data.** We store the search queries our system generates
on your behalf and the products shown to you, so we can improve match quality
and (for account holders) show you your past results.

**Automatically collected data.** Like most web applications, our hosting
provider logs standard technical data such as IP address, browser type, and
access timestamps for security and reliability purposes.

## 2. How We Use Information

We use the information above to:

- Generate personalized footwear and gear recommendations, including by
  sending profile details (without your email or name) to our AI provider
  (OpenAI) to build search queries, and to Google Shopping (via SerpAPI) to
  find real, in-stock products
- Save your fit profile so you don't have to re-enter it on future visits
- Maintain, secure, and improve the Service
- Analyze aggregate trends (e.g., which cushioning types are most requested
  for plantar fasciitis) to improve our matching logic
- Communicate with you about your account, if you have one

## 3. How We Share Information

**Service providers.** We share data with the vendors that make the Service
work, solely to provide that service to you:

| Provider | Purpose |
|---|---|
| Supabase | Account authentication and database storage |
| OpenAI | Generating gear search queries from your fit profile |
| SerpAPI / Google Shopping | Finding real, buyable products |
| Render | Application hosting |

These providers only receive the data needed to perform their function and
are contractually restricted from using it for their own purposes.

**Aggregated and de-identified data.** We may share or sell **aggregated,
de-identified, or anonymized data** — for example, trends like "35% of trail
runners with high arches prefer a specific cushioning level" — with gear
brands and commercial partners, to help fund the free tools we offer and to
help brands build better products. This aggregated data is derived from many
users' profiles combined together and **does not include your name, email
address, account identifiers, or any information that could reasonably be
used to identify you individually.**

**We do not sell your personal information** (your email, account, or any
data that identifies you specifically) to third parties, and we never will
without providing clear notice and a choice beforehand.

**Legal requirements.** We may disclose information if required by law, or
to protect the rights, property, or safety of FITFXR, our users, or others.

## 4. Data Retention

We retain account and fit profile information for as long as your account is
active, so we can keep providing personalized results. Guest (non-account)
sessions are retained only in aggregate/analytical form. You can request
deletion of your account and associated data at any time (see Section 6).

## 5. Your Choices & Rights

- **Access & correction.** Log in at any time to view and update your fit
  profile.
- **Deletion.** Email us at **privacy@fitfxr.com** to request deletion of your
  account and personal data. We'll complete verified requests within 30 days.
- **Opt out of data sharing.** If you'd prefer your profile never be included
  in aggregated data shared with partners, email us at the address above and
  we will exclude your account.
- **California residents (CCPA/CPRA).** You have the right to know what
  personal information we collect, request its deletion, and opt out of any
  "sale" or "sharing" of personal information as those terms are defined
  under California law. Because the data we share with partners is
  aggregated and de-identified, it generally falls outside these
  definitions — but you may still submit a request to the email above and we
  will honor it.
- **EU/UK residents (GDPR).** You have the right to access, correct, export,
  or delete your data, and to object to certain processing. Contact us at the
  email above to exercise these rights.

## 6. Children's Privacy

FITFXR is not directed to children under 13, and we do not knowingly collect
information from children under 13. If you believe a child has provided us
information, contact us and we will delete it.

## 7. Security

We use industry-standard safeguards — including encrypted connections,
hashed passwords, and database-level access controls (Row Level Security) —
to protect your information. No method of transmission or storage is 100%
secure, and we cannot guarantee absolute security.

## 8. Cookies & Local Session Data

FITFXR uses local session storage to keep you logged in and to remember your
in-progress answers as you move through the questionnaire. We do not use
third-party advertising trackers.

## 9. Changes to This Policy

We may update this Privacy Policy as FITFXR grows. We'll update the "Last
updated" date above, and for material changes we'll provide more prominent
notice (such as an in-app banner or email).

## 10. Contact Us

Questions about this policy or your data? Email **privacy@fitfxr.com**.

---

*This policy is written in plain language for our beta launch and will be
reviewed by counsel as FITFXR scales. It is not a substitute for legal
advice.*
""")
