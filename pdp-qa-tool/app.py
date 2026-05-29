
import streamlit as st
import pandas as pd
from playwright.sync_api import sync_playwright
from rapidfuzz import fuzz

st.title("PDP QA Tool (Playwright Version)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# Use REAL browser to load page
# -----------------------------
def get_page_data(url):

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url, timeout=60000)

        # Wait for page to load
        page.wait_for_timeout(5000)

        # ✅ Grab visible text
        text = page.inner_text("body")

        # ✅ Grab images
        images = page.eval_on_selector_all(
            "img",
            "imgs => imgs.map(img => img.src)"
        )

        browser.close()

        return text, list(set(images))


# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    results = []

    for _, row in df.iterrows():

        # Load BOTH pages fully rendered
        s_text, s_images = get_page_data(row["salsify_url"])
        r_text, r_images = get_page_data(row["retail_url"])

        # -----------------------------
        # Text comparison
        # -----------------------------
        desc_score = fuzz.partial_ratio(s_text, r_text)

        # -----------------------------
        # Image comparison (count-based)
        # -----------------------------
        image_match_pct = round(
            (min(len(r_images), len(s_images)) / max(len(s_images), 1)) * 100,
            1
        )

        # -----------------------------
        # Status logic
        # -----------------------------
        status = "PASS" if (
            desc_score > 80 and image_match_pct > 60
        ) else "FAIL"

        results.append({
            "SKU": row["sku"],
            "Text Score": desc_score,
            "Image Match %": image_match_pct,
            "Salsify Images": len(s_images),
            "Retail Images": len(r_images),
            "Status": status
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Results", csv, "qa_results.csv")
