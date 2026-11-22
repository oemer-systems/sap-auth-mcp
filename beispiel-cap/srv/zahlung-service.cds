// Beispiel-Service für den SoD-Linter.
// Er ist absichtlich FEHLERHAFT gebaut, damit der Linter etwas findet.

@mcp
service ZahlungService {

  // Regel 1: schreibbare Entität ohne jeden Schutz
  entity Lieferanten {
    key ID        : String(10);
        name      : String(80);
        iban      : String(34);
  }

  // Regel 2: kritische Aktion für jeden angemeldeten Benutzer
  @requires: 'authenticated-user'
  action zahlungFreigeben(zahlungID : String) returns String;

  // Regel 3: eine Rolle darf Lieferanten anlegen UND Zahlungen ausführen
  @requires: 'Kreditorenbuchhaltung'
  action lieferantAnlegen(name : String, iban : String) returns String;

  @requires: 'Kreditorenbuchhaltung'
  action zahlungAusfuehren(zahlungID : String) returns String;

  // Sauber: eigene Rolle nur zum Lesen
  @restrict: [{ grant: 'READ', to: 'Pruefer' }]
  entity Zahlungen {
    key ID     : String(10);
        betrag : Decimal(15,2);
  }
}