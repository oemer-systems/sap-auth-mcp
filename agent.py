import csv
import json
import os
import re
import sys
from pathlib import Path

import anyio
from anthropic import AnthropicError, AsyncAnthropic, AuthenticationError
from mcp import Client, StdioServerParameters
from mcp.types import TextContent
from ollama import AsyncClient, ResponseError

MODELL = "claude-haiku-5-5"
LOKALES_MODELL = os.environ.get("LOKALES_MODELL", "qwen3:8b")
SYSTEM = (
    "Du bist ein Assistent für SAP-Berechtigungen. Nutze für Fakten ausschließlich die Werkzeuge. "
    "Ob ein SoD-Konflikt vorliegt und welche Priorität ein Befund hat, entscheiden allein die Werkzeuge. "
    "Befunde mit der Priorität 'prüfen' sind Hinweise, keine Verstöße: Weise darauf hin, dass ein Mensch "
    "beurteilen muss, ob die Berechtigung zur Tätigkeit passt. "
    "Abgesicherte Konflikte sind durch eine genehmigte Kontrolle abgedeckt: Nenne sie als Information, "
    "nicht als offenen Befund. "
    "Wenn nach Lösungen gefragt wird, nutze das Werkzeug loesungsvorschlaege. "
    "Nenne nur Rollen, Transaktionen und Regeln, die wörtlich in Werkzeugergebnissen vorkommen. Erfinde keine Namen. "
    "Rufe für jede Frage nach Fakten ein Werkzeug auf, auch bei kurzen Rückfragen wie 'ja'. "
    "Gesperrte oder ausgetretene Benutzer werden nie entsperrt: Empfiehl für sie nur, die Rollen zu entziehen. "
    "Bei jedem Änderungswunsch (entsperren, sperren, Rollen entziehen oder zuordnen) rufe aenderung_anfragen auf. "
    "Du kannst nichts ändern. Kurzfassung, Antragstext und alle Hinweise aus den Ergebnissen zeigt das System "
    "automatisch an: Wiederhole sie nicht und begründe nicht selbst, warum nichts ausgeführt wurde. "
    "Bei der SoD-Prüfung zeigt das System die Fakten automatisch als Tabelle unter deiner Antwort an: "
    "Wiederhole keine Einzelheiten aus der Tabelle, sondern ordne in höchstens drei Sätzen ein. "
    "Nenne dem Nutzer nie Werkzeugnamen wie aenderung_anfragen. Er fragt einfach in normaler Sprache weiter. "
    "Erkläre Ergebnisse verständlich. Entscheidungen trifft immer ein Mensch. "
    "Antworte auf Deutsch und sprich den Nutzer immer mit du an."
)
# Nur für den dritten Versuch, wird nicht im Gesprächsverlauf gespeichert
ANSTOSS = {"role": "system", "content": "Deine letzte Antwort war leer. Antworte nie leer. Brauchst du Fakten, "
                                        "rufe jetzt das passende Werkzeug auf, sonst antworte in einem Satz."}
LEERE_ANTWORT = ("Das Sprachmodell hat keine Antwort erzeugt. Bitte die Frage noch einmal stellen, "
                 "gern etwas anders formuliert.")
SERVER = StdioServerParameters(
    command=sys.executable,
    args=[str(Path(__file__).parent / "server.py")],
)


def ergebnis_als_text(result):
    """Macht aus dem Ergebnis eines MCP-Werkzeugs Text für das Modell."""
    text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
    return text or json.dumps(result.structured_content, ensure_ascii=False)


def als_daten(result):
    """Das Werkzeugergebnis als Daten, damit der Code feste Teile der Antwort selbst schreiben kann."""
    try:
        return json.loads(ergebnis_als_text(result))
    except (ValueError, TypeError):
        return None


KEIN_SCHLUESSEL = ("Für den Cloud-Modus fehlt der API-Schlüssel. Wähle links „Lokal“, dann bleiben alle Daten "
                   "auf diesem Rechner. Für den Cloud-Modus den Schlüssel vor dem Start als ANTHROPIC_API_KEY "
                   "setzen, nie in eine Datei im Projekt schreiben.")


def cloud_bereit():
    """Ist ein API-Schlüssel für den Cloud-Modus gesetzt? Der Schlüssel selbst wird nie gelesen oder angezeigt."""
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


async def _frage(verlauf, frage):
    """Cloud-Modus: beantwortet eine Frage mit Claude und den Werkzeugen des MCP-Servers."""
    nachrichten = verlauf + [{"role": "user", "content": frage}]
    aufrufe, fakten = [], []
    if not cloud_bereit():
        return KEIN_SCHLUESSEL, aufrufe, fakten

    async with Client(SERVER) as mcp:
        tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in (await mcp.list_tools()).tools
        ]
        while True:
            try:
                antwort = await AsyncAnthropic().messages.create(
                    model=MODELL, max_tokens=4000, system=SYSTEM,
                    tools=tools, messages=nachrichten,
                )
            except AuthenticationError:
                return "Der API-Schlüssel wurde abgelehnt. Bitte prüfen oder links „Lokal“ wählen.", aufrufe, fakten
            except AnthropicError as fehler:
                return f"Die KI-Anfrage ist fehlgeschlagen: {fehler}", aufrufe, fakten

            nachrichten.append({"role": "assistant", "content": antwort.content})
            if antwort.stop_reason != "tool_use":
                break

            ergebnisse = []
            for block in antwort.content:
                if block.type == "tool_use":
                    aufrufe.append(f"{block.name}({json.dumps(block.input, ensure_ascii=False)})")
                    result = await mcp.call_tool(block.name, block.input)
                    fakten.append((block.name, als_daten(result)))
                    ergebnisse.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": ergebnis_als_text(result),
                        "is_error": bool(result.is_error),
                    })
            nachrichten.append({"role": "user", "content": ergebnisse})

    verlauf[:] = nachrichten
    text = "\n".join(b.text for b in antwort.content if b.type == "text")
    return text or LEERE_ANTWORT, aufrufe, fakten


async def _frage_lokal(verlauf, frage):
    """Lokaler Modus: dieselbe Schleife, aber mit einem Modell in Ollama auf dem eigenen Rechner."""
    nachrichten = verlauf + [{"role": "user", "content": frage}]
    aufrufe, fakten = [], []

    async with Client(SERVER) as mcp:
        tools = [
            {"type": "function", "function": {
                "name": t.name, "description": t.description or "", "parameters": t.input_schema,
            }}
            for t in (await mcp.list_tools()).tools
        ]
        ollama = AsyncClient()
        while True:
            try:
                # Erst schnell ohne Nachdenken. Zweiter Versuch mit Nachdenken, wenn die Antwort leer ist
                # oder wenn die Frage einen Benutzer nennt, aber kein Werkzeug die Fakten geprüft hat.
                # Dritter Versuch mit ausdrücklichem Anstoß, falls beide leer bleiben (so bei A4b gesehen).
                for denken, anstoss in ((False, []), (True, []), (False, [ANSTOSS])):
                    antwort = await ollama.chat(
                        model=LOKALES_MODELL,
                        messages=[{"role": "system", "content": SYSTEM}] + nachrichten + anstoss,
                        tools=tools,
                        think=denken,
                        options={"num_ctx": 16384, "temperature": 0},  # 0: gleiche Frage, gleiche Antwort
                    )
                    braucht_fakten = not aufrufe and nennt_benutzer(frage)
                    if antwort.message.tool_calls or (antwort.message.content and not braucht_fakten):
                        break
            except ConnectionError:
                return "Ollama ist nicht erreichbar. Läuft das Programm Ollama?", aufrufe, fakten
            except ResponseError as fehler:
                return f"Ollama meldet einen Fehler: {fehler.error}", aufrufe, fakten

            nachrichten.append(antwort.message)
            if not antwort.message.tool_calls:
                break

            for aufruf in antwort.message.tool_calls:
                name, argumente = aufruf.function.name, dict(aufruf.function.arguments)
                aufrufe.append(f"{name}({json.dumps(argumente, ensure_ascii=False)})")
                result = await mcp.call_tool(name, argumente)
                fakten.append((name, als_daten(result)))
                nachrichten.append({"role": "tool", "tool_name": name, "content": ergebnis_als_text(result)})

    verlauf[:] = nachrichten
    return antwort.message.content or LEERE_ANTWORT, aufrufe, fakten


def nennt_benutzer(frage):
    """Erwähnt die Frage eine bekannte SAP-Kennung wie MMUSTER? Dann braucht die Antwort Fakten
    aus einem Werkzeug. Liest dafür nur die Benutzerliste."""
    with open(Path(__file__).parent / "data" / "benutzer.csv", encoding="utf-8", newline="") as f:
        kennungen = {z["benutzer"] for z in csv.DictReader(f)}
    woerter = set(re.findall(r"[A-Z0-9_]+", frage.upper()))
    return bool(kennungen & woerter)


WARNUNG_ENTSPERREN = ("\n\n---\n⚠️ Hinweis des Systems: Diese Antwort erwähnt das Entsperren, ohne dass der "
                      "Änderungswunsch geprüft wurde. Ausgetretene Mitarbeiter werden nie entsperrt. "
                      "Bitte mit „Darf ich <Benutzer> entsperren?“ prüfen lassen.")


# Namen aller Werkzeuge, direkt aus server.py gelesen, damit die Liste nie veraltet
WERKZEUGE = set(re.findall(r"@mcp\.tool\(\)\s*\ndef (\w+)",
                           (Path(__file__).parent / "server.py").read_text(encoding="utf-8")))


# Abkürzungen mit Punkt sind kein Satzende. Sonst würde aus "Falls du ..., z. B. den Entzug, nutze
# aenderung_anfragen." nur der hintere Teil entfernt und ein halber Satz bliebe stehen.
ABKUERZUNGEN = re.compile(r"\b(?:z\. ?B|d\. ?h|u\. ?a|bzw|ggf|evtl|inkl|ca|usw|vgl|Nr)\.\s", re.IGNORECASE)


# Fester Satz statt des entfernten Hinweises der KI auf den Änderungsantrag.
HINWEIS_ANTRAG = ("\n\nWenn du eine dieser Änderungen möchtest, sag mir, welche. Ich bereite dann "
                  "einen Änderungsantrag vor. Ausgeführt wird nichts, die Entscheidung trifft ein Mensch.")


# Interne Namen, die der Nutzer nie sehen soll: Werkzeuge und Felder aus den Werkzeugergebnissen
INTERNA = {w.lower() for w in WERKZEUGE} | {"antrag_text", "kurzfassung", "richtiges_vorgehen", "status_in_sap"}


def nennt_interna(text):
    return any(name in text.lower() for name in INTERNA)


def ohne_werkzeugnamen(antwort):
    """Entfernt Sätze, in denen die KI dem Nutzer einen Werkzeugnamen nennt, etwa
    "rufe das Werkzeug aenderung_anfragen auf". Der Nutzer fragt in normaler Sprache, nicht mit
    Werkzeugnamen. Codeblöcke bleiben unverändert."""
    zeilen, im_code = [], False
    for zeile in antwort.split("\n"):
        if zeile.strip().startswith("```"):
            im_code = not im_code
        if im_code or zeile.strip().startswith("```") or not nennt_interna(zeile):
            zeilen.append(zeile)
            continue
        geschuetzt = ABKUERZUNGEN.sub(lambda m: m.group(0).replace(" ", "\0"), zeile)
        saetze = [s.replace("\0", " ") for s in re.split(r"(?<=[.!?])\s+", geschuetzt)]
        rest = " ".join(s for s in saetze if not nennt_interna(s))
        if rest.strip():
            zeilen.append(rest)
    bereinigt = "\n".join(zeilen)
    return bereinigt if bereinigt == antwort else ohne_leere_ueberschriften(zeilen)


def ist_ueberschrift(zeile):
    z = zeile.strip()
    return z.startswith("#") or bool(re.fullmatch(r"\*\*[^*]+\*\*:?", z)) or z.endswith(":")


def ohne_leere_ueberschriften(zeilen):
    """Entfernt Überschriften, unter denen nach dem Filtern nichts mehr steht, etwa ein
    "Nächster Schritt" am Ende, dessen einziger Satz einen Werkzeugnamen enthielt."""
    ergebnis = []
    for zeile in reversed(zeilen):
        folgt = next((z for z in reversed(ergebnis) if z.strip()), None)
        leer = folgt is None or ist_ueberschrift(folgt)
        letzte_kurz = folgt is None and len(zeile.strip()) <= 40 and not re.search(r"[.!?)`|]$", zeile.strip())
        if zeile.strip() and leer and (ist_ueberschrift(zeile) or letzte_kurz):
            continue
        ergebnis.append(zeile)
    return "\n".join(reversed(ergebnis)).rstrip()


def datum(iso):
    """2026-11-28 wird 28.11.2026."""
    return ".".join(reversed(iso.split("-"))) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso or "") else iso


def befundliste(uebersicht):
    """Die vollständige Befundliste der Risikoübersicht, geschrieben vom Code statt von der KI. So steht
    jeder Befund genau einmal und unter seiner wahren Priorität."""
    zeilen = [f"### Befundliste vom {datum(uebersicht['stand'])}",
              f"{uebersicht['anzahl_befunde']} Befunde, inaktiv ab {uebersicht['inaktiv_ab_tagen']} Tagen.", ""]
    for prioritaet, titel in (("hoch", "🔴 Hoch"), ("mittel", "🟡 Mittel"), ("prüfen", "🔵 Prüfen")):
        befunde = [b for b in uebersicht["befunde"] if b["prioritaet"] == prioritaet]
        if befunde:
            zeilen += [f"**{titel} ({len(befunde)})**", "", "| Kategorie | Benutzer | Befund |", "|---|---|---|"]
            zeilen += [f"| {b['kategorie']} | {b['name']} ({b['benutzer']}) | {b['detail'].replace('|', '/')} |"
                       for b in befunde] + [""]
    if uebersicht.get("abgesicherte_konflikte"):
        zeilen += ["**✅ Abgesichert, kein offener Befund**", ""]
        zeilen += [f"- {a}" for a in uebersicht["abgesicherte_konflikte"]]
    return "\n".join(zeilen).rstrip()


def tabelle(kopf, zeilen):
    """Eine Markdown-Tabelle. Senkrechte Striche in den Werten würden die Spalten zerreißen."""
    zellen = lambda werte: "| " + " | ".join(str(w).replace("|", "/") for w in werte) + " |"
    return "\n".join([zellen(kopf), "|" + "---|" * len(kopf)] + [zellen(z) for z in zeilen])


def konfliktliste(pruefung):
    """Die SoD-Konflikte eines Benutzers mit Regel, Risiko und Transaktionen, geschrieben vom Code."""
    titel = f"### SoD-Prüfung {pruefung['name']} ({pruefung['benutzer']})"
    if not pruefung["konflikte"]:
        return f"{titel}\nKeine SoD-Konflikte."
    zeilen = [(k["regel_id"], k["risiko"], k["status"], k["beschreibung"],
               ", ".join(k["transaktionen_a"]) + " und " + ", ".join(k["transaktionen_b"]))
              for k in pruefung["konflikte"]]
    anzahl = pruefung["anzahl_konflikte"]
    return (f"{titel}\n{anzahl} Konflikt{'' if anzahl == 1 else 'e'}, davon {pruefung['davon_offen']} offen.\n\n"
            + tabelle(("Regel", "Risiko", "Status", "Konflikt", "Transaktionen"), zeilen))


def optionsliste(vorschlaege):
    """Die Lösungsvorschläge als Tabelle: was ein Entzug löst, was wegfällt und was davon genutzt wird."""
    zeilen = [(("⭐ " if o["rolle_entziehen"] == vorschlaege["empfehlung"] else "") + o["rolle_entziehen"],
               ", ".join(o["loest"]), ", ".join(o["verlorene_transaktionen"]),
               ", ".join(o["davon_genutzt"]) or "keine", o["bewertung"])
              for o in vorschlaege["optionen"]]
    teile = [f"### Lösungsvorschläge {vorschlaege['benutzer']} ({vorschlaege['abteilung']})",
             f"Offene Konflikte: {', '.join(vorschlaege['offene_konflikte'])}.", "",
             tabelle(("Rolle entziehen", "Löst", "Fällt weg", "Davon genutzt", "Bewertung"), zeilen)]
    wechsel = next((o["nutzung_seit_wechsel"] for o in vorschlaege["optionen"] if o.get("nutzung_seit_wechsel")), None)
    teile += ["", "Gezählt wird die Nutzung der letzten 13 Monate" + (
        f", bei Rollen der alten Abteilung erst seit dem Abteilungswechsel am {datum(wechsel)}." if wechsel else ".")]
    if vorschlaege.get("mit_fachbereich_abstimmen"):
        teile += ["", f"Mit dem Fachbereich abstimmen: {vorschlaege['mit_fachbereich_abstimmen']}."]
    teile += ["", f"Alternative: {vorschlaege['alternative']}."]
    return "\n".join(teile)


def einordnung_loesungen(vorschlaege):
    """Feste Einordnung der Lösungsvorschläge. Sie ersetzt den Freitext der KI, die eine genutzte Rolle
    als ungenutzt beschrieben hatte."""
    benutzer = vorschlaege["benutzer"]
    if not vorschlaege["offene_konflikte"]:
        return f"**{benutzer} hat keine offenen SoD-Konflikte.** Es gibt nichts zu lösen."
    empfehlung, abstimmen = vorschlaege["empfehlung"], vorschlaege.get("mit_fachbereich_abstimmen")
    if empfehlung:
        option = next(o for o in vorschlaege["optionen"] if o["rolle_entziehen"] == empfehlung)
        satz = f"**Empfehlung für {benutzer}: Rolle {empfehlung} entziehen.** Das löst {', '.join(option['loest'])}"
        if option.get("nutzung_seit_wechsel") and option["rolle_nicht_benoetigt"]:
            satz += (f", und seit dem Abteilungswechsel am {datum(option['nutzung_seit_wechsel'])} wurde keine "
                     "ihrer Transaktionen genutzt.")
        elif option["rolle_nicht_benoetigt"] or not option["davon_genutzt"]:
            satz += ", und keine in 13 Monaten genutzte Transaktion fällt weg."
        else:
            satz += (f". Dabei fällt die genutzte Transaktion {', '.join(option['davon_genutzt'])} weg: "
                     "vorher mit dem Fachbereich klären, ob sie noch gebraucht wird.")
    elif abstimmen:
        satz = (f"**Für {benutzer} gibt es keine eindeutige Empfehlung.** Die Rolle {abstimmen} wird nicht "
                "benötigt, gehört aber zur Abteilung: den Entzug mit dem Fachbereich abstimmen.")
    else:
        satz = f"**Für {benutzer} löst kein Entzug alle Konflikte, ohne die Tätigkeit zu berühren.**"
    return satz + " Alle Optionen stehen in der Tabelle."


def ungenutzte_liste(ergebnis):
    """Nicht benötigte und teilweise genutzte Rollen als Tabellen, geschrieben vom Code."""
    teile = []
    if ergebnis["nicht_benoetigte_rollen"]:
        teile += ["### Nicht benötigte Rollen", tabelle(
            ("Benutzer", "Rolle", "Nutzung", "Gehört zur Abteilung", "Entzug löst"),
            [(f"{r['name']} ({r['benutzer']})", r["rolle"], r["nutzung"],
              "ja" if r["rolle_gehoert_zur_abteilung"] else "nein", ", ".join(r["loest_sod_konflikte"]) or "keine")
             for r in ergebnis["nicht_benoetigte_rollen"]]), ""]
    if ergebnis["teilweise_genutzte_rollen"]:
        teile += ["### Teilweise genutzte Rollen", tabelle(
            ("Benutzer", "Rolle", "Ungenutzte Transaktionen"),
            [(r["benutzer"], r["rolle"], ", ".join(r["ungenutzte_transaktionen"]))
             for r in ergebnis["teilweise_genutzte_rollen"]]), ""]
    if ergebnis["ausgenommen"]:
        teile += ["**Ausgenommen**", ""] + [f"- {a['benutzer']}: {a['grund']}" for a in ergebnis["ausgenommen"]]
    return "\n".join(teile).rstrip()


def einordnung_ungenutzt(ergebnis):
    """Feste Einordnung der ungenutzten Rechte. Sie ersetzt den Freitext der KI, die eine Abteilungsrolle
    als fremde Rolle beschrieben hatte."""
    n = ergebnis["anzahl_nicht_benoetigt"]
    kopf = "Keine Rolle wird nicht benötigt." if n == 0 else \
        f"{n} Rolle{'' if n == 1 else 'n'} {'wird' if n == 1 else 'werden'} nicht benötigt."
    wechsel = any(r.get("nutzung_seit_wechsel") for r in ergebnis["nicht_benoetigte_rollen"])
    return (f"**{kopf}** Geprüft über {ergebnis['monate_mit_daten']} Monate Nutzungsdaten "
            f"({ergebnis['zeitraum']})" + (", bei Rollen der alten Abteilung erst ab dem Abteilungswechsel"
                                          if wechsel else "") +
            ". Gehört eine Rolle zur Abteilung, den Entzug mit dem Fachbereich abstimmen.")


def einordnung_antrag(ergebnis):
    """Feste Antwort auf einen Änderungswunsch: der Antragstext als Codeblock, sonst nichts. Die Kurzfassung
    mit dem wahren Grund steht schon darüber. Die KI hatte den Antrag weggelassen oder falsch begründet."""
    return f"```text\n{ergebnis['antrag_text']}\n```" if ergebnis.get("antrag_text") else ""


def einordnung_risiko(uebersicht):
    """Feste Einordnung der Risikoübersicht. Sie ersetzt den Freitext der KI, der Befunde vertauscht hatte."""
    n = uebersicht["nach_prioritaet"]
    return (f"**Risikoübersicht: {uebersicht['anzahl_befunde']} Befunde, davon {n.get('hoch', 0)} hoch, "
            f"{n.get('mittel', 0)} mittel und {n.get('prüfen', 0)} zum Prüfen.** Befunde mit „hoch“ und "
            "„mittel“ sind offene Risiken. Befunde mit „prüfen“ sind Hinweise: Ein Mensch beurteilt, ob die "
            "Berechtigung zur Tätigkeit passt. Abgesicherte Konflikte sind durch eine genehmigte Kontrolle "
            "abgedeckt. Ausgeführt wird nichts, die Entscheidung trifft ein Mensch.")


# Werkzeuge, bei denen der Code die Einordnung schreibt, und das Feld, an dem ein gültiges Ergebnis zu erkennen ist
FESTE_EINORDNUNG = {
    "risiko_uebersicht": (einordnung_risiko, "befunde"),
    "loesungsvorschlaege": (einordnung_loesungen, "optionen"),
    "ungenutzte_rechte": (einordnung_ungenutzt, "nicht_benoetigte_rollen"),
    "aenderung_anfragen": (einordnung_antrag, "kurzfassung"),
}


def feste_einordnung(fakten):
    """Die feste Einordnung, wenn in dieser Antwort nur ein solches Werkzeug aufgerufen wurde, sonst None."""
    namen = {n for n, _ in fakten or []}
    if len(namen) != 1 or next(iter(namen)) not in FESTE_EINORDNUNG:
        return None
    name, daten = fakten[0]
    funktion, feld = FESTE_EINORDNUNG[name]
    return funktion(daten) if isinstance(daten, dict) and feld in daten else None


def mit_fakten(antwort, fakten):
    """Schreibt feste Teile aus den Werkzeugergebnissen in die Antwort, statt sie der KI zu überlassen:
    oben die Kurzfassung eines Änderungswunsches und alle Hinweise (etwa "von 6 auf 13 Monate angehoben"),
    unten die Befundliste der Risikoübersicht."""
    oben, unten = [], []
    for name, daten in fakten or []:
        if not isinstance(daten, dict):
            continue
        if name == "aenderung_anfragen" and daten.get("kurzfassung"):
            oben.append(f"**{daten['kurzfassung']}**")
        oben += [f"> ℹ️ Hinweis: {h}" for h in daten.get("hinweise") or []]
        if name == "risiko_uebersicht" and "befunde" in daten:
            unten.append(befundliste(daten))
        if name == "sod_pruefen" and "konflikte" in daten:
            unten.append(konfliktliste(daten))
        if name == "loesungsvorschlaege" and "optionen" in daten and daten["offene_konflikte"]:
            unten.append(optionsliste(daten))
        if name == "ungenutzte_rechte" and "nicht_benoetigte_rollen" in daten:
            unten.append(ungenutzte_liste(daten))
    # Nur ein Werkzeug mit fester Einordnung aufgerufen: Sie ersetzt den Freitext der KI ganz.
    fest = feste_einordnung(fakten)
    antwort = antwort if fest is None else fest
    oben = list(dict.fromkeys(oben))  # dieselbe Zeile nur einmal, auch bei zwei gleichen Aufrufen
    unten = list(dict.fromkeys(unten))
    return "\n\n".join(teil for teil in oben + [antwort.strip()] + unten if teil)


def pruefe_antwort(antwort, aufrufe, fakten=None):
    """Feste Sicherungen nach der KI. Erstens: Sätze mit Werkzeugnamen werden entfernt. Zweitens:
    Spricht eine Antwort vom Entsperren, ohne dass aenderung_anfragen die Zulässigkeit geprüft hat,
    wird ein Warnhinweis angehängt. Drittens: Kurzfassung, Hinweise und Befundliste schreibt der Code
    aus den Werkzeugergebnissen selbst. Die KI kann nichts davon umgehen."""
    bereinigt = ohne_werkzeugnamen(antwort)
    namen = {n for n, _ in fakten or []}
    antrag_hinweis = (("aenderung_anfragen" in antwort and "aenderung_anfragen" not in bereinigt)
                      or bool(namen & {"loesungsvorschlaege", "ungenutzte_rechte"})) \
        and "aenderung_anfragen" not in namen  # ein Antrag ist dann schon da
    antwort = mit_fakten(bereinigt, fakten) if fakten else bereinigt
    if antrag_hinweis:
        antwort += HINWEIS_ANTRAG  # ganz am Ende, unter einer Tabelle
    geprueft = any(a.startswith("aenderung_anfragen") for a in aufrufe)
    if "entsperr" in bereinigt.lower() and not geprueft:
        antwort += WARNUNG_ENTSPERREN
    return antwort


def frage_stellen(verlauf, frage, modus="cloud"):
    """Einstiegspunkt für Terminal und Browser. modus: "cloud" oder "lokal"."""
    funktion = _frage_lokal if modus == "lokal" else _frage
    antwort, aufrufe, fakten = anyio.run(funktion, verlauf, frage)
    return pruefe_antwort(antwort, aufrufe, fakten), aufrufe
