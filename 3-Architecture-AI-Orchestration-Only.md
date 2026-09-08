# The A11y Fixer — AI Orchestration Only

Scoped strictly to `deep_agent.py`'s `deepagents` graph — no CLI, no guardrails, no PR
delivery, no HITL dashboard. This is what runs once per violation, purely the agentic
reasoning layer. Read directly from the source on 2026-09-08.

![The A11y Fixer AI orchestration graph](architecture-ai-orchestration.png)

<details>
<summary>Diagram source (click to expand)</summary>

```mermaid
%% The A11y Fixer -- Architecture: AI Orchestration Only
%% Scoped strictly to deep_agent.py's deepagents graph -- no CLI, guardrails, or delivery plumbing.
flowchart TD
    classDef agent fill:#6D28D9,stroke:#3B0F91,color:#FFFFFF,stroke-width:2px
    classDef top fill:#4C1D95,stroke:#2E1065,color:#FFFFFF,stroke-width:3px
    classDef store fill:#334155,stroke:#1E293B,color:#FFFFFF,stroke-width:2px
    classDef io fill:#0F766E,stroke:#0B4F49,color:#FFFFFF,stroke-width:2px
    classDef hitl fill:#C2410C,stroke:#7C2D0A,color:#FFFFFF,stroke-width:2px,stroke-dasharray: 4 3

    Input["One axe-core violation<br/>rule, selector, failing HTML, page URL"]:::io

    subgraph GRAPH["create_deep_agent() -- one LangGraph graph per violation"]
        Top["Top-level orchestrator<br/>system prompt: delegate to all 3 subagents,<br/>in order, every time -- never skip, never guess a score"]:::top
        Memory[("wiki/AGENTS.md<br/>MemoryMiddleware: past HITL-rejection<br/>lessons loaded as context")]:::store

        CP["compliance_planner<br/>tools: wcag-mcp (live)<br/>skill: a11y-fixer remediation algorithm<br/>output: fix candidate, score left at 0"]:::agent
        CC["codebase_compiler<br/>tools: angular-cli MCP + locate_selector_in_component<br/>middleware: FilesystemMiddleware (path allow-list)<br/>+ RubricMiddleware (grades &amp; retries, max 2x)<br/>output: applied patch, ng build verified"]:::agent
        QC["qa_critic<br/>tools: chrome-devtools MCP (CLS / bbox drift)<br/>tool: score_rubric() -&gt; domain/rubric.py<br/>output: 0-20 composite score"]:::agent
        AC["audit_crawler (subagent form)<br/>tools: Playwright MCP<br/>delegated ad hoc, mid-task only --<br/>e.g. &quot;did my fix break navigation?&quot;"]:::agent

        Top -- "task() call 1 (required)" --> CP
        CP -- "fix candidate" --> Top
        Top -- "task() call 2 (required)" --> CC
        CC -- "applied + build-verified patch" --> Top
        Top -- "task() call 3 (required)" --> QC
        QC -- "0-20 rubric score" --> Top
        Top -. "optional, on demand" .-> AC

        Memory -. "loaded at graph construction" .-> Top

        Write{{"write_file / edit_file<br/>(inside codebase_compiler)"}}:::hitl
        CC -.-> Write
        Write -. "interrupt_on: pauses graph,<br/>waits for approve/reject" .-> Top
    end

    Output["ViolationResponse (structured output)<br/>rule, wcag, selector, technique_id, code,<br/>rationale, score 0-20, route: auto or human"]:::io

    Input --> Top
    Top --> Output
```

</details>

## Reading the diagram

- **Teal** — the graph's input (one flattened axe-core violation) and output (the structured `ViolationResponse` — the only thing the rest of the system, `deliver_violation()`, ever sees from this graph).
- **Dark violet** — the top-level orchestrator. Its system prompt is deliberately strict: it must call all three subagents, in that exact order, every single time — it is explicitly forbidden from investigating or editing the codebase itself, or fabricating a score instead of getting one from `qa_critic`.
- **Violet** — the three subagents that are always called, plus `audit_crawler` in its ad hoc, LLM-driven form (distinct from the deterministic crawl used for the initial site-wide audit — that one lives outside this graph entirely).
- **Orange, dashed** — `interrupt_on`: every `write_file`/`edit_file` call inside `codebase_compiler` pauses the whole graph and waits for a human `approve`/`reject` decision before continuing.
- **Slate** — `wiki/AGENTS.md`, loaded once as memory context so the agent sees lessons learned from past human rejections.

**Verified:** rendered locally via `@mermaid-js/mermaid-cli` against a real headless Chromium — the actual render output, not a mockup.
