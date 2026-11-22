# TrueNorth: how it's built

![TrueNorth architecture](diagram.png)

```mermaid
%%{init: {"theme":"dark","themeVariables":{"edgeLabelBackground":"#163A2E","lineColor":"#CBD5E1"}, "flowchart":{"htmlLabels":true}}}%%
flowchart LR
  subgraph IN["Inputs"]
    T["Tender text"]
    CV["CV: PDF / Word / text"]
    B["bid.json or uploaded CVs"]
  end
  CV --> X["extract text<br/>(pypdf, python-docx)"] --> RR{"rules reader<br/>(regex, NHAI layout)"}
  RR -- "read" --> D["dates.py<br/>deterministic dates"]
  RR -- "unknown layout" --> LLM["Claude reader<br/>(copies verbatim,<br/>fallback only)"] --> D
  T --> PT["Claude reads tender"] --> AP{"Person approves<br/>rules"}
  CD[("Crustdata<br/>/person/enrich")] --> N["normalise.py<br/>jobs, name matching,<br/>duplicates"]
  CI[("Crustdata<br/>/company/identify")] --> F["firm.py<br/>firm + bid checks"]
  B --> F
  D --> CHK["checker.py<br/>deterministic rules<br/>CV vs itself · vs record · vs tender"]
  N --> CHK
  AP --> CHK
  CHK --> Q["questions.py<br/>fixed templates"]
  F --> Q
  Q --> R["Report + clarification letter<br/>(questions, not findings)"] --> UI["Web app + Pip<br/>(real stages only)"]
  L[("cache/ + credits.csv<br/>never pay twice")] -.-> CD
  L -.-> CI

  classDef ai fill:#FEF3C7,stroke:#D97706,color:#78350F
  classDef ours fill:#DCFCE7,stroke:#163A2E,color:#163A2E
  classDef data fill:#E0F2FE,stroke:#0369A1,color:#0C4A6E
  classDef human fill:#F1F5F9,stroke:#475569,color:#1E293B
  class LLM,PT ai
  class X,RR,D,N,F,CHK,Q,R,UI ours
  class CD,CI,L data
  class AP human
```

Amber = AI (reads text only) · Green = our deterministic code (decides) · Blue = data · Grey = a person approves.

CVs in the standard NHAI layout are read by rules with no AI; Claude reads only unfamiliar layouts, copying text verbatim. Every decision is made by tested, deterministic code.
