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

download_placeholder = st.empty()
export_rows = []

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
# ✅ SALSIFY IMAGES - DEBUG VERSION
# =========================================
def get_salsify_images(url):
    """
    Extract Salsify images from rendered HTML.
    """
    html = get_html(url)
    images = []
    seen_urls = set()
    
    try:
        # ✅ SAVE DEBUG HTML
        with open("salsify_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"📄 HTML saved to salsify_debug.html ({len(html)} chars)")
        
        soup = BeautifulSoup(html, "html.parser")
        
        # ✅ DEBUG: Show what we're looking for
        print("\n🔍 Looking for images...\n")
        
        # Try method 1: asset-list_images containers
        asset_containers = soup.find_all("div", {"class": "asset-list_images__2aKCB"})
        print(f"Method 1 - asset-list_images__2aKCB: Found {len(asset_containers)} containers")
        
        if len(asset_containers) > 0:
            for idx, container in enumerate(asset_containers[:3]):  # Show first 3
                print(f"  Container {idx + 1}:")
                print(f"    aria-label: {container.get('aria-label', 'N/A')}")
                noscript = container.find("noscript")
                print(f"    Has noscript: {noscript is not None}")
                if noscript:
                    img = noscript.find("img")
                    print(f"    Has img: {img is not None}")
                    if img:
                        srcset = img.get("srcset", "")
                        print(f"    srcset length: {len(srcset)}")
                        if srcset:
                            first_url = srcset.split(",")[0].strip().split()[0]
                            print(f"    First URL: {first_url[:80]}...")
        
        # Try method 2: Find all img tags with salsify URLs
        print(f"\n\nMethod 2 - All img tags with 'salsify' in src/srcset:")
        img_tags = soup.find_all("img")
        print(f"Total img tags: {len(img_tags)}")
        
        salsify_count = 0
        for img in img_tags:
            src = img.get("src", "")
            srcset = img.get("srcset", "")
            if "salsify" in src or "salsify" in srcset:
                salsify_count += 1
                if salsify_count <= 5:  # Show first 5
                    print(f"  Img {salsify_count}:")
                    if srcset:
                        first_url = srcset.split(",")[0].strip().split()[0]
                        print(f"    srcset: {first_url[:80]}...")
                    if src and src.startswith("http"):
                        print(f"    src: {src[:80]}...")
        
        print(f"Total salsify imgs: {salsify_count}")
        
        # Now actually extract
        print(f"\n\n✅ EXTRACTING IMAGES:\n")
        
        for idx, container in enumerate(asset_containers):
            aria_label = container.get("aria-label", "").strip()
            prop_name = aria_label.replace("-", "").strip() if aria_label else f"Image {idx + 1}"
            
            noscript = container.find("noscript")
            if not noscript:
                continue
            
            img_tag = noscript.find("img")
            if not img_tag:
                continue
            
            srcset = img_tag.get("srcset", "")
            src = img_tag.get("src", "")
            
            img_url = None
            
            if srcset and "salsify" in srcset:
                urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
                img_url = urls[-1] if urls else None
            elif src and "salsify" in src:
                img_url = src
            
            if img_url and img_url not in seen_urls:
                seen_urls.add(img_url)
                images.append({
                    "type": prop_name,
                    "url": img_url
                })
                print(f"✅ {prop_name}")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\n📊 Total images: {len(images)}\n")
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
# ✅ RENDER IMAGE COMPARISON
# =========================================
def render_image_comparison_by_property(s_images, r_images):
    st.markdown("## Image Comparison ✅")
    st.write(f"Salsify Images: {len(s_images)} | CVS Images: {len(r_images)}")

    max_len = max(len(s_images), len(r_images))

    for i in range(max_len):
        col1, col2 = st.columns(2)

        if i < len(s_images):
            col1.markdown(f"**{s_images[i]['type']}**")
            col1.image(s_images[i]["url"])
        else:
            col1.write("❌ Missing in Salsify")

        if i < len(r_images):
            col2.markdown(f"**CVS Image {i+1}**")
            col2.image(r_images[i])
        else:
            col2.write("❌ Missing in CVS")

# =========================================
# ✅ SALSIFY TEXT
# =========================================
def get_salsify_text(url):
    """Extract title, description, and features from Salsify."""
    try:
        soup = get_soup(url)
        description = ""
        features = []
        
        rows = soup.find_all("tr")
        
        for row in rows:
            label = row.get_text(" ", strip=True).lower()
            
            if "general description" in label:
                content = row.find("span", {"data-testid": "property-content"})
                if content:
                    description = clean_text(content.get_text(" ", strip=True))
            
            if re.search(r'general feature \d+', label, re.IGNORECASE):
                clean = re.sub(r'general feature \d+\s*', '', label, flags=re.IGNORECASE)
                if clean.strip():
                    features.append(clean.strip())
        
        return {
            "description": description,
            "features": features[:5]
        }
    except:
        return {"description": "", "features": []}

# =========================================
# ✅ CVS TEXT
# =========================================
def get_cvs_text(html):
    """Extract title, description, and features from CVS."""
    description = ""
    features = []

    if not html:
        return {"description": "", "features": []}

    try:
        text = html.replace('\\"', '"')
        text = re.sub(r'\\n', ' ', text)

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

        description = re.sub(r'\s+', ' ', description).strip()

        feature_matches = re.findall(
            r'([A-Z][A-Z\s\-]{5,}:\s[^"]+)',
            text
        )

        for f in feature_matches:
            clean_f = re.sub(r'\s+', ' ', f).strip()
            if len(clean_f) > 20:
                features.append(clean_f)

        features = list(dict.fromkeys(features))[:5]

    except Exception as e:
        print("CVS parse error:", e)

    return {
        "description": description,
        "features": features
    }

# =========================================
# ✅ TEXT NORMALIZATION
# =========================================
def normalize_text(text):
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# =========================================
# ✅ KEYWORD SCORE
# =========================================
def keyword_score(a, b):
    a = normalize_text(a)
    b = normalize_text(b)

    if not a or not b:
        return 0

    ratio = max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a[:200], b[:200]).ratio()
    )

    a_words = set(a.split())
    b_words = set(b.split())

    overlap = len(a_words & b_words)
    total = len(a_words | b_words)

    word_score = (overlap / total) if total else 0

    final = (ratio * 0.6) + (word_score * 0.4)

    return int(final * 100)

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
            # Get images
            s_images = get_salsify_images(row["salsify_url"])
            s_images = [img for img in s_images if img.get("url")]

            r_images = get_cvs_images(row["retail_url"])

            # Debug view
            with st.expander("🧺 Salsify Images", expanded=True):
                st.write("Total Salsify images:", len(s_images))
                cols = st.columns(3)

                for i, img in enumerate(s_images):
                    cols[i % 3].image(img["url"], caption=img["type"], use_container_width=True)

            # Comparison
            render_image_comparison_by_property(s_images, r_images)

            # Image score
            img_score = 100 if len(s_images) == len(r_images) else int((min(len(s_images), len(r_images)) / max(len(s_images), len(r_images), 1)) * 100)
            st.write(f"✅ Image Match: {img_score}%")

            # Get text
            s_text = get_salsify_text(row["salsify_url"])
            r_text = get_cvs_text(full_html)

            # Title
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
            c1.write(f"Salsify: {s_title}")
            c2.write(f"CVS: {r_title}")

            title_score = int(SequenceMatcher(None, s_title.lower(), r_title.lower()).ratio() * 100)
            st.write(f"✅ Title Match: {title_score}%")

            # Description
            st.markdown("## Description")
            c1, c2 = st.columns(2)
            c1.write(s_text.get("description", "")[:200])
            c2.write(r_text.get("description", "")[:200])

            desc_score = keyword_score(s_text.get("description", ""), r_text.get("description", ""))
            st.write(f"✅ Description Match: {desc_score}%")

            # Summary
            overall_score = int((title_score + desc_score + img_score) / 3)

            summary_rows.append({
                "SKU": row["sku"],
                "Title %": title_score,
                "Description %": desc_score,
                "Image %": img_score,
                "Overall %": overall_score
            })

        except Exception as e:
            st.error(f"❌ Error on SKU {row['sku']}: {e}")
            import traceback
            traceback.print_exc()
            continue

# =========================================
# ✅ EXPORT
# =========================================
if summary_rows:
    summary_df = pd.DataFrame(summary_rows)

    file_name = "pdp_qa_results.xlsx"

    with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")

    with open(file_name, "rb") as f:
        download_placeholder.download_button(
            label="📥 Download Excel Report",
            data=f,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
