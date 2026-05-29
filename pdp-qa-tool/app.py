
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

st.title("PDP QA Tool (Stable Version)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# Fetch rendered page using text fallback
# -----------------------------
def get_page_text(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")

        return soup.get_text(" ", strip=True)

    except:
        return ""

# -----------------------------
# Cleaner image extraction (focused)
# -----------------------------
def get_images(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers)
        soup = BeautifulSoup(res.text, "html.parser")

        imgs = soup.find_all("img")

        image_urls = []

        for img in imgs:
            src = img.get("src") or ""

            # keep likely PDP images only
            if any(k in src.lower() for k in ["zoom", "product", "image"]):
                if not any(bad in src.lower() for bad in ["icon", "logo"]):
                    image_urls.append(src)

        return list(set(image_urls))

    except:
        return []

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    results = []

    for _, row in df.iterrows():

        s_text = get_page_text(row["salsify_url"])
        r_text = get_page_text(row["retail_url"])

        s_images = get_images(row["salsify_url"])
        r_images = get_images(row["retail_url"])

        # ✅ Text comparison
        text_score = fuzz.partial_ratio(s_text, r_text)

        # ✅ Image comparison by COUNT (reliable)
        image_match_pct = round(
            (min(len(r_images), len(s_images)) / max(len(s_images), 1)) * 100,
            1
        )

        # ✅ Status logic
        status = "PASS" if text_score > 70 and image_match_pct > 50 else "FAIL"

        results.append({
            "SKU": row["sku"],
            "Text Score": text_score,
            "Image Match %": image_match_pct,
            "Salsify Images": len(s_images),
            "Retail Images": len(r_images),
            "Status": status
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Results", csv, "qa_results.csv")
