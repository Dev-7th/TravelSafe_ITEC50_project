from doe_RegionData_fetcher import get_all_latest_reports
from pdf_fuel_scraper import scrape_pdf_content
from Manager_DB import save_fuel_data, save_adjustment_data
# import json

def main_controller_data_retrieving():
    print("Fetching reports from DOE...")
    mock_reports = get_all_latest_reports()

    fuel_data = []
    adjustment_data = []
    
    for report in mock_reports:
 
        if report.get("url"):
            try:

                pdf_results = scrape_pdf_content(report["url"], report["category"])
                

                category_name = str(report.get("category", "")).upper()
                
                if category_name == "ADJUSTMENT":
                    adjustment_data.extend(pdf_results)
                else:
                    fuel_data.extend(pdf_results)
                    
            except Exception as e:
                print(f"⚠️ Failed to scrape {report.get('url')}: {e}")
                continue

    print(f"\n✅ Scraped {len(fuel_data)} total fuel price records.")
    print(f"✅ Scraped {len(adjustment_data)} total adjustment records.")
    
    if fuel_data:
        save_fuel_data(fuel_data)
    else:
        print("No fuel data to save this run.")
        
    if adjustment_data:
        save_adjustment_data(adjustment_data)
    else:
        print("No adjustment data to save this run.")

if __name__ == "__main__":
    main_controller_data_retrieving()