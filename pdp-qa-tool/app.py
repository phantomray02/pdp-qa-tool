import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO
from playwright.sync_api import sync_playwright
import atexit

st.write("🚀 VERSION ORDERED QA FIX")
st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
download_placeholder = st.empty()

# =========================================
# ✅ START PLAYWRIGHT
# =========================================
p = sync_playwright().start()
browser = p.chromium.launch(headless=True)
atexit.register(lambda: (browser.close(), p.stop()))

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}

# =========================================
# ✅ GET HTML
# =========================================
def get_html(url):
    if not url:
        return ""

    if url in html_cache:
        return html_cache[url]

    try:
        page = browser.new_page()
        page.goto(url, timeout=30000, wait_until="networkidle")

        for _ in range(10):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)

        page.wait_for_timeout(3000)

        html = page.content()
        page.close()

        html_cache[url] = html
        return html

    except Exception as e:
        print("HTML error:", e)
        return ""

def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ LOAD IMAGE
# =========================================
def load_image(url):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            return Image.open(BytesIO(r.content))
    except:
        return None
    return None

# =========================================
# ✅ FIXED SALSIFY EXTRACTION + ORDER
# =========================================
def get_salsify_images(url):
    html = get_html(url)
    soup = BeautifulSoup(html, "html.parser")

    raw_images = []

    # ✅ JSON (handles escaped URLs)
    json_matches = re.findall(r'https?:\\\\?/\\\\?/images\\\\?.salsify\\\\?.com[^"]+', html)
    for m in json_matches:
        clean = m.replace("\\/", "/").split("?")[0]
        raw_images.append(clean)

    # ✅ DOM fallback
    for img in soup.find_all("img"):
        src = img.get("src", "")
        srcset = img.get("srcset", "")

        if "salsify" in src:
            raw_images.append(src.split("?")[0])

        if "salsify" in srcset:
            urls = [u.split()[0] for u in srcset.split(",") if u]
            if urls:
                raw_images.append(urls[-1].split("?")[0])

    # ✅ dedupe (keep order)
    seen = set()
    images = []
    for url in raw_images:
        if url not in seen:
            seen.add(url)
            images.append(url)

    # ✅ FORCE ORDER (best-effort heuristic)
    ordered = []

    # try to prioritize front image first
    for url in images:
        if "front" in url.lower():
            ordered.append(url)

    for url in images:
        if url not in ordered:
            ordered.append(url)

    return ordered

# =========================================
# ✅ CVS IMAGES (ORDER ALREADY CORRECT)
# =========================================
def get_cvs_images(url):
    html = get_html(url)

    matches = re.findall(
        r'/bizcontent/merchandising/productimages/high_res/[^\s"]+\.jpg',
        html
    )

    images = ["https://www.cvs.com" + m for m in matches]

    return images

# =========================================
# ✅ NORMALIZE TEXT
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def keyword_score(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    ratio = SequenceMatcher(None, a, b).ratio()

    a_words = set(a.split())
    b_words = set(b.split())

    overlap = len(a_words & b_words)
    total = len(a_words | b_words)

    word_score = (overlap / total) if total else 0

    return int(((ratio * 0.6) + (word_score * 0.4)) * 100)

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        full_html = get_html(row["retail_url"])

        try:
            # ✅ GET IMAGES
            s_images = get_salsify_images(row["salsify_url"])
            r_images = get_cvs_images(row["retail_url"])

            st.write(f"✅ Salsify images: {len(s_images)}")

            # =========================================
            # ✅ SIDE-BY-SIDE ORDERED VIEW
            # =========================================
            st.markdown("## 🔍 Ordered Image Comparison")

            max_len = max(len(s_images), len(r_images))

            for i in range(max_len):
                col1, col2 = st.columns(2)

                # ===== SALSIFY =====
                if i < len(s_images):
                    col1.markdown(f"**Salsify #{i+1}**")
                    img_obj = load_image(s_images[i])

                    if img_obj:
                        col1.image(img_obj, use_container_width=True)
                    else:
                        col1.write("❌ Failed")
                        col1.write(s_images[i])
                else:
                    col1.write("❌ Missing Salsify")

                # ===== CVS =====
                if i < len(r_images):
                    col2.markdown(f"**CVS #{i+1}**")
                    img_obj = load_image(r_images[i])

                    if img_obj:
                        col2.image(img_obj, use_container_width=True)
                else:
                    col2.write("❌ Missing CVS")

            # ✅ SIMPLE MATCH SCORE
            img_score = int((min(len(s_images), len(r_images)) / max(len(s_images), len(r_images), 1)) * 100)
            st.write(f"✅ Image Match: {img_score}%")

            summary_rows.append({
                "SKU": row["sku"],
                "Image Count Salsify": len(s_images),
                "Image Count CVS": len(r_images),
                "Image %": img_score
            })

        except Exception as e:
            st.error(f"❌ Error: {e}")

# =========================================
# ✅ EXPORT
# =========================================
if 'summary_rows' in locals() and summary_rows:
    summary_df = pd.DataFrame(summary_rows)

    with pd.ExcelWriter("pdp_qa_results.xlsx", engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False)

    with open("pdp_qa_results.xlsx", "rb") as f:
        download_placeholder.download_button(
            "📥 Download Excel",
            f,
            "pdp_qa_results.xlsx"
        )
