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
# ✅ GET HTML (REPLACED PLAYWRIGHT ONLY)
# =========================================
def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)

        if r.status_code == 200:
            html_cache[url] = r.text
            return r.text

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
    soup = get_soup(url)
    images = []
    seen_urls = set()
    
    try:
        rows = soup.find_all("tr")
        
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            
            prop_name_cell = cells[0]
            prop_value_cell = cells[1]
            
            prop_text = prop_name_cell.get_text(strip=True).lower()
            
            if "image" not in prop_text and "photo" not in prop_text:
                continue
            
            img_tags = prop_value_cell.find_all("img")
            
            for img in img_tags:
                img_src = img.get("src", "")
                
                if ("salsify" in img_src or "images.salsify" in img_src) and img_src not in seen_urls:
                    seen_urls.add(img_src)
                    
                    prop_label = prop_name_cell.get_text(strip=True)
                    
                    images.append({
                        "type": prop_label,
                        "url": img_src
                    })
    
    except Exception as e:
        print(f"Salsify image extraction error: {e}")
    
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
# ✅ RENDER IMAGE COMPARISON BY SALSIFY PROPERTY
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
