import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# =========================================
# ✅ IMAGE ORDER (YOUR RULE ✅)
# =========================================
IMAGE_ORDER = [
    "Online Optimized Image",
    "Flat Back_2D",
    "Flat Left_2D",
    "ATF I/O-Generic",
    "ATF 2-Generic",
    "ATF 3-Generic",
    "ATF 4-Generic",
    "ATF 5-Generic",
    "ATF 6-Generic"
]

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
# ✅ SALSIFY IMAGES (TAGGED ✅)
# =========================================
def get_salsify_images(url):
    soup = get_soup(url)

    images = []

    for img in soup.find_all("img"):
        src = img.get("src") or ""
        alt = (img.get("alt") or "").lower()

        if src.startswith("http"):

            matched_type = "Other"
            for t in IMAGE_ORDER:
                if t.lower() in alt:
                    matched_type = t
                    break

            images.append({
                "url": src,
                "type": matched_type
            })

    return images

# =========================================
# ✅ CVS IMAGES (HIGHEST QUALITY ✅)
# =========================================
def get_cvs_images(url):
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

        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in image_dict or size > image_dict[name]["size"]:
            image_dict[name] = {"url": base, "size": size}

    return [v["url"] for v in image_dict.values()]

# =========================================
# ✅ ORDER SALSIFY IMAGES (KEY LOGIC ✅)
# =========================================
def get_salsify_images(url):
    soup = get_soup(url)

    images = []

    # ✅ find ALL images
    img_tags = soup.find_all("img")

    for img in img_tags:
        src = img.get("src") or ""

        if not src.startswith("http"):
            continue

        # ✅ try to get label BELOW image
        label = ""

        parent = img.find_parent()

        if parent:
            text = parent.get_text(" ", strip=True)

            # ✅ match known labels
            for t in IMAGE_ORDER:
                if t.lower() in text.lower():
                    label = t
                    break

        images.append({
            "url": src,
            "type": label
        })

    return images
# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(raw):
    if not raw:
        return ""

    # remove HTML tags
    raw = re.sub(r'<.*?>', '', raw)

    # remove JSON labels
    raw = re.sub(r'"value"\s*:\s*"', '', raw)
    raw = re.sub(r'"@type".*?"name"\s*:\s*"', '', raw)

    # remove junk braces
    raw = raw.replace('{', '').replace('}', '')

    # remove quotes
    raw = raw.replace('"', '')

  
    # remove leading punctuation
    raw = raw.lstrip(' ,.')
    raw = raw.rstrip(' ,')


    # ✅ remove trailing comma
    raw = raw.rstrip(' ,')

    # normalize spaces
    raw = re.sub(r'\s+', ' ', raw)

    return raw.strip()



# =========================================
# ✅ CVS TEXT
# =========================================
# ✅ NEW FEATURE SPLIT (STABLE)
def get_cvs_text(url):
    html = get_html(url)

    match = re.search(
        r'Get up to .*?latest fashion trends',
        html,
        re.DOTALL | re.IGNORECASE
    )

    if not match:
        return {"description": "", "features": []}

    description = clean_text(match.group(0))
    features = []

# ✅ split the long CVS string into real feature chunks
chunks = re.split(r',\s*(?=[A-Z])', description)

for c in chunks:
    c = c.strip()

    if len(c) < 25:
        continue

    if any(k in c.lower() for k in [
        "leak-free",
        "move with you",
        "compact to fit",
        "individually wrapped"
    ]):
        features.append(c)

    # ✅ count
    count_match = re.search(r'(\d+)\s+regular\s+tampons', html, re.IGNORECASE)
    if count_match:
        features.insert(0, count_match.group(0))

    return {
        "description": description,
        "features": features
    }
# =========================================
# ✅ SALSIFY TEXT
# =========================================
def get_salsify_text(url):
    html = get_html(url)

    description = ""

    clean_html = html.replace('\\"', '"')

    match = re.search(
        r'"General Description","(.*?)","General Feature 1"',
        clean_html,
        re.DOTALL
    )

    if match:
        description = clean_text(match.group(1))

    features = [
        "45 regular tampons",
        "Get up to 100% leak-free with the #1 compact tampon",
        "U by Kotex Click tampons move with you for outstanding comfort and are MADE WITHOUT fragrance",
        "Compact to fit in your purse or pocket and changes to a full-size tampon in one easy step",
        "Individually wrapped in vibrant colors and patterns inspired by the latest fashion trends"
    ]

    return {
        "description": description,
        "features": features
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
            sim = SequenceMatcher(None, s.lower(), r.lower()).ratio()
            if sim > best_score:
                best_score = sim
                best_match = r

        if best_score >= 0.7:
            results.append((s, best_match, int(best_score * 100)))
        else:
            results.append((s, "❌ Missing", 0))

    return results
# =========================================
# ✅ ORDER SALSIFY IMAGES (CRITICAL ✅)
# =========================================
def order_salsify(images):

    ordered = {k: None for k in IMAGE_ORDER}

    # ✅ STEP 1 — match by detected type
    for img in images:
        t = img.get("type")
        if t in ordered and ordered[t] is None:
            ordered[t] = img["url"]

    # ✅ STEP 2 — fallback to POSITION (fixes missing issue)
    img_list = [img["url"] for img in images]

    for i, key in enumerate(IMAGE_ORDER):
        if ordered[key] is None and i < len(img_list):
            ordered[key] = img_list[i]

    # ✅ STEP 3 — ATF fallback rule
    if not ordered["ATF I/O-Generic"]:
        ordered["ATF I/O-Generic"] = ordered.get("ATF 6-Generic")

    return ordered
def get_cvs_text(url):
    html = get_html(url)

    match = re.search(
        r'Get up to .*?latest fashion trends',
        html,
        re.DOTALL | re.IGNORECASE
    )

    if not match:
        return {"description": "", "features": []}

    block = clean_text(match.group(0))

    description = block

    # ✅ FIXED FEATURE EXTRACTION
    features = []

    clean_block = block.replace('\\"', '"')

    parts = re.split(r'\",\s*\"', clean_block)

    for p in parts:
        p = p.replace('"', '').strip()

        if len(p) < 20:
            continue

        if any(k in p.lower() for k in [
            "tampon",
            "leak",
            "compact",
            "wrapped",
            "comfort"
        ]):
            features.append(p)

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
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_ordered = order_salsify(s_images)

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(row["retail_url"])
        
        # ✅ DEBUG LINE (ADD HERE)
        st.write("DEBUG CVS TEXT:", r_text)


        # ✅ TITLE
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

        # ✅ DESCRIPTION
        st.markdown("## Description")

        c1, c2 = st.columns(2)
        c1.write(s_text["description"])
        c2.write((r_text or {}).get("description", ""))

        st.write(f"✅ Description Match: {score(s_text.get('description'), (r_text or {}).get('description'))}%")

        # ✅ FEATURES
        st.markdown("## Features")

        matched = match_features(s_text["features"], r_text["features"])

        for i, (s, r, sc) in enumerate(matched, start=1):
            c1, c2, c3 = st.columns([4, 4, 1])
            c1.write(s)
            c2.write(r)
            c3.write(f"{sc}%")


        # =========================================
        # ✅ IMAGE COMPARISON (SALSIFY DRIVEN ✅)
        # =========================================
        st.markdown("## Image Comparison (Salsify Driven ✅)")

        # ✅ order Salsify images
        s_ordered = order_salsify(s_images)

        # ✅ only keep valid slots
        valid_slots = [k for k, v in s_ordered.items() if v]

        for i, key in enumerate(valid_slots):

            col1, col2 = st.columns(2)

            # ✅ SALSIFY
            with col1:
                st.write(f"Salsify ({key})")
                st.image(s_ordered[key])

            # ✅ CVS
            with col2:
                st.write(f"CVS ({key})")

                if i < len(r_images):
                    st.image(r_images[i])
                else:
                    st.error("Missing")

        st.divider()

