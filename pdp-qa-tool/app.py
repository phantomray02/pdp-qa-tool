
import streamlit as st
import pandas as pd
import requests
import re
from bs4 import BeautifulSoup


# =========================================
# ✅ FETCH HTML
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
# ✅ CLEAN PAGE TEXT (IMMERSIVE STYLE)
# =========================================
def extract_visible_text(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ")

    text = re.sub(r"\s+", " ", text)
    return text.strip()


# =========================================
# ✅ CVS TEXT EXTRACTION (STABLE)
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    if not html:
        return {"title": "", "description": ""}

    text = extract_visible_text(html)

    title = ""
    description = ""

    # ✅ TITLE
    t = re.search(r"[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)", text)
    if t:
        title = t.group(0)

    # ✅ DESCRIPTION (broad but stable)
    d = re.search(
        r"(Get .*?)(?:Rated|Reviews|Directions|Ingredients)",
        text,
        re.DOTALL
    )

    if d:
        description = d.group(1).strip()
    else:
        description = text[:1000]

    return {
        "title": title,
        "description": description
    }


# =========================================
# ✅ NORMALIZATION
# =========================================
def normalize(text):
    return re.sub(r"[^a-z0-9 ]", "", str(text).lower())


# =========================================
# ✅ FEATURE MATCHING (THIS FIXES YOUR ISSUE)
# =========================================
def match_features(salsify_features, cvs_description):
    results = []
    scores = []

    desc = normalize(cvs_description)

    for feat in salsify_features:
        f = normalize(feat)

        words = f.split()
        matches = sum(1 for w in words if w in desc)
        score = matches / len(words) if words else 0

        if score >= 0.5:
            results.append(feat)
        else:
            results.append("❌ Missing")

        scores.append(int(score * 100))

    return results, scores


# =========================================
# ✅ STREAMLIT UI
# =========================================
st.title("PDP QA Tool")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

if uploaded_file:
    df = pd.read_csv(uploaded_file)

    # ✅ show column names (so no KeyError again)
    st.write("Detected Columns:", df.columns.tolist())

    # =========================================
    # ✅ AUTO COLUMN DETECTION (no guessing)
    # =========================================
    def find_col(name_list):
        for col in df.columns:
            for name in name_list:
                if name.lower() in col.lower():
                    return col
        return None

    TITLE_COL = find_col(["title", "name"])
    DESC_COL = find_col(["description"])
    FEAT_COL = find_col(["feature", "bullet"])
    URL_COL = find_col(["url"])

    st.write("Using Columns:", TITLE_COL, DESC_COL, FEAT_COL, URL_COL)

    # =========================================
    # ✅ MAIN LOOP
    # =========================================
    for _, row in df.iterrows():

        st.header("General Product Title")

        s_title = row.get(TITLE_COL, "")
        s_desc = row.get(DESC_COL, "")
        s_feats_raw = row.get(FEAT_COL, "")
        url = row.get(URL_COL, "")

        r_text = get_cvs_text(url)

        st.write("Salsify:", s_title)
        st.write("CVS:", r_text["title"])

        # ✅ TITLE MATCH
        title_score = 0
        if r_text["title"]:
            title_words = set(normalize(s_title).split())
            cvs_words = set(normalize(r_text["title"]).split())

            if title_words:
                title_score = int(
                    100 * len(title_words & cvs_words) / len(title_words)
                )

        st.write("Match:", f"{title_score}%")

        # =========================================
        st.header("General Description")

        st.write("Salsify:", s_desc)
        st.write("CVS:", r_text["description"])

        desc_score = 0
        if r_text["description"]:
            s = set(normalize(s_desc).split())
            c = set(normalize(r_text["description"]).split())

            if s:
                desc_score = int(100 * len(s & c) / len(s))

        st.write("Match:", f"{desc_score}%")

        # =========================================
        st.header("Features")

        # ✅ split Salsify features safely
        salsify_features = str(s_feats_raw).split("|")

        cvs_results, scores = match_features(
            salsify_features,
            r_text["description"]
        )

        for i in range(len(salsify_features)):
            st.write(
                f"• {salsify_features[i]}",
                " | CVS:", cvs_results[i],
                " | Match:", f"{scores[i]}%" if cvs_results[i] != "❌ Missing" else "--"
            )

        st.divider()
