from agent import frage_stellen

verlauf = []
print("SAP-Berechtigungsassistent. 'exit' zum Beenden.\n")

while True:
    frage = input("Du: ").strip()
    if frage.lower() in ("exit", "quit"):
        break
    if not frage:
        continue
    antwort, aufrufe = frage_stellen(verlauf, frage)
    for aufruf in aufrufe:
        print(f"  [Werkzeug] {aufruf}")
    print(f"\nKI: {antwort}\n")