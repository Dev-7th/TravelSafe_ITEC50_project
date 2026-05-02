import pdfplumber
import requests
import re
import json
import os
import tempfile
from io import BytesIO
from doe_RegionData_fetcher import get_all_latest_reports 

# 1. Added missing brands found in the PDFs
BRANDS = ["PETRON", "SHELL", "CALTEX", "PHOENIX", "UNIOIL", "SEAOIL", "PTT", "FLYING V", "INDEPENDENT", "OVERALL RANGE"]
FUELS = ["RON 100", "RON 97", "RON 95", "RON 91", "DIESEL", "DIESEL PLUS", "KEROSENE"]
_PADDLE_OCR = None

def clean_price(price_str):
    """Extracts min/max prices. Allows 0.00 as a minimum if a valid max exists."""
    if not price_str:
        return None, None
        
    raw_str = str(price_str).upper().replace(',', '')
    
    # 1. Only exclude known 'junk' strings. 
    # Removed "0.00" and "0.0" from this list so they don't trigger an early exit.
    if any(x in raw_str for x in ["LFRO", "#N/A", "NONE", "EMPTY"]):
        return None, None
    
    # 2. Extract all numbers with decimals
    prices = re.findall(r'\d+\.\d+', raw_str)
    
    # 3. Convert to floats. We allow 0.0 here (>= 0).
    floats = [float(p) for p in prices]
    
    if not floats:
        return None, None
        
    # 4. Logic check: If all prices found are 0.0, it's effectively empty data.
    if max(floats) == 0:
        return None, None

    return min(floats), max(floats)

def get_column_mapping(table_rows):
    """Scans the top rows to map Brand columns. Ignores Location/Product."""
    mapping = {}
    for row in table_rows[:6]: # Increased buffer to 6 rows to be safe
        clean_row = [str(c).upper().replace('\n', ' ') if c else "" for c in row]

        for idx, cell in enumerate(clean_row):
            if "INDEPENDENT" in cell and "OVERALL RANGE" in cell:
                mapping.setdefault("INDEPENDENT", idx)
                if idx + 1 < len(clean_row):
                    mapping.setdefault("OVERALL RANGE", idx + 1)
        
        for brand in BRANDS:
            if brand not in mapping:
                for idx, cell in enumerate(clean_row):
                    if brand in cell:
                        mapping[brand] = idx
                        
    return mapping


def _blank_brand_fields():
    fields = {}
    for brand in BRANDS:
        key_base = brand.lower().replace(" ", "_")
        fields[f"{key_base}_min"] = None
        fields[f"{key_base}_max"] = None
    return fields


def _build_base_entry(category_name, province, city, product):
    entry = {
        "category": category_name,
        "province": province,
        "city": city,
        "product": product,
    }
    entry.update(_blank_brand_fields())
    return entry


def _set_brand_prices(entry, brand, min_price, max_price):
    key_base = brand.lower().replace(" ", "_")
    entry[f"{key_base}_min"] = min_price
    entry[f"{key_base}_max"] = max_price


def _extract_float_prices(value):
    if not value:
        return []
    raw = str(value).upper().replace(",", "")
    if any(x in raw for x in ["LFRO", "#N/A", "NONE", "EMPTY"]):
        return []
    return [float(p) for p in re.findall(r"\d+\.\d+", raw)]

def _row_has_any_brand_prices(clean_row, col_map, exclude_brand=None):
    """True if any OTHER brand cell on this row has numeric prices."""
    for brand, idx in col_map.items():
        # OVERALL RANGE often has values even when brand prices are merged elsewhere.
        # It should not block South Luzon carry-over consumption.
        if brand == "OVERALL RANGE":
            continue
        if exclude_brand and brand == exclude_brand:
            continue
        if idx >= len(clean_row):
            continue
        if _extract_float_prices(clean_row[idx]):
            return True
    return False


def _price_pairs_from_floats(price_floats):
    return [
        (price_floats[i], price_floats[i + 1])
        for i in range(0, len(price_floats) - 1, 2)
    ]


def _center_y(box):
    return (box[1] + box[3]) / 2.0


def _column_bbox(table_obj, col_idx):
    for row in table_obj.rows:
        if col_idx < len(row.cells) and row.cells[col_idx]:
            return row.cells[col_idx]
    return None


def _row_idx_for_word(table_obj, word, candidate_rows):
    word_y = (word["top"] + word["bottom"]) / 2.0
    containing_rows = []
    best_idx = None
    best_dist = None
    for row_idx in candidate_rows:
        bbox = table_obj.rows[row_idx].bbox
        y0, y1 = bbox[1], bbox[3]
        if y0 - 2 <= word_y <= y1 + 2:
            containing_rows.append((y1 - y0, row_idx))
            continue
        dist = abs(word_y - _center_y(bbox))
        if best_dist is None or dist < best_dist:
            best_idx = row_idx
            best_dist = dist
    if containing_rows:
        return min(containing_rows)[1]
    return best_idx if best_dist is not None and best_dist <= 12 else None


def _build_geometric_price_map(table_obj, table_rows, col_map, sorted_fuels):
    """
    Build {(row_idx, brand): (min, max)} using PDF word positions.
    This keeps merged visual cells from shifting values into blank fuel rows.
    """
    if not table_obj:
        return {}

    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"]
    product_rows = []
    for row_idx, row in enumerate(table_rows):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, _ = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is not None and found_fuel:
            product_rows.append(row_idx)
    if not product_rows:
        return {}

    words = table_obj.page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
    price_map = {}

    for brand, col_idx in col_map.items():
        bbox = _column_bbox(table_obj, col_idx)
        if not bbox:
            continue

        x0, _, x1, _ = bbox
        line_numbers = {}
        for word in words:
            text = str(word.get("text", ""))
            if not re.fullmatch(r"\d+\.\d+", text):
                continue
            word_x = (word["x0"] + word["x1"]) / 2.0
            if not (x0 - 2 <= word_x <= x1 + 2):
                continue

            row_idx = _row_idx_for_word(table_obj, word, product_rows)
            if row_idx is None:
                continue
            line_key = (row_idx, round((word["top"] + word["bottom"]) / 2.0, 1))
            line_numbers.setdefault(line_key, []).append((word["x0"], float(text)))

        for (row_idx, _), values in line_numbers.items():
            nums = [num for _, num in sorted(values)]
            if len(nums) >= 2 and max(nums[:2]) > 0:
                price_map[(row_idx, brand)] = (nums[0], nums[1])

    return price_map


def _configure_ocr_cache():
    cache_root = os.path.join(tempfile.gettempdir(), ".paddlex")
    os.environ["HOME"] = tempfile.gettempdir()
    os.environ["XDG_CACHE_HOME"] = os.path.join(tempfile.gettempdir(), ".cache")
    os.environ["PADDLE_PDX_CACHE_HOME"] = cache_root
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.makedirs(cache_root, exist_ok=True)


def _get_paddle_ocr():
    global _PADDLE_OCR
    if _PADDLE_OCR is None:
        _configure_ocr_cache()
        from paddleocr import PaddleOCR

        _PADDLE_OCR = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    return _PADDLE_OCR


def _ocr_pdf_document(pdf_open_target):
    import pypdfium2 as pdfium

    if isinstance(pdf_open_target, BytesIO):
        return pdfium.PdfDocument(pdf_open_target.getvalue())
    return pdfium.PdfDocument(pdf_open_target)


def _ocr_result_to_words(result):
    words = []
    data = dict(result)
    texts = data.get("rec_texts") or []
    scores = data.get("rec_scores") or []
    boxes = data.get("rec_boxes")
    polys = data.get("rec_polys") or data.get("dt_polys") or []

    for idx, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue

        score = scores[idx] if idx < len(scores) else 1.0
        if score is not None and score < 0.45:
            continue

        if boxes is not None and idx < len(boxes):
            x0, y0, x1, y1 = [float(v) for v in boxes[idx]]
        elif idx < len(polys):
            points = polys[idx]
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        else:
            continue

        words.append({
            "text": text,
            "x0": x0,
            "x1": x1,
            "top": y0,
            "bottom": y1,
            "cx": (x0 + x1) / 2.0,
            "cy": (y0 + y1) / 2.0,
            "score": score,
        })
    return words


def _normalize_ocr_text(text):
    return re.sub(r"[^A-Z0-9#./& -]", "", str(text).upper()).strip()


def _looks_like_price_token(text):
    normalized = _normalize_ocr_text(text)
    return bool(
        re.fullmatch(r"\d+\.\d+", normalized)
        or normalized in {"#N/A", "N/A", "NA", "-", "0.00"}
    )


def _ocr_line_groups(words, y_tolerance=10):
    lines = []
    for word in sorted(words, key=lambda w: (w["cy"], w["cx"])):
        matched = None
        for line in lines:
            if abs(line["cy"] - word["cy"]) <= y_tolerance:
                matched = line
                break
        if matched is None:
            matched = {"cy": word["cy"], "words": []}
            lines.append(matched)
        matched["words"].append(word)
        matched["cy"] = sum(w["cy"] for w in matched["words"]) / len(matched["words"])

    for line in lines:
        line["words"].sort(key=lambda w: w["cx"])
    return sorted(lines, key=lambda line: line["cy"])


def _find_fuel_in_text(text):
    normalized = _normalize_ocr_text(text)
    for fuel in sorted(FUELS, key=len, reverse=True):
        if fuel in normalized:
            return fuel
    return None


def _detect_ocr_columns(words):
    aliases = {
        "province": ["PROVINCE"],
        "city": ["MUNICIPALITY", "CITYT", "CITY/"],
        "product": ["PRODUCT"],
        "PETRON": ["PETRON"],
        "SHELL": ["SHELL"],
        "CALTEX": ["CALTEX"],
        "PHOENIX": ["PHOENIX"],
        "UNIOIL": ["UNIOIL"],
        "SEAOIL": ["SEAOIL"],
        "PTT": ["PTT"],
        "FLYING V": ["FLYING V", "FLYING"],
        "INDEPENDENT": ["INDEPENDENT"],
        "OVERALL RANGE": ["OVERALL"],
    }
    columns = {}
    for key, names in aliases.items():
        candidates = []
        for word in words:
            text = _normalize_ocr_text(word["text"])
            if any(name in text for name in names):
                candidates.append(word)
        if candidates:
            columns[key] = min(candidates, key=lambda w: w["cy"])["cx"]

    # North Luzon uses TOTAL/CLEAN FUEL columns, but the current schema does not
    # expose those brands. Keep only columns that map to existing output fields.
    return columns


def _column_for_x(x, columns):
    usable = {key: val for key, val in columns.items() if key in BRANDS}
    if not usable:
        return None

    ordered = sorted(usable.items(), key=lambda item: item[1])
    best_key = None
    best_dist = None
    for idx, (key, center) in enumerate(ordered):
        left = (ordered[idx - 1][1] + center) / 2.0 if idx > 0 else -float("inf")
        right = (center + ordered[idx + 1][1]) / 2.0 if idx + 1 < len(ordered) else float("inf")
        if left <= x < right:
            return key
        dist = abs(x - center)
        if best_dist is None or dist < best_dist:
            best_key = key
            best_dist = dist
    return best_key if best_dist is not None and best_dist <= 90 else None


def _text_in_column(line, columns, column_name):
    if column_name not in columns:
        return ""
    center = columns[column_name]
    other_centers = sorted(set(columns.values()))
    pos = other_centers.index(center)
    left = (other_centers[pos - 1] + center) / 2.0 if pos > 0 else -float("inf")
    right = (center + other_centers[pos + 1]) / 2.0 if pos + 1 < len(other_centers) else float("inf")
    return " ".join(
        word["text"]
        for word in line["words"]
        if left <= word["cx"] < right
    ).strip()


def _scrape_north_luzon_ocr(pdf_open_target, category_name):
    try:
        ocr = _get_paddle_ocr()
    except Exception as exc:
        print(f"North Luzon OCR unavailable: {exc}")
        return []

    pdf_doc = _ocr_pdf_document(pdf_open_target)
    data_rows = []
    current_province = "Unknown"
    current_city = "Unknown"

    for page_index in range(len(pdf_doc)):
        page = pdf_doc[page_index]
        image = page.render(scale=2).to_pil()
        import numpy as np
        ocr_result = ocr.predict(np.array(image))
        words = []
        for result in ocr_result:
            words.extend(_ocr_result_to_words(result))
        if not words:
            continue

        columns = _detect_ocr_columns(words)
        if "product" not in columns:
            continue

        pending_block_start = len(data_rows)
        for line in _ocr_line_groups(words):
            product_text = _text_in_column(line, columns, "product")
            found_fuel = _find_fuel_in_text(product_text)
            if not found_fuel:
                continue

            if found_fuel == "RON 100":
                pending_block_start = len(data_rows)

            province_text = _text_in_column(line, columns, "province")
            city_text = _text_in_column(line, columns, "city")
            if province_text:
                current_province = province_text
            if city_text:
                current_city = city_text
            if province_text or city_text:
                for entry in data_rows[pending_block_start:]:
                    entry["province"] = current_province
                    entry["city"] = current_city

            entry = _build_base_entry(category_name, current_province, current_city, found_fuel)
            price_tokens_by_brand = {}
            for word in line["words"]:
                if not _looks_like_price_token(word["text"]):
                    continue
                brand = _column_for_x(word["cx"], columns)
                if not brand:
                    continue
                price_tokens_by_brand.setdefault(brand, []).append(word)

            for brand, tokens in price_tokens_by_brand.items():
                tokens = sorted(tokens, key=lambda w: w["cx"])
                raw_price = " ".join(token["text"] for token in tokens)
                b_min, b_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, b_min, b_max)

            data_rows.append(entry)

    return data_rows

def _avg_pair(pair):
    return (pair[0] + pair[1]) / 2.0

def _fuel_match_score(fuel, pair):
    """
    Higher score means the price pair is more plausible for that fuel type.
    """
    v = _avg_pair(pair)
    ranges = {
        "RON 100": (100.0, 130.0),
        "RON 97": (98.0, 120.0),
        "RON 95": (85.0, 108.0),
        "RON 91": (80.0, 102.0),
        "DIESEL": (110.0, 155.0),
        "DIESEL PLUS": (115.0, 165.0),
        "KEROSENE": (90.0, 145.0),
    }
    low, high = ranges.get(fuel, (0.0, 999.0))
    if low <= v <= high:
        return 100.0
    # Soft penalty outside range to still allow best-effort match.
    if v < low:
        return max(0.0, 100.0 - (low - v))
    return max(0.0, 100.0 - (v - high))

def _align_pairs_to_fuels_with_gaps(grouped_pairs, fuel_sequence):
    """
    Align pairs to a fuel sequence while allowing missing fuels (gaps).
    This avoids shifted assignments when a brand has empty fuel rows.
    """
    if not grouped_pairs or not fuel_sequence:
        return []

    n = len(grouped_pairs)
    m = len(fuel_sequence)
    neg_inf = -10**9
    dp = [[neg_inf] * (m + 1) for _ in range(n + 1)]
    choice = [[None] * (m + 1) for _ in range(n + 1)]

    for j in range(m + 1):
        dp[n][j] = 0
    for i in range(n - 1, -1, -1):
        dp[i][m] = neg_inf

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            skip_score = dp[i][j + 1]
            match_score = _fuel_match_score(fuel_sequence[j], grouped_pairs[i]) + dp[i + 1][j + 1]
            if match_score >= skip_score:
                dp[i][j] = match_score
                choice[i][j] = "match"
            else:
                dp[i][j] = skip_score
                choice[i][j] = "skip"

    tagged = []
    i = 0
    j = 0
    while i < n and j < m:
        if choice[i][j] == "match":
            tagged.append({"fuel": fuel_sequence[j], "pair": grouped_pairs[i]})
            i += 1
            j += 1
        else:
            j += 1
    return tagged


def _pair_within_range(pair, range_pair, tolerance=0.25):
    if not pair or not range_pair:
        return False
    return (
        range_pair[0] - tolerance <= pair[0] <= range_pair[1] + tolerance
        and range_pair[0] - tolerance <= pair[1] <= range_pair[1] + tolerance
    )


def _align_pairs_to_fuels_by_overall_range(grouped_pairs, fuel_sequence, overall_ranges):
    """
    Align merged brand prices using the PDF's own per-fuel overall ranges.
    This is safer than a plain ladder when a blank fuel row is omitted.
    """
    if not grouped_pairs or not fuel_sequence:
        return []

    n = len(grouped_pairs)
    m = len(fuel_sequence)
    neg_inf = -10**9
    dp = [[neg_inf] * (m + 1) for _ in range(n + 1)]
    choice = [[None] * (m + 1) for _ in range(n + 1)]

    for j in range(m + 1):
        dp[n][j] = 0

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            skip_score = dp[i][j + 1]
            range_pair = overall_ranges.get(fuel_sequence[j])
            if _pair_within_range(grouped_pairs[i], range_pair):
                match_score = 1000 + _fuel_match_score(fuel_sequence[j], grouped_pairs[i]) + dp[i + 1][j + 1]
            else:
                match_score = neg_inf

            if match_score >= skip_score:
                dp[i][j] = match_score
                choice[i][j] = "match"
            else:
                dp[i][j] = skip_score
                choice[i][j] = "skip"

    if dp[0][0] <= neg_inf // 2:
        return _align_pairs_to_fuels_with_gaps(grouped_pairs, fuel_sequence)

    tagged = []
    i = 0
    j = 0
    while i < n and j < m:
        if choice[i][j] == "match":
            tagged.append({"fuel": fuel_sequence[j], "pair": grouped_pairs[i]})
            i += 1
            j += 1
        else:
            j += 1

    return tagged if len(tagged) == n else _align_pairs_to_fuels_with_gaps(grouped_pairs, fuel_sequence)


def _collect_following_overall_ranges(table, start_row_idx, col_map, sorted_fuels):
    overall_idx = col_map.get("OVERALL RANGE")
    if overall_idx is None:
        return {}

    ranges = {}
    for row in table[start_row_idx + 1:]:
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, _ = _extract_row_context(
            clean_row,
            sorted_fuels,
            ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"],
        )
        if p_idx is None:
            continue
        if clean_row[0]:
            break

        raw_range = clean_row[overall_idx] if overall_idx < len(clean_row) else ""
        range_min, range_max = clean_price(raw_range)
        if range_min is not None and range_max is not None:
            ranges[found_fuel] = (range_min, range_max)

    return ranges


def _extract_row_context(clean_row, sorted_fuels, headers_to_ignore):
    p_idx = -1
    found_fuel = ""
    for idx, cell in enumerate(clean_row):
        cell_upper = cell.upper()
        for fuel in sorted_fuels:
            if fuel in cell_upper:
                p_idx = idx
                found_fuel = fuel
                break
        if p_idx != -1:
            break

    if p_idx == -1:
        return None, None, None

    current_province = None
    current_city = None
    if p_idx == 1:
        val = clean_row[0]
        if val and not any(h == val.upper() for h in headers_to_ignore):
            current_city = val
    elif p_idx >= 2:
        prov_val = clean_row[0]
        city_val = clean_row[1]
        if prov_val and not any(h == prov_val.upper() for h in headers_to_ignore):
            current_province = prov_val
        if city_val and not any(h == city_val.upper() for h in headers_to_ignore):
            current_city = city_val

    return p_idx, found_fuel, (current_province, current_city)


def _scrape_default_table(table, category_name, current_province, current_city, global_col_map):
    data_rows = []
    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)

    col_map = get_column_mapping(table)
    if col_map:
        global_col_map = col_map
    else:
        col_map = global_col_map
    if not col_map:
        return data_rows, current_province, current_city, global_col_map

    for row in table:
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        province_from_row, city_from_row = location_ctx
        if province_from_row:
            current_province = province_from_row
        if city_from_row:
            current_city = city_from_row

        entry = _build_base_entry(category_name, current_province, current_city, found_fuel)
        for brand in BRANDS:
            if brand in col_map:
                b_idx = col_map[brand]
                raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
                b_min, b_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, b_min, b_max)

        data_rows.append(entry)

    return data_rows, current_province, current_city, global_col_map


def _scrape_south_luzon_table(table, category_name, current_province, current_city, global_col_map, pending_brand_pairs, table_obj=None):
    data_rows = []
    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)

    col_map = get_column_mapping(table)
    if col_map:
        global_col_map = col_map
    else:
        col_map = global_col_map
    if not col_map:
        return data_rows, current_province, current_city, global_col_map, pending_brand_pairs

    geometric_price_map = _build_geometric_price_map(table_obj, table, col_map, sorted_fuels)

    for row_idx, row in enumerate(table):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        province_from_row, city_from_row = location_ctx
        location_changed = False
        if province_from_row and province_from_row != current_province:
            current_province = province_from_row
            location_changed = True
        if city_from_row and city_from_row != current_city:
            current_city = city_from_row
            location_changed = True
        if location_changed:
            pending_brand_pairs = {}

        entry = _build_base_entry(category_name, current_province, current_city, found_fuel)

        for brand in BRANDS:
            if brand not in col_map:
                continue

            geometric_pair = geometric_price_map.get((row_idx, brand))
            if geometric_pair:
                _set_brand_prices(entry, brand, geometric_pair[0], geometric_pair[1])
                continue

            if geometric_price_map:
                continue

            b_idx = col_map[brand]
            raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
            price_floats = _extract_float_prices(raw_price)

            if len(price_floats) >= 2:
                current_fuel_index = FUELS.index(found_fuel) if found_fuel in FUELS else -1
                # South Luzon PDFs sometimes place a multi-fuel merged value block on a blank RON 100 row.
                # In that case, keep RON 100 as null and defer all extracted pairs to lower fuel rows.
                shifted_from_blank_top_row = found_fuel == "RON 100" and len(price_floats) >= 4

                if shifted_from_blank_top_row:
                    grouped = _price_pairs_from_floats(price_floats)
                    expected_fuels = FUELS[current_fuel_index + 1:] if current_fuel_index >= 0 else []
                else:
                    current_pair = (price_floats[0], price_floats[1])
                    _set_brand_prices(entry, brand, current_pair[0], current_pair[1])
                    leftovers = price_floats[2:]
                    grouped = _price_pairs_from_floats(leftovers) if leftovers else []
                    expected_fuels = FUELS[current_fuel_index + 1:] if current_fuel_index >= 0 else []

                if grouped:
                    fuel_tagged_pairs = _align_pairs_to_fuels_with_gaps(grouped, expected_fuels)
                    if fuel_tagged_pairs:
                        pending_brand_pairs[brand] = {
                            "strong": len(grouped) >= 2,
                            "queue": fuel_tagged_pairs,
                        }
                    elif brand in pending_brand_pairs:
                        # If we can't map exactly, do NOT carry-over (prevents misaligned steals).
                        del pending_brand_pairs[brand]
                elif brand in pending_brand_pairs:
                    del pending_brand_pairs[brand]
            elif brand in pending_brand_pairs and pending_brand_pairs[brand].get("queue"):
                queue = pending_brand_pairs[brand]["queue"]
                next_expected = queue[0]
                # Only consume carry-over when:
                # - it matches the fuel row
                # - the row doesn't already look like it contains real per-row prices
                # - and either multiple brands are pending OR this brand was detected as a strong merged-block
                if next_expected["fuel"] == found_fuel:
                    row_has_other_prices = _row_has_any_brand_prices(clean_row, col_map, exclude_brand=brand)
                    pending_brands_count = sum(
                        1
                        for v in pending_brand_pairs.values()
                        if isinstance(v, dict) and v.get("queue")
                    )
                    allow_single_brand_carry = bool(pending_brand_pairs[brand].get("strong"))
                    if not row_has_other_prices and (pending_brands_count >= 2 or allow_single_brand_carry):
                        next_pair = queue.pop(0)["pair"]
                        _set_brand_prices(entry, brand, next_pair[0], next_pair[1])
                        if not queue:
                            del pending_brand_pairs[brand]
            else:
                b_min, b_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, b_min, b_max)

        data_rows.append(entry)

    return data_rows, current_province, current_city, global_col_map, pending_brand_pairs


def _scrape_ncr_table(table, category_name, current_province, current_city, global_col_map, pending_brand_pairs, table_obj=None):
    data_rows = []
    headers_to_ignore = ["CITY", "MUNICIPALITY", "PROVINCE", "AREA", "CITY/MUNICIPALITY", "CITY/AREA"]
    sorted_fuels = sorted(FUELS, key=len, reverse=True)

    col_map = get_column_mapping(table)
    if col_map:
        global_col_map = col_map
    else:
        col_map = global_col_map
    if not col_map:
        return data_rows, current_province, current_city, global_col_map, pending_brand_pairs

    geometric_price_map = _build_geometric_price_map(table_obj, table, col_map, sorted_fuels)

    for row_idx, row in enumerate(table):
        clean_row = [str(cell).strip().replace("\n", " ") if cell else "" for cell in row]
        p_idx, found_fuel, location_ctx = _extract_row_context(clean_row, sorted_fuels, headers_to_ignore)
        if p_idx is None:
            continue

        _, city_from_row = location_ctx
        if city_from_row and city_from_row != current_city:
            current_city = city_from_row
            pending_brand_pairs = {}

        entry = _build_base_entry(category_name, current_province, current_city, found_fuel)

        for brand in BRANDS:
            if brand not in col_map:
                continue

            geometric_pair = geometric_price_map.get((row_idx, brand))
            if geometric_pair:
                _set_brand_prices(entry, brand, geometric_pair[0], geometric_pair[1])
                continue

            if geometric_price_map:
                continue

            b_idx = col_map[brand]
            raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
            floats = _extract_float_prices(raw_price)

            # NCR special: merged row at RON 100; align the extracted prices
            # with allowed gaps so blank fuel rows don't steal prices from lower fuels.
            if found_fuel == "RON 100" and len(floats) >= 4:
                pairs = _price_pairs_from_floats(floats)
                overall_idx = col_map.get("OVERALL RANGE")
                current_overall = None
                if overall_idx is not None and overall_idx < len(clean_row):
                    current_min, current_max = clean_price(clean_row[overall_idx])
                    if current_min is not None and current_max is not None:
                        current_overall = (current_min, current_max)
                if pairs and _pair_within_range(pairs[0], current_overall):
                    current_pair = pairs.pop(0)
                    _set_brand_prices(entry, brand, current_pair[0], current_pair[1])

                fuel_ladder = ["RON 97", "RON 95", "RON 91", "DIESEL", "DIESEL PLUS", "KEROSENE"]
                overall_ranges = _collect_following_overall_ranges(table, row_idx, col_map, sorted_fuels)
                if pairs:
                    pending_brand_pairs[brand] = _align_pairs_to_fuels_by_overall_range(
                        pairs,
                        fuel_ladder,
                        overall_ranges,
                    )
                continue

            if len(floats) >= 2:
                cur_min, cur_max = clean_price(raw_price)
                _set_brand_prices(entry, brand, cur_min, cur_max)
                continue

            if brand in pending_brand_pairs and pending_brand_pairs[brand]:
                next_expected = pending_brand_pairs[brand][0]
                if next_expected["fuel"] == found_fuel:
                    next_pair = pending_brand_pairs[brand].pop(0)["pair"]
                    _set_brand_prices(entry, brand, next_pair[0], next_pair[1])
                    if not pending_brand_pairs[brand]:
                        del pending_brand_pairs[brand]

        data_rows.append(entry)

    return data_rows, current_province, current_city, global_col_map, pending_brand_pairs


def _get_strategy(category_name):
    if str(category_name).upper().startswith("REGION IV"):
        return "south_luzon"
    if str(category_name).strip().upper() == "SOUTH LUZON":
        return "south_luzon"
    if str(category_name).strip().upper() == "NORTH LUZON":
        return "north_luzon"
    if str(category_name).strip().upper() == "NCR":
        return "ncr"
    return "default"

def scrape_pdf_content(pdf_url, category_name):
    print(f"Opening PDF: {pdf_url}")
    try:
        if os.path.exists(pdf_url):
            pdf_open_target = pdf_url
        else:
            response = requests.get(pdf_url, timeout=10)
            response.raise_for_status()
            pdf_open_target = BytesIO(response.content)

        with pdfplumber.open(pdf_open_target) as pdf:
            data_rows = []
            image_only_pages = 0
            
            # Initialize state
            current_province = "Unknown"
            current_city = "Unknown"
            global_col_map = {}
            pending_brand_pairs = {}
            strategy = _get_strategy(category_name)

            if strategy == "north_luzon":
                return _scrape_north_luzon_ocr(pdf_open_target, category_name)

            for page in pdf.pages:
                table_obj = page.find_table()
                table = table_obj.extract() if table_obj else page.extract_table()
                if not table:
                    if page.images and not page.chars:
                        image_only_pages += 1
                    continue

                if strategy == "south_luzon":
                    page_rows, current_province, current_city, global_col_map, pending_brand_pairs = _scrape_south_luzon_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                        pending_brand_pairs,
                        table_obj,
                    )
                elif strategy == "ncr":
                    page_rows, current_province, current_city, global_col_map, pending_brand_pairs = _scrape_ncr_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                        pending_brand_pairs,
                        table_obj,
                    )
                else:
                    page_rows, current_province, current_city, global_col_map = _scrape_default_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                    )
                data_rows.extend(page_rows)
            if not data_rows and image_only_pages:
                print(
                    "No rows scraped because this PDF has no extractable text layer for pdfplumber. "
                    f"{image_only_pages} page(s) had page images but no PDF text objects. "
                    "Some PDF viewers can still select text using their own OCR; this scraper needs "
                    "an OCR step or an OCR fallback for this report."
                )
            return data_rows
    except Exception as e:
        print(f"Error scraping {pdf_url}: {e}")
        return []

if __name__ == "__main__":
    # --- TEST EXECUTION ---
    # Mocking the reports for the sake of standard testing
    mock_reports = [
{"url": "https://prod-cms.doe.gov.ph/documents/d/guest/lf-price-monitoring-for-april-28-may-4-2026-pages-pdf", "category": "NORTH LUZON"}
    ]

    final_data = []
    for report in mock_reports:
        if report["url"]:
            pdf_results = scrape_pdf_content(report["url"], report["category"])
            final_data.extend(pdf_results)

    print(f"\nScraped {len(final_data)} total fuel price records.")
    if final_data:
        print(json.dumps(final_data[:5], indent=4))
