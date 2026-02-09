import streamlit as st
import requests
import shapefile
import tempfile
import os
import zipfile
import shutil
from io import BytesIO
import urllib3

# --- Configuration to ignore SSL warnings ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Constants ---
DATASET_ID = "kadastra-informacijas-sistemas-atverti-telpiskie-dati"
CKAN_API_URL = f"https://data.gov.lv/dati/lv/api/3/action/package_show?id={DATASET_ID}"

# --- Helper Functions ---

@st.cache_data
def get_territory_list():
    """Fetches the list of territories, bypassing SSL checks."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(CKAN_API_URL, headers=headers, verify=False, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        resources = {}
        if data.get('success'):
            for res in data['result']['resources']:
                fmt = res.get('format', '').upper()
                url = res.get('url', '')
                
                if fmt in ['SHP', 'ZIP'] or url.lower().endswith('.zip'):
                    name = res.get('name')
                    if name and url:
                        resources[name] = url
        return resources
    except Exception as e:
        st.error(f"API Error: {e}")
        return {}

def get_sort_key(text):
    """Sorts '1. Name' before '10. Name'."""
    try:
        first_part = text.split('.')[0]
        if first_part.isdigit():
            return int(first_part)
    except:
        pass
    return 999999

def merge_shapefiles(file_paths, output_path):
    """Merges multiple shapefiles using pyshp."""
    if not file_paths:
        return False

    w = shapefile.Writer(output_path)
    
    # Find first valid file
    first_sf = None
    for path in file_paths:
        try:
            temp_sf = shapefile.Reader(path)
            if len(temp_sf.fields) > 1: 
                first_sf = temp_sf
                break
        except:
            continue
            
    if not first_sf:
        print(f"No valid shapefiles found for {output_path}")
        return False

    # Copy fields
    for field in first_sf.fields:
        if field[0] == 'DeletionFlag': 
            continue
        w.field(*field)

    # Merge
    for path in file_paths:
        try:
            with shapefile.Reader(path) as sf:
                if len(sf.fields) != len(first_sf.fields):
                    continue
                for shape_rec in sf.iterShapeRecords():
                    w.record(*shape_rec.record)
                    w.shape(shape_rec.shape)
        except Exception as e:
            print(f"Error reading {path}: {e}")

    w.close()
    
    # Copy .prj
    first_prj = file_paths[0].replace(".shp", ".prj")
    output_prj = output_path.replace(".shp", ".prj")
    if os.path.exists(first_prj):
        shutil.copy(first_prj, output_prj)
        
    return True

def process_territories(selected_names, resource_map, selected_types):
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(selected_names)
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    # Define what we are looking for
    do_parcels = "KKParcel" in selected_types
    do_buildings = "KKBuilding" in selected_types

    with tempfile.TemporaryDirectory() as temp_dir:
        parcels_to_merge = []
        buildings_to_merge = []
        
        # 1. DOWNLOAD & EXTRACT LOOP
        for idx, territory_name in enumerate(selected_names):
            url = resource_map[territory_name]
            status_text.text(f"Downloading ({idx+1}/{total}): {territory_name}...")
            
            try:
                r = requests.get(url, headers=headers, verify=False, timeout=60)
                r.raise_for_status()
                z = zipfile.ZipFile(BytesIO(r.content))
                
                for filename in z.namelist():
                    # Check for PARCELS
                    if do_parcels and "KKParcel" in filename and "KKParcelPart" not in filename and filename.lower().endswith(".shp"):
                        base = filename.rsplit('.', 1)[0]
                        for rf in [f for f in z.namelist() if f.startswith(base)]:
                            z.extract(rf, temp_dir)
                        parcels_to_merge.append(os.path.join(temp_dir, filename))
                    
                    # Check for BUILDINGS
                    if do_buildings and "KKBuilding" in filename and "KKBuildingPart" not in filename and filename.lower().endswith(".shp"):
                        base = filename.rsplit('.', 1)[0]
                        for rf in [f for f in z.namelist() if f.startswith(base)]:
                            z.extract(rf, temp_dir)
                        buildings_to_merge.append(os.path.join(temp_dir, filename))
                                
            except Exception as e:
                print(f"Failed to process {territory_name}: {e}")
            
            progress_bar.progress((idx + 1) / total)

        # 2. MERGE LOOP
        files_created = []

        # Merge Parcels
        if parcels_to_merge:
            status_text.text("Merging Parcels...")
            p_out = os.path.join(temp_dir, "Parcels_merged.shp")
            if merge_shapefiles(parcels_to_merge, p_out):
                files_created.append("Parcels_merged")

        # Merge Buildings
        if buildings_to_merge:
            status_text.text("Merging Buildings...")
            b_out = os.path.join(temp_dir, "Buildings_merged.shp")
            if merge_shapefiles(buildings_to_merge, b_out):
                files_created.append("Buildings_merged")

        # 3. ZIP RESULT
        if files_created:
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for base_name in files_created:
                    base_path = os.path.join(temp_dir, base_name)
                    for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                        if os.path.exists(base_path + ext):
                            zip_file.write(base_path + ext, f"{base_name}{ext}")
            
            status_text.text("Done!")
            return zip_buffer.getvalue()
        
        return None

# --- Main App Interface ---

st.title("🇱🇻 Cadastre Merger (Multi-Type)")
st.markdown("Download and merge Land Parcels and/or Buildings from data.gov.lv.")

# 1. Load Data
with st.spinner("Connecting..."):
    resource_map = get_territory_list()

if not resource_map:
    st.stop()

territory_names = sorted(list(resource_map.keys()), key=get_sort_key)

# 2. Controls
col1, col2 = st.columns(2)
with col1:
    selected_territories = st.multiselect("1. Select Territories:", territory_names)
    if st.checkbox("Select All Territories"):
        selected_territories = territory_names

with col2:
    # DATA TYPE SELECTOR
    data_types = st.multiselect(
        "2. Select Data Types:",
        ["KKParcel", "KKBuilding"],
        default=["KKParcel"],
        format_func=lambda x: "Zemes vienības (Parcels)" if x == "KKParcel" else "Būves (Buildings)"
    )

# 3. Process
if st.button("Download and Merge", type="primary"):
    if not selected_territories:
        st.error("Please select at least one territory.")
    elif not data_types:
        st.error("Please select at least one data type (Parcels or Buildings).")
    else:
        zip_data = process_territories(selected_territories, resource_map, data_types)
        
        if zip_data:
            st.success(f"Merged successfully!")
            st.download_button(
                label="Download Merged Data (.zip)",
                data=zip_data,
                file_name="merged_cadastre_data.zip",
                mime="application/zip"
            )
        else:
            st.error("No data found for the selected criteria.")