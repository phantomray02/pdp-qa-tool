import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO

# =========================================
# ✅ HEADER
# =========================================
st.write("🚀 VERSION PRODUCTION NO PLAYWRIGHT")
st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
download_placeholder = st.empty()

# =========================================
# ✅ HTML CACHE
# =========================================
html_cache = {}

def get_html(url):
    if not url:
        return ""

    if url in html_cache:
        return html_cache[url]

    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            html_cache[url] = r.text
            return r.text
    except Exception as e:
        print("Request error:", e)

    return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ IMAGE LOADER
# =========================================
def load_image(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None
    return None

# =========================================
# ✅ BUILD PROPERTY MAP (KEY STEP)
# =========================================
def build_property_map(soup):
    prop_map = {}

    rows = soup.find_all("tr")

    for row in rows:
        label = row.get_text(" ", strip=True)

        span = row.find("span", {"data-testid": "property-content"})
        if not span:
            continue

        img = span.find("img")
        if not img:
            continue

        src = img.get("src", "")

        if "salsify" in src:
            prop_map[label.strip()] = src.split("?")[0]

    return prop_map

# =========================================
# ✅ COALESCE HELPER
# =========================================
def get_first_available(prop_map, props):
    for p in props:
        for key in prop_map.keys():
            if p.lower() in key.lower():
                return prop_map[key]
    return None

# =========================================
# ✅ YOUR COALESCE ORDER (CORE LOGIC)
# =========================================
def build_salsify_order(prop_map):

    rules = [
        ["Online Optimized Image", "Flat Back", "Flat Left", "ATF I/O", "ATF 2", "ATF 3", "ATF 4", "ATF 5", "ATF 6"],
        ["Flat Back", "Flat Left", "ATF I/O", "ATF 2", "ATF 3", "ATF 4", "ATF 5", "ATF 6"],
        ["Flat Left", "ATF I/O", "ATF 2", "ATF 3", "ATF 4", "ATF 5", "ATF 6"],
        ["ATF I/O", "ATF 2", "ATF 3", "ATF 4", "ATF 5", "ATF 6"],
        ["ATF 2", "ATF 3", "ATF 4", "ATF 5", "ATF 6"],
        ["ATF 3", "ATF 4", "ATF 5", "ATF 6"],
        ["ATF 4", "ATF 5", "ATF 6"],
        ["ATF 5", "ATF 6"]
    ]

    ordered = []
    used = set()

    for rule in rules:
        img = get_first_available(prop_map, rule)

        if img and img not in used:
            ordered.append(img)
            used.add(img)

    return ordered

# =========================================
# ✅ CVS IMAGES (ORDERED BY SITE)
# =========================================
def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg',
        html
    )

    return ["https://www.cvs.com" + m for m in matches]

# =========================================
# ✅ TEXT NORMALIZATION (KEPT FROM YOUR APP)
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def keyword_score(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    ratio = SequenceMatcher(None, a, b).ratio()

    a_words = set(a.split())
    b_words = set(b.split())

    overlap = len(a_words & b_words)
    total = len(a_words | b_words)

    word_score = (overlap / total) if total else 0

    return int(((ratio * 0.6) + (word_score * 0.4)) * 100)

# =========================================
# ✅ MAIN LOOP
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        try:
            # ✅ NEW COALESCE FLOW
            soup = get_soup(row["salsify_url"])
            prop_map = build_property_map(soup)
            s_images = build_salsify_order(prop_map)

            r_images = get_cvs_images(row["retail_url"])

            st.write(f"✅ Salsify images: {len(s_images)}")

            # ✅ ORDERED SIDE-BY-SIDE QA
            max_len = max(len(s_images), len(r_images))

            for i in range(max_len):
                col1, col2 = st.columns(2)

                # SALSIFY
                if i < len(s_images):
                    col1.markdown(f"**Salsify #{i+1}**")
                    img_obj = load_image(s_images[i])

                    if img_obj:
                        col1.image(img_obj, use_container_width=True)
                    else:
                        col1.write("❌ Failed")
                        col1.write(s_images[i])
                else:
                    col1.write("❌ Missing Salsify")

                # CVS
                if i < len(r_images):
                    col2.markdown(f"**CVS #{i+1}**")
                    img_obj = load_image(r_images[i])

                    if img_obj:
                        col2.image(img_obj, use_container_width=True)
                else:
                    col2.write("❌ Missing CVS")

            # ✅ SCORE
            img_score = int(
                (min(len(s_images), len(r_images)) /
                 max(len(s_images), len(r_images), 1)) * 100
            )

            st.write(f"✅ Image Match: {img_score}%")

            summary_rows.append({
                "SKU": row["sku"],
                "Salsify Count": len(s_images),
                "CVS Count": len(r_images),
                "Image %": img_score
            })

        except Exception as e:
            st.error(f"❌ Error: {e}")

# =========================================
# ✅ EXPORT
# =========================================
if 'summary_rows' in locals() and summary_rows:
    summary_df = pd.DataFrame(summary_rows)

    with pd.ExcelWriter("pdp_qa_results.xlsx", engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False)

    with open("pdp_qa_results.xlsx", "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            "pdp_qa_results.xlsx"
        )
