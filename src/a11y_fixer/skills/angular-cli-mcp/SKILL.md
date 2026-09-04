---
name: angular-cli-mcp
description: 'Use the Angular CLI MCP Server to interact with Angular workspaces. USE WHEN: generating code with ng generate, running builds/tests/lint, analyzing workspace structure, migrating components to OnPush/zoneless, searching Angular docs, starting/stopping dev servers, or applying Angular best practices. TRIGGER WORDS: angular, ng generate, ng build, ng test, ng serve, angular workspace, ivy, standalone component, OnPush, zoneless migration, angular best practices, angular docs.'
---

# Angular CLI MCP Server

Provides AI-actionable tools for Angular workspace analysis, code generation, compilation, testing, and documentation search. All tools are exposed via the `@angular/cli mcp` server (`npx @angular/cli mcp`).

## Available Tools

| Tool | Purpose |
|------|---------|
| `list_projects` | Read `angular.json`; returns all apps and libraries with their targets, paths, and test configs |
| `get_best_practices` | Load Angular version-aligned coding standards (standalone components, typed forms, signals, etc.) |
| `search_documentation` | Full-text search of https://angular.dev |
| `run_target` | Execute any configured Angular CLI target: `build`, `test`, `lint`, `e2e`, `deploy` |
| `devserver.start` | Async-start `ng serve`; returns immediately |
| `devserver.wait_for_build` | Return the latest build log from the running dev server |
| `devserver.stop` | Stop the active dev server |
| `onpush_zoneless_migration` | Analyze a file or directory and return the next actionable OnPush/zoneless migration step |
| `ai_tutor` | Launch the interactive Angular AI tutor with curriculum and persona |

## Mandatory First Step

**Always call `list_projects` first.** It returns the workspace `path` required by all other tools, and reveals project names, target configurations, and framework versions.

## Coding Standards

**Before writing or modifying Angular code, call `get_best_practices`** with the workspace path. Standards are version-specific — do not assume defaults from prior knowledge.

## Workflows

### 1. Feature Development & TDD Loop

1. `list_projects` — discover workspace, project names, and test framework (Jasmine/Jest/Vitest)
2. `get_best_practices` — load version-aligned standards
3. `search_documentation` — look up unfamiliar APIs or syntax (e.g., `@defer`, `effect()`, `input()`)
4. `devserver.start` — start background dev server
5. Edit code; call `devserver.wait_for_build` to confirm compilation
6. Write test file; `run_target` with `"test"` to verify
7. `devserver.stop` — clean up

### 2. OnPush / Zoneless Migration

1. `list_projects` — identify component paths and configurations
2. Run any prerequisite signal migrations via `run_target` (e.g., `ng generate @angular/core:signal-input-migration`)
3. `onpush_zoneless_migration` with the absolute path of the component file or directory
4. Apply the single returned change to the codebase
5. `run_target` with `"test"` to verify no regressions
6. Repeat `onpush_zoneless_migration` until the tool reports migration complete

### 3. Workspace Analysis & Code Review

1. `list_projects` — enumerate all projects, targets, and paths
2. `get_best_practices` — load standards for the detected Angular version
3. `run_target` with `"lint"` — surface existing violations
4. `run_target` with `"build"` — confirm zero compilation errors

### 4. Documentation Lookup

Use `search_documentation` for any Angular API, directive, signal primitive, or configuration question. Always prefer this over prior model knowledge — Angular evolves rapidly.

## Command Options

Append to the `args` array in MCP config as needed:

| Flag | Effect |
|------|--------|
| `--read-only` | Registers only non-mutating tools (safe for analysis-only sessions) |
| `--local-only` | Registers only tools that work without internet (no `search_documentation` or `ai_tutor`) |

## MCP Server Configuration (VS Code)

The server is configured in `.vscode/mcp.json`:

```json
{
  "servers": {
    "angular-cli": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@angular/cli", "mcp"]
    }
  }
}
```

## Key Rules

- The `workspace` or `workspacePath` parameter for each tool is the `path` value returned by `list_projects` — never guess it.
- Do not hardcode Angular version assumptions; always call `get_best_practices` first.
- Prefer `run_target` over raw shell commands for builds and tests — it respects workspace configuration.
- Use `devserver.wait_for_build` (not polling) to confirm compilation after edits.
