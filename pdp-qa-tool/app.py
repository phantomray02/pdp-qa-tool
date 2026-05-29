
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Visual Image Compare - Stable)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_soup(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")

# -----------------------------
# ✅ SALSIFY IMAGES (clean)
# -----------------------------
def get_salsify_images(url):
    try:
        soup = get_soup(url)
        imgs = soup.find_all("img")

        images = []
        for img in imgs:
            src = img.get("src") or ""

            if src.startswith("http"):
                images.append(src)

        # remove duplicates, limit to realistic carousel
        return list(dict.fromkeys(images))[:8]

    except:
        return []

# -----------------------------
# ✅ CVS IMAGES (WORKING VERSION)
# -----------------------------
def get_cvs_images(url):
    try:
        soup = get_soup(url)
        imgs = soup.find_all("img")

        thumbs = []

        for img in imgs:
            src = img.get("src") or ""
            width = img.get("width")
            height = img.get("height")

            try:
                # ✅ thumbnails = small images
                if width and height:
                    if int(width) <= 300 and int(height) <= 300:
                        if src.startswith("http"):
                            thumbs.append(src)
            except:
                continue

        # ✅ remove duplicates
        return list(dict.fromkeys(thumbs))

    except:
        return []

# -----------------------------
# ✅ SAFE IMAGE DISPLAY
# -----------------------------
def display_images(label, images):
    st.markdown(f"### {label}")

    cols = st.columns(3)

    for i, img in enumerate(images):
        try:
            if img.startswith("http"):
                cols[i % 3].image(
                    img,
                    caption=f"{i+1}",
                    use_container_width=True
                )
        except:
            continue

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # ✅ GET IMAGES
        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # ✅ COUNTS
        st.write(f"Salsify Images: {len(s_images)}")
        st.write(f"CVS Images: {len(r_images)}")

        # ✅ SIDE-BY-SIDE
        col1, col2 = st.columns(2)

        with col1:
            display_images("Salsify", s_images)

        with col2:
            display_images("CVS", r_images)

        # ✅ RESULT
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
