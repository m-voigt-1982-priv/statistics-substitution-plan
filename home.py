import streamlit as st
from datetime import datetime, timedelta
import logging


# Hauptprogramm für Streamlit
def main():
    # Anpassung des Seitenlayouts
    st.set_page_config(layout="wide")

    # Logging konfigurieren
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    #if not check_password():
    #    st.stop()  # Do not continue if check_password is not True.

    st.title("Daten zum Unterrichtsausfall")
    st.write("Bitte die Funktion rechts auswählen")


if __name__ == "__main__":
    main()