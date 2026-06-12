# Tutoriums-Vorstellungs-Planer

Werkzeug zur Verwaltung der Hausaufgaben-Vorstellungen im Tutorium: Filtern der
Studierenden, die vorstellen müssen, Verfügbarkeitserfassung per Magic Link,
Slot-Zuteilung, Mailversand und Anwesenheits-Tracking. No-Shows wandern
automatisch in die nächste Runde.

## Architektur

Strikte Schichtung:

- `domain/`: reine Logik, kein I/O (`models`, `filtering`, `availability`, `scheduling`, `tracking`, `results_submission`). mypy strict.
- `adapters/`: alles I/O (`ingest_excel`, `ingest_api`, `sparky`, `mail`, `db`, `submit_results`).
- `web/`: FastAPI: `public` (Magic-Link-Verfügbarkeit), `tutor` (Anwesenheit), `admin` (Kuration, Solver, Export, stu-mgmt-Sync).
- `security.py`: Token-Signatur, Admin-Auth, CSRF. `config.py`: Settings (pydantic-settings, fail-fast).
- `cli.py`: `import`, `plan`, `send`, `remind`, `sync`, `purge`.

## Setup

Voraussetzung: Python ≥ 3.12.

```bash
make install        # legt .venv an und installiert das Paket inkl. dev-Extras
```

Unter Windows ohne `make`:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

### Konfiguration

Die App liest `.env` (pydantic-settings) und **startet nicht** ohne gültigen
`SECRET_KEY` (≥ 32 Zeichen). Kopiere die Vorlage und fülle die Pflichtwerte:

```bash
cp .env.example .env
```

Wichtige Variablen:

| Variable | Bedeutung |
|---|---|
| `SECRET_KEY` | Signatur der Magic Links. Pflicht, ≥ 32 Zeichen. |
| `DATABASE_URL` | z. B. `sqlite:///./planer.db` (lokal) oder `sqlite:////data/planer.db` (Container). |
| `PUBLIC_BASE_URL` | Basis-URL für die in Mails versendeten Links. |
| `INDIVIDUAL_THRESHOLD` | Punktschwelle der INDIVIDUAL-Regel (Default 3). |
| `TOKEN_MAX_AGE_HOURS` | Gültigkeit der Magic Links (Default 168). |
| `ADMIN_AUTH_MODE` | `forward_auth` (Default, **keine** App-Prüfung) oder `password`. |
| `ADMIN_FORWARD_AUTH_HEADER` | Optional, nur Forward-Auth: Header wird **nur fürs Logging** der Admin-Identität gelesen, nicht erzwungen. |
| `ADMIN_PASSWORD_HASH` | Argon2-Hash, **nur** bei `ADMIN_AUTH_MODE=password` erforderlich. |
| `COOKIE_SECURE` | `true` in Produktion (HTTPS); `false` für lokales HTTP-Dev (sonst wird das CSRF-Cookie nicht gesendet). |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` (Default `INFO`). |
| `LOG_JSON` | `true` → ein JSON-Objekt pro Zeile (für Log-Aggregation); `false` → lesbarer Text. |
| `SMTP_*`, `MAIL_FROM`, `MAIL_REPLY_TO` | Mailversand über STARTTLS. |
| `STUMGMT_*`, `SPARKY_*` | stu-mgmt-API + Sparkyservice-Auth. |
| `STUMGMT_PRESENTATION_ASSIGNMENT_ID` / `_NAME` | Ziel-Hausaufgabe für `planer sync`: per ID setzen, oder leer lassen und den exakten Namen angeben. |
| `PRESENTATION_PASS_POINTS` | Beim Import gilt ein Studierender nur als „bereits geprüft" (und fällt aus der Fälligkeit), wenn sein Vorstellungs-Assessment **freigegeben** (kein Entwurf) ist **und** mindestens so viele Punkte hat. Entwürfe / weniger Punkte → bleibt fällig. Default 1. |

## Entwicklung

```bash
make dev      # uvicorn auf 127.0.0.1:8000 mit --reload
make check    # lint + typecheck + test (muss grün sein vor jedem Meilenstein)
make test     # pytest mit Coverage
make format   # ruff auto-fix
```

Unter Windows direkt:

```powershell
.venv\Scripts\uvicorn planer.web.app:app --reload --host 127.0.0.1 --port 8000
```

- Health: <http://127.0.0.1:8000/health>
- API-Doku: <http://127.0.0.1:8000/docs>

### Admin-Bereich erreichen

Im Default-Modus (`forward_auth`) macht die App **keine** eigene Auth-Prüfung:
`/admin` ist direkt erreichbar. Die Absicherung liegt beim Reverse Proxy bzw.
Netzwerk. In Produktion läuft die App hinter Nginx/Authentik und wird **nicht**
direkt öffentlich exponiert. Ein vorhandener Proxy-Identity-Header wird nur fürs
Logging gelesen.

Wer App-seitige Auth will, schaltet den Passwort-Modus ein. Argon2-Hash erzeugen:

```bash
.venv/bin/python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('dein-passwort'))"
```

und `ADMIN_AUTH_MODE=password` + `ADMIN_PASSWORD_HASH=...` setzen (dann HTTP Basic Auth).

## CLI

```bash
planer import --excel export.xlsx   # Teilnehmer aus Excel-Export importieren
planer import                       # ... oder aus der stu-mgmt-API (STUMGMT_*/SPARKY_*)
planer plan <round>                 # Solver-Lauf für eine Runde
planer send availability <round>    # Verfügbarkeits-Mails (Magic Link)
planer send assignment  <round>     # Zuteilungs-Mails
planer remind <round>               # Erinnerung an Nicht-Antwortende
planer sync <round>                 # Vorstellungs-Ergebnisse als Assessments ins stu-mgmt schreiben
planer purge --round <round> --yes  # Runde restlos löschen (Semesterende)
```

`send`/`remind` sind idempotent (kein Doppelversand dank `EmailLog`); `--force`
erzwingt erneuten Versand.

`planer sync <round>` schreibt für jede vorgestellte Gruppe pro Mitglied ein
Assessment (volle Punktzahl, als Entwurf) auf die konfigurierte Hausaufgabe ins
stu-mgmt zurück, damit deren verpflichtende Pass-Regel greift. Idempotent pro
Nutzer: bereits Bewertete werden übersprungen, `--force` aktualisiert sie (PATCH),
`--dry-run` zeigt nur an, was geschrieben würde. Der API-Account braucht dafür
Schreibrechte (Lecturer/Tutor) im Kurs; die Assessments entstehen als Entwurf und
müssen im stu-mgmt-UI final freigegeben werden.

## Workflow

1. `planer import --excel export.xlsx` (oder `planer import` für die API).
2. Runde im Admin-Dashboard anlegen, Termine (Slots) eintragen.
3. Kuratieren: „Alle abwählen" und einzelne Gruppen per „Ja" + Speichern zulassen.
4. **Modus wählen** (pro Runde):
   - *Verfügbarkeit abfragen* (Default): `planer send availability <round>` → Studierende haken alle passenden Termine an → **Solver** (`planer plan <round>` oder Button).
   - *Termin bestätigen*: je Gruppe einen Slot pinnen (Spalte „Pinned Slot") → `planer send availability <round>` → Studierende sehen nur ihren festen Termin mit Ja/Nein.
5. `planer send assignment <round>` → Studierende erhalten ihren Termin.
6. Tutor:in markiert per slot-spezifischem Magic Link `vorgestellt` / `nicht erschienen`.
7. CSV-Export aus dem Dashboard. No-Shows wandern automatisch in die nächste Runde.
8. Optional: `planer sync <round>` (oder Button „Ergebnisse an stu-mgmt übertragen") schreibt die Vorstellungen als Assessments zurück. Vorher mit `--dry-run` bzw. „Vorschau" prüfen.

## Logging

Zentral konfiguriert in [logging_setup.py](src/planer/logging_setup.py); Level und
Format kommen aus der Env (`LOG_LEVEL`, `LOG_JSON`). Server (FastAPI-Lifespan) und
CLI (`main()`) richten das Logging beim Start ein.

- **Strukturiert:** jede Log-Zeile trägt Kontextfelder (`student_id`, `group_id`,
  `round_id`, `slot_id`, `admin`, …). Im Textformat als `key=value` angehängt, mit
  `LOG_JSON=true` als ein JSON-Objekt pro Zeile (ideal für Loki/ELK).
- **PII-frei:** geloggt werden nur IDs, **keine** E-Mail-Adressen oder
  Matrikelnummern. Mail-Fehler landen nur als Exception-*Typ* im Log.
- **Token-Redaction:** Die Access-Log-Middleware ersetzt Magic-Link-Tokens im Pfad
  durch `<token>` (`/availability/<token>`, `/tutor/<token>`). Tokens sind
  Credentials und dürfen nie im Klartext im Log stehen.

Was geloggt wird: HTTP-Requests (Methode, redigierter Pfad, Status, Dauer),
Admin-Aktionen (Runde/Slot/Kuration/Export mit Admin-Kennung), Solver-Läufe
(Zuteilung + nicht platzierbare Gruppen), Mailversand (Summen + Einzel-Fehler als
Warnung), Verfügbarkeits- und Anwesenheits-Eingaben, abgelehnte CSRF/Tokens.

> **Wichtig:** uvicorn mit `--no-access-log` starten (so im `make dev`-Target),
> sonst loggt uvicorns eigenes Access-Log die **vollständigen URLs inkl. Tokens**.
> Unser redigiertes Access-Log ersetzt es.

```powershell
# Dev (Text, INFO, ohne uvicorn-Access-Log):
.venv\Scripts\uvicorn planer.web.app:app --reload --host 127.0.0.1 --port 8000 --no-access-log
# Prod-artig: LOG_JSON=true + LOG_LEVEL=INFO in .env setzen.
```

## Sicherheit (Kurzfassung)

- Secrets nur aus Env; `.env` ist gitignored; fail-fast bei fehlendem/kurzem `SECRET_KEY`.
- Magic-Link-Tokens signiert + zeitlich begrenzt, **ohne PII** im Payload; Manipulation → 400.
- Admin-Schreibformulare CSRF-geschützt (Double-Submit-Cookie, `HttpOnly`, `SameSite=Strict`).
- Rate-Limiting (slowapi) auf den öffentlichen Endpunkten pro IP.
- Nur ORM/parametrisierte Queries; Jinja2-Autoescape an.
- Logs ohne PII (nur IDs); Magic-Link-Tokens werden aus Access-Logs redigiert (siehe Logging).
- In Produktion `ENABLE_DOCS=false` setzen → `/docs`, `/redoc`, `/openapi.json` aus (sonst werden u.a. die Admin-Routen-Pfade öffentlich aufgelistet). Lokal sind die Docs standardmäßig an.
- App hinter Reverse Proxy (TLS-Terminierung am Nginx); im Container nicht direkt öffentlich exponieren.

## Tests

```bash
make check
```

Testpyramide: viele Unit-Tests (Domäne, Ziel 100 %), einige Integrationstests
(Adapter, In-Memory-SQLite, gemocktes httpx), wenige Web-Tests (TestClient).
