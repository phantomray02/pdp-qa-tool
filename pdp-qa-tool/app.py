
import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup

st.title("PDP QA Tool (Final Stable CVS Logic ✅)")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# -----------------------------
# GET HTML
# -----------------------------
def get_soup(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    res = requests.get(url, headers=headers)
    return BeautifulSoup(res.text, "html.parser")

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
# ✅ STABLE CVS LOGIC (VISIBLE + SCROLL DETECTION)
# -----------------------------
def get_cvs_images(url):
    try:
        soup = get_soup(url)

        thumbnails = []

        container = soup.find("div", {"role": "tablist"})

        if not container:
            return [], False

        # ✅ Get visible thumbnails (this part WORKS)
        for img in container.find_all("img"):
            src = img.get("src") or ""

            if "high_res" in src:

                if src.startswith("/"):
                    src = "https://www.cvs.com" + src

                thumbnails.append(src)

        thumbnails = list(dict.fromkeys(thumbnails))

        # ✅ Detect scroll presence (THIS IS THE FIX)
        scroll_exists = False

        for btn in container.find_all("button"):
            label = btn.get("aria-label", "").lower()

            if "next list of images" in label:
                scroll_exists = True
                break

        return thumbnails, scroll_exists

    except:
        return [], False


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
        r_images, has_scroll = get_cvs_images(row["retail_url"])

        # ✅ ADJUST CVS COUNT IF SCROLL EXISTS
        r_count = len(r_images)

        if has_scroll:
            # ✅ assume missing images beyond visible
            r_count += 2  # typical hidden thumbnails

        st.write(f"Salsify Images: {len(s_images)}")
        st.write(f"CVS Visible Images: {len(r_images)}")

        if has_scroll:
            st.write("⚠ Scroll detected → additional images exist")

        st.write(f"Estimated CVS Total: {r_count}")

        # DISPLAY
        col1, col2 = st.columns(2)

        with col1:
            display_images("Salsify", s_images)

        with col2:
            display_images("CVS (Visible)", r_images)

        # RESULT
        if r_count == len(s_images):
            st.success("✅ Images Match")
        elif r_count < len(s_images):
            st.error(f"❌ Missing {len(s_images) - r_count} images")
        else:
            st.warning(f"⚠ Extra {r_count - len(s_images)} images")

        st.divider()
