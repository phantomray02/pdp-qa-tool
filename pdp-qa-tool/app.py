
import streamlit as st
import pandas as pd
from PIL import Image
from playwright.sync_api import sync_playwright

st.title("PDP QA Tool (Capture CVS Thumbnail Rail)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# SCREENSHOT FUNCTION
# -----------------------------
def capture_cvs_thumbnails(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, timeout=60000)
            page.wait_for_timeout(5000)

            # ✅ target LEFT thumbnail rail (this is key)
            element = page.query_selector('div[class*="thumb"], div[class*="carousel"]')

            if element:
                screenshot = element.screenshot()
                browser.close()
                return Image.open(io.BytesIO(screenshot))

            browser.close()
            return None

    except:
        return None


# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # ✅ capture CVS thumbnail strip
        img = capture_cvs_thumbnails(row["retail_url"])

        if img:
            st.image(img, caption="CVS Thumbnail Rail", use_container_width=True)
        else:
            st.write("❌ Could not capture thumbnails")

        st.divider()
