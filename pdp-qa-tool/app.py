
import streamlit as st
import pandas as pd
from playwright.sync_api import sync_playwright
from PIL import Image
import io

st.title("PDP QA Tool (CVS Thumbnail Capture - Working)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# ✅ SCREENSHOT + CROP LEFT SIDE
# -----------------------------
def capture_cvs_thumbnails(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width":1400, "height":2000})

            page.goto(url, timeout=60000)
            page.wait_for_timeout(5000)

            # ✅ take FULL page screenshot
            screenshot = page.screenshot(full_page=True)

            browser.close()

            # ✅ open image
            img = Image.open(io.BytesIO(screenshot))

            # ✅ crop LEFT section (thumbnail rail area)
            width, height = img.size

            # adjust if needed slightly
            cropped = img.crop((
                0,              # left
                200,            # top (skip header)
                int(width * 0.25), # right (left 25% of page)
                height - 200    # bottom (skip footer)
            ))

            return cropped

    except:
        return None


# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        img = capture_cvs_thumbnails(row["retail_url"])

        if img:
            st.image(img, caption="CVS Thumbnail Section", use_container_width=True)
        else:
            st.error("❌ Screenshot failed")

        st.divider()
