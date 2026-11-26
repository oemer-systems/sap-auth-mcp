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
    "Du kannst nichts ändern: Sage klar, dass nichts ausgeführt wurde, und gib den vorbereiteten Antrag aus. "
    "Erkläre Ergebnisse verständlich. Entscheidungen trifft immer ein Mensch. "
    "Antworte auf Deutsch."
)
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


async def _frage(verlauf, frage):
    """Cloud-Modus: beantwortet eine Frage mit Claude und den Werkzeugen des MCP-Servers."""
    nachrichten = verlauf + [{"role": "user", "content": frage}]
    aufrufe = []

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
                return "API-Key fehlt oder ist ungültig. Bitte ANTHROPIC_API_KEY setzen.", aufrufe
            except AnthropicError as fehler:
                return f"Die KI-Anfrage ist fehlgeschlagen: {fehler}", aufrufe

            nachrichten.append({"role": "assistant", "content": antwort.content})
            if antwort.stop_reason != "tool_use":
                break

            ergebnisse = []
            for block in antwort.content:
                if block.type == "tool_use":
                    aufrufe.append(f"{block.name}({json.dumps(block.input, ensure_ascii=False)})")
                    result = await mcp.call_tool(block.name, block.input)
                    ergebnisse.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": ergebnis_als_text(result),
                        "is_error": bool(result.is_error),
                    })
            nachrichten.append({"role": "user", "content": ergebnisse})

    verlauf[:] = nachrichten
    text = "\n".join(b.text for b in antwort.content if b.type == "text")
    return text or LEERE_ANTWORT, aufrufe


async def _frage_lokal(verlauf, frage):
    """Lokaler Modus: dieselbe Schleife, aber mit einem Modell in Ollama auf dem eigenen Rechner."""
    nachrichten = verlauf + [{"role": "user", "content": frage}]
    aufrufe = []

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
                for denken in (False, True):
                    antwort = await ollama.chat(
                        model=LOKALES_MODELL,
                        messages=[{"role": "system", "content": SYSTEM}] + nachrichten,
                        tools=tools,
                        think=denken,
                        options={"num_ctx": 16384, "temperature": 0},  # 0: gleiche Frage, gleiche Antwort
                    )
                    braucht_fakten = not aufrufe and nennt_benutzer(frage)
                    if antwort.message.tool_calls or (antwort.message.content and not braucht_fakten):
                        break
            except ConnectionError:
                return "Ollama ist nicht erreichbar. Läuft das Programm Ollama?", aufrufe
            except ResponseError as fehler:
                return f"Ollama meldet einen Fehler: {fehler.error}", aufrufe

            nachrichten.append(antwort.message)
            if not antwort.message.tool_calls:
                break

            for aufruf in antwort.message.tool_calls:
                name, argumente = aufruf.function.name, dict(aufruf.function.arguments)
                aufrufe.append(f"{name}({json.dumps(argumente, ensure_ascii=False)})")
                result = await mcp.call_tool(name, argumente)
                nachrichten.append({"role": "tool", "tool_name": name, "content": ergebnis_als_text(result)})

    verlauf[:] = nachrichten
    return antwort.message.content or LEERE_ANTWORT, aufrufe


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


def pruefe_antwort(antwort, aufrufe):
    """Feste Sicherung nach der KI: Spricht eine Antwort vom Entsperren, ohne dass aenderung_anfragen
    die Zulässigkeit geprüft hat, wird ein Warnhinweis angehängt. Die KI kann das nicht umgehen."""
    geprueft = any(a.startswith("aenderung_anfragen") for a in aufrufe)
    if "entsperr" in antwort.lower() and not geprueft:
        return antwort + WARNUNG_ENTSPERREN
    return antwort


def frage_stellen(verlauf, frage, modus="cloud"):
    """Einstiegspunkt für Terminal und Browser. modus: "cloud" oder "lokal"."""
    funktion = _frage_lokal if modus == "lokal" else _frage
    antwort, aufrufe = anyio.run(funktion, verlauf, frage)
    return pruefe_antwort(antwort, aufrufe), aufrufe
