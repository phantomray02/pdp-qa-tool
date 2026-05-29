
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Screenshot View)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

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
# CVS FALLBACK: SHOW FULL PDP
# -----------------------------
def show_cvs_page(url):
    try:
        st.markdown("### CVS PDP (Live View)")
        st.markdown(
            f'<iframe src="{url}" width="100%" height="600"></iframe>',
            unsafe_allow_html=True
        )
    except:
        st.write("Unable to load CVS page")

# -----------------------------
# DISPLAY GRID
# -----------------------------
def display_images(images):
    cols = st.columns(3)

    for i, img in enumerate(images):
        try:
            cols[i % 3].image(img, caption=f"{i+1}", use_container_width=True)
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

        st.write(f"Salsify Images: {len(s_images)}")

        # -----------------------------
        # LEFT SIDE: SALSIFY
        # -----------------------------
        st.markdown("## Salsify")
        display_images(s_images)

        # -----------------------------
        # RIGHT: LIVE CVS PAGE
        # -----------------------------
        st.markdown("## CVS")
        show_cvs_page(row["retail_url"])

        st.divider()
