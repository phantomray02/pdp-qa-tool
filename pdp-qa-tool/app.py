import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO
import json

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# ✅ TOP DOWNLOAD BUTTON PLACEHOLDER
download_placeholder = st.empty()

# ✅ STORAGE FOR EXPORT DATA
export_rows = []

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
image_cache = {}

# =========================================
# ✅ GET HTML (PLAYWRIGHT REMOVED, SAME LOGIC KEPT)
# =========================================
def get_html(url):
    if url in html_cache:
        return html_cache[url]

    headers = {"User-Agent": "Mozilla/5.0"}

    # =========================================
    # ✅ STEP 1: TRY API USING PRODUCT ID
    # =========================================
    product_id_match = re.search(r'prodid-(\d+)', url)

    if product_id_match:
        product_id = product_id_match.group(1)
        api_url = f"https://www.cvs.com/api/product/v2/{product_id}"

        try:
            res = requests.get(api_url, headers=headers, timeout=15)

            if res.status_code == 200 and res.text.strip():
                html_cache[url] = res.text
                return res.text
        except:
            pass

    # =========================================
    # ✅ STEP 2: FALLBACK TO NORMAL PAGE
    # =========================================
    try:
        res = requests.get(url, headers=headers, timeout=15)
        html_cache[url] = res.text
        return res.text
    except Exception as e:
        print(f"Request failed for {url}: {e}")

    return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(raw):
    if not raw:
        return ""

    raw = re.sub(r'&lt;.*?&gt;', '', raw)
    raw = raw.replace('"value":"', '')
    raw = raw.replace('"}', '')
    raw = raw.replace('{', '').replace('}', '')
    raw = raw.replace('"', '')

    raw = raw.lstrip(' ,.') 
    raw = raw.rstrip(' ,')
    raw = re.sub(r'\s+', ' ', raw)

    return raw.strip()

# =========================================
# ✅ SALSIFY IMAGE BUCKETS
# =========================================
def get_salsify_images(url):
    html = get_html(url)
    images = []
    
    try:
        json_match = re.search(
            r'&lt;script[^&gt;]*&gt;.*?"product".*?"properties":\s*(\[.*?\])',
            html,
            re.DOTALL
        )
        
        if not json_match:
            return images
        
        json_str = json_match.group(1)
        properties = json.loads(json_str)
        
        seen_combinations = set()
        
        for prop in properties:
            if not isinstance(prop, dict):
                continue
            
            prop_name = prop.get("property", "")
            value = prop.get("value", "")
            
            if "image" not in prop_name.lower():
                continue
            
            if isinstance(value, str) and "salsify.com" in value:
                combo = (prop_name, value)
                if combo not in seen_combinations:
                    seen_combinations.add(combo)
                    images.append({
                        "type": prop_name.strip(),
                        "url": value
                    })

            elif isinstance(value, dict) and "salsify:url" in value:
                url_val = value.get("salsify:url", "")
                combo = (prop_name, url_val)

                if combo not in seen_combinations:
                    seen_combinations.add(combo)
                    images.append({
                        "type": prop_name.strip(),
                        "url": url_val
                    })

    except Exception as e:
        print(f"Salsify error: {e}")

    return images

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
# ✅ RENDER IMAGE COMPARISON
# =========================================
def render_image_comparison_by_property(s_images, r_images):
    st.markdown("## Image Comparison ✅")
    st.write(f"Salsify Images: {len(s_images)} | CVS Images: {len(r_images)}")

    max_len = max(len(s_images), len(r_images))

    for i in range(max_len):
        col1, col2 = st.columns(2)

        if i < len(s_images):
            col1.markdown(f"**{s_images[i]['type']}**")
            col1.image(s_images[i]["url"])
        else:
            col1.write("❌ Missing in Salsify")

        if i < len(r_images):
            col2.markdown(f"**CVS Image {i+1}**")
            col2.image(r_images[i])
        else:
            col2.write("❌ Missing in CVS")

# =========================================
# ✅ TEXT / SCORING (UNCHANGED)
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

    ratio = max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a[:200], b[:200]).ratio()
    )

    a_words = set(a.split())
    b_words = set(b.split())

    overlap = len(a_words & b_words)
    total = len(a_words | b_words)

    word_score = (overlap / total) if total else 0
    final = (ratio * 0.6) + (word_score * 0.4)

    return int(final * 100)

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    export_rows = []
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")
        full_html = get_html(row["retail_url"])

        try:
            s_images = get_salsify_images(row["salsify_url"])
            s_images = [img for img in s_images if img.get("url")]

            r_images = get_cvs_images(row["retail_url"])

            with st.expander("🧺 Salsify Images", expanded=True):
                st.write("Total Salsify images:", len(s_images))
                cols = st.columns(3)

                for i, img in enumerate(s_images):
                    cols[i % 3].image(img["url"], caption=img["type"])

            render_image_comparison_by_property(s_images, r_images)

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
            continue

# =========================================
# ✅ EXPORT (UNCHANGED ✅)
# =========================================
if summary_rows:

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(export_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        detail_df.to_excel(writer, index=False, sheet_name="Details")

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
