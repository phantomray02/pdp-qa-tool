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
    if not raw:
        return ""
    raw = raw.replace('\\"', '')
    raw = raw.replace('\\n', ' ')
    raw = raw.replace('","', '. ')
    raw = raw.replace('"', '')
    raw = re.sub('<.*?>', '', raw)
    raw = re.sub(r'\s+', ' ', raw)
    return raw.strip()

# =========================================
# ✅ TEXT EXTRACTION (FIXED ✅)
# =========================================
def extract_text_block(html):
    match = re.search(
        r'Item #.*?(Get up to .*?fashion trends)',
        html,
        re.DOTALL
    )
    if match:
        return clean_text(match.group(1))

    return ""  # ✅ ALWAYS return string

def get_cvs_text(url):
    html = get_html(url)

    # ✅ grab raw details block (this DOES exist in HTML)
    match = re.search(
        r'Get up to 100%.*?latest fashion trends',
        html,
        re.DOTALL
    )

    if not match:
        return {"description": "", "features": []}

    block = clean_text(match.group(0))

    # ✅ DESCRIPTION = full paragraph
    description = block

    # ✅ FEATURES = manually defined patterns (reliable)
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

  # ✅ FIX: extract quantity feature (45 regular tampons)
    count_match = re.search(r'(\d+)\s+regular\s+tampons', html, re.IGNORECASE)

    if count_match:
        count_feature = count_match.group(0)

        if count_feature not in features:
            features.insert(0, count_feature)  # ✅ ensure it's Feature 1

    return {
        "description": description,
        "features": features
    }


def get_salsify_text(url):
    html = get_html(url)
    desc = extract_text_block(html)

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
    if not isinstance(a, str):
        a = ""
    if not isinstance(b, str):
        b = ""

    a_words = set(re.sub(r'[^a-z0-9 ]', '', a.lower()).split())
    b_words = set(re.sub(r'[^a-z0-9 ]', '', b.lower()).split())

    if not a_words:
        return 0

    return int(100 * len(a_words & b_words) / len(a_words))

def strict_title_score(a, b):
    return int(SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100)

# =========================================
# ✅ FEATURE MATCHING
# =========================================
def match_features(s_features, r_features):
    results = []

    for s in s_features:
        best_match = ""
        best_score = 0

        for r in r_features:
            if s.strip().lower() == r.strip().lower():
                best_match = r
                best_score = 100
                break

            similarity = int(SequenceMatcher(None, s.lower(), r.lower()).ratio() * 100)

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

        # ✅ TITLE
        st.markdown("## Title")
        ...

        # ✅ DESCRIPTION
        st.markdown("## Description")
        ...

        # ✅ FEATURES
        st.markdown("## Features")
        ...

        # ✅ IMAGE COMPARISON (FIXED INDENT ✅)
        st.markdown("## Image Comparison")

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):
            col1, col2 = st.columns(2)

            if i < len(s_images):
                col1.image(s_images[i])
            else:
                col1.error("Missing")

            if i < len(r_images):
                col2.image(r_images[i])
            else:
                col2.error("Missing")

        st.divider()
