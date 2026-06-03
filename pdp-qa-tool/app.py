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
# ✅ GET HTML (REPLACED PLAYWRIGHT)
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
    try:
        soup = get_soup(url)
        images = []
        seen_urls = set()
        
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            srcset = img.get("srcset") or ""
            
            if "salsify.com" in src and src not in seen_urls:
                seen_urls.add(src)
                images.append({
                    "url": src,
                    "type": "Image"
                })
            
            if srcset and "salsify.com" in srcset:
                urls = [u.strip().split()[0] for u in srcset.split(",")]
                for url_candidate in urls:
                    if url_candidate not in seen_urls and "salsify.com" in url_candidate:
                        seen_urls.add(url_candidate)
                        images.append({
                            "url": url_candidate,
                            "type": "Image"
                        })
        
        print(f"✅ Found {len(images)} Salsify images")
        return images
    
    except Exception as e:
        print(f"Error extracting Salsify images: {e}")
        return []

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
# ✅ IMAGE COMPARISON
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
            r_images = get_cvs_images(row["retail_url"])

            with st.expander("🧺 Salsify Images", expanded=True):
                st.write("Total Salsify images:", len(s_images))

                for img in s_images:
                    st.write(img["type"], "→", img["url"])

            render_image_comparison_by_property(s_images, r_images)

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
