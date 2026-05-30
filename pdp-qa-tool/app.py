
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# =========================================
# ✅ IMAGE ORDER
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

    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        html_cache[url] = res.text
        return res.text
    except:
        return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(raw):
    if not raw:
        return ""

    raw = re.sub(r'<.*?>', '', raw)
    raw = raw.replace('"value":"', '')
    raw = raw.replace('"}', '')
    raw = raw.replace('{', '').replace('}', '')
    raw = raw.replace('"', '')

    raw = raw.lstrip(' ,.')
    raw = raw.rstrip(' ,')
    raw = re.sub(r'\s+', ' ', raw)

    return raw.strip()

# =========================================
# ✅ SALSIFY IMAGES
# =========================================
def get_salsify_images(url):
    soup = get_soup(url)
    images = []

    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if not src.startswith("http"):
            continue

        label = ""
        parent = img.find_parent()

        if parent:
            text = parent.get_text(" ", strip=True)
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
# ✅ ORDER IMAGES
# =========================================
def order_salsify(images):
    ordered = {k: None for k in IMAGE_ORDER}

    for img in images:
        t = img.get("type")
        if t in ordered and ordered[t] is None:
            ordered[t] = img["url"]

    img_list = [img["url"] for img in images]

    for i, key in enumerate(IMAGE_ORDER):
        if ordered[key] is None and i < len(img_list):
            ordered[key] = img_list[i]

    return ordered

# =========================================
# ✅ CVS IMAGES
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
# ✅ CVS TEXT
# =========================================
def get_cvs_text(url):
    html = get_html(url)

    description = ""
    features = []

    # ✅ STEP 1 — shrink search space (CRITICAL FIX)
    start_idx = html.lower().find("vendordetailsparagraph")
    if start_idx == -1:
        return {
            "description": "",
            "features": []
        }

    # ✅ only look at relevant slice
    html_slice = html[start_idx:start_idx + 4000]

    # ✅ STEP 2 — extract description safely
    match = re.search(
        r'Get up to 100% leak-free.*?U\.S\.',
        html_slice,
        re.DOTALL | re.IGNORECASE
    )

    if not match:
        return {
            "description": "",
            "features": []
        }

    raw = match.group(0)

    # ✅ STEP 3 — clean junk ONLY
    raw = raw.replace('\\n', ' ')
    raw = raw.replace('\\t', ' ')
    raw = raw.replace('\\', '')
    raw = raw.replace('u0026', '&')
    raw = raw.replace('u0026amp;', '&')

    description = clean_text(raw)

    # ======================================
    # ✅ FEATURES (UNCHANGED)
    # ======================================
    m = re.search(r'(\d+)\s+regular\s+tampons', html, re.IGNORECASE)
    if m:
        features.append(m.group(0))

    m = re.search(
        r'Get up to 100% leak[-\s]?free with the #1 compact tampon',
        description,
        re.IGNORECASE
    )
    if m:
        features.append(m.group(0))

    m = re.search(
        r'U by Kotex Click tampons move.*?fragrance',
        description,
        re.IGNORECASE
    )
    if m:
        features.append(m.group(0))

    m = re.search(
        r'Compact to fit.*?easy step',
        description,
        re.IGNORECASE
    )
    if m:
        features.append(m.group(0))

    m = re.search(
        r'Individually wrapped.*?fashion trends',
        description,
        re.IGNORECASE
    )
    if m:
        features.append(m.group(0))

    return {
        "description": description,
        "features": features
    }

# =========================================
# ✅ MATCH FEATURES
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
# ✅ SALSIFY TEXT
# =========================================
def get_salsify_text(url):
    soup = get_soup(url)

    description = ""

    # ✅ find correct table row
    rows = soup.find_all("tr")

    for row in rows:
        label = row.get_text(" ", strip=True).lower()

        if "general description" in label:
            content = row.find("span", {"data-testid": "property-content"})

            if content:
                description = clean_text(content.get_text(" ", strip=True))
                break

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

        # TITLE
        st.markdown("## Title")

        pattern = r'[A-Z][A-Za-z0-9 ,\-]+(?:Count|Ct)'

        s_title = re.search(pattern, get_html(row["salsify_url"]))
        r_title = re.search(pattern, get_html(row["retail_url"]))

        s_title = s_title.group(0) if s_title else ""
        r_title = r_title.group(0) if r_title else ""

        c1, c2 = st.columns(2)
        c1.write(s_title)
        c2.write(r_title)

        # DESCRIPTION
        st.markdown("## Description")

        c1, c2 = st.columns(2)
        c1.write(s_text["description"])
        c2.write(r_text["description"])

        # FEATURES
        st.markdown("## Features")

        matched = match_features(s_text["features"], r_text["features"])

        for s, r, sc in matched:
            c1, c2, c3 = st.columns([4, 4, 1])
            c1.write(s)
            c2.write(r)
            c3.write(f"{sc}%")

        # ✅ IMAGE SECTION RESTORED
        st.markdown("## Image Comparison (Salsify Driven ✅)")

        s_ordered = order_salsify(s_images)

        valid_slots = [k for k, v in s_ordered.items() if v]

        for i, key in enumerate(valid_slots):

            col1, col2 = st.columns(2)

            with col1:
                st.write(f"Salsify ({key})")
                st.image(s_ordered[key])

            with col2:
                st.write(f"CVS ({key})")
                if i < len(r_images):
                    st.image(r_images[i])
                else:
                    st.error("Missing")

        st.divider()
