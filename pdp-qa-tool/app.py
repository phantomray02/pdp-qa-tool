import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# =========================================
# ✅ CACHE (FAST)
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
# ✅ IMAGES (UNCHANGED)
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
# ✅ TEXT EXTRACTION (WORKING)
# =========================================
def extract_text_block(html):
    match = re.search(
        r'Get up to .*?latest fashion trends',
        html,
        re.DOTALL
    )

    if match:
        return clean_text(match.group(0))

    return ""

def get_cvs_text(url):
    html = get_html(url)
    desc = extract_text_block(html)

    # features from sentences
    features = []
    for s in re.split(r'\.\s+', desc):
        if 20 < len(s) < 140:
            features.append(s.strip())

    return {"description": desc, "features": features[:6]}

def get_salsify_text(url):
    html = get_html(url)
    desc = extract_text_block(html)

    features = []
    for s in re.split(r'\.\s+', desc):
        if 20 < len(s) < 140:
            features.append(s.strip())

    return {"description": desc, "features": features[:6]}

# =========================================
# ✅ BETTER SCORING (FIXED)
# =========================================
def score(a, b):
    a_words = set(re.sub(r'[^a-z0-9 ]', '', a.lower()).split())
    b_words = set(re.sub(r'[^a-z0-9 ]', '', b.lower()).split())

    if not a_words:
        return 0

    return int(100 * len(a_words & b_words) / len(a_words))

# =========================================
# ✅ FEATURE MATCHING
# =========================================
def match_features(s_features, r_features):
    results = []

    for s in s_features:
        best_match = ""
        best_score = 0

        for r in r_features:
            sc = score(s, r)
            if sc > best_score:
                best_score = sc
                best_match = r

        if best_score >= 50:
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
# =========================================
# ✅ TITLE (ADD THIS)
# =========================================
st.markdown("## Title")

c1, c2 = st.columns(2)

# extract titles using same pattern
title_pattern = r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)'

s_title_match = re.search(title_pattern, get_html(row["salsify_url"]))
r_title_match = re.search(title_pattern, get_html(row["retail_url"]))

s_title = s_title_match.group(0) if s_title_match else ""
r_title = r_title_match.group(0) if r_title_match else ""

with c1:
    st.write("Salsify")
    st.write(s_title)

with c2:
    st.write("CVS")
    st.write(r_title)
    
        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])

        # =========================
        # ✅ IMAGE COMPARISON
        # =========================
        st.markdown("## Image Comparison")

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):
            c1, c2 = st.columns(2)

            with c1:
                st.write(f"Salsify {i+1}")
                if i < len(s_images):
                    st.image(s_images[i])
                else:
                    st.error("Missing")

            with c2:
                st.write(f"CVS {i+1}")
                if i < len(r_images):
                    st.image(r_images[i])
                else:
                    st.error("Missing")

        # =========================
        # ✅ DESCRIPTION
        # =========================
        st.markdown("## Description")

        c1, c2 = st.columns(2)

        with c1:
            st.write("Salsify")
            st.write(s_text["description"])

        with c2:
            st.write("CVS")
            st.write(r_text["description"])

        st.write("Match:", f"{score(s_text['description'], r_text['description'])}%")

        # =========================
        # ✅ FEATURES (FIXED)
        # =========================
        st.markdown("## Features")

        matched = match_features(
            s_text["features"],
            r_text["features"]
        )

        for s, r, sc in matched:
            c1, c2, c3 = st.columns([3, 3, 1])

            with c1:
                st.write("•", s)

            with c2:
                if r == "❌ Missing":
                    st.error("Missing")
                else:
                    st.write("•", r)

            with c3:
                if sc:
                    st.write(f"{sc}%")

        st.divider()
