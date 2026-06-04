import streamlit as st
import pandas as pd
import requests
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
download_placeholder = st.empty()

# =========================================
# ✅ CACHE HTML
# =========================================
html_cache = {}

def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            html_cache[url] = r.text
            return r.text
    except:
        pass

    return ""

# =========================================
# ✅ LOAD IMAGE
# =========================================
def load_image(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None
    return None

# =========================================
# ✅ NORMALIZE FILE NAME (DEDUP CORE)
# =========================================
def normalize_filename(fname):
    fname = fname.lower()
    fname = re.sub(r'(_|-)?\d+x\d+', '', fname)
    fname = re.sub(r'(_|-)?\d+', '', fname)
    return fname

# =========================================
# ✅ ✅ SALSIFY (FINAL CORRECT ENGINE)
# =========================================
import json
from bs4 import BeautifulSoup

def get_salsify_images(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    # ✅ grab Next.js data
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return []

    data = json.loads(script.string)

    try:
        properties = data["props"]["pageProps"]["product"]["digitalAssets"]["properties"]
    except:
        return []

    images = []

    for prop in properties:

        prop_name = prop.get("property", "").strip()

        values = prop.get("values", [])

        if not values:
            continue

        # ✅ CRITICAL FIX: ONLY FIRST IMAGE
        first = values[0]

        url = first.get("value", "")
        clean = url.split("?")[0]

        images.append({
            "type": prop_name,
            "url": clean
        })

    return images[:8]

# =========================================
# ✅ CVS IMAGES (UNLIMITED + BEST RES)
# =========================================
def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^"]*',
        html
    )

    best_images = {}

    for m in matches:
        full = "https://www.cvs.com" + m
        base = full.split("?")[0]
        name = base.split("/")[-1]

        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in best_images or size > best_images[name]["size"]:
            best_images[name] = {
                "url": base,
                "size": size
            }

    return [v["url"] for v in best_images.values()]

# =========================================
# ✅ TEXT EXTRACTION
# =========================================
def get_salsify_text(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return {}

    data = json.loads(script.string)

    try:
        props = data["props"]["pageProps"]["product"]["propertySets"][0]["properties"]
    except:
        return {}

    text_map = {}

    for p in props:
        key = p.get("property")
        values = p.get("values", [])

        if values:
            text_map[key] = values[0]

    # ✅ RETURN EXACT FIELDS YOU WANT
    return {
        "title": text_map.get("PRODUCT_TITLE", ""),
        "description": text_map.get("DESCRIPTION", ""),
        "feature1": text_map.get("FEATURE_1", ""),
        "feature3": text_map.get("FEATURE_3", ""),
        "feature4": text_map.get("FEATURE_4", ""),
        "feature5": text_map.get("FEATURE_5", "")
    }
import re
import html

def get_cvs_text(html_text):

    desc = ""
    features = []

    # =====================================
    # ✅ DESCRIPTION (LOCK THIS EXACT KEY)
    # =====================================
    desc_match = re.search(
        r'vendorDetailsParagraph":"(.*?)"',
        html_text
    )

    if desc_match:
        desc = html.unescape(desc_match.group(1))

    # =====================================
    # ✅ BULLETS (MULTI-LINE SAFE)
    # =====================================
    bullet_match = re.search(
        r'vendorDetailsBullets":\[(.*?)\]',
        html_text,
        re.DOTALL
    )

    if bullet_match:
        raw = bullet_match.group(1)

        features = [
            html.unescape(x)
            for x in re.findall(r'"(.*?)"', raw)
        ]

    # =====================================
    # ✅ FINAL CLEANUP
    # =====================================
    desc = desc.strip()

    return {
        "description": desc,
        "features": features
    }
# =========================================
# ✅ SCORE
# =========================================
def normalize_text(t):
    return re.sub(r'[^a-z0-9\s]', '', str(t).lower())

def keyword_score(a, b):
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)

# =========================================
# ✅ MAIN APP
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # ✅ FIXED: correct variable name
        retail_html = get_html(row["retail_url"])

        # ✅ GET DATA
        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(retail_html)

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # =========================================
        # ✅ COPY COMPARISON
        # =========================================
        st.markdown("## Copy Comparison")

        fields = [
            ("Title", "title"),
            ("Description", "description"),
            ("Feature 1", "feature1"),
            ("Feature 3", "feature3"),
            ("Feature 4", "feature4"),
            ("Feature 5", "feature5"),
        ]

        for label, key in fields:

            st.markdown(f"### {label}")

            c1, c2 = st.columns(2)

            # ✅ LEFT: SALSIFY
            c1.markdown("**Salsify**")
            c1.write(s_text.get(key, ""))

            # ✅ RIGHT: CVS (only description for now)
            c2.markdown("**CVS**")

            if key == "description":
                c2.write(r_text.get("description", ""))
            else:
                c2.write("")

            # ✅ SCORE (basic for now)
            score = keyword_score(
                s_text.get(key, ""),
                r_text.get("description", "")
            )

            st.write(f"✅ Match: {score}%")

        # =========================================
        # ✅ IMAGE COMPARISON
        # =========================================
        st.markdown("## Image Comparison")

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):

            c1, c2 = st.columns(2)

            if i < len(s_images):
                c1.markdown(f"**{s_images[i]['type']}**")
                img = load_image(s_images[i]["url"])
                if img:
                    c1.image(img, use_container_width=True)

            if i < len(r_images):
                c2.markdown(f"**CVS {i+1}**")
                img = load_image(r_images[i])
                if img:
                    c2.image(img, use_container_width=True)

        # =========================================
        # ✅ SCORE SUMMARY
        # =========================================
        img_score = int(
            (min(len(s_images), len(r_images)) /
             max(len(s_images), len(r_images), 1)) * 100
        )

        desc_score = keyword_score(
            s_text.get("description", ""),
            r_text.get("description", "")
        )

        overall = int((img_score + desc_score) / 2)

        summary_rows.append({
            "SKU": row["sku"],
            "Image %": img_score,
            "Description %": desc_score,
            "Overall %": overall
        })

# =========================================
# ✅ EXPORT
# =========================================
if 'summary_rows' in locals() and summary_rows:

    df = pd.DataFrame(summary_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            file_name
        )
