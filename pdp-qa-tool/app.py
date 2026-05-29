
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re

st.title("PDP QA Tool (Correct CVS Image Extraction ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_html(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    return requests.get(url, headers=headers).text

# -----------------------------
# SALSIFY
# -----------------------------
def get_salsify_images(url):
    try:
        soup = BeautifulSoup(get_html(url), "html.parser")

        images = []
        for img in soup.find_all("img"):
            src = img.get("src") or ""

            if src.startswith("http"):
                images.append(src)

        return list(dict.fromkeys(images))[:8]

    except:
        return []

# -----------------------------
# ✅ FINAL CVS IMAGE EXTRACTION
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

            # ✅ extract size (wid/Resize)
            size_match = re.search(r'Resize=\((\d+),', m)
            size = int(size_match.group(1)) if size_match else 0

            # ✅ keep highest resolution for each image name
            if name not in image_dict or size > image_dict[name]["size"]:
                image_dict[name] = {
                    "url": base,
                    "size": size
                }

        # ✅ return only highest res images
        final = [v["url"] for v in image_dict.values()]

        return list(dict.fromkeys(final))

    except:
        return []

# -----------------------------
# DISPLAY
# -----------------------------
def display_images(label, images):
    st.markdown(f"### {label}")

    cols = st.columns(4)

    for i, img in enumerate(images):
        try:
            cols[i % 4].image(img, caption=f"{i+1}", use_container_width=True)
        except:
            cols[i % 4].write(f"{i+1} ❌")

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])
        r_images = get_cvs_images(row["retail_url"])

        st.write(f"Salsify Images: {len(s_images)}")
        st.write(f"CVS Images: {len(r_images)}")

        col1, col2 = st.columns(2)

        with col1:
            display_images("Salsify", s_images)

        with col2:
            display_images("CVS (True Images ✅)", r_images)

        # ✅ RESULT
        if len(r_images) == len(s_images):
            st.success("✅ Images Match")
        elif len(r_images) < len(s_images):
            st.error(f"❌ Missing {len(s_images) - len(r_images)} images")
        else:
            st.warning(f"⚠ Extra {len(r_images) - len(s_images)} images")

        st.divider()
