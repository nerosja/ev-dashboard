import pandas as pd
import requests
import sqlite3
import json
from geopy.geocoders import Nominatim

# --- KONFIGURATION ---
API_KEY = "DEIN-API-KEY-Hier"      # OpenChargeMap
USER_AGENT = "ev costs in a local field (ComSci-Project)"
EV_DATABASE_NAME = "ev_datenbank.db"
JSON_DATEI_PFAD = "data/open-ev-data-v1.24.0.json" 


# ==========================================
# 1. DATEN-IMPORT & SETUP (ETL-Pipeline)
# ==========================================

def setup_database():
    conn = sqlite3.connect(EV_DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fahrzeuge (
            id TEXT PRIMARY KEY,
            marke TEXT,
            modell TEXT,
            jahr INTEGER,
            batterie_netto_kwh REAL,
            waermepumpe INTEGER,
            max_ladeleistung_dc REAL,
            reichweite_wltp REAL
        )
    """)
    conn.commit()
    return conn

def importiere_lokale_json():
    print(f"Öffne lokale JSON: {JSON_DATEI_PFAD}...")
    with open(JSON_DATEI_PFAD, "r", encoding="utf-8") as datei:
        daten = json.load(datei)
    
    fahrzeug_liste = daten.get("vehicles", [])
    print(f"{len(fahrzeug_liste)} Fahrzeuge gefunden. Starte Import...")

    with sqlite3.connect(EV_DATABASE_NAME) as conn:
        cursor = conn.cursor()
        for auto in fahrzeug_liste:
            unique_id = auto.get("unique_code")
            marke = auto.get("make", {}).get("name", "Unbekannt")
            modell = auto.get("model", {}).get("name", "Unbekannt")
            jahr = auto.get("year")
            batterie_netto = auto.get("battery", {}).get("pack_capacity_kwh_net", 0.0)
            has_heat_pump = 1 if auto.get("battery", {}).get("heat_pump") is True else 0
            max_dc = auto.get("charging", {}).get("dc", {}).get("max_power_kw", 0.0)
            
            reichweite_wltp = 0.0
            for entry in auto.get("range", {}).get("rated", []):
                if entry.get("cycle") == "wltp":
                    reichweite_wltp = entry.get("range_km", 0.0)
                    break 

            cursor.execute("""
                INSERT OR REPLACE INTO fahrzeuge (
                    id, marke, modell, jahr, batterie_netto_kwh, waermepumpe, max_ladeleistung_dc, reichweite_wltp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (unique_id, marke, modell, jahr, batterie_netto, has_heat_pump, max_dc, reichweite_wltp))

        print("JSON-Import abgeschlossen!")

def import_plz_excel():
    print("Lese Excel-Sheet...") 
    df = pd.read_excel("data/plz-stadtlandkreis.xlsx", dtype={"PLZ": str})
    df["PLZ"] = df["PLZ"].str.zfill(5)   
    
    with sqlite3.connect(EV_DATABASE_NAME) as conn:
        print("Schreibe Regionen in die Datenbank...")
        df.to_sql("regionen", conn, if_exists="replace", index=False)
    print("Excel-Import abgeschlossen!")


# ==========================================
# 2. OPEN CHARGE MAP API
# ==========================================

def ladesaeulen_umkreis(lat, lng, umkreis_km=50):
    url_open_charge_map = "https://api.openchargemap.io/v3/poi/"
    params = {
        "output" : "json",
        "latitude" : lat,
        "longitude" : lng,
        "distance" : umkreis_km,
        "distandeunit" : "KM",
        "maxresults" : 250,
        "compact" : "true",
        "verbose" : "false"
    }
    headers = {"X-API-KEY": API_KEY, "User-Agent": USER_AGENT}
    
    print(f"Frage API für Koordinaten ({lat},{lng}) ab...")
    response = requests.get(url_open_charge_map, params=params, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error at API-request: Status {response.status_code}")
        return []
    
def speichere_ladesaeulen_in_db(poi_liste):
    with sqlite3.connect(EV_DATABASE_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ladesaeulen(
                id INTEGER PRIMARY KEY,
                breitengrad REAL,
                laengengrad REAL,
                ort TEXT,
                postleitzahl TEXT,
                betreiber TEXT,
                anzahl_ladepunkte INTEGER,
                usage_cost TEXT,
                last_verified TEXT,
                is_recently_verified INTEGER,
                maxKW INTEGER
            )
        """) 
        for poi in poi_liste:
            poi_id = poi.get("ID")
            address_info = poi.get("AddressInfo", {})
            lat = address_info.get("Latitude")
            lng = address_info.get("Longitude") 
            ort = address_info.get("Town")
            plz = address_info.get("Postcode")
            betreiber = poi.get("OperatorInfo", {}).get("Title", "Unbekannt")
            anzahl_punkte = len(poi.get("Connections", [])) # Bugfix: Connections ist oft eine Liste
            usage_cost = poi.get("UsageCost", "Keine Angabe")
            last_verified = poi.get("DateLastVerified")
            is_recently_verified = poi.get("IsRecentlyVerified")
            
            connections = poi.get("Connections", [])
            max_kw = 0.0
            for conn_item in connections:
                kw = conn_item.get("PowerKW", 0.0)
                if kw > max_kw:
                    max_kw = kw
            
            cursor.execute("""
                INSERT OR REPLACE INTO ladesaeulen (id, breitengrad, laengengrad, ort, postleitzahl, betreiber, anzahl_ladepunkte, usage_cost, last_verified, is_recently_verified, maxKW)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (poi_id, lat, lng, ort, plz, betreiber, anzahl_punkte, usage_cost, last_verified, is_recently_verified, max_kw))
        print(f"{len(poi_liste)} Ladesäulen gespeichert.")


# ==========================================
# 3. BUSINESS LOGIK & BERECHNUNG
# ==========================================

def convert_plz_to_latlgn(plz):
    UNIQUE_USER_AGENT = "ev_cost_perMonth_UnqiueAgent123_projectE"
    geolocator = Nominatim(user_agent=UNIQUE_USER_AGENT)
    
    try:
        print(f"Geocoding: Suche Koordinaten für PLZ {plz} in Deutschland...")
        zip_code = plz
        location = geolocator.geocode(
            query=f"{plz}, Germany",
            timeout=10,
            country_codes="de"
        )
        
        if location:
            print(f"The coordinates for zip-code {zip_code} are ({location.latitude}, {location.longitude})")
            return (location.latitude, location.longitude)
        else:
            print(f"Location not found {plz}")  
            return None
        
    except Exception as e:
        print(f"Fehler beim Geocoding aufgetreten: {e}")        
        return None

def get_region_by_plz(plz):
    with sqlite3.connect(EV_DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT Bundesland, Kreis FROM regionen WHERE PLZ = ?", (plz,))
        return cursor.fetchone()

def get_kwh_price_in_region(fahrleistung_pro_jahr, verbrauch_kwh_100km, bundesland, ladeprofil="mix"):      
    strompreis_home_dict = {
        "Bayern" : 0.36, "Sachsen" : 0.36, "Hamburg" : 0.39,
        "Baden-Württemberg" : 0.38, "Berlin" : 0.36, "Brandenburg" : 0.35,
        "Hessen" : 0.36, "Bremen" : 0.35, "Mecklenburg-Vorpommern" : 0.36,
        "Nordrhein-Westfalen" : 0.38, "Rheinland-Pfalz" : 0.37,
        "Saarland" : 0.35, "Sachsen-Anhalt" : 0.37, "Schleswig-Holstein" : 0.35,
        "Thüringen" : 0.40,
    }   
    
    preis_home = strompreis_home_dict.get(bundesland, 0.35) 
    preis_public_dc = 0.65 
    
    km_monat = fahrleistung_pro_jahr / 12
    kwh_monat = (km_monat / 100) * verbrauch_kwh_100km
    
    if ladeprofil == "mix":
        kosten = (kwh_monat * 0.7 * preis_home) + (kwh_monat * 0.3 * preis_public_dc)
    elif ladeprofil == "home":
        kosten = kwh_monat * preis_home
    else:
        kosten = kwh_monat * preis_public_dc
        
    return round(kosten, 2)
    
def get_evcar_range_real(reichweite_wltp, waerme_pumpe=0):
    range_100to20_percent = round(reichweite_wltp * 0.8, 3)
    range_80to20_percent = round(reichweite_wltp * 0.6, 3)

    if waerme_pumpe == 1:
        highway_cold_weather = round(reichweite_wltp * 0.614, 3)
        city_cold_weather = round(reichweite_wltp * 0.7736, 3)
        combined_cold_weather = round(reichweite_wltp * 0.7, 3)
    else:
        highway_cold_weather = round((reichweite_wltp * 0.614) * 0.85, 3)
        combined_cold_weather = round((reichweite_wltp * 0.7) * 0.85, 3)
        city_cold_weather = round((reichweite_wltp * 0.7736) * 0.85, 3)

    city_mild_weather = round(reichweite_wltp * 1.2453, 3)
    highway_mild_weather = round(reichweite_wltp * 0.82076, 3)
    combined_mild_weather = round(reichweite_wltp, 3)
    real_range_gemini = round(reichweite_wltp * 0.89375, 3)

    print(f"""
    range 100-20%: {range_100to20_percent},
    range 80-20%: {range_80to20_percent},
    range highway cold weather: {highway_cold_weather},
    range highway mild weather: {highway_mild_weather},
    range combined cold weather: {combined_cold_weather},
    range combined mild weather: {combined_mild_weather},
    range city cold weather: {city_cold_weather},
    range city mild weather: {city_mild_weather},
    real range acc. to gemini: {real_range_gemini},
    """)
    
    # FIX: Runde Klammern -> Tuple (kein Set!)
    return (real_range_gemini, range_100to20_percent, range_80to20_percent, highway_cold_weather, highway_mild_weather, combined_cold_weather, combined_mild_weather, city_cold_weather, city_mild_weather)
    
    
def berechne_gesamtkosten(car_id, plz, fahrleistung_pro_jahr, ladeprofil="mix"):
    region = get_region_by_plz(plz)
    if region is None:
        print(f"Postleitzahl {plz} wurde nicht gefunden")
        return None
    
    bundesland = region["Bundesland"]
    kreis = region["Kreis"]  
    
    with sqlite3.connect(EV_DATABASE_NAME) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM fahrzeuge WHERE id = ?", (car_id,))
        auto = cursor.fetchone()
        
        if auto is None:
            print(f"Fahrzeug mit ID {car_id} nicht gefunden")
            return None
        
    # Verbrauch WLTP berechnen
    try:
        verbrauch_100km = (auto["batterie_netto_kwh"] / auto["reichweite_wltp"]) * 100
    except Exception as e:
        verbrauch_100km = 17.5
        print("Konnte den Verbrauch auf 100km nicht berechnen, nehme Standardwert")
            
    if auto["waermepumpe"] == 0:
        verbrauch_100km *= 1.15
        
    # Reale Reichweite und Verbrauch
    # FIX: Tuple entpacken (Wir greifen mit [0] auf real_range_gemini zu)
    real_range_tuple = get_evcar_range_real(auto["reichweite_wltp"], auto["waermepumpe"])
    real_range_gemini = real_range_tuple[0] 
    
    try:
        realverbrauch_100km = (auto["batterie_netto_kwh"] / real_range_gemini) * 100 * 1.145
    except ZeroDivisionError:
        realverbrauch_100km = verbrauch_100km # Fallback

    # Ladesäulen abfragen und speichern
    coordinates = convert_plz_to_latlgn(plz)
    if coordinates is not None:
        lat = coordinates[0]
        lng = coordinates[1]
        
        print(f"Suche Ladesäulen im Umkreis von 10km um PLZ {plz}...")
        daten = ladesaeulen_umkreis(lat, lng, umkreis_km=10)
        if daten:
            speichere_ladesaeulen_in_db(daten) 
            print(f"Info: {len(daten)} Ladesäulen gespeichert!")
    else:
        print("Konnte Koordinaten nicht ermitteln. Überspringe Ladesäulen-Suche.")

    # Kosten berechnen lassen
    kosten = get_kwh_price_in_region(fahrleistung_pro_jahr, verbrauch_100km, bundesland, ladeprofil)  
    kostenreal = get_kwh_price_in_region(fahrleistung_pro_jahr, realverbrauch_100km, bundesland, ladeprofil)
    
    # FIX: Einfache Float-Variablen ausgeben (keine Listen-Indizes mehr)
    print(f"\n--- Auswertung für Region: {kreis} ({bundesland}) ---")
    print(f"Gewähltes Fahrzeug: {auto['marke']} {auto['modell']}")
    print(f"Angenommener Verbrauch (WLTP inkl. WP-Check): {round(verbrauch_100km, 2)} kWh/100km")
    print(f"Realistischer Verbrauch (Gemini): {round(realverbrauch_100km, 2)} kWh/100km")
    print(f"Geschätzte Ladekosten: {kosten} € pro Monat und {round(kosten * 12, 2)} € pro Jahr")  
    print(f"Realere Ladekosten: {kostenreal} € pro Monat und {round(kostenreal * 12, 2)} € pro Jahr")
    
    return kostenreal

# ==========================================
# HAUPTPROGRAMM (STEUERPULT)
# ==========================================

if __name__ == "__main__":
    print("--- EV Dashboard Backend Start ---")
    
    # 1. Einmalige Setup-Schritte (Nur einkommentieren, wenn Daten aktualisiert werden müssen!)
    #setup_database()
    #importiere_lokale_json()
    #import_plz_excel()
  
    # 2. Live-Test der Business Logik
    berechne_gesamtkosten(
        car_id="audi:a6_e_tron:2024:a6_e_tron", 
        plz="01067", 
        fahrleistung_pro_jahr=15000, 
        ladeprofil="mix"
    )