---
name: a11y-fixer
description: Domain-specific remediation guide for fixing axe-core WCAG 2.2 AA violations. Instructs the agent on how to translate axe-core rules to WCAG success criteria (SC), query the wcag-mcp server efficiently, select valid techniques, and generate compliant JSON code patches. Sits strictly as the workflow layer above the raw wcag-mcp tool reference.
---

# The A11y Fixer — Remediation Workflow

This skill dictates the exact sequence for remediating axe-core accessibility violations. The agent MUST query the `wcag-mcp` server dynamically at runtime for every violation. Do not rely on static training data for WCAG compliance.

## Execution Constraints

1.  **Atomic Queries:** You MUST use `get-full-criterion-context` whenever the SC number is known. Do NOT make separate calls for techniques and failures; the full-context endpoint returns all required data (requirement, sufficient techniques, advisory techniques, and known failures) in one payload.
2.  **Failure Avoidance:** You MUST parse the "failures" array from the context payload. If your proposed code fix matches a known failure pattern, discard it and select a different technique.
3.  **Live Fetching:** Never cache WCAG rules to static files.
4.  **Scoring:** Do NOT calculate the `score` field in the final output. Leave it as `0` for the QA Critic agent to evaluate.

## Remediation Algorithm

### Phase 1: Resolve Rule to Success Criterion (SC)

Map the `axe-core` rule ID to the corresponding WCAG 2.2 SC number using the mapping table below.

| axe-core rule    | WCAG SC(s)   | Fallback Topic (`search-wcag`) |
| ---------------- | ------------ | ------------------------------ |
| `html-has-lang`  | 3.1.1        | "language of page"             |
| `color-contrast` | 1.4.3        | "contrast minimum"             |
| `image-alt`      | 1.1.1        | "non-text content"             |
| `keyboard`       | 2.1.1        | "keyboard accessible"          |
| `label`          | 1.3.1, 4.1.2 | "labels or instructions"       |
| `button-name`    | 4.1.2        | "name role value"              |
| `link-name`      | 2.4.4        | "link purpose in context"      |
| `heading-order`  | 1.3.1        | "section headings"             |

- **IF** the rule is in the table: Proceed to Phase 2 with the SC number.
- **IF** the rule is missing: Call `search-wcag` with the rule ID (e.g., `query: "aria-valid"`) to locate the SC number, then proceed to Phase 2.

### Phase 2: Fetch Context & Filter Techniques

1. Call `get-full-criterion-context(ref_id: "<SC number>")`.
2. Evaluate the returned techniques using strict hierarchy:
   - **Sufficient:** PREFERRED. Using this technique guarantees compliance.
   - **Advisory:** SECONDARY. Use only to supplement a sufficient technique, or if no sufficient technique applies to the specific DOM context.
   - **Failure:** BANNED. Never implement these patterns.

### Phase 3: Validate Against Common Anti-Patterns

Before generating the JSON patch, verify the fix does not trigger a framework-specific or common failure:

- **Angular `button-name`:** Do not use plain HTML `aria-label=""` if the value requires dynamic localization. Use `[attr.aria-label]="..."`. (`alt=` on a `<button>` is strictly invalid HTML).
- **`image-alt`:** Do not use `alt=""` for informative images; empty alt tags are exclusively for decorative images.
- **`color-contrast`:** Ensure contrast ratio is computed against the _actual_ rendered background (including absolute positioning/z-index), not just the immediate DOM parent.
- **`html-has-lang`:** The `lang` attribute MUST be applied to the root `<html>` element, not a child container.

### Phase 4: Construct Fix Candidate

Output the final remediation strictly matching this JSON schema:

```json
{
  "rule": "<axe-core rule id>",
  "wcag": "<SC number>",
  "selector": "<CSS axe-core failure from node selector>",
  "fix": {
    "technique_id": "<e.g., H37, ARIA1>",
    "technique_type": "<sufficient | advisory>",
    "code": "<the proposed code patch implementing the technique>"
  },
  "rationale": "<1-2 sentence justification citing the SC and technique>",
  "score": 0
}
```

## Domain Routing (Boundary Enforcement)

| Trigger Scenario                                                                                    | Required Skill / Tool                                  |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Needs the exact schema/parameters for `get-full-criterion-context`.                                 | **`wcag-mcp`** (Raw tool reference)                    |
| Remediating an axe-core violation and needs a strategy.                                             | **`a11y-fixer`** (This skill)                          |
| Mechanically adding `lang="en"` (well-known, no WCAG lookup needed) or fixing Angular Ivy bindings. | **`cmu-capstone-docs`** (LLM Wiki / Codebase Compiler) |
| User asks "Why did the capstone project choose axe-core?"                                           | **`cmu-capstone-docs`** (Project design rationale)     |
| Validating the fix in the actual Angular DOM.                                                       | **`angular-cli-mcp`** or **`playwright-mcp`**          |
