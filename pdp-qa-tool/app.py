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
# ✅ SALSIFY IMAGES (FINAL PERFECT ✅)
# =========================================
def get_salsify_images(url):
    html = get_html(url)

    matches = re.findall(r'https://images\.salsify\.com[^"]+', html)

    image_map = {}

    for m in matches:
        base = m.split("?")[0]

        # ✅ remove junk thumbnails / selectors
        if any(x in m.lower() for x in ["thumb", "small", "icon", "tile"]):
            continue

        file_name = base.split("/")[-1]

        # ✅ resolution detection
        size = 0
        size_match = re.search(r'Resize=\((\d+)', m)
        if size_match:
            size = int(size_match.group(1))

        # ✅ remove tiny assets
        if size and size < 500:
            continue

        # ✅ keep highest res per asset
        if file_name not in image_map or size > image_map[file_name]["size"]:
            image_map[file_name] = {
                "url": base,
                "size": size
            }

    # ✅ FINAL CLEAN LIST
    images = list(image_map.values())

    # ✅ LIMIT to PDP main images (VERY IMPORTANT)
    images = images[:8]

    return [
        {"type": f"Salsify {i+1}", "url": img["url"]}
        for i, img in enumerate(images)
    ]

# =========================================
# ✅ CVS IMAGES (CLEAN ✅)
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
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)

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

        # ✅ COPY AT TOP
        st.markdown("## Description")

        c1, c2 = st.columns(2)
        c1.write(s_text.get("description", ""))
        c2.write(r_text.get("description", ""))

        desc_score = keyword_score(
            s_text.get("description", ""),
            r_text.get("description", "")
        )

        st.write(f"✅ Description Match: {desc_score}%")

        # ✅ IMAGES
        st.markdown("## Image Comparison")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        st.write(f"Salsify: {len(s_images)} | CVS: {len(r_images)}")

        # ✅ LOOP BASED ON MAX BUT NO FAKE FAILS
        max_len = max(len(s_images), len(r_images))

        for i in range(max_len):

            c1, c2 = st.columns(2)

            # ✅ LEFT (REAL ONLY)
            if i < len(s_images):
                c1.markdown(f"Salsify {i+1}")
                img = load_image(s_images[i]["url"])
                if img:
                    c1.image(img)
            else:
                c1.write("")  # ← NO more "failed"

            # ✅ RIGHT
            if i < len(r_images):
                c2.markdown(f"CVS {i+1}")
                img = load_image(r_images[i])
                if img:
                    c2.image(img)
            else:
                c2.write("")

        # ✅ SCORING
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
        df.to_excel(writer, index=False)

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            file_name
        )
