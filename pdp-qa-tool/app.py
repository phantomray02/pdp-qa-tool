import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

st.title("PDP QA Tool")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

def get_text(url):
    try:
        res = requests.get(url)
        soup = BeautifulSoup(res.text, "html.parser")
        title = soup.find("h1")
        return title.get_text(strip=True) if title else ""
    except:
        return ""

if uploaded_file:
    df = pd.read_csv(uploaded_file)

    results = []
    
def get_images(url):
    try:
        res = requests.get(url)
        soup = BeautifulSoup(res.text, "html.parser")

        images = soup.find_all("img")

        image_urls = []
        for img in images:
            src = img.get("src")
            if src and "http" in src:
                image_urls.append(src)

        return list(set(image_urls))

    except:
        return []

for _, row in df.iterrows():

    s_text = get_salsify_data(row["salsify_url"])
    cvs_desc, cvs_features = get_cvs_data(row["retail_url"])

    # NEW: Image extraction
    s_images = get_images(row["salsify_url"])
    r_images = get_images(row["retail_url"])

    # Compare images
    s_set = set(s_images)
    r_set = set(r_images)

    match_count = len(s_set & r_set)
    total_salsify = len(s_set)

    image_match_pct = round((match_count / total_salsify) * 100, 1) if total_salsify > 0 else 0

    # Scores
    desc_score = fuzz.partial_ratio(s_text, cvs_desc)
    feat_score = fuzz.partial_ratio(s_text, cvs_features)

    # Status logic
    status = "PASS" if desc_score > 85 and feat_score > 80 and image_match_pct > 50 else "FAIL"

    results.append({
        "SKU": row["sku"],
        "Description Score": desc_score,
        "Feature Score": feat_score,
        "Image Match %": image_match_pct,
        "Salsify Images": total_salsify,
        "CVS Images": len(r_set),
        "Status": status
    })
