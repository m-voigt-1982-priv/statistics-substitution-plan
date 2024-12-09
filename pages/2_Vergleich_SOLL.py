import streamlit as st
import logging
import hashlib
import pandas as pd
import gspread
import altair as alt

from google.oauth2.service_account import Credentials
import streamlit as st
from utils import extract_klassenstufe
from utils import generate_year_week_pairs
from utils import load_vergleich_for_schuljahr
from utils import load_vertretungsplan_data_from_gsheet


def init_vergleich_table():
    # Google Sheets API initialisieren
    scope = ['https://www.googleapis.com/auth/spreadsheets']
    credentials = Credentials.from_service_account_info(
        st.secrets["connections"]["gsheets"]["credentials"],
        scopes=scope
    )
    gc = gspread.authorize(credentials)
    
    # Haupt-Spreadsheet öffnen (Anpassen an Ihre URL)
    sh = gc.open_by_url(st.secrets["connections"]["gsheets"]["vergleich-sollstunden"])
    
    # Tabellenblatt "schuljahr" laden
    worksheet_schuljahr = sh.worksheet("schuljahr")
    data_schuljahr = worksheet_schuljahr.get_all_records()
    df_schuljahr = pd.DataFrame(data_schuljahr)


    # Headers für "vergleich"
    vergleich_header = ["ID","Schuljahr","Jahr","KW","Klasse","Fach","Klassenstufe","Soll","Ist","Delta","Keine-Daten"]

    schuljahr_data = {}  # key: schuljahr, value: list of rows

    for idx, row in df_schuljahr.iterrows():
        schuljahr = str(row['Schuljahr'])
        jahr_start = int(row['Jahr-Start'])
        kw_start = int(row['KW-Start'])
        jahr_ende = int(row['Jahr-Ende'])
        kw_ende = int(row['KW-Ende'])
        klassen_str = str(row['Klassen'])
        klassen_liste = [k.strip() for k in klassen_str.split(';') if k.strip()]

        # Name des Soll-Blatts bestimmen
        soll_sheet_name = "soll-" + schuljahr
        worksheet_soll = sh.worksheet(soll_sheet_name)
        data_soll = worksheet_soll.get_all_records()
        df_soll = pd.DataFrame(data_soll)

        # Spalten säubern, sicherstellen dass Klassenstufe numerisch ist
        df_soll['Klassenstufe'] = pd.to_numeric(df_soll['Klassenstufe'], errors='coerce')

        # Fächer bestimmen (alle Spalten außer Klassenstufe)
        subjects = [col for col in df_soll.columns if col != "Klassenstufe"]

        # Alle (jahr, kw) Paare generieren
        year_week_pairs = generate_year_week_pairs(jahr_start, kw_start, jahr_ende, kw_ende)

        # Liste für dieses Schuljahr initialisieren, falls noch nicht vorhanden
        if schuljahr not in schuljahr_data:
            schuljahr_data[schuljahr] = []

        # Durch alle Jahr/KW Paare iterieren
        for (y, w) in year_week_pairs:
            for klasse in klassen_liste:
                klassenstufe = extract_klassenstufe(klasse)
                if klassenstufe is None:
                    logging.warning(f"Keine Klassenstufe für Klasse '{klasse}' extrahierbar.")
                    continue

                df_soll_row = df_soll.loc[df_soll['Klassenstufe'] == klassenstufe]
                if df_soll_row.empty:
                    logging.warning(f"Keine Soll-Daten für Klassenstufe {klassenstufe} im Schuljahr {schuljahr}.")
                    continue

                # Für jedes Fach nur Zeile anlegen, wenn Soll != 0
                for fach in subjects:
                    soll_wert = df_soll_row[fach].values[0]

                    # Nur wenn Soll != 0
                    if soll_wert != 0:
                        ist = 0
                        delta = 0 
                        keine_daten = True

                        unique_str = f"{schuljahr}_{y}_{w}_{klasse}_{fach}"
                        unique_id = hashlib.md5(unique_str.encode('utf-8')).hexdigest()

                        schuljahr_data[schuljahr].append([
                            unique_id,
                            schuljahr,
                            y,
                            w,
                            klasse,
                            fach,
                            klassenstufe,
                            soll_wert,
                            ist,
                            delta,
                            'True' if keine_daten else 'False'
                        ])
    
    
    # Jetzt schreiben wir für jedes Schuljahr ein eigenes Tabellenblatt
    for sj, rows in schuljahr_data.items():
        sheet_title = f"vergleich-{sj}"
        try:
            wsv = sh.worksheet(sheet_title)
        except:
            wsv = sh.add_worksheet(title=sheet_title, rows=1000, cols=len(vergleich_header))
        
        wsv.clear()
        wsv.append_row(vergleich_header, value_input_option='RAW')
        
        if rows:
            # Alle Werte in Strings umwandeln:
            rows_str = [[str(x) for x in row] for row in rows]
            wsv.append_rows(rows_str, value_input_option='RAW')
        else:
            logging.info(f"Keine Zeilen für Schuljahr {sj} generiert, vermutlich Soll=0 für alle Fächer?")

    logging.info("Vergleich-Tabellen pro Schuljahr erfolgreich initialisiert!")
    st.success('Vergleich-Tabellen wurden erfolgreich initialisiert!')

@st.cache_data(ttl=3600)
def calculate_ist_delta(vergleich_df, vp_df, schuljahr: str):
    """
    Berechnet Ist- und Delta-Werte auf Basis der Ausfalldaten.
    Logik:
    - Bestimme die minimalen Jahr/KW aus vp_df.
    - Filtere vergleich_df, sodass nur Jahr/KW ab dieser minimalen Grenze berücksichtigt werden.
    - Gruppiere vp_df nach (Schuljahr, Jahr, KW, Klasse, Fach, Klassenstufe), zähle Ausfallstunden (Ausfall=True).
    - Ist = Soll - Ausfall_count
    - Delta = Ausfall_count
    - Wenn Ausfall_count=0 => Ist=Soll, Delta=0

    Parameter:
    - vergleich_df: Enthält Spalten [Schuljahr, Jahr, KW, Klasse, Fach, Klassenstufe, Soll]
    - vp_df: Enthält Vertretungsplan-Daten mit 'Ausfall' (bool), 'Klasse', 'Ausfall-Fach', 'Klassenstufe', 'Datum'
    - schuljahr: String, z. B. "2024/25"

    Rückgabe:
    - Ein DataFrame mit zusätzlichen Spalten Ist und Delta.
    """
    # Duplikate entfernen
    vp_df = vp_df.drop_duplicates(subset=['ID'], keep='first')

    # Schuljahr als String sicherstellen
    vergleich_df['Schuljahr'] = vergleich_df['Schuljahr'].astype(str)
    # Schuljahr an vp_df setzen
    vp_df['Schuljahr'] = schuljahr

    # Datentypen angleichen
    # Klassenstufe auf beiden Seiten Int64
    vergleich_df['Klassenstufe'] = pd.to_numeric(vergleich_df['Klassenstufe'], errors='coerce').astype('Int64', errors='ignore')
    vp_df['Klassenstufe'] = pd.to_numeric(vp_df['Klassenstufe'], errors='coerce').astype('Int64', errors='ignore')

    # Fach und Klasse strippen
    vergleich_df['Ausfall-Fach'] = vergleich_df['Fach'].astype(str).str.strip()
    vergleich_df['Klasse'] = vergleich_df['Klasse'].astype(str).str.strip()

    vp_df['Ausfall-Fach'] = vp_df['Ausfall-Fach'].astype(str).str.strip()
    vp_df['Klasse'] = vp_df['Klasse'].astype(str).str.strip()

    # ISO-Kalenderwoche aus vp_df extrahieren
    iso_cal = vp_df['Datum'].dt.isocalendar()
    vp_df['Jahr'] = iso_cal['year']
    vp_df['KW'] = iso_cal['week']

    # Minimale Jahr/KW aus vp_df bestimmen
    df_dates = vp_df[['Jahr','KW']].dropna().drop_duplicates().astype(int).sort_values(by=['Jahr','KW'])
    if df_dates.empty:
        # Falls vp_df leer ist oder keine Daten enthält
        # Dann hat man keine Ist-Werte, Ist=Soll, Delta=0
        # Hier können wir einfach vergleich_df mit Ist=Soll, Delta=0 zurückgeben
        vergleich_df['Ist'] = vergleich_df['Soll']
        vergleich_df['Delta'] = 0
        return vergleich_df
    
    #st.write("Kalenderwochen zur Berechnung: ")
    #st.write(df_dates['KW'])

    min_jahr = df_dates.iloc[0]['Jahr']
    min_kw = df_dates.iloc[0]['KW']

    # vergleich_df filtern ab min_jahr/min_kw
    vergleich_df = vergleich_df[((vergleich_df['Jahr'] > min_jahr) | ((vergleich_df['Jahr'] == min_jahr) & (vergleich_df['KW'] >= min_kw)))]

    grp_cols = ['Schuljahr','Jahr','KW','Klasse','Ausfall-Fach','Klassenstufe']

    # Ausfallstunden zählen
    # (x == True).sum() zählt, wie oft Ausfall True ist
    vp_group = vp_df.groupby(grp_cols)['Ausfall'].apply(lambda x: (x == True).sum()).reset_index(name='ausfall_count')

    #st.write(vp_group)

    # Merge
    merged = pd.merge(vergleich_df, vp_group, on=grp_cols, how='left')
    merged['ausfall_count'] = merged['ausfall_count'].fillna(0).astype(int)

    # Ist und Delta berechnen
    # Ist = Soll - ausfall_count
    # Delta = ausfall_count
    merged['Ist'] = merged['Soll'] - merged['ausfall_count']
    merged['Delta'] = merged['ausfall_count']

    # Nun alle KW filtern, die in vp_df tatsächlich vorkommen (nicht nur ab min_jahr/min_kw)
    # Durch den Merge mit df_dates behalten wir nur die (Jahr, KW), die auch in vp_df vorkommen.
    merged = pd.merge(merged, df_dates[['Jahr','KW']], on=['Jahr','KW'], how='inner')
    merged['Keine-Daten'] = False

    # Wenn Ausfall_count=0 => Ist=Soll, Delta=0, ist schon erfüllt durch Ist=Soll -0, Delta=0

    return merged

# Daten darstellen
def visualize_data(merged):
    # Erstellt eine Hilfsspalte JahrKW für die X-Achse
    merged['JahrKW'] = merged['Jahr'].astype(str) + '-KW' + merged['KW'].astype(str)

    # Sidebar-Filter
    # Klasse-Filter
    klassen_options = sorted(merged['Klasse'].unique().tolist())
    klassen_options = ['Alle'] + klassen_options
    selected_klassen = st.sidebar.multiselect("Klasse", options=klassen_options, default='Alle')


    # Fach-Filter
    # Zuerst NaN entfernen und alles in Strings umwandeln
    fach_values = merged['Fach'].dropna().astype(str).unique().tolist()
    fach_options = ['Alle'] + sorted(fach_values)
    selected_faecher = st.sidebar.multiselect("Fach", options=fach_options, default='Alle')

    # Filter anwenden
    df_filtered = merged.copy()
    if 'Alle' not in selected_klassen:
        df_filtered = df_filtered[df_filtered['Klasse'].isin(selected_klassen)]
    if 'Alle' not in selected_faecher:
        df_filtered = df_filtered[df_filtered['Fach'].isin(selected_faecher)]


    # Nun Wandeln wir `Ist` und `Delta` in Long-Format, damit Altair stacked bars darstellen kann
    # Wir haben zwei Kategorien: 'Ist' und 'Delta'
    df_melted = df_filtered.melt(
        id_vars=['Schuljahr','Jahr','KW','Klasse','Fach','Klassenstufe','Soll','JahrKW','Keine-Daten'],
        value_vars=['Ist','Delta'],
        var_name='Art',
        value_name='Stunden'
    )

    # Wir möchten ein gestapeltes Balkendiagramm:
    # X-Achse: JahrKW
    # Y-Achse: sum(Stunden)
    # Farbe: Art (Ist oder Delta)
    chart = alt.Chart(df_melted).mark_bar().encode(
        x=alt.X('JahrKW:N', title='Jahr-KW', sort=None),
        y=alt.Y('sum(Stunden):Q', title='Stunden'),
        color=alt.Color('Art:N', title='Art', scale=alt.Scale(domain=['Ist','Delta'], range=['#4daf4a','#e41a1c'])),
        tooltip=['Schuljahr','Jahr','KW','Klasse','Fach','Klassenstufe','Soll','Art','Stunden']
    ).properties(
        width=800,
        height=400
    )

    st.write("Überblick von  Ist und Ausfall Stunden pro Jahr/KW")
    st.altair_chart(chart, use_container_width=True)


def visualize_heatmaps(merged):
    # JahrKW erstellen, falls nicht vorhanden
    if 'JahrKW' not in merged.columns:
        merged['JahrKW'] = merged['Jahr'].astype(str) + '-KW' + merged['KW'].astype(str)

    # Nur Delta != 0 Zeilen anzeigen
    #df_delta = merged[merged['Delta'] != 0].copy()
    df_delta = merged.copy()
    if df_delta.empty:
        st.write("Keine Abweichungen vorhanden (Delta=0 für alle gefilterten Einträge).")
        return

    # Für die Fach-Heatmap aggregieren wir nach (JahrKW, Fach)
    df_fach_agg = df_delta.groupby(['Schuljahr','Jahr','KW','JahrKW','Fach'], as_index=False).agg({'Delta':'sum','Soll':'sum'})
    df_fach_agg['RelDelta'] = (df_fach_agg['Delta']/df_fach_agg['Soll'])*100

    # Für die Klassen-Heatmap aggregieren wir nach (JahrKW, Klasse)
    df_klasse_agg = df_delta.groupby(['Schuljahr','Jahr','KW','JahrKW','Klasse'], as_index=False).agg({'Delta':'sum','Soll':'sum'})
    df_klasse_agg['RelDelta'] = (df_klasse_agg['Delta']/df_klasse_agg['Soll'])*100

    # Heatmap 1: nach Fach
    fach_heatmap = alt.Chart(df_fach_agg).mark_rect().encode(
        x=alt.X('JahrKW:N', title='Jahr-KW', sort=None),
        y=alt.Y('Fach:N', title='Fach', sort=None),
        #color=alt.Color('RelDelta:Q', title='Rel. Delta (%)', scale=alt.Scale(scheme='blueorange', domain=[-100,100])),
        color=alt.Color('RelDelta:Q', title='Rel. Delta (%)', scale=alt.Scale(domain=[0,100], range=['white','red'])),
        tooltip=['Schuljahr','Jahr','KW','Fach','Soll','Delta', alt.Tooltip('RelDelta:Q', title='RelDelta (%)', format=".1f")]
    ).properties(
        width=600,
        height=800,
        title="Relative Abweichungen pro Fach"
    )

    # Heatmap 2: nach Klasse
    klassen_heatmap = alt.Chart(df_klasse_agg).mark_rect().encode(
        x=alt.X('JahrKW:N', title='Jahr-KW', sort=None),
        y=alt.Y('Klasse:N', title='Klasse', sort=None),
        #color=alt.Color('RelDelta:Q', title='Rel. Delta (%)', scale=alt.Scale(scheme='blueorange', domain=[-100,100])),
        color=alt.Color('RelDelta:Q', title='Rel. Delta (%)', scale=alt.Scale(domain=[0,100], range=['white','red'])),
        tooltip=['Schuljahr','Jahr','KW','Klasse','Soll','Delta', alt.Tooltip('RelDelta:Q', title='RelDelta (%)', format=".1f")]
    ).properties(
        width=600,
        height=800,
        title="Relative Abweichungen pro Klasse"
    )


    st.altair_chart(fach_heatmap, use_container_width=True)
    st.altair_chart(klassen_heatmap, use_container_width=True)


# Beispielhafter Aufruf
st.title("Vergleich zu SOLL Stunden")
st.write("Hier können Sie Ihre Daten anzeigen.")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
#init_vergleich_table()
ist = calculate_ist_delta(load_vergleich_for_schuljahr("2024-25"),load_vertretungsplan_data_from_gsheet(),"2024-25")
st.write(ist)
visualize_data(ist)
visualize_heatmaps(ist)