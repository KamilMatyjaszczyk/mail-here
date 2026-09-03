# Mailklient

Mailklient skal bli en lett og modulær desktop e-postklient for Linux, skrevet i Python med PySide6.

Prosjektet er under utvikling. Foreløpig finnes grunnstruktur, et minimalt vindu,
testoppsett, et første SQLite-skjema og et lite repository-lag for lokal cache
av kontoer, mapper og meldingsmetadata, et enkelt service-lag og en GUI-layout
som viser lokale demo-data via service-laget. Det finnes også en enkel lokal
konto-dialog med passord til keyring, servermetadata, kontoliste, lokal sletting
av kontoer, rene IMAP/SMTP-konfigmodeller, tilkoblingstester og første IMAP-
headerhenting til lokal cache. GUI-et har knapper for å teste IMAP/SMTP og
synkronisere valgt konto. IMAP/SMTP-oppsett støtter SSL og STARTTLS, og IMAP-
headerhenting bruker UID og flags for mer stabil lokal cache.

## Utviklingsmiljø

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Starte programmet

```bash
mailklient
```

Alternativt:

```bash
python -m mailklient.main
```

## Kjøre tester

```bash
pytest
```
