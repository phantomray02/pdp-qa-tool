
import streamlit as st
import pandas as pd
import requests
import re


# =========================================
# ✅ FETCH HTML
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
# ✅ CLEAN DESCRIPTION STRING
# =========================================
def clean_text(raw):
    raw = raw.replace('\\"', '')
    raw = raw.replace('\\n', ' ')
    raw = raw.replace('&amp;', '&')
    raw = re.sub(r'<.*?>', '', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()


# =========================================
# ✅ CVS TEXT EXTRACTION (FIXED + STABLE)
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    title = ""
    description = ""

    if not html:
        return {"title": "", "description": ""}

    # ✅ TITLE
    t = re.search(r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)', html)
    if t:
        title = t.group(0).strip()

    # ✅ DESCRIPTION (SAFE WINDOW FROM LABEL)
    start = html.find("General Description")

    if start != -1:
        raw = html[start:start + 2000]  # grab safe chunk

        raw = clean_text(raw)

        # remove label
        raw = raw.replace("General Description", "").strip()

        # stop at next section-like keyword
        raw = re.split(r'(Reviews|Ingredients|Directions|Highlights)', raw)[0]

        description = raw.strip()
    else:
        description = ""

    return {
        "title": title,
        "description": description
    }


# =========================================
# ✅ SALSIFY TEXT EXTRACTION (SAME LOGIC)
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

    start = html.find("General Description")

    if start != -1:
        raw = html[start:start + 2000]

        raw = clean_text(raw)
        raw = raw.replace("General Description", "").strip()

        raw = re.split(r'(Reviews|Ingredients|Directions|Highlights)', raw)[0]

        description = raw.strip()
    else:
        description = ""

    return title, description


# =========================================
# ✅ NORMALIZATION
# =========================================
def normalize(text):
    return re.sub(r'[^a-z0-9 ]', '', str(text).lower())


# =========================================
# ✅ FEATURE MATCHING (STABLE + SCALABLE)
# =========================================
def match_features(salsify_desc, cvs_desc):

    s_sentences = re.split(r'\.\s+', salsify_desc)

    results = []
    cv = normalize(cvs_desc)

    for s in s_sentences[:5]:
        words = normalize(s).split()

        if not words:
            results.append("❌ Missing")
            continue

        matches = sum(1 for w in words if w in cv)
        score = matches / len(words)

        if score > 0.5:
            results.append(s)
        else:
            results.append("❌ Missing")

    return s_sentences[:5], results


# =========================================
# ✅ STREAMLIT UI
# =========================================
st.title("PDP QA Tool")

file = st.file_uploader("Upload CSV", type="csv")

if file:
    df = pd.read_csv(file)

    st.write("Columns:", df.columns.tolist())

    for _, row in df.iterrows():

        cvs_url = row["retail_url"]
        salsify_url = row["salsify_url"]

        cvs = get_cvs_text(cvs_url)
        s_title, s_desc = get_salsify_text(salsify_url)

        # =========================================
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

        # =========================================
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

        # =========================================
        st.header("Features")

        s_feats, cvs_results = match_features(
            s_desc,
            cvs["description"]
        )

        for i in range(len(s_feats)):
            st.write(
                "•", s_feats[i],
                "| CVS:", cvs_results[i]
            )

        st.divider()
