import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO

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
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code == 200:
            html_cache[url] = r.text
            return r.text
    except:
        pass

    return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ IMAGE LOAD
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
# ✅ SALSIFY IMAGES (FIXED)
# =========================================
def get_salsify_images(url):
    html = get_html(url)

    images = []
    seen = set()

    try:
        matches = re.findall(
            r'https://images\.salsify\.com[^"]+',
            html
        )

        for m in matches:
            clean = m.split("?")[0]

            if clean not in seen:
                seen.add(clean)
                images.append({
                    "type": "Salsify",
                    "url": clean
                })

    except Exception as e:
        print("Salsify image error:", e)

    return images[:8]

# =========================================
# ✅ CVS IMAGES (DEDUPED)
# =========================================
def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg',
        html
    )

    seen = set()
    results = []

    for m in matches:
        full = "https://www.cvs.com" + m
        name = full.split("/")[-1]

        if name not in seen:
            seen.add(name)
            results.append(full)

    return results

# =========================================
# ✅ SALSIFY TEXT (FIXED)
# =========================================
def get_salsify_text(url):
    html = get_html(url)

    description = ""
    features = []

    try:
        desc = re.search(r'"generalDescription":"(.*?)"', html)
        if desc:
            description = desc.group(1)

        features = re.findall(r'"generalFeature\d+":"(.*?)"', html)

    except:
        pass

    return {
        "description": description,
        "features": features[:5]
    }

# =========================================
# ✅ CVS TEXT (FIXED)
# =========================================
def get_cvs_text(html):
    description = ""

    try:
        text = html.replace('\\"', '"')

        match = re.search(r'vendorDetailsParagraph":"(.*?)"', text)
        if match:
            description = match.group(1)

    except:
        pass

    return {"description": description}

# =========================================
# ✅ TEXT SCORING
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return text

def keyword_score(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    return int(SequenceMatcher(None, a, b).ratio() * 100)

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # ✅ IMAGE COMPARISON
        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):
            c1, c2 = st.columns(2)

            if i < len(s_images):
                c1.markdown(f"**Salsify {i+1}**")
                img = load_image(s_images[i]["url"])
                if img:
                    c1.image(img)
                else:
                    c1.write("❌ failed")
            else:
                c1.write("❌ Missing")

            if i < len(r_images):
                c2.markdown(f"**CVS {i+1}**")
                img = load_image(r_images[i])
                if img:
                    c2.image(img)
                else:
                    c2.write("❌ failed")
            else:
                c2.write("❌ Missing")

        # =========================================
        # ✅ TEXT
        # =========================================
        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(get_html(row["retail_url"]))

        st.markdown("## Description")
        c1, c2 = st.columns(2)
        c1.write(s_text.get("description", ""))
        c2.write(r_text.get("description", ""))

        desc_score = keyword_score(
            s_text.get("description", ""),
            r_text.get("description", "")
        )

        st.write(f"✅ Description Match: {desc_score}%")

        # =========================================
        # ✅ IMAGE SCORE
        # =========================================
        img_score = int(
            (min(len(s_images), len(r_images)) /
            max(len(s_images), len(r_images), 1)) * 100
        )

        # =========================================
        # ✅ SUMMARY
        # =========================================
        overall = int((img_score + desc_score) / 2)

        summary_rows.append({
            "SKU": row["sku"],
            "Image %": img_score,
            "Description %": desc_score,
            "Overall %": overall
        })

# =========================================
# ✅ EXPORT ✅
# =========================================
if 'summary_rows' in locals() and summary_rows:

    df = pd.DataFrame(summary_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Summary")

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            file_name
        )
