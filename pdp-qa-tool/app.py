
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Salsify vs CVS - Practical)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# SALSIFY IMAGES
# -----------------------------
def get_salsify_images(url):
    try:
        soup = BeautifulSoup(requests.get(url).text, "html.parser")

        images = []
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if src.startswith("http"):
                images.append(src)

        return list(dict.fromkeys(images))[:8]
    except:
        return []

# -----------------------------
# DISPLAY GRID
# -----------------------------
def display_images(images):
    cols = st.columns(4)

    for i, img in enumerate(images):
        try:
            cols[i % 4].image(img, caption=f"{i+1}", use_container_width=True)
        except:
            continue

# -----------------------------
# MAIN
# -----------------------------
if uploaded_file:

    df = pd.read_csv(uploaded_file)

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        s_images = get_salsify_images(row["salsify_url"])

        # ✅ SHOW SALSIFY
        st.markdown("### ✅ Salsify (Source)")
        st.write(f"Expected Images: {len(s_images)}")
        display_images(s_images)

        # ✅ CVS LINK (THIS IS THE KEY CHANGE)
        st.markdown("### 🔗 CVS PDP")
        st.markdown(f"[Open CVS Product Page]({row['retail_url']})", unsafe_allow_html=True)

        # ✅ QUICK QA INPUT
        cvs_count = st.number_input(
            f"Enter visible CVS thumbnail count for SKU {row['sku']}",
            min_value=0,
            max_value=15,
            value=0,
            key=row["sku"]
        )

        # ✅ RESULT
        if cvs_count == len(s_images):
            st.success("✅ Images Match")
        elif cvs_count < len(s_images):
            st.error(f"❌ Missing {len(s_images) - cvs_count} images")
        else:
            st.warning(f"⚠ Extra {cvs_count - len(s_images)} images")

        st.divider()
