import json
import sys
from pathlib import Path

import anyio
from anthropic import AnthropicError, AsyncAnthropic, AuthenticationError
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

MODELL = "claude-haiku-5-5"
SYSTEM = (
    "Du bist ein Assistent für SAP-Berechtigungen. Nutze für Fakten ausschließlich die Werkzeuge. "
    "Ob ein SoD-Konflikt vorliegt, entscheidet allein das Werkzeug sod_pruefen. "
    "Erkläre Ergebnisse verständlich und schlage Lösungen vor. Entscheidungen trifft immer ein Mensch."
)
SERVER = StdioServerParameters(
    command=sys.executable,
    args=[str(Path(__file__).parent / "server.py")],
)


async def _frage(verlauf, frage):
    """Beantwortet eine Frage mit Werkzeugen. Gibt Antworttext und Werkzeugaufrufe zurück."""
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
                    model=MODELL, max_tokens=2000, system=SYSTEM,
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
                    text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
                    ergebnisse.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text or json.dumps(result.structured_content, ensure_ascii=False),
                        "is_error": bool(result.is_error),
                    })
            nachrichten.append({"role": "user", "content": ergebnisse})

    verlauf[:] = nachrichten
    text = "\n".join(b.text for b in antwort.content if b.type == "text")
    return text, aufrufe


def frage_stellen(verlauf, frage):
    """Einstiegspunkt für Terminal und Browser."""
    return anyio.run(_frage, verlauf, frage)