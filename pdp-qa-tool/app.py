
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import os

st.title("PDP QA Tool (Images + Content)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# Get page HTML
# -----------------------------
def get_soup(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")

# -----------------------------
# Extract text
# -----------------------------
def get_page_text(url):
    try:
        soup = get_soup(url)
        return soup.get_text(" ", strip=True).lower()
    except:
        return ""

# -----------------------------
# Extract image URLs
# -----------------------------
def get_images(url):
    try:
        soup = get_soup(url)
        imgs = soup.find_all("img")

        image_urls = []

        for img in imgs:
            src = img.get("src") or ""
            src_lower = src.lower()

            # ✅ keep likely product images
            if any(k in src_lower for k in ["product", "image", "zoom"]):
                if not any(bad in src_lower for bad in ["icon", "logo", "sprite"]):
                    image_urls.append(src)

        return list(set(image_urls))

    except:
        return []

# -----------------------------
# Extract filename (KEY FIX)
# -----------------------------
def extract_filename(url):
    try:
        return os.path.basename(url.split("?")[0])
    except:
        return ""

# -----------------------------
# Extract key keywords
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

    found = []
    for k in keywords:
        if k in text:
            found.append(k)

    return found

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    results = []

    for _, row in df.iterrows():

        # --- TEXT ---
        s_text = get_page_text(row["salsify_url"])
        r_text = get_page_text(row["retail_url"])

        # --- IMAGES ---
        s_images = get_images(row["salsify_url"])
        r_images = get_images(row["retail_url"])

        s_files = [extract_filename(u) for u in s_images]
        r_files = [extract_filename(u) for u in r_images]

        s_set = set(s_files)
        r_set = set(r_files)

        missing_images = list(s_set - r_set)
        extra_images = list(r_set - s_set)

        if len(missing_images) == 0:
            image_result = "✅ OK"
        else:
            image_result = f"❌ Missing {len(missing_images)}"

        # --- DESCRIPTION ---
        if s_text[:200] in r_text:
            desc_result = "✅ OK"
        else:
            desc_result = "❌ Different"

        # --- FEATURES (KEYWORD MATCH) ---
        keywords = extract_keywords(s_text)

        missing_keywords = []
        for k in keywords:
            if k not in r_text:
                missing_keywords.append(k)

        if len(missing_keywords) == 0:
            feature_result = "✅ OK"
        else:
            feature_result = f"❌ Missing: {', '.join(missing_keywords)}"

        # --- STATUS ---
        status = "PASS" if (
            image_result == "✅ OK" and
            desc_result == "✅ OK" and
            feature_result == "✅ OK"
        ) else "FAIL"

        # --- OUTPUT ---
        results.append({
            "SKU": row["sku"],
            "Images": image_result,
            "Missing Image Count": len(missing_images),
            "Sample Missing Images": ", ".join(missing_images[:3]),
            "Extra Image Count": len(extra_images),
            "Description": desc_result,
            "Features": feature_result,
            "Status": status
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Results", csv, "qa_results.csv")
