import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO
import json

st.write("🚀 VERSION TEST - NO PLAYWRIGHT")
st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

download_placeholder = st.empty()
export_rows = []

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
# ✅ IMAGE HELPERS
# =========================================
def extract_best_image_from_tag(img_tag):
    if not img_tag:
        return None

    srcset = img_tag.get("srcset", "")
    src = img_tag.get("src", "")

    if srcset and "salsify" in srcset:
        urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
        return urls[-1] if urls else None

    if src and "salsify" in src:
        return src

    return None


def extract_from_json(html):
    results = []

    try:
        matches = re.findall(r'https://images\\.salsify\\.com[^"]+', html)

        for m in matches:
            clean = m.split("?")[0]
            results.append(clean)

        return list(dict.fromkeys(results))
    except:
        return []

# =========================================
# ✅ SALSIFY IMAGE EXTRACTION (UNCHANGED STRUCTURE)
# =========================================
def get_salsify_images(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    images = []

    # JSON
    json_imgs = extract_from_json(html)
    for url in json_imgs:
        images.append({
            "type": "JSON Image",
            "url": url
        })

    # DOM
    for img in soup.find_all("img"):
        url = extract_best_image_from_tag(img)
        if url and "salsify" in url:
            images.append({
                "type": "DOM Image",
                "url": url
            })

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
# ✅ LOAD IMAGE
# =========================================
def load_image(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/*,*/*;q=0.8"
        }
        r = requests.get(url, headers=headers, timeout=10)

        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None

    return None

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

            img_obj = load_image(s_images[i]["url"])
            if img_obj:
                col1.image(img_obj)
            else:
                col1.write("❌ Failed to load")

        else:
            col1.write("❌ Missing in Salsify")

        if i < len(r_images):
            col2.markdown(f"**CVS Image {i+1}**")

            img_obj = load_image(r_images[i])
            if img_obj:
                col2.image(img_obj)
            else:
                col2.write("❌ Failed to load CVS image")
        else:
            col2.write("❌ Missing in CVS")

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
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
                    st.write(f"{img['type']} → {img['url']}")

            render_image_comparison_by_property(s_images, r_images)

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
