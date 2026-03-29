# Frank Bot 🤖

**RAG-powered AI assistant for any business. Drop in your documents. Ask Frank anything.**

Frank is a production-ready AI chatbot platform built on:
- **ChromaDB** — local vector store, no external embedding API needed
- **Claude (Anthropic)** — LLM backend
- **Flask** — lightweight Python web server
- **Sentence Transformers** — local embeddings (all-MiniLM-L6-v2)

The client supplies their own Anthropic API key. Their documents never leave their server.

---

## How It Works

```
Client uploads docs (PDF/DOCX/TXT)
         ↓
Frank chunks + embeds them into ChromaDB (local, self-hosted)
         ↓
User asks a question in the chat UI
         ↓
RAG engine retrieves top-k relevant chunks
         ↓
Claude answers using only the retrieved context
         ↓
Grounded, document-based response — no hallucination
```

---

## Architecture

```
frank-bot/
├── run_bot.py              # Universal entrypoint (systemd / Docker)
├── shared/
│   ├── frank_bot.py        # FrankBot class — Flask app, all endpoints, chat UI
│   ├── rag_engine.py       # ChromaDB RAG store — chunk, embed, retrieve, build context
│   └── bot_config.py       # BotConfig schema — tier limits, add-ons, env loading
├── clients/
│   └── vertical_prompts.py # 12 industry-tuned prompt personalities (Layer 1/2/3 system)
├── examples/
│   └── demo_config.json    # Example bot config
└── docs/
    └── SETUP.md            # Deployment guide
```

---

## Vertical Personalities

Frank ships with tuned system prompts for 12 industry verticals:

| Vertical | Description |
|---|---|
| 💼 HR & Workplace | Any industry — policy, entitlements, Fair Work |
| ⛏️ Resources & Mining | FIFO, WHS, EBA, site safety |
| 🏗️ Construction | SWMS, white card, subcontractors, EBA |
| 🏥 Aged Care | SIRS, Quality Standards, SCHADS Award |
| 🏛️ Local Government | Governance, LG Award, PID, procurement |
| ⚕️ Healthcare | AHPRA, NSQHS, scope of practice |
| 🎓 Education | Child Safe, mandatory reporting, ASQA |
| 📊 Finance & Professional | AFS licence, AML/CTF, AFCA |
| 📋 Project Management | Document retrieval, scope, risk, workstreams |
| 🦺 Safety & WHS | Notifiable incidents, permits, PCBU duties |
| 🏗️ Building & Architecture | NCC, DA/CC, drawing interpretation |
| 🔧 Custom | Fully custom — define your own personality |

Each vertical uses a layered prompt architecture:
- **Layer 1** — Universal security, confidentiality, distress protocols (always on)
- **Layer 2** — Vertical personality and domain knowledge (swappable)
- **Layer 3** — Client-specific custom instructions (additive only)

---

## Quick Start

### 1. Install dependencies

```bash
pip install flask anthropic chromadb pdfplumber pypdf python-docx sentence-transformers
```

### 2. Configure your bot

```bash
cp examples/demo_config.json /opt/frankbot/config.json
# Edit config.json — set company_name, vertical, bot_id
```

### 3. Set your API key

```bash
export LLM_API_KEY=sk-ant-...
```

### 4. Run

```bash
python run_bot.py
# → http://localhost:8080
```

---

## Configuration

All config lives in `config.json` (or environment variables).

```json
{
  "bot_id": "acme-hr",
  "bot_name": "Frank",
  "company_name": "Acme Resources",
  "vertical": "hr_resources",
  "tier": "professional",
  "llm_provider": "anthropic",
  "llm_model": "claude-haiku-4-5",
  "rag_enabled": true,
  "rag_top_k": 15,
  "max_docs": 30,
  "max_doc_size_mb": 10,
  "forms_enabled": true,
  "port": 8080
}
```

### Tiers

| Tier | Max Docs | Max Doc Size | Forms |
|---|---|---|---|
| Starter | 10 | 5 MB | 1 |
| Professional | 30 | 10 MB | 3 |
| Enterprise | 100 | 20 MB | 5 |

---

## RAG Engine

The core of Frank is `shared/rag_engine.py`:

```python
from shared.rag_engine import FrankRAGStore

store = FrankRAGStore(bot_id="acme-hr", persist_dir="/opt/frankbot/chroma")

# Index a document
store.index_document("Leave Policy", leave_policy_text)

# Retrieve relevant chunks
chunks = store.retrieve("how much annual leave do I get?", top_k=5)

# Build context string for injection into system prompt
context = store.build_context("how much annual leave do I get?")
```

### Key features

- **Local embeddings** — sentence-transformers, no external API
- **Query expansion** — Claude Haiku generates 4 alternative phrasings, merged results
- **Source boost** — named documents in queries guaranteed to be retrieved
- **Clean re-index** — re-uploading a document replaces all its chunks atomically
- **Overlap chunking** — 400-word chunks with 80-word overlap, paragraph-aware

---

## Prompt Architecture

```python
from clients.vertical_prompts import get_vertical_personality, get_layer1_always

# Build system prompt
system = f"""
{get_vertical_personality("hr_resources")}

## Your organisation
Company: Acme Resources
Custom instructions: Always refer to the EBA for pay questions.

{get_layer1_always()}

## Context from documents:
{context}
"""
```

Layer 1 is injected **last** — it always takes precedence over custom instructions.

---

## Deployment

Frank is designed to run as a systemd service on a Linux VM:

```ini
[Unit]
Description=Frank Bot — Acme Resources
After=network.target

[Service]
WorkingDirectory=/opt/frankbot/app
ExecStart=/opt/frankbot/venv/bin/python3 run_bot.py
Restart=always
EnvironmentFile=/opt/frankbot/.env

[Install]
WantedBy=multi-user.target
```

One droplet per client. Each bot is isolated — documents, ChromaDB store, and API keys never shared.

---

## Built by

[LevelUp](https://lvlup.au) — AI systems for Australian workplaces.

Frank is deployed across HR, construction, mining, aged care, and local government organisations.

---

## Licence

MIT
