
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
    }
    try:
        return requests.get(url, headers=headers, timeout=20).text
    except:
        return ""


# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


# =========================================
# ✅ CVS EXTRACTION
# =========================================
def get_cvs_text(url):
    html = get_html(url)
    text = clean_text(html)

    title = ""
    description = ""

    t = re.search(r"[A-Z].+?(Count|Ct)", text)
    if t:
        title = t.group(0)

    d = re.search(r"(Get .*?)(Reviews|Rated|Ingredients)", text)

    if d:
        description = d.group(1)
    else:
        description = text[:800]

    return title, description


# =========================================
# ✅ SALSIFY EXTRACTION (NEW ✅)
# =========================================
def get_salsify_text(url):
    html = get_html(url)
    text = clean_text(html)

    title = ""
    description = ""

    t = re.search(r"[A-Z].+?(Count|Ct)", text)
    if t:
        title = t.group(0)

    d = re.search(r"(Get .*?)(Features|Directions|Ingredients|Details)", text)

    if d:
        description = d.group(1)
    else:
        description = text[:800]

    return title, description


# =========================================
# ✅ NORMALIZE
# =========================================
def normalize(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower())


# =========================================
# ✅ FEATURE MATCH
# =========================================
def match_features(features, cvs_desc):
    results = []

    desc = normalize(cvs_desc)

    for f in features:
        words = normalize(f).split()

        matches = sum(1 for w in words if w in desc)
        score = matches / len(words) if words else 0

        if score > 0.5:
            results.append(f)
        else:
            results.append("❌ Missing")

    return results


# =========================================
# ✅ UI
# =========================================
st.title("PDP QA Tool")

file = st.file_uploader("Upload CSV", type="csv")

if file:
    df = pd.read_csv(file)

    for _, row in df.iterrows():

        st.header("General Product Title")

        cvs_title, cvs_desc = get_cvs_text(row["retail_url"])
        s_title, s_desc = get_salsify_text(row["salsify_url"])

        st.write("Salsify:", s_title)
        st.write("CVS:", cvs_title)

        # ✅ Title match
        score = 0
        if s_title:
            score = int(
                100 * len(set(normalize(s_title).split()) &
                          set(normalize(cvs_title).split()))
                / len(set(normalize(s_title).split()))
            )

        st.write("Match:", f"{score}%")

        # =========================================
        st.header("General Description")

        st.write("Salsify:", s_desc)
        st.write("CVS:", cvs_desc)

        desc_score = 0
        if s_desc:
            desc_score = int(
                100 * len(set(normalize(s_desc).split()) &
                          set(normalize(cvs_desc).split()))
                / len(set(normalize(s_desc).split()))
            )

        st.write("Match:", f"{desc_score}%")

        # =========================================
        st.header("Features")

        # ✅ simulate features from Salsify description
        s_features = re.split(r"\.\s+", s_desc)[:5]

        cvs_results = match_features(s_features, cvs_desc)

        for i in range(len(s_features)):
            st.write(
                "•", s_features[i],
                "| CVS:", cvs_results[i]
            )

        st.divider()
