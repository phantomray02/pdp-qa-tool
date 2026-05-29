
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Working CVS + Exact Salsify)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
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
# ✅ EXACT SALSIFY LOGIC (KEEP THIS)
# -----------------------------
def get_salsify_expected_count(text):

    count = 0

    if "online optimized image-" in text:
        count += 1

    if "flat back_2d-" in text:
        count += 1

    if "flat left_2d-" in text:
        count += 1

    if "atf 2-generic" in text:
        count += 1

    if "atf 3-generic" in text:
        count += 1

    if "atf 4-generic" in text:
        count += 1

    if "atf 5-generic" in text:
        count += 1

    has_io = "atf i/o-generic" in text

    if has_io:
        count += 1
    else:
        if "atf 6-generic" in text:
            count += 1

    return count


# -----------------------------
# ✅ CVS THUMBNAIL DETECTION (RESTORED WORKING VERSION)
# -----------------------------
def get_cvs_thumbnail_count(url):

    try:
        soup = get_soup(url)
        imgs = soup.find_all("img")

        thumbs = []

        for img in imgs:
            src = img.get("src") or ""
            src_lower = src.lower()

            # ✅ CVS thumbnails are SMALL images (<300px usually)
            width = img.get("width")
            height = img.get("height")

            try:
                if width and height:
                    if int(width) <= 300 and int(height) <= 300:
                        thumbs.append(src)
            except:
                continue

        # ✅ remove duplicates
        seen = set()
        ordered = []
        for t in thumbs:
            if t not in seen:
                ordered.append(t)
                seen.add(t)

        return min(len(ordered), 6)

    except:
        return 0


# -----------------------------
# FEATURES
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

        # ✅ Salsify
        s_count = get_salsify_expected_count(s_text)

        # ✅ CVS (WORKING AGAIN)
        r_count = get_cvs_thumbnail_count(row["retail_url"])

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
            "Expected (Salsify)": s_count,
            "Found (CVS)": r_count,
            "Description": desc_result,
            "Features": feature_result,
            "Status": status
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download Results", csv, "qa_results.csv")
