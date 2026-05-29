
import streamlit as st
import pandas as pd
import requests
import re
from PIL import Image
from io import BytesIO

st.title("PDP QA Tool (CVS Thumbnails FINAL WORKING)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# DOWNLOAD IMAGE
# -----------------------------
def download_image(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=6)

        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None


# -----------------------------
# SALSIFY IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        html = requests.get(url).text
        imgs = re.findall(r'https://[^"]+\.(jpg|png)', html)

        clean = []
        for img in imgs:
            if isinstance(img, tuple):
                continue
            if "http" in img:
                clean.append(img)

        return list(dict.fromkeys(clean))[:8]

    except:
        return []


# -----------------------------
# ✅ REAL CVS IMAGE EXTRACTION
# -----------------------------
def get_cvs_images(url):
    try:
        html = requests.get(url).text

        # ✅ grab ALL scene7 images
        matches = re.findall(r'https://[^"]*scene7[^"]+\.jpg', html)

        images = []

        for m in matches:
            clean = m.split("?")[0]

            # ✅ filter real product images only
            if any(x in clean.lower() for x in [
                "content", "prod", "package", "-p"
            ]):
                images.append(clean)

        # ✅ remove duplicates
        unique = list(dict.fromkeys(images))

        # ✅ remove file duplicates
        final = []
        seen = set()

        for img in unique:
            name = img.split("/")[-1]
            if name not in seen:
                final.append(img)
                seen.add(name)

        return final

    except:
        return []


# -----------------------------
# DISPLAY
# -----------------------------
def display_images(label, urls):
    st.markdown(f"### {label}")

    cols = st.columns(3)

    for i, url in enumerate(urls):
        img = download_image(url)

        if img:
            cols[i % 3].image(img, caption=f"{i+1}", use_container_width=True)
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
        if len(r_images) == 0:
            st.error("❌ CVS images still blocked (page requires JS)")
        elif len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
