
import streamlit as st
import pandas as pd
import requests
import re

st.title("PDP QA Tool (Final Accurate CVS Fix)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET RAW PAGE TEXT
# -----------------------------
def get_text(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers)
        return res.text.lower()
    except:
        return ""

# -----------------------------
# ✅ SALSIFY LOGIC (unchanged)
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
# ✅ REAL CVS IMAGE EXTRACTION (FROM JSON)
# -----------------------------
def get_cvs_count(text):

    # Find all image URLs from embedded data
    images = re.findall(r'https://[^"]+\\.jpg', text)

    # keep ONLY product images (Scene7 + not icons)
    filtered = []

    for img in images:
        if "scene7.com" in img:

            # remove small UI junk
            if not any(bad in img for bad in [
                "icon", "logo", "swatch", "thumbnail-default"
            ]):
                filtered.append(img.split("?")[0])

    # ✅ remove duplicates
    unique = list(set(filtered))

    # ✅ REMOVE duplicates of same base image (size variations)
    final = []
    seen_names = set()

    for img in unique:
        name = img.split("/")[-1]

        if name not in seen_names:
            final.append(name)
            seen_names.add(name)

    # ✅ LIMIT to real carousel range
    return min(len(final), 6)

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

        s_count = get_salsify_expected_count(s_text)
        r_count = get_cvs_count(r_text)

        # ✅ IMAGE RESULT
        if s_count == 0 and r_count == 0:
            image_result = "⚠ No Images Found"
        elif r_count == s_count:
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
