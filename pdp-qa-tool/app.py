
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

st.title("PDP QA Tool (Correct Carousel Matching)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET PAGE
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
# ✅ SMART IMAGE SIZE FILTER
# -----------------------------
def get_carousel_images(url):
    try:
        soup = get_soup(url)

        images = []

        for img in soup.find_all("img"):

            src = img.get("src") or ""

            # skip blanks
            if not src or "data:image" in src:
                continue

            # ✅ GET WIDTH / HEIGHT ATTRIBUTES
            width = img.get("width")
            height = img.get("height")

            # ✅ THUMBNAILS are SMALL (usually <300px)
            try:
                if width and height:
                    if int(width) < 300 and int(height) < 300:
                        images.append(src)
            except:
                continue

        # ✅ REMOVE DUPES KEEP ORDER
        seen = set()
        ordered = []
        for img in images:
            if img not in seen:
                ordered.append(img)
                seen.add(img)

        return ordered[:6]  # limit to carousel size

    except:
        return []

# -----------------------------
# KEYWORDS
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

        # ✅ FIXED IMAGE EXTRACTION
        s_images = get_carousel_images(row["salsify_url"])
        r_images = get_carousel_images(row["retail_url"])

        s_count = len(s_images)
        r_count = len(r_images)

        # ✅ IMAGE RESULT
        if r_count == s_count:
            image_result = "✅ Match"
        elif r_count < s_count:
            image_result = f"❌ Missing {s_count - r_count}"
        else:
            image_result = f"⚠ Extra {r_count - s_count}"

        # ✅ DESCRIPTION
        desc_result = "✅ OK" if s_text[:200] in r_text else "❌ Different"

        # ✅ FEATURES
        keywords = extract_keywords(s_text)
        missing = [k for k in keywords if k not in r_text]

        if len(missing) == 0:
            feature_result = "✅ OK"
        else:
            feature_result = f"❌ Missing: {', '.join(missing)}"

        status = "PASS" if (
            image_result == "✅ Match"
            and desc_result == "✅ OK"
            and feature_result == "✅ OK"
        ) else "FAIL"

        results.append({
            "SKU": row["sku"],
            "Images": image_result,
            "Salsify Thumbnails": s_count,
            "CVS Thumbnails": r_count,
            "Description": desc_result,
            "Features": feature_result,
            "Status": status
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Results", csv, "qa_results.csv")
