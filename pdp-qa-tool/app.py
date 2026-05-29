
import streamlit as st
import pandas as pd
import requests
import re
from bs4 import BeautifulSoup

# =========================================
# ✅ FETCH HTML (important for CVS)
# =========================================
def get_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/"
    }

    try:
        res = requests.get(url, headers=headers, timeout=20)
        return res.text
    except:
        return ""

# =========================================
# ✅ CLEAN TEXT (like immersive reader)
# =========================================
def extract_visible_text(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ")

    text = re.sub(r"\s+", " ", text)
    return text.strip()

# =========================================
# ✅ GET CVS TEXT (STABLE VERSION)
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    if not html:
        return {"title": "", "description": "", "features": []}

    text = extract_visible_text(html)

    title = ""
    description = ""

    # ✅ TITLE
    t = re.search(r"[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)", text)
    if t:
        title = t.group(0)

    # ✅ DESCRIPTION (generic capture)
    d = re.search(
        r"(Get .*?)(?:Rated|Reviews|Ingredients|Directions)",
        text,
        re.DOTALL
    )

    if d:
        description = d.group(1).strip()
    else:
        description = text[:800]

    return {
        "title": title,
        "description": description,
        "features": []  # we DO NOT scrape features anymore
    }

# =========================================
# ✅ FEATURE MATCHING (THIS FIXES EVERYTHING)
# =========================================
def normalize(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower())

def match_features(salsify_features, cvs_description):
    results = []
    scores = []

    desc = normalize(cvs_description)

    for feat in salsify_features:
        f = normalize(feat)

        words = f.split()
        match_count = sum(1 for w in words if w in desc)
        score = match_count / len(words) if words else 0

        if score >= 0.5:
            results.append(feat)
        else:
            results.append("❌ Missing")

        scores.append(round(score * 100))

    return results, scores

# =========================================
# ✅ STREAMLIT APP
# =========================================
st.title("PDP QA Tool")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

if uploaded_file:
    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.header("General Product Title")

        # ✅ CVS data
        r_text = get_cvs_text(row["retail_url"])

        st.write("Salsify:", row["title"])
        st.write("CVS:", r_text["title"])

        # simple title match
        title_score = 0
        if r_text["title"]:
            title_score = int(
                100 * (len(set(r_text["title"].lower().split()) &
                           set(row["title"].lower().split()))
                       / len(row["title"].lower().split()))
            )

        st.write("Match:", f"{title_score}%")

        # ============================
        # ✅ DESCRIPTION
        # ============================
        st.header("General Description")

        st.write("Salsify:", row["description"])
        st.write("CVS:", r_text["description"])

        desc_score = 0
        if r_text["description"]:
            s = normalize(row["description"])
            c = normalize(r_text["description"])

            desc_score = int(
                100 * (len(set(s.split()) & set(c.split())) / len(s.split()))
            )

        st.write("Match:", f"{desc_score}%")

        # ============================
        # ✅ FEATURES (CORRECT WAY ✅)
        # ============================
        st.header("Features")

        # Expect your CSV column already split
        salsify_features = str(row["features"]).split("|")

        cvs_results, scores = match_features(
            salsify_features,
            r_text["description"]
        )

        for i in range(len(salsify_features)):
            st.write(
                "•", salsify_features[i],
                " | CVS:", cvs_results[i],
                " | Match:", f"{scores[i]}%" if cvs_results[i] != "❌ Missing" else "--"
            )
