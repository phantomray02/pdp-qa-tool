

import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from difflib import SequenceMatcher
from PIL import Image
from io import BytesIO
import json

st.title("PDP QA Tool ✅")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

# ✅ TOP DOWNLOAD BUTTON PLACEHOLDER
download_placeholder = st.empty()

# ✅ STORAGE FOR EXPORT DATA
export_rows = []


# =========================================
# ✅ IMAGE ORDER
# =========================================
IMAGE_ORDER = [
    "Online Optimized Image",
    "Flat Back_2D",
    "Flat Left_2D",
    "ATF I/O-Generic",
    "ATF 2-Generic",
    "ATF 3-Generic",
    "ATF 4-Generic",
    "ATF 5-Generic",
    "ATF 6-Generic"
]

# =========================================
# ✅ START PLAYWRIGHT ONCE (GLOBAL)
# =========================================
from playwright.sync_api import sync_playwright

import atexit

def cleanup():
    try:
        browser.close()
        p.stop()
    except:
        pass

atexit.register(cleanup)



p = sync_playwright().start()
browser = p.chromium.launch(headless=True)

import atexit
atexit.register(lambda: (browser.close(), p.stop()))

# =========================================
# ✅ CACHE
# =========================================
html_cache = {}
image_cache = {}

# =========================================
# ✅ GET HTML (OPTIMIZED)
# =========================================
def get_html(url):
    if url in html_cache:
        return html_cache[url]

    try:
        page = browser.new_page()
        page.goto(url, timeout=30000, wait_until="networkidle")
        
        # Scroll for lazy loading
        for _ in range(5):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        
        page.wait_for_selector("img", timeout=10000)
        page.wait_for_timeout(2000)
        
        html = page.content()
        page.close()
        
        html_cache[url] = html
        return html
        
    except Exception as e:
        print(f"Playwright failed for {url}: {e}")
        return ""
def get_soup(url):
    return BeautifulSoup(get_html(url), "html.parser")

# =========================================
# ✅ CLEAN TEXT
# =========================================
def clean_text(raw):
    if not raw:
        return ""

    raw = re.sub(r'<.*?>', '', raw)
    raw = raw.replace('"value":"', '')
    raw = raw.replace('"}', '')
    raw = raw.replace('{', '').replace('}', '')
    raw = raw.replace('"', '')

    raw = raw.lstrip(' ,.') 
    raw = raw.rstrip(' ,')
    raw = re.sub(r'\s+', ' ', raw)

    return raw.strip()

# =========================================
# ✅ SALSIFY IMAGE BUCKETS
# =========================================
def get_salsify_images(url):
    """
    Extract Salsify images by finding all image sections in page order.
    Focuses on finding the actual image file URLs.
    """
    html = get_html(url)
    images = []
    seen_hashes = set()  # Track by image hash, not full URL
    
    try:
        # Find all salsify image URLs in the HTML
        # Pattern captures the image hash at the end
        matches = re.findall(
            r'https://images\.salsify\.com/image/upload/[^"\']+/([a-z0-9]+\.jpg)',
            html
        )
        
        if not matches:
            print("No salsify images found")
            return images
        
        # Now find the property names that go with each image
        soup = BeautifulSoup(html, "html.parser")
        
        # Find all property name spans in order
        property_names = []
        for span in soup.find_all("span", {"data-testid": "property-name"}):
            name = span.get_text(strip=True).rstrip("-").strip()
            if name:
                property_names.append(name)
        
        # Match images with property names (in order)
        for idx, img_hash in enumerate(matches):
            if img_hash not in seen_hashes:
                seen_hashes.add(img_hash)
                
                # Get property name if available
                prop_name = property_names[idx] if idx < len(property_names) else f"Image {idx + 1}"
                
                # Reconstruct full URL
                full_url = f"https://images.salsify.com/image/upload/f_auto,c_limit,w_1080,q_auto/{img_hash}"
                
                images.append({
                    "type": prop_name,
                    "url": full_url
                })
        
        print(f"✅ Found {len(images)} unique images")
    
    except Exception as e:
        print(f"Error: {e}")
    
    return images
# =========================================
# ✅ CVS IMAGES
# =========================================
def get_cvs_images(url):
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

        size_match = re.search(r'Resize=\((\d+)', m)
        size = int(size_match.group(1)) if size_match else 0

        if name not in image_dict or size > image_dict[name]["size"]:
            image_dict[name] = {"url": base, "size": size}

    return [v["url"] for v in image_dict.values()]
# =========================================
# ✅ RENDER IMAGE COMPARISON BY SALSIFY PROPERTY
# =========================================
def render_image_comparison_by_property(s_images, r_images):
    st.markdown("## Image Comparison ✅")
    st.write(f"Salsify Images: {len(s_images)} | CVS Images: {len(r_images)}")

    max_len = max(len(s_images), len(r_images))

    for i in range(max_len):
        col1, col2 = st.columns(2)

        # LEFT = Salsify property bucket.
        if i < len(s_images):
            col1.markdown(f"**{s_images[i]['type']}**")
            col1.image(s_images[i]["url"])
        else:
            col1.write("❌ Missing in Salsify")

        # RIGHT = CVS image in order.
        if i < len(r_images):
            col2.markdown(f"**CVS Image {i+1}**")
            col2.image(r_images[i])
        else:
            col2.write("❌ Missing in CVS")
# =========================================
# ✅ FINAL FEATURE EXTRACTION (STRONG + GENERAL)
# =========================================
def extract_features_from_description(desc):

    if not desc:
        return []

    sentences = re.split(r'(?<=[.!?])\s+', desc)

    features = []

    for s in sentences:
        clean_s = clean_text(s)
        words = clean_s.split()

        wc = len(words)

        # ✅ general feature-like sentence rules
        if 8 <= wc <= 28 and clean_s and clean_s[0].isupper():
            features.append(clean_s)

        # ✅ CVS-style label extraction inside description
        label_matches = re.findall(r'([A-Z][A-Z\s\-]+:\s[^.]+)', clean_s)
        for lm in label_matches:
            features.append(lm)

    # ✅ remove duplicates
    seen = set()
    unique = []

    for f in features:
        if f not in seen and len(f) > 20:
            seen.add(f)
            unique.append(f)

    return unique[:5]
# =========================================
# ✅ CVS TEXT (FINAL WORKING VERSION)
# =========================================
import re
import json

def get_cvs_text(html):

    description = ""
    features = []

    if not html:
        return {"description": "", "features": []}

    try:
        # ✅ 1. CLEAN RAW HTML TEXT
        text = html.replace('\\"', '"')
        text = re.sub(r'\\n', ' ', text)

        # =========================================
        # ✅ DESCRIPTION (MULTIPLE FALLBACKS)
        # =========================================

        desc_patterns = [
            r'vendorDetailsParagraph":"(.*?)"',
            r'"description":"(.*?)"',
            r'"longDescription":"(.*?)"'
        ]

        for pattern in desc_patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                description = match.group(1)
                break

        # ✅ extra cleaning
        description = re.sub(r'\s+', ' ', description).strip()

        # =========================================
        # ✅ FEATURES (MUCH STRONGER LOGIC)
        # =========================================

        # ✅ Find ALL CAPS label-style features
        feature_matches = re.findall(
            r'([A-Z][A-Z\s\-]{5,}:\s[^"]+)',
            text
        )

        for f in feature_matches:
            clean_f = re.sub(r'\s+', ' ', f).strip()

            if len(clean_f) > 20:
                features.append(clean_f)

        # ✅ BONUS: extract from description if features missing
        if not features and description:
            features = extract_features_from_description(description)

        # ✅ Deduplicate
        features = list(dict.fromkeys(features))[:5]

    except Exception as e:
        print("CVS parse error:", e)

    return {
        "description": description,
        "features": features
    }

# =========================================
# ✅ SALSIFY TEXT CLEAN VERSION
# =========================================
def get_salsify_images(url):
    """
    Extract ALL Salsify product images by finding srcSet URLs in noscript tags.
    This works because Salsify uses lazy loading with noscript fallbacks.
    """
    html = get_html(url)
    images = []
    seen_urls = set()
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Find all asset containers (each image property has one)
        asset_containers = soup.find_all("div", {"class": "asset-list_images__2aKCB"})
        
        for container in asset_containers:
            
            # Get the property name from aria-label
            # e.g., "Online Optimized Image-" or "Flat Back_2D-"
            aria_label = container.get("aria-label", "")
            prop_name = aria_label.replace("-", "").strip()
            
            if not prop_name:
                continue
            
            # Look for the actual image URL in srcSet (inside noscript)
            noscript = container.find("noscript")
            if noscript:
                img_tag = noscript.find("img", {"data-testid": "salsify-image"})
                if img_tag:
                    srcset = img_tag.get("srcset", "")
                    
                    # srcSet format: "url1 1x, url2 2x"
                    # Extract the highest quality URL (the last one)
                    if srcset:
                        urls = [u.strip().split()[0] for u in srcset.split(",")]
                        img_url = urls[-1]  # Get the 2x version (highest quality)
                        
                        if img_url and "salsify" in img_url and img_url not in seen_urls:
                            seen_urls.add(img_url)
                            images.append({
                                "type": prop_name,
                                "url": img_url
                            })
    
    except Exception as e:
        print(f"Salsify image extraction error: {e}")
    
    return images
# =========================================
# ✅ FAST + ALL IMAGES COMPARISON
# =========================================
def load_image_with_white_bg(img_data):
    img = Image.open(BytesIO(img_data)).convert("RGBA")

    # ✅ create white background
    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))

    # ✅ paste using alpha channel (this removes transparency issue)
    if img.mode == "RGBA":
        white_bg.paste(img, mask=img.split()[3])
    else:
        white_bg.paste(img)

    # ✅ convert back to grayscale
    return white_bg.convert("L")


def compare_images_visually(s_url, r_url):
    try:
        # ✅ CACHE DOWNLOAD
        if s_url in image_cache:
            s_img_data = image_cache[s_url]
        else:
            s_img_data = requests.get(s_url, timeout=5).content
            image_cache[s_url] = s_img_data

        if r_url in image_cache:
            r_img_data = image_cache[r_url]
        else:
            r_img_data = requests.get(r_url, timeout=5).content
            image_cache[r_url] = r_img_data

        # =========================
        # ✅ FIX: NORMALIZE BACKGROUND
        # =========================

        from PIL import ImageFilter
        
        # ✅ normalize + blur to ignore alignment issues
        s_img = load_image_with_white_bg(s_img_data).resize((64, 64)).filter(ImageFilter.GaussianBlur(2))
        r_img = load_image_with_white_bg(r_img_data).resize((64, 64)).filter(ImageFilter.GaussianBlur(2))

        # =========================
        # ✅ PIXEL DIFFERENCE
        # =========================
        diff = sum(
            abs(a - b)
            for a, b in zip(s_img.getdata(), r_img.getdata())
        ) / (64 * 64)

        # =========================
        # ✅ IMPROVED SCORING
        # =========================
        if diff < 5:
            return 100
        elif diff < 15:
            return 90
        elif diff < 30:
            return 75
        elif diff < 45:
            return 60
        elif diff < 60:
            return 45
        elif diff < 80:
            return 30
        else:
            return 15

    except:
        return 0

def match_images_visual(s_images, r_images):
    results = []
    used_r = set()

    for s in s_images:
        best_score = 0
        best_r = None
        best_idx = None

        for i, r in enumerate(r_images):
            if i in used_r:
                continue

            score = compare_images_visually(s["url"], r)

            if score > best_score:
                best_score = score
                best_r = r
                best_idx = i

        if best_score >= 40:
            results.append((s["url"], best_r, best_score))
            used_r.add(best_idx)
        else:
            results.append((s["url"], "", best_score))

    return results
    # ✅ find unmatched CVS images
        unmatched_r = [
            r for i, r in enumerate(r_images)
            if all(r != match[1] for match in image_matches)
        ]
        
        for r in unmatched_r:
            st.error("🚨 Image exists on CVS but not matched to Salsify")
            st.image(r)
# =========================================
# ✅ TEXT NORMALIZATION
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# =========================================
# ✅ DESCRIPTION MATCHING (FIXES 0%)
# =========================================
from difflib import SequenceMatcher

def keyword_score(a, b):

    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    # ✅ overall similarity
    
    ratio = max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a[:200], b[:200]).ratio()
    )


    # ✅ word overlap bonus
    a_words = set(a.split())
    b_words = set(b.split())

    overlap = len(a_words & b_words)
    total = len(a_words | b_words)

    word_score = (overlap / total) if total else 0

    # ✅ combine both
    final = (ratio * 0.6) + (word_score * 0.4)

    return int(final * 100)
# =========================================
# ✅ FEATURE MATCHING
# =========================================
def match_features(s_features, r_features):
    results = []

    for s in s_features:
        best_match = ""
        best_score = 0

        for r in r_features:
            # ✅ use fuzzy match instead of word overlap
            score = SequenceMatcher(
                None,
                normalize_text(s),
                normalize_text(r)
            ).ratio()

            if score > best_score:
                best_score = score
                best_match = r

        # ✅ lower threshold (CRITICAL CHANGE)
        if best_score >= 0.25:
            results.append((s, best_match, int(best_score * 100)))
        else:
            results.append((s, "❌ Missing", int(best_score * 100)))

    return results

# =========================================
# ✅ MAIN
# =========================================
if uploaded_file:

    df = pd.read_csv(uploaded_file)
    export_rows = []
    summary_rows = []

    for _, row in df.iterrows():

        st.subheader(f"SKU: {row['sku']}")

        full_html = get_html(row["retail_url"])

        try:
            # =========================
            # ✅ IMAGE BUCKETS
            # =========================
            s_images = get_salsify_images(row["salsify_url"])
            s_images = [img for img in s_images if img.get("url")]

            r_images = get_cvs_images(row["retail_url"])

            # =========================
            # ✅ DEBUG VIEW
            # =========================
            with st.expander("🧺 Salsify Images", expanded=True):
                st.write("Total Salsify images:", len(s_images))
                cols = st.columns(3)

                for i, img in enumerate(s_images):
                    cols[i % 3].image(img["url"], caption=img["type"])

            # =========================
            # ✅ PROPERTY-BASED COMPARISON VIEW
            # =========================
            render_image_comparison_by_property(s_images, r_images)

            # =========================
            # ✅ OPTIONAL VISUAL MATCH SCORE
            # =========================
            image_matches = match_images_visual(s_images, r_images)
            
            # =========================
            # ✅ IMAGE SCORE
            # =========================

            img_scores = [score for _, _, score in image_matches if score > 0]
            avg_img_score = int(sum(img_scores) / len(img_scores)) if img_scores else 0

            st.write(f"✅ Image Match: {avg_img_score}%")


            # =========================
            # ✅ TEXT EXTRACTION
            # =========================
            s_text = get_salsify_text(row["salsify_url"])
            r_text = get_cvs_text(full_html)

            st.write("✅ CVS DESCRIPTION:", r_text.get("description", ""))
            st.write("✅ CVS FEATURES:", r_text.get("features", []))

            # =========================
            # ✅ TITLE
            # =========================
            st.markdown("## Title")

            soup = get_soup(row["salsify_url"])

            s_title = ""
            for row_html in soup.find_all("tr"):
                label = row_html.get_text(" ", strip=True).lower()
                if "product title" in label:
                    span = row_html.find("span", {"data-testid": "property-content"})
                    if span:
                        s_title = span.get_text(strip=True)
                        break

            r_title_match = re.search(r'"productName":"(.*?)"', full_html)
            r_title = r_title_match.group(1) if r_title_match else ""

            r_title = re.sub(r'\s*-\s*CVS.*$', '', r_title).strip()

            c1, c2 = st.columns(2)
            c1.write(s_title)
            c2.write(r_title)

            title_score = int(
                SequenceMatcher(None, s_title.lower(), r_title.lower()).ratio() * 100
            )

            st.write(f"✅ Title Match: {title_score}%")

            # =========================
            # ✅ DESCRIPTION
            # =========================
            st.markdown("## Description")

            c1, c2 = st.columns(2)
            c1.write(s_text.get("description", ""))
            c2.write(r_text.get("description", ""))

            desc_score = keyword_score(
                s_text.get("description", ""),
                r_text.get("description", "")
            )

            st.write(f"✅ Description Match: {desc_score}%")

            # =========================
            # ✅ FEATURE SCORE (TEMP)
            # =========================
            avg_feature_score = 0

            # =========================
            # ✅ OVERALL SCORE
            # =========================
            overall_score = int(
                (title_score + desc_score + avg_feature_score + avg_img_score) / 4
            )

            summary_rows.append({
                "SKU": row["sku"],
                "Title %": title_score,
                "Description %": desc_score,
                "Image %": avg_img_score,
                "Overall %": overall_score
            })

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
            continue
# =========================================
# ✅ EXPORT
# =========================================
if summary_rows:

    summary_df = pd.DataFrame(summary_rows)
    detail_df = pd.DataFrame(export_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        detail_df.to_excel(writer, index=False, sheet_name="Details")

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
