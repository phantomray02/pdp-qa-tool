import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image, ImageFilter

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
download_placeholder = st.empty()

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}

def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
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
        if src.startswith("http"):
            images.append({"url": src})

    return images

# =========================================
# ✅ CVS IMAGES
# =========================================
def get_cvs_images(url):
    html = get_html(url)
    matches = re.findall(r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg', html)
    return ["https://www.cvs.com" + m for m in matches]

# =========================================
# ✅ TEXT FUNCTIONS
# =========================================
def get_salsify_text(url):
    html = get_html(url)
    return {
        "description": clean_text(html[:3000]),
        "features": ["Feature 1","Feature 2","Feature 3","Feature 4","Feature 5"]
    }

def get_cvs_text(url):
    html = get_html(url)
    return {
        "description": clean_text(html[:3000]),
        "features": ["Feature 1","Feature 2","Feature 3","Feature 4","Feature 5"]
    }

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
# ✅ IMAGE COMPARISON
# =========================================
from io import BytesIO

def compare_images_visually(s_url, r_url):
    try:
        s_img = Image.open(BytesIO(requests.get(s_url, timeout=5).content)).convert("L").resize((256,256))
        r_img = Image.open(BytesIO(requests.get(r_url, timeout=5).content)).convert("L").resize((256,256))

        s_img = s_img.filter(ImageFilter.BLUR)
        r_img = r_img.filter(ImageFilter.BLUR)

        diff = sum(abs(a-b) for a,b in zip(s_img.getdata(), r_img.getdata()))/(256*256)

        if diff < 10: return 100
        elif diff < 20: return 95
        elif diff < 30: return 85
        else: return 70
    except:
        return 0

def match_images_visual(s_images, r_images):
    results = []
    for i, s in enumerate(s_images):
        s_url = s["url"]
        r_url = r_images[i] if i < len(r_images) else ""
        score = compare_images_visually(s_url, r_url) if r_url else 0
        results.append((s_url, r_url, score))
    return results

# =========================================
# ✅ URL BUILDERS
# =========================================
def build_salsify_url_from_sku7(sku7):
    base = "https://sites.salsify.com/c59eb481-0fb4-407b-ac3d-710e4b28a712/83f32e36-ef43-47a1-92e5-8c9a07b01e56/product"
    return f"{base}/{sku7}"


def get_cvs_url_from_sku(sku):
    try:
        search_url = f"https://www.cvs.com/search?searchTerm={sku}"
        html = get_html(search_url)

        matches = re.findall(r'href="(/shop/[^"]+)"', html)

        for m in matches:

            # ✅ must be real product page
            if "prodid" not in m:
                continue

            if "seasonal" in m or "promo" in m:
                continue

            # ✅ ensure correct SKU match
            if f"skuId={sku}" in m:
                return "https://www.cvs.com" + m

        return ""

    except:
        return ""


# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        sku7 = row["SKU7"]
        sku = row["sku"]

        # ✅ BUILD URLs
        salsify_url = build_salsify_url_from_sku7(sku7)
        cvs_url = get_cvs_url_from_sku(sku)

        st.write(f"Salsify URL: {salsify_url}")
        st.write(f"CVS URL: {cvs_url}")

        if not cvs_url:
            st.error(f"❌ CVS product not found for SKU {sku}")
            continue

        # ✅ FETCH DATA
        s_images = get_salsify_images(salsify_url)
        r_images = get_cvs_images(cvs_url)

        s_text = get_salsify_text(salsify_url)
        r_text = get_cvs_text(cvs_url)

        # ✅ TITLE
        html = get_html(salsify_url)
        match = re.search(r'<title>(.*?)</title>', html)
        s_title = match.group(1) if match else ""

        r_match = re.search(r'[A-Z].+?(?:Count|Ct)', get_html(cvs_url))
        r_title = r_match.group(0) if r_match else ""

        st.markdown("## Title")
        st.write(s_title, "|", r_title)

        title_score = int(SequenceMatcher(None, s_title.lower(), r_title.lower()).ratio() * 100)
        st.write(f"✅ Title Match: {title_score}%")

        # ✅ DESCRIPTION
        st.markdown("## Description")
        st.write(s_text["description"])
        st.write(r_text["description"])

        desc_score = int(SequenceMatcher(None, s_text["description"], r_text["description"]).ratio() * 100)
        st.write(f"✅ Description Match: {desc_score}%")

        # ✅ FEATURES
        st.markdown("## Features")
        matched = match_features(s_text["features"], r_text["features"])

        for s, r, sc in matched:
            st.write(f"{s} | {r} | {sc}%")

        # ✅ IMAGES
        st.markdown("## Image Comparison ✅")
        image_matches = match_images_visual(s_images, r_images)

        for s, r, sc in image_matches:
            st.image(s, width=150)
            if r:
                st.image(r, width=150)
            st.write(f"{sc}%")

        img_scores = [sc for _,_,sc in image_matches if sc>0]
        avg_img_score = int(sum(img_scores)/len(img_scores)) if img_scores else 0

        st.write(f"✅ Image Match: {avg_img_score}%")
