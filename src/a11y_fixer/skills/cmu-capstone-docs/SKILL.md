---
name: cmu-capstone-docs
description: Access CMU Agentic AI Capstone (The A11y Fixer) architecture docs via MCP. Covers ReAct design, Hybrid RAG, Tree-of-Thought (ToT) DFS, and multi-agent LangGraph orchestration. Use exclusively for project design rationale and documentation lookups. For raw WCAG 2.2 specs or axe-core remediation guides, use wcag-mcp or a11y-fixer instead.
---

# CMU Capstone Docs MCP

Provides access to the `cmu-capstone-docs` MCP server (`https://mdrmtz.mintlify.site/mcp`). This server exposes 3 tools to query the 9 published pages of The A11y Fixer documentation.

## Tool Selection Guide

| Action | Tool to Call | Example Argument / Command |
|---|---|---|
| **Semantic Search** (Find relevant pages) | `search_the_a11y_fixer` | `query: "Tree-of-Thought DFS"` |
| **Read Full Page** | `query_docs_filesystem_the_a11y_fixer` | `cat /checkpoints/checkpoint-4-tree-of-thought.mdx` |
| **Grep Keyword/Snippet** | `query_docs_filesystem_the_a11y_fixer` | `rg -C 5 "MMR" /checkpoints/checkpoint-3-rag-retrieval.mdx` |
| **Read Multiple Headers** | `query_docs_filesystem_the_a11y_fixer` | `head -80 /index.mdx /quickstart.mdx` |
| **Map Directory Tree** | `query_docs_filesystem_the_a11y_fixer` | `tree / -L 2` |
| **Report Doc Error** | `submit_feedback` | `path: "/checkpoints/checkpoint-3...", feedback: "..."` |

## Repository Architecture (Target Paths)

```text
/index.mdx                                             # Overview & architecture matrix
/quickstart.mdx                                        # 3-step workflow & scoring rubric
/checkpoints/checkpoint-1-agent-scoping.mdx            # Problem space, ReAct action definitions
/checkpoints/checkpoint-2-agent-design.mdx             # ReAct loop, dual memory architecture
/checkpoints/checkpoint-3-rag-retrieval.mdx            # Hybrid RAG (LLM Wiki + MMR router)
/checkpoints/checkpoint-4-tree-of-thought.mdx          # ToT DFS, adaptive sibling inflation
/checkpoints/checkpoint-5-multi-agent-architecture.mdx # LangGraph schema, Codebase Compiler, QA Critic
/demos/rag-basic-retrieval.mdx                         # Baseline cosine similarity RAG
/demos/rag-hybrid-retrieval.mdx                        # LLM Wiki + MMR implementation

```

## Agent Workflows

* **IF broad conceptual query** (e.g., "How does the agent fix violations?"):
1. Call `search_the_a11y_fixer` to identify target paths.
2. Call `query_docs_filesystem_the_a11y_fixer` using `cat <path>.mdx` on the top result.


* **IF precise technical lookup** (e.g., "What is the MMR lambda?"):
1. Call `query_docs_filesystem_the_a11y_fixer` using `rg -C 3 "<keyword>" <path>.mdx`.


* **IF structural exploration**:
1. Call `query_docs_filesystem_the_a11y_fixer` using `tree / -L 2`.
2. Follow up with `cat` on identified target files.



## Domain Routing (Strict Boundary Enforcement)

| User Prompt Intent | Required Tool | Justification |
| --- | --- | --- |
| "What does WCAG criterion 1.4.3 dictate?" | **`wcag-mcp`** | Authoritative W3C spec; bypasses project docs. |
| "How do I fix `button-name` in Angular?" | **`a11y-fixer`** | Axe-core remediation domain guidance. |
| "Why did the capstone use Hybrid RAG?" | **`cmu-capstone-docs`** | Project-specific architecture rationale. |
| "What are the 3 agents in the pipeline?" | **`cmu-capstone-docs`** | Project-specific design documentation. |

## Execution Constraints

1. **Stateless Filesystem:** `cd` commands do not persist between calls. Always use **absolute paths** (e.g., `/checkpoints/...`).
2. **File Extensions:** You MUST append `.mdx` to all file paths during filesystem calls (e.g., `/quickstart.mdx`).
3. **Output Limits:** Filesystem output caps at 30KB. For large files, prioritize `rg -C` or `head -N` over `cat` to prevent context truncation.
4. **Citation Formatting:** When providing URLs to the user, strip the `.mdx` extension (e.g., render as `https://mdrmtz.mintlify.site/quickstart`).
5. **Keyword Bias:** Leverage these terms for higher-accuracy searches: *axe-core, LLM Wiki, Stateless Semantic Search, MMR, Tree-of-Thought, DFS, LangGraph, Compliance Planner, Codebase Compiler, QA Critic, Angular Ivy, AST, HyDE, contrastive backpropagation.*
