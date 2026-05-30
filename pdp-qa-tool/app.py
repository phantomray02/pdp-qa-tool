
import streamlit as st
import pandas as pd
import requests
import re


# =========================================
# ✅ GET HTML
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
# ✅ GET CVS IMAGE
# =========================================
def get_cvs_image(html):
    m = re.search(r'"imageUrl":"(https:[^"]+)"', html)
    if m:
        return m.group(1)
    return ""


# =========================================
# ✅ CVS TEXT (WORKING VERSION YOU HAD)
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    title = ""
    description = ""
    image = ""

    if not html:
        return {"title": "", "description": "", "image": ""}

    # ✅ TITLE
    t = re.search(r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)', html)
    if t:
        title = t.group(0).strip()

    # ✅ DESCRIPTION (your working pattern)
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

    # ✅ IMAGE
    image = get_cvs_image(html)

    return {
        "title": title,
        "description": description,
        "image": image
    }


# =========================================
# ✅ SALSIFY TEXT
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
# ✅ FEATURES (FROM DESCRIPTION)
# =========================================
def extract_features(description):
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
# ✅ NORMALIZE
# =========================================
def normalize(text):
    return re.sub(r'[^a-z0-9 ]', '', str(text).lower())


# =========================================
# ✅ APP UI
# =========================================
st.title("PDP QA Tool")

file = st.file_uploader("Upload CSV", type="csv")

if file:
    df = pd.read_csv(file)

    st.write("Columns:", df.columns.tolist())

    for _, row in df.iterrows():

        cvs = get_cvs_text(row["retail_url"])
        s_title, s_desc = get_salsify_text(row["salsify_url"])

        # =========================================
        st.header("General Product Title")

        st.write("Salsify:", s_title)
        st.write("CVS:", cvs["title"])

        # ✅ IMAGE (fixed)
        if cvs["image"]:
            st.image(cvs["image"], width=200)

        # MATCH
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

        cvs_features = extract_features(cvs["description"])

        for f in cvs_features:
            st.write("•", f)

        st.divider()
