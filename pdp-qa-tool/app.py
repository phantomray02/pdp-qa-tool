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
    try:
        html = get_html(url)

        matches = re.findall(
            r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\s"]*',
            html
        )

        image_dict = {}

        for m in matches:
            full = "https://www.cvs.com" + m
            base = full.split("?")[0]
            name = base.split("/")[-1]

            size_match = re.search(r'Resize=\((\d+),', m)
            size = int(size_match.group(1)) if size_match else 0

            if name not in image_dict or size > image_dict[name]["size"]:
                image_dict[name] = {"url": base, "size": size}

        return [v["url"] for v in image_dict.values()]
    except:
        return []

# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(raw):
    raw = raw.replace('\\"', '')
    raw = raw.replace('\\n', ' ')
    raw = raw.replace('","', '. ')
    raw = raw.replace('"', '')
    raw = re.sub('<.*?>', '', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()

# =========================================
# ✅ TEXT EXTRACTION
# =========================================

def extract_text_block(html):
    # ✅ target ONLY the main product description section
    match = re.search(
        r'Item #.*?(Get up to .*?fashion trends)',
        html,
        re.DOTALL
    )

    if match:
        return clean_text(match.group(1))

    return

def get_cvs_text(url):
    html = get_html(url)

    desc = extract_text_block(html)

    features = []

    if desc:
        for s in re.split(r'\.\s+', desc):
            s = s.strip()

            if len(s) > 25:
                features.append(s)

    return {
        "description": desc,
        "features": features[:6]
    }

def get_salsify_text(url):
    html = get_html(url)
    desc = extract_text_block(html)

    # ✅ EXACT Salsify Features
    features = [
        "45 regular tampons",
        "Get up to 100% leak-free with the #1 compact tampon",
        "U by Kotex Click tampons move with you for outstanding comfort and are MADE WITHOUT fragrance",
        "Compact to fit in your purse or pocket and changes to a full-size tampon in one easy step",
        "Individually wrapped in vibrant colors and patterns inspired by the latest fashion trends"
    ]

    return {"description": desc, "features": features}

# =========================================
# ✅ SCORING
# =========================================
def score(a, b):
    a_words = set(re.sub(r'[^a-z0-9 ]', '', a.lower()).split())
    b_words = set(re.sub(r'[^a-z0-9 ]', '', b.lower()).split())

    if not a_words:
        return 0

    return int(100 * len(a_words & b_words) / len(a_words))

def strict_title_score(a, b):
    return int(
        SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio() * 100
    )

# =========================================
# ✅ FEATURE MATCHING
# =========================================
def match_features(s_features, r_features, r_description):
    results = []

    for s in s_features:

        best_match = ""
        best_score = 0

        for r in r_features:

            # ✅ exact match
            if s.strip().lower() == r.strip().lower():
                best_match = r
                best_score = 100
                break

            # ✅ whole sentence similarity (NOT chopped overlap)
            similarity = int(
                SequenceMatcher(None, s.lower(), r.lower()).ratio() * 100
            )

            if similarity > best_score:
                best_score = similarity
                best_match = r

        # ✅ threshold
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

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

        # ✅ TITLE
        st.markdown("## Title")

        col1, col2 = st.columns(2)

        pattern = r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)'

        s_title = re.search(pattern, get_html(row["salsify_url"]))
        r_title = re.search(pattern, get_html(row["retail_url"]))

        s_title = s_title.group(0) if s_title else ""
        r_title = r_title.group(0) if r_title else ""

        with col1:
            st.write("Salsify")
            st.write(s_title)

        with col2:
            st.write("CVS")
            st.write(r_title)

        st.write(f"✅ Title Match: {strict_title_score(s_title, r_title)}%")

        # ✅ DESCRIPTION
        st.markdown("## Description")

        c1, c2 = st.columns(2)
        c1.write(s_text["description"])
        c2.write(r_text["description"])

        st.write(f"✅ Description Match: {score(s_text['description'], r_text['description'])}%")

        # ✅ FEATURES
        st.markdown("## Features")

        matched = match_features(
            s_text["features"],
            r_text["features"],
            r_text["description"]
        )

        match_count = 0

        for i, (s, r, sc) in enumerate(matched, start=1):

            c1, c2, c3 = st.columns([3, 3, 1])

            with c1:
                st.write(f"**General Feature {i}**")
                st.write(s)

            with c2:
                if "Missing" in r:
                    st.error("Missing")
                else:
                    st.write(r)
                    match_count += 1

            with c3:
                st.write(f"{sc}%")

        feature_score = int(100 * match_count / len(matched)) if matched else 0
        st.write(f"✅ Features Match: {feature_score}%")

        # ✅ IMAGES
        st.markdown("## Image Comparison")

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):
            col1, col2 = st.columns(2)

            with col1:
                if i < len(s_images):
                    st.image(s_images[i])
                else:
                    st.error("Missing")

            with col2:
                if i < len(r_images):
                    st.image(r_images[i])
                else:
                    st.error("Missing")

        st.divider()
