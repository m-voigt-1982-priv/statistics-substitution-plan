import streamlit as st
import requests
import xml.etree.ElementTree as ET
import pandas as pd
from datetime import datetime, timedelta
import logging
import hashlib
import altair as alt
import dateparser
import gspread
from google.oauth2.service_account import Credentials
from utils import check_password
from utils import extract_klassenstufe
from utils import load_vertretungsplan_data_from_gsheet



# Funktion zum Abrufen der XML-Daten
def retrieve_xml(datum_str, username, password):
    logging.info(f"Verarbeite Datum: {datum_str}")
    # URL der XML-Datei für das aktuelle Datum
    base_url = 'https://www.stundenplan24.de/10222573/vplan/vdaten/'
    xml_url = f'{base_url}VplanKl{datum_str}.xml'

    try:
        # XML-Daten abrufen
        response = requests.get(xml_url, auth=(username, password))
        response.raise_for_status()  # Überprüft, ob die Anfrage erfolgreich war
        return response.content
    except requests.exceptions.HTTPError as http_err:
        if http_err.response.status_code == 404:
            logging.info(f'Datei für das Datum {datum_str} nicht gefunden (404 Fehler).')
        else:
            logging.error(f'HTTP-Fehler aufgetreten: {http_err}')
    except Exception as err:
        logging.error(f'Ein unerwarteter Fehler ist aufgetreten: {err}')
    return None


# Funktion zum Parsen des XML und Erstellen des DataFrames
def parse_xml(xml_content):
    if xml_content is None:
        return pd.DataFrame()  # Leeren DataFrame zurückgeben
    data = []

    # XML-Daten parsen
    root = ET.fromstring(xml_content)

    # Informationen aus dem <kopf>-Element extrahieren
    kopf = root.find('kopf')
    datei = kopf.find('datei').text
    titel = kopf.find('titel').text

    # Datum aus 'titel' extrahieren und in ein Datum-Objekt umwandeln
    # Beispiel: "Montag, 25. November 2024"
    datum_titel_str = titel.strip()
    date_part = datum_titel_str.split(',')[1].strip()
    datum_titel_obj = dateparser.parse(date_part, languages=['de'])
    # datum_titel_obj = datetime.strptime(datum_titel_str.split(',')[1].strip(), '%d. %B %Y')

    # Informationen aus dem <haupt>-Element extrahieren
    haupt = root.find('haupt')
    for aktion in haupt.findall('aktion'):
        klasse_raw = aktion.find('klasse').text or ''
        klassen_liste = parse_klasse(klasse_raw)
        stunde_raw = aktion.find('stunde').text or ''
        stunden_liste = parse_stunde(stunde_raw)
        fach = aktion.find('fach').text or ''
        lehrer = aktion.find('lehrer').text or ''
        raum = aktion.find('raum').text or ''
        info = aktion.find('info').text or ''

        # Ausfall prüfen
        ausfall = True if fach == '---' else False

        # Selbststudium prüfen (Groß-/Kleinschreibung ignorieren)
        selbststudium = True if 'selbst.' in info.lower() else False

        # Neue Felder initialisieren
        ausfall_fach = ''
        ausfall_lehrer = ''

        # Ausfall-Fach und Ausfall-Lehrer extrahieren
        if ausfall and 'fällt aus' in info:
            # 'fällt aus' entfernen
            info_prefix = info.replace('fällt aus', '').strip()
            # In Wörter aufteilen
            words = info_prefix.split()
            if len(words) >= 2:
                ausfall_fach = words[0]
                ausfall_lehrer = ' '.join(words[1:])
            elif len(words) == 1:
                ausfall_fach = words[0]
                ausfall_lehrer = ''
            else:
                ausfall_fach = ''
                ausfall_lehrer = ''
        else:
            ausfall_fach = ''
            ausfall_lehrer = ''

        # Für jede Klasse und jede Stunde einen Datensatz hinzufügen
        for klasse in klassen_liste:
            for stunde in stunden_liste:
                # Generiere eindeutige ID
                unique_str = f"{datum_titel_obj.strftime('%Y%m%d')}_{klasse}_{stunde}_{fach}_{lehrer}_{raum}_{info}"
                unique_id = hashlib.md5(unique_str.encode('utf-8')).hexdigest()

                # **Klassenstufe extrahieren**
                klassenstufe = extract_klassenstufe(klasse)

                data.append({
                    'ID': unique_id,
                    'Datei': datei,
                    'Datum': datum_titel_obj,
                    'Klasse': klasse,
                    'Stunde': stunde,
                    'Fach': fach,
                    'Lehrer': lehrer,
                    'Raum': raum,
                    'Info': info,
                    'Ausfall': ausfall,
                    'Selbststudium': selbststudium,
                    'Ausfall-Fach': ausfall_fach,
                    'Ausfall-Lehrer': ausfall_lehrer,
                    'Klassenstufe': klassenstufe 
                })

    # DataFrame erstellen
    df = pd.DataFrame(data)

    # Konvertieren der 'Klassenstufe'-Spalte in 'Int64'
    df['Klassenstufe'] = pd.to_numeric(df['Klassenstufe'], errors='coerce').astype('Int64')


    return df


# Funktion zum Parsen des 'klasse'-Feldes
def parse_klasse(klasse_str):
    classes = []
    klasse_str = klasse_str.replace(' ', '')  # Leerzeichen entfernen
    parts = klasse_str.split(',')
    for part in parts:
        if '-' in part:
            start, end = part.split('-')
            start_splits = start.split('/')
            end_splits = end.split('/')
            if len(start_splits) == 2 and len(end_splits) == 2:
                base_start, section_start = start_splits
                base_end, section_end = end_splits
                if base_start != base_end:
                    logging.warning(f"Unterschiedliche Basis in Bereich {part}. Bereich wird übersprungen.")
                    continue
                else:
                    base = base_start
                    section_start = int(section_start)
                    section_end = int(section_end)
                    for section in range(section_start, section_end + 1):
                        classes.append(f"{base}/{section}")
            else:
                logging.warning(f"Unerwartetes Format in Klasse: {part}. Bereich wird übersprungen.")
                continue
        else:
            classes.append(part)
    return classes




# Funktion zum Parsen des 'stunde'-Feldes
def parse_stunde(stunde_str):
    stunden = []
    stunde_str = stunde_str.replace(' ', '')  # Leerzeichen entfernen
    parts = stunde_str.split(',')
    for part in parts:
        if '-' in part:
            start, end = part.split('-')
            start = int(start)
            end = int(end)
            for s in range(start, end + 1):
                stunden.append(str(s))
        else:
            stunden.append(part)
    return stunden


# helper if the data has to be update if a column is extended etc
def update_existing_data_in_gsheet():
    # Daten aus Google Sheets laden
    df = load_vertretungsplan_data_from_gsheet()

    if not df.empty:
        # Konvertieren der 'Datum'-Spalte in das gewünschte String-Format
        df['Datum'] = df['Datum'].dt.strftime('%d.%m.%Y')

        # Behandeln von fehlenden Werten
        df = df.fillna('')

        # Konvertieren von booleschen Spalten in Strings
        df['Ausfall'] = df['Ausfall'].astype(str)
        df['Selbststudium'] = df['Selbststudium'].astype(str)

        # Konvertieren der 'Klassenstufe'-Spalte in String und fehlende Werte behandeln
        df['Klassenstufe'] = df['Klassenstufe'].astype(str).replace('<NA>', '')

        # Optional: Konvertieren numerischer Spalten in Strings
        df['Stunde'] = df['Stunde'].astype(str)

        # Speichern der aktualisierten Daten in Google Sheets
        # Google Sheets API initialisieren
        scope = ['https://www.googleapis.com/auth/spreadsheets']
        credentials = Credentials.from_service_account_info(
            st.secrets["connections"]["gsheets"]["credentials"],
            scopes=scope
        )
        gc = gspread.authorize(credentials)
        sh = gc.open_by_url(st.secrets["connections"]["gsheets"]["vertretungsplan_data"])
        worksheet = sh.sheet1

        # Löschen des vorhandenen Inhalts
        worksheet.clear()

        # Schreiben der neuen Daten mit der 'Klassenstufe'-Spalte
        # Zuerst die Kopfzeile schreiben
        worksheet.append_row(df.columns.tolist(), value_input_option='RAW')

        # Dann die Daten schreiben
        worksheet.append_rows(df.values.tolist(), value_input_option='RAW')

        st.success('Bestehende Daten wurden erfolgreich aktualisiert.')
    else:
        st.info('Keine Daten zum Aktualisieren vorhanden.')




# Funktion zum Speichern der Daten in Google Sheets
def save_to_gsheet(df):
    # Google Sheets API initialisieren
    scope = ['https://www.googleapis.com/auth/spreadsheets']
    credentials = Credentials.from_service_account_info(
        st.secrets["connections"]["gsheets"]["credentials"],
        scopes=scope
    )
    gc = gspread.authorize(credentials)
    sh = gc.open_by_url(st.secrets["connections"]["gsheets"]["vertretungsplan_data"])
    worksheet = sh.sheet1

    # Bestehende Daten laden
    existing_df = load_vertretungsplan_data_from_gsheet()

    # Duplikate entfernen
    df = df.drop_duplicates(subset=['ID'], keep='first')

    if existing_df.empty:
        # Spaltenüberschriften hinzufügen
        worksheet.clear()
        worksheet.append_row(df.columns.tolist())
        existing_ids = set()
    else:
        existing_df['ID'] = existing_df['ID'].astype(str)
        existing_ids = set(existing_df['ID'].astype(str).dropna())


    # Neue Datensätze identifizieren
    df['ID'] = df['ID'].astype(str)
    new_df = df[~df['ID'].isin(existing_ids)]

    if not new_df.empty:
        # Konvertieren Sie die 'Datum'-Spalte in String
        new_df['Datum'] = new_df['Datum'].dt.strftime('%d.%m.%Y')

        # Konvertieren Sie alle Spalten in Strings
        new_df = new_df.astype(str)

        # Neue Daten in Listen umwandeln
        rows_to_append = new_df.values.tolist()
        # An das Sheet anhängen
        worksheet.append_rows(rows_to_append, value_input_option='RAW')
        logging.info('Neue Daten wurden erfolgreich in Google Sheets angehängt.')
    else:
        logging.info('Keine neuen Daten zum Hinzufügen.')


# filter
@st.cache_data
def filter_data(df, start_date, end_date, selected_klassen,
                selected_ausfall, selected_selbststudium, selected_ausfall_fach, selected_klassenstufen):
    filtered_df = df[
        (df['Datum'] >= pd.to_datetime(start_date)) &
        (df['Datum'] <= pd.to_datetime(end_date)) &
        (df['Klasse'].isin(selected_klassen)) &
        (df['Klassenstufe'].isin(selected_klassenstufen))
    ]
    if selected_ausfall != 'Alle':
        ausfall_bool = True if selected_ausfall == 'Ja' else False
        filtered_df = filtered_df[filtered_df['Ausfall'] == ausfall_bool]
        

    if selected_selbststudium != 'Alle':
        selbststudium_bool = True if selected_selbststudium == 'Ja' else False
        filtered_df = filtered_df[filtered_df['Selbststudium'] == selbststudium_bool]
        
    if selected_ausfall_fach:
        filtered_df = filtered_df[
            filtered_df['Ausfall-Fach'].fillna('Kein Fach').replace('', 'Kein Fach').isin(selected_ausfall_fach)
        ]
    return filtered_df


# Hauptprogramm für Streamlit
def main():
    # Anpassung des Seitenlayouts
    st.set_page_config(layout="wide")

    # Logging konfigurieren
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    if not check_password():
        st.stop()  # Do not continue if check_password is not True.

    # Anmeldeinformationen
    username = st.secrets["username"]
    password = st.secrets["password"]

    st.title("Daten aus dem Vertretungsplans")
    

    # Daten aus Google Sheets laden
    df = load_vertretungsplan_data_from_gsheet()


    # Überprüfen, ob der Vortag in den Daten enthalten ist
    heute = datetime.now()
    gestern = heute - timedelta(days=1)
    letzter_schultag = gestern
    # Wenn heute Montag ist, prüfen wir das Wochenende
    if heute.weekday() == 0:  # Montag
        # Letzter Schultag ist Freitag
        letzter_schultag = heute - timedelta(days=3)

    # Datum des letzten Schultages als String im Format 'YYYY-MM-DD'
    letzter_schultag_str = letzter_schultag.strftime('%Y-%m-%d')

    if df.empty or letzter_schultag_str not in df['Datum'].dt.strftime('%Y-%m-%d').values:
        st.info("Aktualisiere Daten...")
        # Daten abrufen und speichern
        start_datum = letzter_schultag - timedelta(days=7)
        end_datum = letzter_schultag
        # Generiere Liste der Wochentage innerhalb des Zeitraums
        datum_range = pd.date_range(start=start_datum, end=end_datum, freq='D')
        wochentage = datum_range[datum_range.weekday < 5]  # Montag=0, Sonntag=6

        # Gesamtliste zum Speichern aller Datensätze
        all_data = []

        # Schleife über alle Wochentage
        for datum_obj in wochentage:
            datum_str = datum_obj.strftime('%Y%m%d')
            xml_content = retrieve_xml(datum_str, username, password)
            df_new = parse_xml(xml_content)
            if not df_new.empty:
                all_data.append(df_new)

        # Alle DataFrames zusammenfügen
        if all_data:
            final_df = pd.concat(all_data, ignore_index=True)
            # Speichern in Google Sheets
            save_to_gsheet(final_df)
            st.success('Daten wurden erfolgreich aktualisiert.')
            min_date = df['Datum'].min().date()
            max_date = df['Datum'].max().date()
            # Aktualisierte Daten laden
            df = load_vertretungsplan_data_from_gsheet()
        else:
            st.info('Keine neuen Daten zum Speichern vorhanden.')

    # Anzeige des verfügbaren Datumsbereichs
    if not df.empty:
        # Duplikate entfernen
        df = df.drop_duplicates(subset=['ID'], keep='first')
        min_date = df['Datum'].min().date()
        max_date = df['Datum'].max().date()
        st.write(f"**Verfügbare Daten von {min_date.strftime('%d.%m.%Y')} bis {max_date.strftime('%d.%m.%Y')}**")

        # Sidebar für Filter
        st.sidebar.header('Daten filtern')

        # Datumsauswahl
        start_date = st.sidebar.date_input('Startdatum', min_value=min_date, max_value=max_date, value=min_date)
        end_date = st.sidebar.date_input('Enddatum', min_value=min_date, max_value=max_date, value=max_date)

        # Klasse auswählen
        klassen = sorted(df['Klasse'].unique().tolist())
        all_klassen_selected = st.sidebar.checkbox("Alle Klassen auswählen", value=True)
        if all_klassen_selected:
            selected_klassen = klassen
        else:
            selected_klassen = st.sidebar.multiselect('Klasse', options=klassen, default=klassen)

        # Klassenstufe auswählen
        klassenstufe = sorted(df['Klassenstufe'].unique().tolist())
        all_klassenstufen_selected = st.sidebar.checkbox("Alle Klassenstufen auswählen", value=True)
        if all_klassenstufen_selected:
            selected_klassenstufen = klassenstufe
        else:
            selected_klassenstufen = st.sidebar.multiselect('Klassestufen', options=klassenstufe, default=klassenstufe)

        # Ausfall filtern
        ausfall_optionen = ['Alle', 'Ja', 'Nein']
        selected_ausfall = st.sidebar.selectbox('Ausfall', options=ausfall_optionen, index=0)

        # Selbststudium filtern
        selbststudium_optionen = ['Alle', 'Ja', 'Nein']
        selected_selbststudium = st.sidebar.selectbox('Selbststudium', options=selbststudium_optionen, index=0)

        # Ausfall-Fach auswählen
        ausfall_faecher = sorted(
            set(
                fach.strip() if pd.notnull(fach) and fach.strip() != '' else 'Kein Fach'
                for fach in df['Ausfall-Fach']
            )
        )

        if ausfall_faecher:
            all_faecher_selected = st.sidebar.checkbox("Alle Ausfall-Fächer auswählen", value=True)
            if all_faecher_selected:
                selected_ausfall_fach = ausfall_faecher
            else:
                selected_ausfall_fach = st.sidebar.multiselect('Ausfall-Fach', options=ausfall_faecher, default=ausfall_faecher)
        else:
            selected_ausfall_fach = []


        # Daten filtern
        filtered_df = filter_data(df, start_date, end_date, selected_klassen, 
                                  selected_ausfall, selected_selbststudium, selected_ausfall_fach, selected_klassenstufen)


        # Gefilterte Daten anzeigen
        st.write(f"**Anzahl der Einträge: {len(filtered_df)}**")

        # Tabelle und Chart anzeigen
        with st.container():
            st.dataframe(filtered_df, use_container_width=True)

            # Chart erstellen
            if not filtered_df.empty:
                # Anzahl der Einträge pro Datum zählen
                data_per_day = filtered_df.groupby(filtered_df['Datum'].dt.date).size().reset_index(name='Anzahl')
                data_per_day = data_per_day.sort_values('Datum')

                # Konvertieren der 'Datum'-Spalte in String zur besseren Darstellung auf der x-Achse
                data_per_day['Datum'] = data_per_day['Datum'].astype(str)

                st.write("### Anzahl der Einträge pro Tag")

                chart = alt.Chart(data_per_day).mark_bar().encode(
                    x=alt.X('Datum:N', title='Datum'),
                    y=alt.Y('Anzahl:Q', title='Anzahl der Einträge')
                ).properties(
                    width='container',
                    height=400
                )
                st.altair_chart(chart, use_container_width=True)
            else:
                st.info('Keine Daten für die ausgewählten Filter.')

        # Download-Button für gefilterte Daten
        if not filtered_df.empty:
            csv = filtered_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="Gefilterte Daten als CSV herunterladen",
                data=csv,
                file_name='gefilterte_daten.csv',
                mime='text/csv',
            )

    else:
        st.info('Keine Daten verfügbar.')


if __name__ == "__main__":
    main()