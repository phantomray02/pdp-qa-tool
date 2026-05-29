
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Images + Content)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# Get HTML
# -----------------------------
def get_soup(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")

# -----------------------------
# Get full text
# -----------------------------
def get_text(url):
    try:
        soup = get_soup(url)
        return soup.get_text(" ", strip=True).lower()
    except:
        return ""

# -----------------------------
# Get CLEAN PDP images
# -----------------------------
def get_images(url):
    try:
        soup = get_soup(url)
        imgs = soup.find_all("img")

        cleaned = []

        for img in imgs:
            src = img.get("src") or ""
            src_lower = src.lower()

            # keep product-like images only
            if any(k in src_lower for k in ["product", "zoom", "image"]):

                # remove junk
                if not any(bad in src_lower for bad in [
                    "icon", "logo", "sprite", "banner", "ads"
                ]):
                    cleaned.append(src)

        # take first 8 only (PDP gallery)
        return list(dict.fromkeys(cleaned))[:8]

    except:
        return []

# -----------------------------
# Keyword extraction
# -----------------------------
def extract_keywords(text):
    keywords = [
        "unscented",
        "regular",
        "compact",
        "45",
        "leak",
        "protection",
        "click"
    ]
    return [k for k in keywords if k in text]

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    results = []

    for _, row in df.iterrows():

        s_text = get_text(row["salsify_url"])
        r_text = get_text(row["retail_url"])

        s_images = get_images(row["salsify_url"])
        r_images = get_images(row["retail_url"])

        s_count = len(s_images)
        r_count = len(r_images)

        # -------- IMAGE RESULT --------
        if r_count == s_count:
            image_result = "✅ Match"
        elif r_count < s_count:
            image_result = f"❌ Missing {s_count - r_count}"
        else:
            image_result = f"⚠ Extra {r_count - s_count}"

        # -------- DESCRIPTION --------
        desc_result = "✅ OK" if s_text[:200] in r_text else "❌ Different"

        # -------- FEATURES --------
        keywords = extract_keywords(s_text)
        missing = [k for k in keywords if k not in r_text]

        if len(missing) == 0:
            feature_result = "✅ OK"
        else:
            feature_result = f"❌ Missing: {', '.join(missing)}"

        # -------- STATUS --------
        status = "PASS" if (
            image_result == "✅ Match" and
            desc_result == "✅ OK" and
            feature_result == "✅ OK"
        ) else "FAIL"

        results.append({
            "SKU": row["sku"],
            "Images": image_result,
            "Salsify Count": s_count,
            "Retail Count": r_count,
            "Description": desc_result,
            "Features": feature_result,
            "Status": status
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Results", csv, "qa_results.csv")
