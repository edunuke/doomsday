# Stat-Arb Presentation Deck — Zero-Dependency Node.js server

A minimal Node.js static file server (built-in `http`/`fs` modules only, **no npm packages**).
It serves self-contained HTML decks, reports, and factsheets. All libraries (Bootstrap,
KaTeX, Mermaid) are loaded via CDN directly in the HTML — no local assets needed.

## Layout

```
docs/
└── deck/
    ├── server.js              # Zero-dependency Node.js static server
    ├── package.json           # Metadata only (no dependencies)
    ├── static/                # All HTML decks, reports, and data
    │   ├── presentation_v1.html
    │   ├── presentation_v2.html
    │   ├── presentation_v3.html
    │   ├── distress_report.html
    │   ├── report_v3.html
    │   ├── report_v3.2.html
    │   ├── statarb_checklist.html
    │   ├── mdd_report.html
    │   ├── mdd_data.json
    │   └── ...
    └── README.md
```

## Run

No install required — Node.js only:

```bash
node server.js
```

Open http://localhost:8080.

## Endpoints

| Route          | Content                                      |
|----------------|----------------------------------------------|
| `/`            | v3 presentation deck (default)               |
| `/v1`          | v1 presentation deck                         |
| `/v2`          | v2 presentation deck                         |
| `/v3`          | v3 presentation deck                         |
| `/checklist`   | Risk & deployment audit                      |
| `/report/v3`   | v3.1 backtest run report                     |
| `/report/v3.2` | v3.2 backtest run report                     |
| `/distress/v3` | 18-window stress test results                |
| `/mdd/v3`      | Minimum Disclosure Document factsheet        |
| `/health`      | Liveness probe                               |
