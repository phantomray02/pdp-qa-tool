
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (CVS Scroll Fix ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_soup(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")

# -----------------------------
# SALSIFY IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        soup = get_soup(url)

        images = []
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                images.append(src)

        return list(dict.fromkeys(images))[:8]

    except:
        return []

# -----------------------------
# ✅ FINAL CVS EXTRACTION (SCROLL FIXED)
# -----------------------------

def get_cvs_images(url):
    try:
        soup = get_soup(url)

        thumbnails = []

        # ✅ FIND FULL TABLIST
        container = soup.find("div", {"role": "tablist"})

        if not container:
            return []

        # ✅ COUNT ALL TAB BUTTONS (THIS INCLUDES HIDDEN SCROLLED ITEMS)
        buttons = container.find_all("button", {"role": "tab"})

        for btn in buttons:

            img = btn.find("img")

            if not img:
                continue

            src = img.get("src") or ""

            if "high_res" in src:

                if src.startswith("/"):
                    src = "https://www.cvs.com" + src

                thumbnails.append(src)

        # ✅ remove duplicates
        return list(dict.fromkeys(thumbnails))

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
            display_images("CVS (Full Thumbnail Rail)", r_images)

        # ✅ result
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
