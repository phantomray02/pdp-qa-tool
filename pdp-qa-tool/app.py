
import streamlit as st
import pandas as pd
from playwright.sync_api import sync_playwright
import time

st.title("PDP QA Tool (FINAL Scroll Extraction ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# ✅ PLAYWRIGHT SCROLL + EXTRACT
# -----------------------------
def get_cvs_images(url):

    images = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, timeout=60000)

            # ✅ wait for page load
            time.sleep(5)

            # ✅ SCROLL THUMBNAIL AREA
            for _ in range(5):
                page.mouse.wheel(0, 500)
                time.sleep(1)

            # ✅ NOW HTML CONTAINS ALL THUMBNAILS
            html = page.content()

            # ✅ extract images
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            container = soup.find("div", {"role": "tablist"})

            if container:
                for img in container.find_all("img"):

                    src = img.get("src") or ""

                    if "high_res" in src:

                        if src.startswith("/"):
                            src = "https://www.cvs.com" + src

                        images.append(src)

            browser.close()

        return list(dict.fromkeys(images))

    except:
        return []


# -----------------------------
# SALSIFY (unchanged)
# -----------------------------
import requests
from bs4 import BeautifulSoup

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
            display_images("CVS (Full Scroll Extract)", r_images)

        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
