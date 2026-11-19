import json
import os
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
    "Erkläre Ergebnisse verständlich und schlage Lösungen vor. Entscheidungen trifft immer ein Mensch. "
    "Antworte auf Deutsch."
)
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
    return text, aufrufe


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
                antwort = await ollama.chat(
                    model=LOKALES_MODELL,
                    messages=[{"role": "system", "content": SYSTEM}] + nachrichten,
                    tools=tools,
                    think=False,
                    options={"num_ctx": 16384},
                )
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
    return antwort.message.content or "", aufrufe


def frage_stellen(verlauf, frage, modus="cloud"):
    """Einstiegspunkt für Terminal und Browser. modus: "cloud" oder "lokal"."""
    funktion = _frage_lokal if modus == "lokal" else _frage
    return anyio.run(funktion, verlauf, frage)
