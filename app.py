import streamlit as st

from agent import frage_stellen

st.set_page_config(page_title="SAP-Berechtigungsassistent", page_icon="🔐")
st.title("🔐 SAP-Berechtigungsassistent")
st.caption("Fragen zu Benutzern, Rollen und SoD-Konflikten. "
           "Prüfungen laufen über feste Regellogik im MCP-Server. Musterdaten.")

with st.sidebar:
    st.subheader("Beispielfragen")
    st.markdown("- Erstelle eine Übersicht aller Berechtigungsrisiken.\n"
                "- Gibt es ausgeschiedene Mitarbeiter, die noch Rechte haben?\n"
                "- Wer war seit 30 Tagen nicht angemeldet?\n"
                "- Wer hat kritische Berechtigungen, und passt das zur Abteilung?\n"
                "- Hat MMUSTER SoD-Konflikte?")
    if st.button("Neues Gespräch"):
        st.session_state.clear()
        st.rerun()

if "verlauf" not in st.session_state:
    st.session_state.verlauf = []
    st.session_state.anzeige = []


def zeige(rolle, text, aufrufe):
    with st.chat_message(rolle):
        if aufrufe:
            with st.expander(f"{len(aufrufe)} Werkzeugaufruf(e)"):
                for aufruf in aufrufe:
                    st.code(aufruf)
        st.markdown(text)


for eintrag in st.session_state.anzeige:
    zeige(*eintrag)

if frage := st.chat_input("Frage zu Benutzern, Rollen oder SoD-Konflikten"):
    zeige("user", frage, [])
    with st.spinner("Prüfe ..."):
        antwort, aufrufe = frage_stellen(st.session_state.verlauf, frage)
    zeige("assistant", antwort, aufrufe)
    st.session_state.anzeige += [("user", frage, []), ("assistant", antwort, aufrufe)]