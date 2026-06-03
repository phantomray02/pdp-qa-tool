import requests
from bs4 import BeautifulSoup
import re
from playwright.sync_api import sync_playwright
import json

# URLs to compare
url_working = "https://sites.salsify.com/c59eb481-0fb4-407b-ac3d-710e4b28a712/83f32e36-ef43-47a1-92e5-8c9a07b01e56/product/01247-06/U-by-Kotex-Clean-andamp-Secure-Wrapped-Panty-Liners-Light-Absorbency-Long-Length-16-Count/"
url_broken = "https://sites.salsify.com/c59eb481-0fb4-407b-ac3d-710e4b28a712/83f32e36-ef43-47a1-92e5-8c9a07b01e56/product/19304-13/Poise-Daily-Liners-Incontinence-Panty-Liners-2-Drop-Very-Light-Absorbency-Long-Length-44-Count-of-Pantiliners/"

print("🚀 Starting Salsify page comparison...\n")

def get_html_playwright(url, name):
    """Get HTML using Playwright"""
    print(f"⏳ Loading {name}...")
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=30000, wait_until="networkidle")
        
        print(f"   Scrolling for lazy load...")
        for _ in range(5):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        
        html = page.content()
        page.close()
        browser.close()
        p.stop()
        print(f"   ✅ Loaded ({len(html)} bytes)\n")
        return html
    except Exception as e:
        print(f"   ❌ Error: {e}\n")
        return ""

def analyze_page(url, name, filename):
    """Analyze a Salsify page"""
    print(f"\n{'='*100}")
    print(f"📊 ANALYZING: {name}")
    print(f"{'='*100}\n")
    
    html = get_html_playwright(url, name)
    if not html:
        return None
    
    soup = BeautifulSoup(html, "html.parser")
    
    # Find asset containers
    asset_containers = soup.find_all("div", {"class": "asset-list_images__2aKCB"})
    print(f"✅ Asset containers found: {len(asset_containers)}\n")
    
    container_details = []
    
    if len(asset_containers) > 0:
        print(f"🔍 CONTAINER DETAILS:\n")
        for idx, container in enumerate(asset_containers):
            aria_label = container.get("aria-label", "N/A")
            noscript = container.find("noscript")
            img_in_noscript = noscript.find("img") if noscript else None
            
            print(f"  Container {idx + 1}:")
            print(f"    ├─ aria-label: {aria_label}")
            print(f"    ├─ has noscript: {noscript is not None}")
            print(f"    ├─ has img in noscript: {img_in_noscript is not None}")
            
            img_url = None
            srcset_exists = False
            src_exists = False
            
            if img_in_noscript:
                srcset = img_in_noscript.get("srcset", "")
                src = img_in_noscript.get("src", "")
                srcset_exists = len(srcset) > 0
                src_exists = len(src) > 0
                
                print(f"    ├─ srcset exists: {srcset_exists}")
                print(f"    ├─ src exists: {src_exists}")
                
                if srcset:
                    urls = srcset.split(",")
                    img_url = urls[-1].strip().split()[0]  # Get 2x version
                    print(f"    └─ image URL: {img_url[:90]}...")
                elif src:
                    img_url = src
                    print(f"    └─ image URL (src): {img_url[:90]}...")
                else:
                    print(f"    └─ image URL: NONE")
            else:
                print(f"    └─ image URL: NONE (no img tag)")
            
            container_details.append({
                "index": idx + 1,
                "label": aria_label,
                "has_noscript": noscript is not None,
                "has_img": img_in_noscript is not None,
                "has_srcset": srcset_exists,
                "has_src": src_exists,
                "url": img_url
            })
            
            if idx < len(asset_containers) - 1:
                print()
    
    # All img tags with salsify
    all_imgs = soup.find_all("img")
    salsify_imgs = [img for img in all_imgs if "salsify" in (img.get("src", "") + img.get("srcset", ""))]
    
    print(f"\n{'─'*100}")
    print(f"📈 IMAGE STATISTICS:")
    print(f"{'─'*100}\n")
    print(f"  Total <img> tags: {len(all_imgs)}")
    print(f"  Salsify <img> tags: {len(salsify_imgs)}")
    
    # Save HTML for inspection
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n💾 Full HTML saved to: {filename}")
    
    return {
        "containers": len(asset_containers),
        "total_imgs": len(all_imgs),
        "salsify_imgs": len(salsify_imgs),
        "details": container_details
    }

# Analyze both
print("="*100)
print("🔄 COMPARISON: WORKING vs BROKEN SALSIFY PAGES")
print("="*100)

working = analyze_page(url_working, "WORKING - U by Kotex", "salsify_working_u_by_kotex.html")
broken = analyze_page(url_broken, "BROKEN - Poise", "salsify_broken_poise.html")

# Comparison
print(f"\n\n{'='*100}")
print(f"🎯 SIDE-BY-SIDE COMPARISON")
print(f"{'='*100}\n")

print(f"{'Metric':<40} {'Working':<30} {'Broken':<30}")
print(f"{'-'*40} {'-'*30} {'-'*30}")
print(f"{'Asset containers':<40} {working['containers']:<30} {broken['containers']:<30}")
print(f"{'Total img tags':<40} {working['total_imgs']:<30} {broken['total_imgs']:<30}")
print(f"{'Salsify img tags':<40} {working['salsify_imgs']:<30} {broken['salsify_imgs']:<30}")

# Find differences
print(f"\n\n{'='*100}")
print(f"🔍 DIFFERENCES FOUND:")
print(f"{'='*100}\n")

if working['containers'] != broken['containers']:
    print(f"⚠️  Different number of containers: {working['containers']} vs {broken['containers']}\n")

if working['salsify_imgs'] != broken['salsify_imgs']:
    print(f"⚠️  Different number of salsify images: {working['salsify_imgs']} vs {broken['salsify_imgs']}\n")

# Check for missing containers
working_labels = {d['label'] for d in working['details']}
broken_labels = {d['label'] for d in broken['details']}

missing_in_broken = working_labels - broken_labels
extra_in_broken = broken_labels - working_labels

if missing_in_broken:
    print(f"❌ Missing in BROKEN page:\n")
    for label in missing_in_broken:
        print(f"   - {label}")
    print()

if extra_in_broken:
    print(f"✨ Extra in BROKEN page:\n")
    for label in extra_in_broken:
        print(f"   - {label}")
    print()

# Check for structural differences
print(f"\nContainer structure differences:\n")
for i in range(max(len(working['details']), len(broken['details']))):
    w = working['details'][i] if i < len(working['details']) else None
    b = broken['details'][i] if i < len(broken['details']) else None
    
    if w and b:
        if w['label'] != b['label']:
            print(f"   Position {i+1}: '{w['label']}' (working) vs '{b['label']}' (broken)")
        if w['has_noscript'] != b['has_noscript']:
            print(f"   Position {i+1} noscript: {w['has_noscript']} vs {b['has_noscript']}")
        if w['has_img'] != b['has_img']:
            print(f"   Position {i+1} img tag: {w['has_img']} vs {b['has_img']}")

print(f"\n{'='*100}")
print(f"✅ Analysis complete! Check the HTML files for detailed inspection.")
print(f"{'='*100}\n")

# Now create the robust function
print(f"\n\n{'='*100}")
print(f"🔧 CREATING ROBUST get_salsify_images() FUNCTION")
print(f"{'='*100}\n")

robust_function = '''
def get_salsify_images(url):
    """
    Extract ALL Salsify product images - handles both page structures.
    
    This function is robust and works for:
    - Standard Salsify product pages
    - Pages with missing image properties
    - Pages with alternative structures
    
    Returns images in order they appear on the page.
    """
    html = get_html(url)
    images = []
    seen_urls = set()
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Method 1: Find all asset-list_images containers (primary method)
        asset_containers = soup.find_all("div", {"class": "asset-list_images__2aKCB"})
        
        print(f"🔍 Found {len(asset_containers)} asset containers")
        
        for idx, container in enumerate(asset_containers):
            # Get property name from aria-label
            aria_label = container.get("aria-label", "").strip()
            prop_name = aria_label.replace("-", "").strip() if aria_label else f"Image {idx + 1}"
            
            if not prop_name or prop_name == "":
                continue
            
            # Try to find image URL in noscript (most reliable)
            noscript = container.find("noscript")
            img_url = None
            
            if noscript:
                # Look for img tag with srcset (highest quality)
                img_tag = noscript.find("img")
                if img_tag:
                    srcset = img_tag.get("srcset", "")
                    src = img_tag.get("src", "")
                    
                    # Prefer srcset (has multiple resolutions)
                    if srcset and "salsify" in srcset:
                        # srcSet format: "url1 1x, url2 2x, ..."
                        # Extract all URLs and take the last one (highest quality)
                        urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
                        img_url = urls[-1] if urls else None
                    
                    # Fallback to src
                    elif src and "salsify" in src:
                        img_url = src
            
            # If no image found in noscript, check main img tags in container
            if not img_url:
                main_img = container.find("img", {"data-testid": "salsify-image"})
                if main_img:
                    srcset = main_img.get("srcset", "")
                    src = main_img.get("src", "")
                    
                    if srcset and "salsify" in srcset:
                        urls = [u.strip().split()[0] for u in srcset.split(",") if u.strip()]
                        img_url = urls[-1] if urls else None
                    elif src and "salsify" in src:
                        img_url = src
            
            # Add valid, non-duplicate images
            if img_url and "salsify" in img_url and img_url not in seen_urls:
                seen_urls.add(img_url)
                images.append({
                    "type": prop_name,
                    "url": img_url
                })
                print(f"  ✅ {prop_name}")
        
        print(f"✅ Extracted {len(images)} images total\\n")
    
    except Exception as e:
        print(f"❌ Error extracting images: {e}")
        import traceback
        traceback.print_exc()
    
    return images
'''

print("📋 Robust function created:")
print(robust_function)
print(f"\n✨ This function will:")
print(f"   ✅ Find all asset containers on the page")
print(f"   ✅ Extract property names from aria-label")
print(f"   ✅ Handle both noscript and main img tags")
print(f"   ✅ Prefer srcset over src (higher quality)")
print(f"   ✅ Skip empty/missing images")
print(f"   ✅ Prevent duplicates")
print(f"   ✅ Print debug info to console")
