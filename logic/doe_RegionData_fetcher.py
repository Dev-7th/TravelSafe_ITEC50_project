import requests
from bs4 import BeautifulSoup
def get_request_response(category):
    category_map = {
        "ADJUSTMENT": "Price Adjustments",
        "NCR": "NCR Pump Prices",
        "SOUTH LUZON": "South Luzon Pump Prices",
        "NORTH LUZON": "North Luzon Pump Prices",
        "VISAYAS": "Visayas Pump Prices",
        "MINDANAO": "Mindanao Pump Prices"
    }
    
    base_url = "https://doe.gov.ph/articles/group/liquid-fuels"
    params = {
        "maincat": "Retail Pump Prices",
        "subcategory": category_map.get(category, "NCR Pump Prices"),
        "display_type": "Card"
    }
    response = requests.get(base_url, params=params)
    return response.text

def get_latest_report_NCR_NLuzon_Visayas(category="North Luzon"):

    try:
        print(f"Scouting the {category} section...")
        response = get_request_response(category)
        soup = BeautifulSoup(response, 'html.parser')
        

        main_box = soup.find('div', class_='xl:col-span-7')
        
        if not main_box:
            print("Error: Could not find the main content box.")
            return None
            
        if main_box:

            master_list = main_box.find('ul')
            
            if master_list:

                newest_month_block = master_list.find('li')
                
                if newest_month_block:

                    links = newest_month_block.find_all('a')
                    
                    valid_links = []
                    for link in links:
                        href = link.get('href', '')

                        if "prod-cms.doe.gov.ph" in href or "/documents/d/guest" in href:
                            valid_links.append(link)
                    

                    if valid_links:
                        latest_link = valid_links[-1]
                        
                        href = latest_link.get('href')
                        text = latest_link.get_text().strip()
                        
                        print(f"Success! Found latest report: {text}")
                        
                        if href.startswith('/'):
                            return "https://doe.gov.ph" + href
                        return href
                        
        print("No PDF links found in this section.")
        return None
        
    except Exception as e:
        print(f"Network Error: {e}")
        return None

def get_latest_report_SLuzon(category="SOUTH LUZON"):
    try:
        print(f"Scouting the {category} section...")
        response = get_request_response(category)
        soup = BeautifulSoup(response, 'html.parser')
        
        main_box = soup.find('div', class_='xl:col-span-7')
        if not main_box:
            print("Error: Could not find the main content box.")
            return None
            
        # STEP 1: Find the Master List (Year level)
        master_list = main_box.find('ul')
        if not master_list:
            return None

        # STEP 2: Find the First Month (April)
        month_block = master_list.find('li', recursive=False)
        
        if month_block:
            # STEP 3: Find the list of Weeks inside that month
            weeks_container = month_block.find('ul')
            
            if weeks_container:
                # Get all individual week items (li)
                all_weeks = weeks_container.find_all('li', recursive=False)
                
                if all_weeks:
                    # STEP 4: Pick the LAST week (The latest one)
                    latest_week_item = all_weeks[-1]
                    
                    # STEP 5: Grab the 3 region links ONLY from this week
                    links = latest_week_item.find_all('a')
                    
                    valid_links = []
                    for link in links:
                        href = link.get('href', '')
                        text = link.get_text().strip()
                        
                        if "prod-cms.doe.gov.ph" in href or "/documents/d/guest" in href:
                            # Construct full URL
                            full_url = href if href.startswith('http') else "https://doe.gov.ph" + href
                            print(f"Success! Found latest report for: {text}")
                            region_dict = {
                                "region": text,
                                "link": full_url
                            }
                            valid_links.append(region_dict)
                    
                    return valid_links

        print("No PDF links found in this section.")
        return None
        
    except Exception as e:
        print(f"Network Error: {e}")
        return None



def get_latest_report_Mindanao(category="MINDANAO"):

    try:
        print(f"Scouting the {category} section...")
        response = get_request_response(category)
        soup = BeautifulSoup(response, 'html.parser')
        

        main_box = soup.find('div', class_='xl:col-span-7')
        
        if not main_box:
            print("Error: Could not find the main content box.")
            return None
            
        if main_box:

            master_list = main_box.find('ul')
            
            if master_list:

                newest_month_block = master_list.find('li')
                
                if newest_month_block:

                    links = newest_month_block.find_all('a')
                    
                    valid_links = []
                    for link in links:
                        href = link.get('href', '')

                        if "prod-cms.doe.gov.ph" in href or "/documents/d/guest" in href:
                            valid_links.append(link)
                    

                    if valid_links:
                        latest_link = valid_links[0]
                        
                        href = latest_link.get('href')
                        text = latest_link.get_text().strip()
                        
                        print(f"Success! Found latest report: {text}")
                        
                        if href.startswith('/'):
                            return "https://doe.gov.ph" + href
                        return href
                        
        print("No PDF links found in this section.")
        return None
        
    except Exception as e:
        print(f"Network Error: {e}")
        return None
def get_latest_adjustment(category="ADJUSTMENT"):
    try:
        print(f"Scouting the {category} section...")
        response = get_request_response(category)
        soup = BeautifulSoup(response, 'html.parser')
        
        main_box = soup.find('div', class_='xl:col-span-7')
        
        if not main_box:
            print("Error: Could not find the main content box.")
            return None
            
        p_tag = main_box.find('p')
        if p_tag:
            link_tag = p_tag.find('a')
            if link_tag:

                href = link_tag.get('href', '')
                text = link_tag.get_text().strip()
                
                if href.startswith('/'):
                    full_url = "https://doe.gov.ph" + href
                else:
                    full_url = href
                    
                print(f"Success! Found latest adjustment notice: {text}")
                return full_url  
                
            print("No links found in the Adjustment section.")
            return None

    except Exception as e:
        print(f"Network Error: {e}")
        return None

def get_all_latest_reports():
    category = ["NCR", "NORTH LUZON", "VISAYAS"]
    PDF_Files = []

    for items in category:
        link = get_latest_report_NCR_NLuzon_Visayas(items)
        PDF_Files.append( {
            "category": items,
            "label": f"{items} Main report",
            "url": link
        })

    SLuzon_data = get_latest_report_SLuzon()
    if SLuzon_data:
        for entry in SLuzon_data:
            PDF_Files.append({
                "category": "SOUTH LUZON",
                "label": entry['region'],
                "url": entry['link']
            })

    Adjustment_links = get_latest_adjustment()
    if Adjustment_links:
        PDF_Files.append({
            "category": "ADJUSTMENT",
            "label": "Price Adjustment Notice",
            "url": Adjustment_links
        })
    
    Mindanao_data = get_latest_report_Mindanao()
    if Mindanao_data:
            PDF_Files.append({
                "category": "MINDANAO",
                "label": "Mindanao Main report",
                "url": entry['link']
            })
 
    return PDF_Files


