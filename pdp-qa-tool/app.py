
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Visual Image Compare)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_soup(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")

# -----------------------------
# GET TEXT
# -----------------------------
def get_text(url):
    try:
        soup = get_soup(url)
        return soup.get_text(" ", strip=True).lower()
    except:
        return ""

# -----------------------------
# ✅ SALSIFY IMAGE EXTRACTION
# -----------------------------
def get_salsify_images(url):
    try:
        soup = get_soup(url)
        imgs = soup.find_all("img")

        images = []

        for img in imgs:
            src = img.get("src") or ""

            if "http" in src:
                images.append(src)

        return list(dict.fromkeys(images))[:8]

    except:
        return []

# -----------------------------
# ✅ CVS IMAGE EXTRACTION (Scene7)
# -----------------------------
def get_cvs_images(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers)
        text = res.text

        images = []

        # extract Scene7 images
        import re
        matches = re.findall(r'https://[^"]+\\.jpg', text)

        for m in matches:
            if "scene7.com" in m:
                clean = m.split("?")[0]
                if clean not in images:
                    images.append(clean)

        return images[:8]

    except:
        return []

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # Get images
        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        # ---------- COUNTS ----------
        st.write(f"Salsify Images: {len(s_images)}")
        st.write(f"CVS Images: {len(r_images)}")

        # ---------- SIDE BY SIDE ----------
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### Salsify")
            for i, img in enumerate(s_images):
                st.image(img, caption=f"{i+1}", width=150)

        with col2:
            st.markdown("### CVS")
            for i, img in enumerate(r_images):
                st.image(img, caption=f"{i+1}", width=150)

        # ---------- RESULT ----------
        if len(r_images) == len(s_images):
            st.success("Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"Missing {len(s_images) - len(r_images)} Images")
        else:
            st.warning(f"Extra {len(r_images) - len(s_images)} Images")

        st.divider()
