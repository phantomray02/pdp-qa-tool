
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

st.title("PDP QA Tool (Images + Content ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# -----------------------------
# SALSIFY IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        soup = get_soup(url)
        images = []

        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                images.append(src)

        return list(dict.fromkeys(images))[:8]

    except:
        return []

# -----------------------------
# ✅ CVS IMAGES (CLEAN + HIGH RES)
# -----------------------------
def get_cvs_images(url):
    try:
        html = get_html(url)

        matches = re.findall(
            r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg\?[^\s"]*',
            html
        )

        image_dict = {}

        for m in matches:
            full = "https://www.cvs.com" + m
            base = full.split("?")[0]
            name = base.split("/")[-1]

            size_match = re.search(r'Resize=\((\d+),', m)
            size = int(size_match.group(1)) if size_match else 0

            if name not in image_dict or size > image_dict[name]["size"]:
                image_dict[name] = {
                    "url": base,
                    "size": size
                }

        return [v["url"] for v in image_dict.values()]

    except:
        return []

# -----------------------------
# ✅ TEXT EXTRACTION
# -----------------------------
def get_text_data(url):
    try:
        soup = get_soup(url)

        text = soup.get_text(" ", strip=True)

        # simple extraction rules
        title = ""
        desc = ""
        features = []

        # title
        if soup.title:
            title = soup.title.text.strip()

        # description
        for p in soup.find_all("p"):
            t = p.get_text().lower()
            if len(t) > 100:
                desc = p.get_text()
                break

        # features
        for li in soup.find_all("li"):
            txt = li.get_text().strip()
            if len(txt) > 20:
                features.append(txt)

        return {
            "title": title,
            "description": desc,
            "features": features[:5]
        }

    except:
        return {
            "title": "",
            "description": "",
            "features": []
        }

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        # -----------------------------
        # GET DATA
        # -----------------------------
        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        s_text = get_text_data(row["salsify_url"])
        r_text = get_text_data(row["retail_url"])

        max_len = max(len(s_images), len(r_images))

        # -----------------------------
        # ✅ IMAGE ALIGNMENT
        # -----------------------------
        st.markdown("## Image Comparison")

        for i in range(max_len):

            col1, col2 = st.columns(2)

            with col1:
                st.write(f"Salsify {i+1}")
                if i < len(s_images):
                    st.image(s_images[i], use_container_width=True)
                else:
                    st.error("Missing")

            with col2:
                st.write(f"CVS {i+1}")
                if i < len(r_images):
                    st.image(r_images[i], use_container_width=True)
                else:
                    st.error("Missing")

            st.divider()

        # -----------------------------
        # ✅ IMAGE RESULT
        # -----------------------------
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        # -----------------------------
        # ✅ CONTENT QA
        # -----------------------------
        st.markdown("## Content Comparison")

        st.markdown("### Title")
        st.write("Salsify:", s_text["title"])
        st.write("CVS:", r_text["title"])

        st.markdown("### Description")
        st.write("Salsify:", s_text["description"])
        st.write("CVS:", r_text["description"])

        st.markdown("### Features")
        st.write("Salsify:", s_text["features"])
        st.write("CVS:", r_text["features"])

        st.divider()
