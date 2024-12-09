import streamlit as st
import hmac
from datetime import datetime, timedelta
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# Request a login
def check_password():
    """Returns `True` if the user had the correct password."""

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if hmac.compare_digest(st.session_state["login"], st.secrets["login"]):
            st.session_state["password_correct"] = True
            del st.session_state["login"]  # Don't store the password.
        else:
            st.session_state["password_correct"] = False

    # Return True if the password is validated.
    if st.session_state.get("password_correct", False):
        return True

    # Show input for password.
    st.text_input(
        "Login", type="password", on_change=password_entered, key="login"
    )
    if "password_correct" in st.session_state:
        st.error("😕 Login falsch")
    return False


# Klassenstufe extrahieren
def extract_klassenstufe(klasse_value):
    if isinstance(klasse_value, str):
        if 'Klub' in klasse_value:
            # 'Klub' ignorieren
            return None
        elif 'DAZ' in klasse_value:
            # 'DAZ' ignorieren
            return None
        elif 'JG' in klasse_value:
            # Für Werte wie 'JG12/inf2'
            parts = klasse_value.split('/')
            if parts[0].startswith('JG'):
                klassenstufe = parts[0][2:]  # Extrahiere die Zahl nach 'JG'
                return int(klassenstufe)
            else:
                return None
        elif '/' in klasse_value:
            # Für Werte wie '6/4'
            parts = klasse_value.split('/')
            if parts[0].isdigit():
                return int(parts[0])  # Die Zahl vor dem '/' ist die Klassenstufe
            else:
                return None
        else:
            # Weitere Fälle behandeln, falls nötig
            return None
    else:
        return None

# Generierung von Kalenderwochen über den Jahreswechsel
def generate_year_week_pairs(jahr_start, kw_start, jahr_ende, kw_ende):
    """Generiert alle (Jahr, KW)-Paare von (jahr_start, kw_start) bis (jahr_ende, kw_ende) 
    unter Verwendung einer wöchentlichen Schleife über Datum.
    """
    # Startdatum aus ISO Jahr-Woche berechnen (Montag der betreffenden Woche)
    # %G = ISO Jahr, %V = ISO Woche, %u = ISO Wochentag (1=Montag)
    start_str = f"{jahr_start}-W{kw_start}-1"
    end_str = f"{jahr_ende}-W{kw_ende}-1"
    start_date = datetime.strptime(start_str, "%G-W%V-%u")
    end_date = datetime.strptime(end_str, "%G-W%V-%u")

    pairs = []
    current_date = start_date
    while True:
        iso_year, iso_week, iso_weekday = current_date.isocalendar()
        pairs.append((iso_year, iso_week))
        if iso_year == jahr_ende and iso_week == kw_ende:
            break
        current_date += timedelta(days=7)
    return pairs

#@st.cache_data(ttl=3600)
def load_vertretungsplan_data_from_gsheet():
    # Google Sheets API initialisieren
    scope = ['https://www.googleapis.com/auth/spreadsheets']
    credentials = Credentials.from_service_account_info(
        st.secrets["connections"]["gsheets"]["credentials"],
        scopes=scope
    )
    gc = gspread.authorize(credentials)
    sh = gc.open_by_url(st.secrets["connections"]["gsheets"]["vertretungsplan_data"])
    worksheet = sh.sheet1

    # Daten aus Google Sheets laden
    data = worksheet.get_all_records()
    if data:
        df = pd.DataFrame(data)
    else:
        df = pd.DataFrame()

    if not df.empty:
        # Konvertieren der Spalten in die richtigen Datentypen

        # 'Datum' Spalte in datetime64
        df['Datum'] = pd.to_datetime(df['Datum'], format='%d.%m.%Y', errors='coerce')

        # 'Stunde' Spalte in int64
        df['Stunde'] = pd.to_numeric(df['Stunde'], errors='coerce').astype('Int64')

        # 'Ausfall' und 'Selbststudium' in bool, fehlende Werte als False
        df['Ausfall'] = df['Ausfall'].astype(str).str.lower().map({'true': True, 'false': False})
        df['Ausfall'] = df['Ausfall'].fillna(False).astype(bool)

        df['Selbststudium'] = df['Selbststudium'].astype(str).str.lower().map({'true': True, 'false': False})
        df['Selbststudium'] = df['Selbststudium'].fillna(False).astype(bool)
        
        # **Erstellen der 'Klassenstufe'-Spalte**
        df['Klassenstufe'] = df['Klasse'].apply(extract_klassenstufe)
        df['Klassenstufe'] = pd.to_numeric(df['Klassenstufe'], errors='coerce').astype('Int64')


        # Andere Spalten in String
        other_columns = df.columns.difference(['Datum', 'Stunde', 'Ausfall', 'Selbststudium'])
        df[other_columns] = df[other_columns].astype(str)
        df['Ausfall-Fach'] = df['Ausfall-Fach'].replace({'nan': '', 'None': ''}).fillna('')

        # Entfernen von Zeilen mit ungültigen Datumswerten
        df = df.dropna(subset=['Datum'])

    return df


def load_vergleich_for_schuljahr(schuljahr: str) -> pd.DataFrame:
    """
    Lädt das 'vergleich'-Tabellenblatt für ein bestimmtes Schuljahr als DataFrame.

    Annahmen:
    - Es existiert ein Tabellenblatt mit dem Namen "vergleich-<schuljahr>", z. B. "vergleich-2024/25".
    - Das Tabellenblatt hat die Spalten: 
      ID, Schuljahr, Jahr, KW, Klasse, Fach, Klassenstufe, Soll, Ist, Delta, Keine-Daten

    Rückgabe:
    - Ein DataFrame mit den entsprechenden Datentypen:
      * Jahr, KW, Klassenstufe, Soll, Ist, Delta: int64 oder Int64
      * Keine-Daten: bool
      * Andere Spalten: str oder passend konvertiert
    """

    scope = ['https://www.googleapis.com/auth/spreadsheets']
    credentials = Credentials.from_service_account_info(
        st.secrets["connections"]["gsheets"]["credentials"],
        scopes=scope
    )
    gc = gspread.authorize(credentials)

    sheet_title = f"vergleich-{schuljahr}"
    sh = gc.open_by_url(st.secrets["connections"]["gsheets"]["vergleich-sollstunden"])

    # Versuch, das entsprechende Tabellenblatt zu öffnen
    worksheet = sh.worksheet(sheet_title)
    data = worksheet.get_all_records()

    if data:
        df = pd.DataFrame(data)
    else:
        # Kein Inhalt im Tabellenblatt
        df = pd.DataFrame()

    if not df.empty:
        # Typkonvertierungen vornehmen
        # Jahr, KW, Klassenstufe, Soll, Ist, Delta in numerisch
        numeric_cols = ['Jahr', 'KW', 'Klassenstufe', 'Soll', 'Ist', 'Delta']
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')

        # Keine-Daten von 'True'/'False' in bool umwandeln
        df['Keine-Daten'] = df['Keine-Daten'].astype(str).str.lower().map({'true': True, 'false': False})
        df['Keine-Daten'] = df['Keine-Daten'].fillna(False).astype(bool)

        # Andere Spalten (ID, Schuljahr, Klasse, Fach) bleiben Strings
        # Falls notwendig: df['Fach'] = df['Fach'].astype(str) - aber durch get_all_records() sind sie i.d.R. Strings

    return df