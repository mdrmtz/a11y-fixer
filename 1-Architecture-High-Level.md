# The A11y Fixer — Architecture (High Level)

Read directly from the `agent/` source (`cli.py`, `adapters/violation_store.py`,
`domain/html_lang_fix.py`, `hitl/review_queue.py`) on 2026-09-08. This is the
end-to-end pipeline at a glance — for the full detailed version see
`ARCHITECTURE.md`; for just the AI orchestration layer see
`3-Architecture-AI-Orchestration-Only.md`.

![The A11y Fixer high-level architecture](architecture-high-level.png)

<details>
<summary>Diagram source (click to expand)</summary>

```mermaid
%% The A11y Fixer -- Architecture: High Level
%% Read directly from cmu-capstone/agent source, 2026-09-08
flowchart TD
    classDef cli fill:#1E3A8A,stroke:#0B1F4E,color:#FFFFFF,stroke-width:2px
    classDef discover fill:#0F766E,stroke:#0B4F49,color:#FFFFFF,stroke-width:2px
    classDef fast fill:#0369A1,stroke:#0C4A6E,color:#FFFFFF,stroke-width:2px
    classDef agent fill:#6D28D9,stroke:#3B0F91,color:#FFFFFF,stroke-width:2px
    classDef guard fill:#B91C1C,stroke:#7F1D1D,color:#FFFFFF,stroke-width:2px,stroke-dasharray: 4 3
    classDef auto fill:#15803D,stroke:#0B4A20,color:#FFFFFF,stroke-width:2px
    classDef human fill:#C2410C,stroke:#7C2D0A,color:#FFFFFF,stroke-width:2px
    classDef store fill:#334155,stroke:#1E293B,color:#FFFFFF,stroke-width:2px

    A["a11y-fixer CLI<br/>audit &middot; run &middot; fleet &middot; review"]:::cli
    B["Target resolution<br/>local repo, git clone, or live URL"]:::cli
    C["Route discovery + axe-core audit<br/>deterministic, no LLM"]:::discover
    D[("Per-violation audit report")]:::store
    E{{"Pre-pipeline gate<br/>skip / create / replace<br/>(dedupes against prior runs)"}}:::discover

    A --> B --> C --> D --> E
    E -- "already handled" --> F["no action"]:::discover

    G["Deterministic fast-track<br/>html-has-lang only<br/>apply + build-verify, no LLM"]:::fast
    H["AI agent pipeline<br/>plan -&gt; fix -&gt; grade<br/>(3 specialist subagents)"]:::agent
    E -- "html-has-lang" --> G
    E -- "every other rule" --> H

    I{{"Guardrails + risk routing<br/>path safety &middot; confidence gate &middot; blast-radius check<br/>escalates only, never downgrades"}}:::guard
    G --> I
    H --> I

    J["Automated delivery<br/>PR opened &middot; auto-merged if high-confidence"]:::auto
    K["Human review<br/>dashboard app or CLI"]:::human
    I -- "auto" --> J
    I -- "human" --> K

    L["Reject: lesson learned<br/>written to institutional memory"]:::human
    K -- "approve" --> J
    K -- "reject" --> L
    L -.->|"fed back as context"| H

    M[("Violation state store<br/>tracks every violation across runs")]:::store
    J --> M
    K --> M
```

</details>

## Reading the diagram

- **Blue** — CLI entry and target resolution (local repo, clone, or live URL).
- **Teal** — deterministic, no-LLM discovery, audit, and the per-violation gate that prevents re-processing an already-handled violation.
- **Sky blue** — the one deterministic fast-track (`html-has-lang`) that bypasses the AI pipeline entirely.
- **Violet** — the AI agent pipeline (three specialist subagents; see diagram 3 for the detail).
- **Red, dashed** — guardrails: they can only escalate a fix to human review, never downgrade an AI decision back to automatic.
- **Green** — automated PR delivery and auto-merge.
- **Orange** — human review (dashboard or CLI) and the reject → lesson-learned feedback loop back into the AI pipeline's memory.
- **Slate** — persistent state.

**Verified:** rendered locally via `@mermaid-js/mermaid-cli` against a real headless Chromium — this is the actual render output, not a mockup.
