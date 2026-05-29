
def get_cvs_images(url):
    try:
        soup = get_soup(url)

        thumbnails = []

        # ✅ TARGET TAB CONTAINER
        container = soup.find("div", {"role": "tablist"})

        if not container:
            return []

        # ✅ FIND ALL BUTTONS (THIS FIXES SCROLL ISSUE)
        buttons = container.find_all("button")

        for btn in buttons:

            img = btn.find("img")
            if not img:
                continue

            src = img.get("src") or ""

            # ✅ FILTER REAL PRODUCT THUMBNAILS
            if "high_res" in src:

                if src.startswith("/"):
                    src = "https://www.cvs.com" + src

                thumbnails.append(src)

        # ✅ REMOVE DUPLICATES
        return list(dict.fromkeys(thumbnails))

    except:
        return []
