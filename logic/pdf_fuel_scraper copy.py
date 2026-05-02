import pdfplumber
import requests
import re
import json
import os
from io import BytesIO
from doe_RegionData_fetcher import get_all_latest_reports 

# 1. Added missing brands found in the PDFs
BRANDS = ["PETRON", "SHELL", "CALTEX", "PHOENIX", "UNIOIL", "SEAOIL", "PTT", "FLYING V", "INDEPENDENT", "OVERALL RANGE"]
FUELS = ["RON 100", "RON 97", "RON 95", "RON 91", "DIESEL", "DIESEL PLUS", "KEROSENE"]

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


def _tag_pairs_for_expected_fuels(grouped_pairs, expected_fuels):
    """
    Convert [(min,max), ...] into [{"fuel": fuel, "pair": (min,max)}, ...]
    tagging sequentially to the next fuels.

    We allow partial mapping because PDF extraction can truncate the merged
    block. Misalignment is prevented by guards when consuming carry-over.
    """
    if not expected_fuels or not grouped_pairs:
        return []
    usable = min(len(grouped_pairs), len(expected_fuels))
    return [{"fuel": expected_fuels[i], "pair": grouped_pairs[i]} for i in range(usable)]

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

def _align_pairs_to_fuels(grouped_pairs, expected_fuels):
    """
    Choose the best start offset for merged pairs against expected fuels.
    This prevents one-row shifts (e.g., RON97 taking RON95 price).
    """
    if not grouped_pairs or not expected_fuels:
        return []
    n = len(grouped_pairs)
    m = len(expected_fuels)
    if n >= m:
        return [{"fuel": expected_fuels[i], "pair": grouped_pairs[i]} for i in range(m)]

    best_offset = 0
    best_score = -1.0
    for offset in range(0, m - n + 1):
        score = 0.0
        for i, pair in enumerate(grouped_pairs):
            fuel = expected_fuels[offset + i]
            score += _fuel_match_score(fuel, pair)
        if score > best_score:
            best_score = score
            best_offset = offset

    return [{"fuel": expected_fuels[best_offset + i], "pair": grouped_pairs[i]} for i in range(n)]


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


def _scrape_south_luzon_table(table, category_name, current_province, current_city, global_col_map, pending_brand_pairs):
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

    for row in table:
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

            b_idx = col_map[brand]
            raw_price = clean_row[b_idx] if b_idx < len(clean_row) else ""
            price_floats = _extract_float_prices(raw_price)

            if len(price_floats) >= 2:
                current_fuel_index = FUELS.index(found_fuel) if found_fuel in FUELS else -1
                # South Luzon PDFs sometimes place a multi-fuel merged value block on a blank RON 100 row.
                # In that case, keep RON 100 as null and defer all extracted pairs to lower fuel rows.
                shifted_from_blank_top_row = found_fuel == "RON 100" and len(price_floats) >= 4

                if shifted_from_blank_top_row:
                    grouped = [(price_floats[i], price_floats[i + 1]) for i in range(0, len(price_floats) - 1, 2)]
                    expected_fuels = FUELS[current_fuel_index + 1:] if current_fuel_index >= 0 else []
                else:
                    current_pair = (price_floats[0], price_floats[1])
                    _set_brand_prices(entry, brand, current_pair[0], current_pair[1])
                    leftovers = price_floats[2:]
                    grouped = [(leftovers[i], leftovers[i + 1]) for i in range(0, len(leftovers) - 1, 2)] if leftovers else []
                    expected_fuels = FUELS[current_fuel_index + 1:] if current_fuel_index >= 0 else []

                if grouped:
                    if shifted_from_blank_top_row:
                        fuel_tagged_pairs = _align_pairs_to_fuels(grouped, expected_fuels)
                    else:
                        fuel_tagged_pairs = _tag_pairs_for_expected_fuels(grouped, expected_fuels)
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


def _get_strategy(category_name):
    if str(category_name).upper().startswith("REGION IV"):
        return "south_luzon"
    if str(category_name).strip().upper() == "SOUTH LUZON":
        return "south_luzon"
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
            
            # Initialize state
            current_province = "Unknown"
            current_city = "Unknown"
            global_col_map = {}
            pending_brand_pairs = {}
            strategy = _get_strategy(category_name)

            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue

                if strategy == "south_luzon" or strategy == "ncr":
                    page_rows, current_province, current_city, global_col_map, pending_brand_pairs = _scrape_south_luzon_table(
                        table,
                        category_name,
                        current_province,
                        current_city,
                        global_col_map,
                        pending_brand_pairs,
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
            return data_rows
    except Exception as e:
        print(f"Error scraping {pdf_url}: {e}")
        return []

if __name__ == "__main__":
    # --- TEST EXECUTION ---
    # Mocking the reports for the sake of standard testing
    mock_reports = [
        {"url": "https://prod-cms.doe.gov.ph/documents/d/guest/ncr-price-monitoring-04282026-pdf", "category": "NCR"}
    ]

    final_data = []
    for report in mock_reports:
        if report["url"]:
            pdf_results = scrape_pdf_content(report["url"], report["category"])
            final_data.extend(pdf_results)

    print(f"\nScraped {len(final_data)} total fuel price records.")
    if final_data:
        print(json.dumps(final_data[:5], indent=4))