# signwell-invoicer

A tool for sending PDF invoices to clients for e-signature via SignWell.
Available in two modes: **GUI** (Windows `.exe`) and **CLI** (terminal).

Reads a PDF → matches it to a client in `clients.yaml` → creates a document in SignWell
and sends a signature request → tracks state in a local SQLite database.

## What it does

- Reads a PDF invoice from the filesystem.
- Extracts the client key from the filename (e.g. `2025-04_<client-key>.pdf`).
- Looks up client data (email, name, CC addresses) in `clients.yaml`.
- Creates a SignWell document with `with_signature_page: true`
  (SignWell appends a signature page to the end of the PDF — the original is not modified).
- Creates the document as a draft first, then sends it in a separate API call —
  this allows recovery from crashes without creating duplicate documents.
- Persists state in a local SQLite database (`sent.sqlite`).

## Installation

Requires Python 3.11+.

```bash
git clone <repo> signwell-invoicer
cd signwell-invoicer
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Or with `uv`:

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
```

## Configuration

### 1. SignWell API key

1. Register at <https://www.signwell.com/> (free tier — 25 docs/month,
   a card is required for anti-abuse purposes).
2. Go to Settings → API → Create API Key.
3. Copy the key.

### 2. `.env`

```bash
cp .env.example .env
```

Edit the file:

```env
SIGNWELL_API_KEY=sw_api_...
INVOICER_DEFAULT_MODE=test          # keep this until you've verified everything
INVOICER_SENDER_NAME="Your Name"
INVOICER_SENDER_EMAIL="you@domain.com"
```

### 3. `clients.yaml`

```bash
cp clients.example.yaml clients.yaml
```

Fill in your clients:

```yaml
acme-corp:
  name: Ivan Petrenko
  email: ivan@acme.com
  company: Acme Corp LLC
  cc:
    - accounting@acme.com
  language: en
```

The client key (`acme-corp`) must be a slug: lowercase letters, digits, hyphens.
This key must appear in the PDF filename.

### 4. Verify setup

```bash
invoicer check
```

Expected output:

```
✓ Loaded 2 client(s): acme-corp, globex

Default mode: test
SignWell API: verifying credentials...
✓ Authenticated as Your Name
```

## GUI (recommended for Windows)

Run in development mode:

```bash
python run_gui.py
```

Build a standalone `.exe` (Windows only):

```bat
build_exe.bat
```

Place `.env` and `clients.yaml` next to the `.exe` before distributing.

The GUI lets you:
- select a folder of PDF files — client matching happens automatically,
- check the invoices you want to send and submit them in one click,
- see each invoice's status update in real time.

## CLI

### Filename convention

The client key must appear anywhere in the filename (delimiters: `_`, `-`, `.`, space).
All of the following match the `acme-corp` client:

```
2025-04_acme-corp.pdf
2025-04_acme-corp_services.pdf
invoice_acme-corp_final.pdf
ACME-CORP.pdf
```

If the key cannot be inferred, pass `--client` explicitly.

### Commands

**Dry-run — inspect the payload without calling the API:**

```bash
invoicer send invoices/outbox/2025-04_acme-corp.pdf --dry-run
```

**Test mode — free, not legally binding:**

```bash
invoicer send invoices/outbox/2025-04_acme-corp.pdf --test
# or without a flag if INVOICER_DEFAULT_MODE=test
invoicer send invoices/outbox/2025-04_acme-corp.pdf
```

**Production — sends a real signature request to the client:**

```bash
invoicer send invoices/outbox/2025-04_acme-corp.pdf --prod
# the CLI will ask for confirmation before sending
```

**Explicit client override (if the filename does not contain the key):**

```bash
invoicer send some_random_name.pdf --client acme-corp --test
```

**Check document status:**

```bash
invoicer status <document_id>
```

**List sent invoices:**

```bash
invoicer list                    # pending only (draft + sent)
invoicer list --all              # all records, including completed
invoicer list --all --limit 100
```

## Duplicate protection

The tool hashes the PDF content and checks whether the same file was already sent
in the same mode (test/prod) before making any API calls. If it was, the send is skipped.

Override with `--force`.

## Legal notes

- **Test mode** (`test_mode: true`) — SignWell marks documents as "not legally binding".
  Use this for all testing.
- **Production** — compliant with ESIGN (US), UETA, eIDAS (EU), and HIPAA.

## First real run — checklist

1. Run `invoicer check` — verify auth passes and `clients.yaml` is valid.
2. Run `invoicer send <invoice>.pdf --test` — still in test mode;
   you'll receive a copy via CC or can open the link in the SignWell dashboard.
3. Check the email that arrives — subject, body, signature page.
4. Then run `--prod` on a real invoice. The CLI will ask for confirmation.

## Architecture

```
invoicer/
├── config.py     — Settings (env) + ClientsRegistry (yaml) + filename inference
├── models.py     — Pydantic models for SignWell API payloads
├── signwell.py   — thin httpx wrapper around the API
├── tracking.py   — SQLite tracking (draft → sent → completed/declined)
├── sender.py     — business logic: PDF → draft → send
├── cli.py        — Typer CLI
├── gui.py        — CustomTkinter GUI
└── __main__.py   — python -m invoicer
```

## Common errors

- **`Host not in allowlist`** — your ISP or corporate VPN is blocking signwell.com.
  Try from a different network.
- **`value is not a valid email address: ... reserved name`** — you're using a
  `.test` or `.example` TLD in `clients.yaml`. Email-validator enforces RFC 6761.
  Use a real domain.
- **`Could not infer client from filename`** — the filename contains no key from
  `clients.yaml`. Either rename the file or pass `--client`.
- **`422 Unprocessable Entity` from SignWell** — most often the PDF is
  password-protected, over 50 MB, or the email address is invalid.
