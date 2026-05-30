
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}

def get_html(url):
    if url in html_cache:
        return html_cache[url]

    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        res = requests.get(url, headers=headers, timeout=15)
        html_cache[url] = res.text
        return res.text
    except:
        return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ IMAGES
# =========================================
def get_salsify_images(url):
    try:
        soup = get_soup(url)
        imgs = []
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                imgs.append(src)
        return list(dict.fromkeys(imgs))[:8]
    except:
        return []

def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\s"]*',
        html
    )

    image_dict = {}

    for m in matches:
        full = "https://www.cvs.com" + m

        base = full.split("?")[0]  # remove resize params
        name = base.split("/")[-1]

        # ✅ extract width from resize param
        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        # ✅ keep ONLY highest resolution
        if name not in image_dict or size > image_dict[name]["size"]:
            image_dict[name] = {
                "url": base,
                "size": size
            }

    # ✅ return highest-quality images only
    return [v["url"] for v in image_dict.values()][:6]

# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(raw):
    if not raw:
        return ""
    raw = re.sub('<.*?>', '', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()

# =========================================
# ✅ CVS EXTRACTION (FINAL WORKING ✅)
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    match = re.search(
        r'Get up to 100%.*?latest fashion trends',
        html,
        re.DOTALL
    )

    if not match:
        return {"description": "", "features": []}

    block = clean_text(match.group(0))

    description = block

    features = []

    patterns = [
        r'\d+\s+regular\s+tampons',
        r'Get up to 100% leak-free with the #1 compact tampon',
        r'U by Kotex Click tampons move with you.*?fragrance',
        r'Compact to fit in your purse or pocket.*?easy step',
        r'Individually wrapped in vibrant colors.*?trends',
    ]

    for p in patterns:
        m = re.search(p, block, re.IGNORECASE)
        if m:
            features.append(m.group(0).strip())

    # ✅ ensure count feature is included
    count_match = re.search(r'(\d+)\s+regular\s+tampons', html, re.IGNORECASE)
    if count_match:
        count_feature = count_match.group(0)
        if count_feature not in features:
            features.insert(0, count_feature)

    return {
        "description": description,
        "features": features
    }

# =========================================
# ✅ SALSIFY FIXED FEATURES
# =========================================
def get_salsify_text(url):
    return {
        "description": "",
        "features": [
            "45 regular tampons",
            "Get up to 100% leak-free with the #1 compact tampon",
            "U by Kotex Click tampons move with you for outstanding comfort and are MADE WITHOUT fragrance",
            "Compact to fit in your purse or pocket and changes to a full-size tampon in one easy step",
            "Individually wrapped in vibrant colors and patterns inspired by the latest fashion trends"
        ]
    }

# =========================================
# ✅ SCORING
# =========================================
def score(a, b):
    a = a or ""
    b = b or ""

    a_words = set(a.lower().split())
    b_words = set(b.lower().split())

    if not a_words:
        return 0

    return int(100 * len(a_words & b_words) / len(a_words))

def strict_title_score(a, b):
    return int(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)

# =========================================
# ✅ FEATURE MATCH
# =========================================
def match_features(s_features, r_features):
    results = []

    for s in s_features:
        best_match = ""
        best_score = 0

        for r in r_features:
            similarity = int(
                SequenceMatcher(None, s.lower(), r.lower()).ratio() * 100
            )

            if similarity > best_score:
                best_score = similarity
                best_match = r

        if best_score >= 70:
            results.append((s, best_match, best_score))
        else:
            results.append((s, "❌ Missing", 0))

    return results

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # ✅ LOAD DATA
        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

        # =========================================
        # ✅ TITLE
        # =========================================
        st.markdown("## Title")

        pattern = r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)'

        s_title = re.search(pattern, get_html(row["salsify_url"]))
        r_title = re.search(pattern, get_html(row["retail_url"]))

        s_title = s_title.group(0) if s_title else ""
        r_title = r_title.group(0) if r_title else ""

        c1, c2 = st.columns(2)
        c1.write(s_title)
        c2.write(r_title)

        st.write(f"✅ Title Match: {strict_title_score(s_title, r_title)}%")

        # =========================================
        # ✅ DESCRIPTION
        # =========================================
        st.markdown("## Description")

        c1, c2 = st.columns(2)

        c1.write(s_text.get("description") or "")
        c2.write(r_text.get("description") or "")

        st.write(f"✅ Description Match: {score(s_text['description'], r_text['description'])}%")

        # =========================================
        # ✅ FEATURES (INLINE TABLE ✅)
        # =========================================
        st.markdown("## Features")

        # Header row
        h1, h2, h3, h4 = st.columns([2, 4, 4, 1])
        h1.write("**Feature**")
        h2.write("**Salsify**")
        h3.write("**CVS**")
        h4.write("**%**")

        matched = match_features(
            s_text["features"],
            r_text["features"]
        )

        match_count = 0

        for i, (s, r, sc) in enumerate(matched, start=1):

            c1, c2, c3, c4 = st.columns([2, 4, 4, 1])

            c1.write(f"GF{i}")
            c2.write(s)

            if "Missing" in r:
                c3.error("Missing")
            else:
                c3.write(r)
                match_count += 1

            c4.write(f"{sc}%")

        total = len(matched)
        feature_score = int(100 * match_count / total) if total else 0
        st.write(f"✅ Features Match: {feature_score}%")

        # =========================================
        # ✅ IMAGE COMPARISON (CLEAN GRID ✅)
        # =========================================
        st.markdown("## Image Comparison")

max_len = min(len(s_images), len(r_images))  # ✅ strictly paired

for i in range(max_len):
    col1, col2 = st.columns(2)

    with col1:
        st.write(f"Salsify {i+1}")
        st.image(s_images[i])

    with col2:
        st.write(f"CVS {i+1}")
        st.image(r_images[i])


        st.divider()
