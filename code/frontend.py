import streamlit as st
import sqlite3
import pandas as pd

from ev_script import(
    EV_DATABASE_NAME,
    convert_plz_to_latlgn,
    ladesaeulen_umkreis,
    berechne_gesamtkosten,
    get_region_by_plz
)

st.set_page_config(page_title="E-Mobility Dashboard", page_icon="E", layout="wide")

st.title("E-Mobility TCO & Infrastruktur Dashboard")
st.markdown("Berechne die realen Ladekosten und entdecke die lokale Ladeinfrastruktur")

@st.cache_data
def lade_fahrzeuge():
    with sqlite3.connect(EV_DATABASE_NAME) as conn:
        return pd.read_sql_query("SELECT id, marke, modell FROM fahrzeuge ORDER BY marke", conn)
    

fahrzeuge_df = lade_fahrzeuge()

fahrzeug_optionen = {f"{row['marke']} {row['modell']}" : row['id'] for _, row in fahrzeuge_df.iterrows()}

with st.sidebar:
    st.header("Deine Parameter")
    
    gewaehltes_auto_name = st.selectbox("Wähle ein Fahrzeug:", list(fahrzeug_optionen.keys()))
    car_id = fahrzeug_optionen[gewaehltes_auto_name]
    
    plz = st.text_input("Deine Postleitzahl: " ,value="01067", max_chars=5)
    
    km_pro_jahr = st.slider("Fahrleistung pro Jahr (km):" , min_value=5000, max_value=50000, value=15000, step=1000)
    
    ladeprofil = st.radio("Wie lädst du meistens?", options=["mix", "home", "public"], format_func=lambda x: "70% Zuhause / 30% Schnelllader" if x=="mix" else ("100% Zuhause (AC)" if x=="home" else "100% Unterwegs (DC)"))
    
    berechnen_btn = st.button("Kosten berechnen", use_container_width=True)
    
if berechnen_btn:
    
    with st.spinner('Analysiere Region und berechne Preise...'):
        
        region_info = get_region_by_plz(plz)
        if region_info is None:
            st.error(f"Die Postleitzahl {plz} wurde nicht gefunden. Bitte überprüfe die Eingabe")
        else:
            
            try:
                monatskosten_real = berechne_gesamtkosten(car_id, plz, km_pro_jahr, ladeprofil)
            
            except Exception as e:
                st.error(f"Fehler bei Berechnung: Detail {e}")    
            
            if monatskosten_real is not None:
                st.subheader(f"Region: {region_info['Kreis']} ({region_info['Bundesland']})")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric(label="Fahrzeug", value=gewaehltes_auto_name)
                with col2:
                    st.metric(label="Geschätze Kosten (Monat)", value=f"{round(monatskosten_real,2)} €")
                with col3:
                    st.metric(label="Geschätze Kosten (Jahr)", value=f"{round(monatskosten_real * 12, 2)} €")
                
                st.caption("Diese Kosten beinhalten durchschnittliche Ladeverluste an Ladesäulen")
                st.divider()
                
                st.subheader("Ladeinfrastruktur in deiner Nähe (10km)")
                
                coords = convert_plz_to_latlgn(plz)
                if coords:
                    lat, lon = coords
                    
                    ladesaeulen_daten = ladesaeulen_umkreis(lat, lon, umkreis_km=10)
                    
                    if ladesaeulen_daten:
                        
                        map_data = [] 
                        for poi in ladesaeulen_daten:
                            address = poi.get("AddressInfo", {})
                            if address.get("Latitude") and address.get("Longitude"):
                                map_data.append({
                                    "lat" : address["Latitude"],
                                    "lon" : address["Longitude"]
                                })               
                        
                        df_map = pd.DataFrame(map_data)
                        
                        st.map(df_map, zoom=11)
                        
                        st.caption(f"Es wurden {len(ladesaeulen_daten)} Ladestandorte im Umkreis von 10km gefunden.")
                        
                    
                    else:
                        st.warning("Keine Ladesäule in diesem Umkreis gefunden")
                else:
                    st.error("Konnte die Postleitzahl nicht auf der Karte lokalisieren")
                            
else:
    st.info("Bitte wähle deine Parameter in der Seitenleiste und klicke auf 'Kosten berechnen")
                    
                                    
        
    
    