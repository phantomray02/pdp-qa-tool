
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Free + Reliable)")

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
# DISPLAY
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

        # ✅ SHOW Salsify
        st.markdown("### ✅ Salsify Images (Source of Truth)")
        st.write(f"Expected Images: {len(s_images)}")
        display_images(s_images)

        # ✅ BUTTON → OPEN CVS
        st.markdown("### 🔗 CVS PDP")
        st.link_button("Open CVS Product Page", row["retail_url"])

        # ✅ QUICK INPUT CHECK
        cvs_count = st.number_input(
            f"Enter visible thumbnail count",
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
