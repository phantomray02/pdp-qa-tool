
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Accurate Images + Content)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# Get HTML
# -----------------------------
def get_soup(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")

# -----------------------------
# Extract FULL PAGE TEXT
# -----------------------------
def get_text(url):
    try:
        soup = get_soup(url)
        return soup.get_text(" ", strip=True).lower()
    except:
        return ""

# -----------------------------
# Extract ONLY PDP CAROUSEL IMAGES
# -----------------------------
def get_images(url):
    try:
        soup = get_soup(url)

        image_urls = []

        for img in soup.find_all("img"):
            src = img.get("src") or ""
            src_lower = src.lower()

            # ✅ TARGET REAL PRODUCT IMAGES ONLY
            if any(k in src_lower for k in [
                "zoom", "large", "500", "800"
            ]):

                # ❌ REMOVE NON-PDP IMAGES
                if not any(bad in src_lower for bad in [
                    "icon", "logo", "sprite", "banner", "ads", "thumbnail-default"
                ]):
                    image_urls.append(src)

        # ✅ REMOVE DUPES BUT KEEP ORDER
        seen = set()
        ordered = []
        for img in image_urls:
            if img not in seen:
                ordered.append(img)
                seen.add(img)

        # ✅ LIMIT TO FIRST 6 (real PDP carousel size)
        return ordered[:6]

    except:
        return []

# -----------------------------
# Extract KEYWORDS
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

        # ✅ IMAGE COMPARISON (FIXED)
        if r_count == s_count:
            image_result = "✅ Match"
        elif r_count < s_count:
            image_result = f"❌ Missing {s_count - r_count}"
        else:
            image_result = f"⚠ Extra {r_count - s_count}"

        # ✅ DESCRIPTION CHECK
        desc_result = "✅ OK" if s_text[:200] in r_text else "❌ Different"

        # ✅ FEATURE CHECK
        keywords = extract_keywords(s_text)
        missing = [k for k in keywords if k not in r_text]

        if len(missing) == 0:
            feature_result = "✅ OK"
        else:
            feature_result = f"❌ Missing: {', '.join(missing)}"

        # ✅ FINAL STATUS
        status = "PASS" if (
            image_result == "✅ Match"
            and desc_result == "✅ OK"
            and feature_result == "✅ OK"
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
