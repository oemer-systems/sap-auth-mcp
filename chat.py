import json
import sys

import anyio
from anthropic import AsyncAnthropic
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

MODELL = "claude-haiku-5-5"
SYSTEM = (
    "Du bist ein Assistent für SAP-Berechtigungen. Nutze für Fakten ausschließlich die Werkzeuge. "
    "Ob ein SoD-Konflikt vorliegt, entscheidet allein das Werkzeug sod_pruefen. "
    "Erkläre Ergebnisse verständlich und schlage Lösungen vor. Entscheidungen trifft immer ein Mensch."
)
SERVER = StdioServerParameters(command=sys.executable, args=["server.py"])


async def main():
    ki = AsyncAnthropic()
    async with Client(SERVER) as mcp:
        tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
            for t in (await mcp.list_tools()).tools
        ]
        print("Verbunden. Werkzeuge:", ", ".join(t["name"] for t in tools))
        print("Frage eingeben, 'exit' zum Beenden.\n")

        verlauf = []
        while True:
            frage = await anyio.to_thread.run_sync(input, "Du: ")
            if frage.strip().lower() in ("exit", "quit"):
                break
            verlauf.append({"role": "user", "content": frage})

            while True:
                antwort = await ki.messages.create(
                    model=MODELL, max_tokens=2000, system=SYSTEM,
                    tools=tools, messages=verlauf,
                )
                verlauf.append({"role": "assistant", "content": antwort.content})
                if antwort.stop_reason != "tool_use":
                    break

                ergebnisse = []
                for block in antwort.content:
                    if block.type == "tool_use":
                        print(f"  [Werkzeug] {block.name}({block.input})")
                        result = await mcp.call_tool(block.name, block.input)
                        text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
                        ergebnisse.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": text or json.dumps(result.structured_content, ensure_ascii=False),
                            "is_error": bool(result.is_error),
                        })
                verlauf.append({"role": "user", "content": ergebnisse})

            for block in antwort.content:
                if block.type == "text":
                    print(f"\nKI: {block.text}\n")


if __name__ == "__main__":
    anyio.run(main)