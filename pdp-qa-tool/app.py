
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from io import BytesIO
from PIL import Image

st.title("PDP QA Tool (Image Download + Compare)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# DOWNLOAD IMAGE (KEY FIX)
# -----------------------------
def download_image(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return Image.open(BytesIO(response.content))
    except:
        return None

# -----------------------------
# GET HTML
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

# -----------------------------
# SALSIFY IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        soup = BeautifulSoup(get_html(url), "html.parser")
        images = []

        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                images.append(src)

        return list(dict.fromkeys(images))[:8]

    except:
        return []

# -----------------------------
# CVS IMAGES (SCENE7 EXTRACTION)
# -----------------------------
def get_cvs_images(url):
    try:
        html = get_html(url)

        # ✅ extract ALL scene7 images
        matches = re.findall(r'https://[^"]+scene7[^"]+\.jpg', html)

        images = []

        for m in matches:
            clean = m.split("?")[0]

            if not any(x in clean.lower() for x in [
                "icon", "logo", "swatch", "thumbnail-default"
            ]):
                images.append(clean)

        # remove duplicates
        unique = list(dict.fromkeys(images))

        # remove variations (same filename)
        final = []
        seen = set()

        for img in unique:
            name = img.split("/")[-1]

            if name not in seen:
                final.append(img)
                seen.add(name)

        return final[:10]

    except:
        return []

# -----------------------------
# DISPLAY GRID WITH DOWNLOADED IMAGES
# -----------------------------
def display_images(label, image_urls):
    st.markdown(f"### {label}")

    cols = st.columns(3)

    for i, url in enumerate(image_urls):
        img = download_image(url)

        if img:
            cols[i % 3].image(
                img,
                caption=f"{i+1}",
                use_container_width=True
            )
        else:
            cols[i % 3].write(f"{i+1} ❌")

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        st.write(f"Salsify Images: {len(s_images)}")
        st.write(f"CVS Images: {len(r_images)}")

        # side-by-side
        col1, col2 = st.columns(2)

        with col1:
            display_images("Salsify", s_images)

        with col2:
            display_images("CVS", r_images)

        # result
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
