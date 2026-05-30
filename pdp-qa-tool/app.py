
import streamlit as st
import pandas as pd
import requests
import re


# =========================================
# ✅ FETCH HTML (keep this exactly)
# =========================================
def get_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    try:
        return requests.get(url, headers=headers, timeout=20).text
    except:
        return ""


# =========================================
# ✅ CVS EXTRACTION (THIS IS YOUR WORKING VERSION)
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    title = ""
    description = ""

    if not html:
        return {"title": "", "description": ""}

    # ✅ TITLE (this part was already working)
    t = re.search(r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)', html)
    if t:
        title = t.group(0).strip()

    # ✅ DESCRIPTION (THIS IS THE KEY PART ✅)
    d = re.search(
        r'Get up to .*?latest fashion trends',
        html,
        re.DOTALL
    )

    if d:
        raw = d.group(0)

        # ✅ CLEAN (light cleanup only)
        raw = raw.replace('\\"', '')
        raw = raw.replace('\\n', ' ')
        raw = raw.replace('","', '. ')
        raw = raw.replace('"', '')

        raw = re.sub('<.*?>', '', raw)
        raw = re.sub(r'\s+', ' ', raw).strip()

        description = raw

    return {
        "title": title,
        "description": description
    }


# =========================================
# ✅ SALSIFY EXTRACTION (same logic)
# =========================================
def get_salsify_text(url):
    html = get_html(url)

    title = ""
    description = ""

    if not html:
        return "", ""

    t = re.search(r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)', html)
    if t:
        title = t.group(0).strip()

    d = re.search(
        r'Get up to .*?latest fashion trends',
        html,
        re.DOTALL
    )

    if d:
        raw = d.group(0)

        raw = raw.replace('\\"', '')
        raw = raw.replace('\\n', ' ')
        raw = raw.replace('","', '. ')
        raw = raw.replace('"', '')

        raw = re.sub('<.*?>', '', raw)
        raw = re.sub(r'\s+', ' ', raw).strip()

        description = raw

    return title, description


# =========================================
# ✅ NORMALIZE TEXT
# =========================================
def normalize(text):
    return re.sub(r'[^a-z0-9 ]', '', str(text).lower())


# =========================================
# ✅ FEATURE MATCHING (THIS PART WORKED BEFORE)
# =========================================
def match_features(description):
    sentences = re.split(r'\.\s+', description)

    features = []

    for s in sentences:
        s = s.strip()
        if (
            20 < len(s) < 120 and
            any(word in s.lower() for word in [
                "tampon",
                "leak",
                "compact",
                "wrapped",
                "comfort"
            ])
        ):
            features.append(s)

    return list(dict.fromkeys(features))[:5]


# =========================================
# ✅ STREAMLIT APP
# =========================================
st.title("PDP QA Tool")

file = st.file_uploader("Upload CSV", type="csv")

if file:
    df = pd.read_csv(file)

    st.write("Columns:", df.columns.tolist())

    for _, row in df.iterrows():

        cvs = get_cvs_text(row["retail_url"])
        s_title, s_desc = get_salsify_text(row["salsify_url"])

        # ============================
        st.header("General Product Title")

        st.write("Salsify:", s_title)
        st.write("CVS:", cvs["title"])

        title_score = 0
        if s_title:
            title_score = int(
                100 * len(set(normalize(s_title).split()) &
                          set(normalize(cvs["title"]).split()))
                / len(set(normalize(s_title).split()))
            )

        st.write("Match:", f"{title_score}%")

        # ============================
        st.header("General Description")

        st.write("Salsify:", s_desc)
        st.write("CVS:", cvs["description"])

        desc_score = 0
        if s_desc:
            desc_score = int(
                100 * len(set(normalize(s_desc).split()) &
                          set(normalize(cvs["description"]).split()))
                / len(set(normalize(s_desc).split()))
            )

        st.write("Match:", f"{desc_score}%")

        # ============================
        st.header("Features")

        cvs_features = match_features(cvs["description"])

        for f in cvs_features:
            st.write("•", f)

        st.divider()
