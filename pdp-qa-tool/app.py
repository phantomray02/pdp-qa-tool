
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

st.title("PDP QA Tool (Correct CVS Image Extraction ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

# -----------------------------
# SALSIFY
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
# ✅ FINAL CVS IMAGE EXTRACTION
# -----------------------------
def get_cvs_images(url):

    try:
        html = get_html(url)

        # ✅ find ALL high_res image paths
        matches = re.findall(
            r'/bizcontent/merchandising/products/images/high_res/[^"]+\.jpg',
            html
        )

        images = []

        for m in matches:
            full_url = "https://www.cvs.com" + m

            # ✅ FILTER OUT RESIZED VERSIONS
            if "im=" in m:
                continue

            images.append(full_url)

        # ✅ clean duplicates
        unique = list(dict.fromkeys(images))

        return unique

    except:
        return []

# -----------------------------
# DISPLAY
# -----------------------------
def display_images(label, images):
    st.markdown(f"### {label}")

    cols = st.columns(4)

    for i, img in enumerate(images):
        try:
            cols[i % 4].image(img, caption=f"{i+1}", use_container_width=True)
        except:
            cols[i % 4].write(f"{i+1} ❌")

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
            display_images("CVS (True Images ✅)", r_images)

        # ✅ RESULT
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
