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
# ✅ SALSIFY IMAGES (DEDUPED ✅)
# =========================================
def get_salsify_images(url):
    html = get_html(url)

    images = []
    seen = set()

    matches = re.findall(r'https://images\.salsify\.com[^"]+', html)

    for m in matches:
        clean = m.split("?")[0].strip()

        # ✅ REMOVE DUPLICATES
        if clean in seen:
            continue

        seen.add(clean)

        images.append({
            "type": f"Salsify {len(images)+1}",
            "url": clean
        })

    return images[:8]

# =========================================
# ✅ CVS IMAGES (HIGHEST RES ONLY ✅)
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
# ✅ TEXT
# =========================================
def get_salsify_text(url):
    html = get_html(url)

    desc = ""
    features = []

    d = re.search(r'"generalDescription":"(.*?)"', html)
    if d:
        desc = d.group(1)

    features = re.findall(r'"generalFeature\d+":"(.*?)"', html)

    return {
        "description": desc,
        "features": features[:5]
    }

def get_cvs_text(html):
    desc = ""

    text = html.replace('\\"', '"')

    m = re.search(r'vendorDetailsParagraph":"(.*?)"', text)
    if m:
        desc = m.group(1)

    return {"description": desc}

# =========================================
# ✅ TEXT SCORE
# =========================================
def normalize_text(t):
    return re.sub(r'[^a-z0-9\s]', '', str(t).lower())

def keyword_score(a, b):
    a = normalize_text(a)
    b = normalize_text(b)
    return int(SequenceMatcher(None, a, b).ratio() * 100)

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        html = get_html(row["retail_url"])

        s_text = get_salsify_text(row["salsify_url"])
        r_text = get_cvs_text(html)

        # =========================================
        # ✅ COPY AT TOP ✅
        # =========================================
        st.markdown("## Description")

        c1, c2 = st.columns(2)
        c1.markdown("**Salsify**")
        c1.write(s_text.get("description", ""))

        c2.markdown("**CVS**")
        c2.write(r_text.get("description", ""))

        desc_score = keyword_score(
            s_text.get("description", ""),
            r_text.get("description", "")
        )

        st.write(f"✅ Description Match: {desc_score}%")

        # ✅ FEATURES
        st.markdown("## Features")

        c1, c2 = st.columns(2)
        c1.write(s_text.get("features", []))
        c2.write("N/A")

        # =========================================
        # ✅ IMAGES (LEFT = SALSIFY | RIGHT = CVS)
        # =========================================
        st.markdown("## Image Comparison")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):

            c1, c2 = st.columns(2)

            # ✅ LEFT = SALSIFY
            if i < len(s_images):
                c1.markdown(f"**Salsify {i+1}**")
                img = load_image(s_images[i]["url"])
                if img:
                    c1.image(img)
                else:
                    c1.write("❌ Failed")
            else:
                c1.write("❌ Missing")

            # ✅ RIGHT = CVS
            if i < len(r_images):
                c2.markdown(f"**CVS {i+1}**")
                img = load_image(r_images[i])
                if img:
                    c2.image(img)
                else:
                    c2.write("❌ Failed")
            else:
                c2.write("❌ Missing")

        # =========================================
        # ✅ SCORING
        # =========================================
        img_score = int(
            (min(len(s_images), len(r_images)) /
             max(len(s_images), len(r_images), 1)) * 100
        )

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
