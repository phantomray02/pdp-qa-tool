
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import json
from PIL import Image
from io import BytesIO

st.title("PDP QA Tool (REAL CVS Data Extraction)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# DOWNLOAD IMAGE SAFELY
# -----------------------------
def download_image(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None

# -----------------------------
# SALSIFY IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        soup = BeautifulSoup(requests.get(url).text, "html.parser")

        images = []
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                images.append(src)

        return list(dict.fromkeys(images))[:8]
    except:
        return []

# -----------------------------
# ✅ REAL CVS IMAGE EXTRACTION
# -----------------------------
def get_cvs_images(url):

    try:
        html = requests.get(url).text
        soup = BeautifulSoup(html, "html.parser")

        scripts = soup.find_all("script")

        for script in scripts:
            if "__INITIAL_STATE__" in script.text:

                # Extract JSON block
                text = script.text.split("=",1)[1].strip().rstrip(";")

                data = json.loads(text)

                images = []

                try:
                    media_items = data["product"]["product"]["media"]["items"]

                    for item in media_items:
                        src = item.get("zoomImageURL")

                        if src:
                            images.append(src)

                except:
                    continue

                return images

        return []

    except:
        return []

# -----------------------------
# DISPLAY GRID
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

        col1, col2 = st.columns(2)

        with col1:
            display_images("Salsify", s_images)

        with col2:
            display_images("CVS", r_images)

        # RESULT
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
