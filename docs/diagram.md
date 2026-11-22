# TrueNorth: how it's built

![TrueNorth architecture](diagram.png)

```mermaid
%%{init: {"theme":"dark","themeVariables":{"edgeLabelBackground":"#163A2E","lineColor":"#CBD5E1"}, "flowchart":{"htmlLabels":true}}}%%
flowchart LR
  subgraph IN["Inputs"]
    T["Tender text"]
    CV["CV: PDF / Word / text"]
    B["Saved bid or uploaded CVs"]
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
  AP -. "preview" .-> FE["Find experts (preview)<br/>Crustdata /person/search<br/>+ same year maths<br/>+ sector filter"]
  FE -.-> UI
  L[("cache/ + credits.csv<br/>never pay twice")] -.-> CD
  L -.-> CI

  classDef ai fill:#FEF3C7,stroke:#D97706,color:#78350F
  classDef ours fill:#DCFCE7,stroke:#163A2E,color:#163A2E
  classDef data fill:#E0F2FE,stroke:#0369A1,color:#0C4A6E
  classDef human fill:#F1F5F9,stroke:#475569,color:#1E293B
  classDef preview fill:#1E293B,stroke:#94A3B8,color:#E2E8F0,stroke-dasharray: 5 5
  class LLM,PT ai
  class X,RR,D,N,F,CHK,Q,R,UI ours
  class CD,CI,L data
  class AP human
  class FE preview

  subgraph KEY["What the colours mean"]
    direction TB
    K1["AI: only reads text<br/>(copies verbatim, never decides)"]
    K2["Our code: deterministic,<br/>tested rules that decide"]
    K3[("Data: Crustdata records,<br/>cache and cost ledger")]
    K4{"A person approves"}
    K5["Preview feature"]
  end
  class K1 ai
  class K2 ours
  class K3 data
  class K4 human
  class K5 preview
```

Amber = AI (reads text only) · Green = our deterministic code (decides) · Blue = data · Grey = a person approves · Dashed = preview.

CVs in the standard NHAI layout are read by rules with no AI; Claude reads only unfamiliar layouts, copying text verbatim. Every decision is made by tested, deterministic code.
