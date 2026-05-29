
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

st.title("PDP QA Tool (FINAL - Real CVS Images)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

# -----------------------------
# ✅ SALSIFY IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        soup = BeautifulSoup(get_html(url), "html.parser")
        imgs = soup.find_all("img")

        images = []
        for img in imgs:
            src = img.get("src") or ""
            if src.startswith("http"):
                images.append(src)

        return list(dict.fromkeys(images))[:8]

    except:
        return []

# -----------------------------
# ✅ CVS IMAGES (REAL FIX)
# -----------------------------
def get_cvs_images(url):
    try:
        html = get_html(url)

        # ✅ extract ALL scene7 images
        matches = re.findall(r'https://[^"]+scene7[^"]+\\.jpg', html)

        cleaned = []

        for m in matches:
            # remove resizing params
            base = m.split("?")[0]

            # remove icons / small junk
            if not any(x in base.lower() for x in [
                "icon", "logo", "swatch", "thumbnail-default"
            ]):
                cleaned.append(base)

        # ✅ dedupe
        unique = list(dict.fromkeys(cleaned))

        # ✅ FINAL FILTER: keep REAL product images only
        final = []
        seen_names = set()

        for img in unique:
            name = img.split("/")[-1]

            # avoid duplicates of same file
            if name not in seen_names:
                final.append(img)
                seen_names.add(name)

        return final[:8]

    except:
        return []

# -----------------------------
# ✅ SAFE DISPLAY
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

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        st.write(f"Salsify Images: {len(s_images)}")
        st.write(f"CVS Images: {len(r_images)}")

        col1, col2 = st.columns(2)

        with col1:
            display_images("Salsify", s_images)

        with col2:
            display_images("CVS", r_images)

        # ✅ result
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
