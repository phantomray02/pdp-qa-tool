
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

st.title("PDP QA Tool")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# Get Salsify text + images
# -----------------------------
def get_salsify_data(url):
    try:
        res = requests.get(url)
        soup = BeautifulSoup(res.text, "html.parser")

        # All visible text
        text = soup.get_text(" ", strip=True)

        # Images
       
imgs = soup.find_all("img")

image_urls = []

for img in imgs:
    src = img.get("src")

    if not src:
        continue

    # ✅ KEEP only real product images
    if any(keyword in src.lower() for keyword in [
        "cvs",        # CVS CDN
        "product",    # product paths
        "image",      
        "zoom"
    ]):

        # ❌ REMOVE junk images
        if not any(bad in src.lower() for bad in [
            "icon",
            "logo",
            "sprite",
            "placeholder",
            "thumbnail-default"
        ]):
            image_urls.append(src)

# remove duplicates
image_urls = list(set(image_urls))


        return text, list(set(image_urls))

    except:
        return "", []

# -----------------------------
# Get CVS description + features + images
# -----------------------------
def get_cvs_data(url):
    try:
        res = requests.get(url)
        soup = BeautifulSoup(res.text, "html.parser")

        # Description (first paragraph)
        desc_tag = soup.find("p")
        description = desc_tag.get_text(strip=True) if desc_tag else ""

        # Bullet points
        bullets = soup.find_all("li")
        features = " ".join([b.get_text(strip=True) for b in bullets])

        # Images
        imgs = soup.find_all("img")
        image_urls = [img.get("src") for img in imgs if img.get("src")]

        return description, features, list(set(image_urls))

    except:
        return "", "", []

# -----------------------------
# Main logic
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    results = []

    for _, row in df.iterrows():

        # Pull data
        s_text, s_images = get_salsify_data(row["salsify_url"])
        cvs_desc, cvs_features, r_images = get_cvs_data(row["retail_url"])

        # -----------------------------
        # Text scoring
        # -----------------------------
        desc_score = fuzz.partial_ratio(s_text, cvs_desc)
        feat_score = fuzz.partial_ratio(s_text, cvs_features)

        # -----------------------------
        # Image comparison
        # -----------------------------
        s_set = set(s_images)
        r_set = set(r_images)

        match_count = len(s_set & r_set)
        total_salsify = len(s_set)

        image_match_pct = round(
            (match_count / total_salsify) * 100, 1
        ) if total_salsify > 0 else 0

        # -----------------------------
        # Status logic (tune later)
        # -----------------------------
        status = "PASS" if (
            desc_score > 85 and
            feat_score > 75 and
            image_match_pct > 40
        ) else "FAIL"

        # -----------------------------
        # Save result
        # -----------------------------
        results.append({
            "SKU": row["sku"],
            "Description Score": desc_score,
            "Feature Score": feat_score,
            "Image Match %": image_match_pct,
            "Salsify Images": len(s_set),
            "Retail Images": len(r_set),
            "Status": status
        })

    result_df = pd.DataFrame(results)

    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode('utf-8')
    st.download_button("Download Results", csv, "qa_results.csv")
