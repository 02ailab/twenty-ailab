# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Twenty is an open-source CRM built with modern technologies in a monorepo structure. The codebase is organized as an Nx workspace with multiple packages.

## Key Commands

### Development
```bash
# Start development environment (frontend + backend + worker)
yarn start

# Individual package development
npx nx start twenty-front     # Start frontend dev server
npx nx start twenty-server    # Start backend server
npx nx run twenty-server:worker  # Start background worker
```

### Testing
```bash
# Preferred: run a single test file (fast)
npx jest path/to/test.test.ts --config=packages/PROJECT/jest.config.mjs

# Run all tests for a package
npx nx test twenty-front      # Frontend unit tests
npx nx test twenty-server     # Backend unit tests
npx nx run twenty-server:test:integration:with-db-reset  # Integration tests with DB reset
# To run an indivual test or a pattern of tests, use the following command:
cd packages/{workspace} && npx jest "pattern or filename"

# Storybook
npx nx storybook:build twenty-front
npx nx storybook:test twenty-front

# When testing the UI end to end, click on "Continue with Email" and use the prefilled credentials.
```

### Code Quality
```bash
# Linting (diff with main - fastest, always prefer this)
npx nx lint:diff-with-main twenty-front
npx nx lint:diff-with-main twenty-server
npx nx lint:diff-with-main twenty-front --configuration=fix  # Auto-fix

# Linting (full project - slower, use only when needed)
npx nx lint twenty-front
npx nx lint twenty-server

# Type checking
npx nx typecheck twenty-front
npx nx typecheck twenty-server

# Format code
npx nx fmt twenty-front
npx nx fmt twenty-server
```

### Build
```bash
# Build packages (twenty-shared must be built first)
npx nx build twenty-shared
npx nx build twenty-front
npx nx build twenty-server
```

### Database Operations
```bash
# Database management
npx nx database:reset twenty-server         # Reset database
npx nx run twenty-server:database:init:prod # Initialize database
npx nx run twenty-server:database:migrate:prod # Run instance commands (fast only)

# Generate an instance command (fast or slow)
npx nx run twenty-server:database:migrate:generate --name <name> --type <fast|slow>
```

### Database Inspection (Postgres MCP)

A read-only Postgres MCP server is configured in `.mcp.json`. Use it to:
- Inspect workspace data, metadata, and object definitions while developing
- Verify migration results (columns, types, constraints) after running migrations
- Explore the multi-tenant schema structure (core, metadata, workspace-specific schemas)
- Debug issues by querying raw data to confirm whether a bug is frontend, backend, or data-level
- Inspect metadata tables to debug GraphQL schema generation issues

This server is read-only — for write operations (reset, migrations, sync), use the CLI commands above.

### GraphQL
```bash
# Generate GraphQL types (run after schema changes)
npx nx run twenty-front:graphql:generate
npx nx run twenty-front:graphql:generate --configuration=metadata
```

## Architecture Overview

### Tech Stack
- **Frontend**: React 18, TypeScript, Jotai (state management), Linaria (styling), Vite
- **Backend**: NestJS, TypeORM, PostgreSQL, Redis, GraphQL (with GraphQL Yoga)
- **Monorepo**: Nx workspace managed with Yarn 4

### Package Structure
```
packages/
├── twenty-front/          # React frontend application
├── twenty-server/         # NestJS backend API
├── twenty-ui/             # Shared UI components library
├── twenty-shared/         # Common types and utilities
├── twenty-emails/         # Email templates with React Email
├── twenty-website/    # Next.js marketing website
├── twenty-docs/           # Documentation website
├── twenty-zapier/         # Zapier integration
└── twenty-e2e-testing/    # Playwright E2E tests
```

### Key Development Principles
- **Functional components only** (no class components)
- **Named exports only** (no default exports)
- **Types over interfaces** (except when extending third-party interfaces)
- **String literals over enums** (except for GraphQL enums)
- **No 'any' type allowed** — strict TypeScript enforced
- **Event handlers preferred over useEffect** for state updates
- **Props down, events up** — unidirectional data flow
- **Composition over inheritance**
- **No abbreviations** in variable names (`user` not `u`, `fieldMetadata` not `fm`)

### Naming Conventions
- **Variables/functions**: camelCase
- **Constants**: SCREAMING_SNAKE_CASE
- **Types/Classes**: PascalCase (suffix component props with `Props`, e.g. `ButtonProps`)
- **Files/directories**: kebab-case with descriptive suffixes (`.component.tsx`, `.service.ts`, `.entity.ts`, `.dto.ts`, `.module.ts`)
- **TypeScript generics**: descriptive names (`TData` not `T`)

### File Structure
- Components under 300 lines, services under 500 lines
- Components in their own directories with tests and stories
- Use `index.ts` barrel exports for clean imports
- Import order: external libraries first, then internal (`@/`), then relative

### Comments
- Use short-form comments (`//`), not JSDoc blocks
- Explain WHY (business logic), not WHAT
- Do not comment obvious code
- Multi-line comments use multiple `//` lines, not `/** */`

### State Management
- **Jotai** for global state: atoms for primitive state, selectors for derived state, atom families for dynamic collections
- Component-specific state with React hooks (`useState`, `useReducer` for complex logic)
- GraphQL cache managed by Apollo Client
- Use functional state updates: `setState(prev => prev + 1)`

### Backend Architecture
- **NestJS modules** for feature organization
- **TypeORM** for database ORM with PostgreSQL
- **GraphQL** API with code-first approach
- **Redis** for caching and session management
- **BullMQ** for background job processing

### Database & Upgrade Commands
- **PostgreSQL** as primary database
- **Redis** for caching and sessions
- **ClickHouse** for analytics (when enabled)
- When changing entity files, generate an **instance command** (`database:migrate:generate --name <name> --type <fast|slow>`)
- **Fast** instance commands handle schema changes; **slow** ones add a `runDataMigration` step for data backfills
- **Workspace commands** iterate over all active/suspended workspaces for per-workspace upgrades
- Commands use `@RegisteredInstanceCommand` and `@RegisteredWorkspaceCommand` decorators for automatic discovery
- Include both `up` and `down` logic in instance commands
- Never delete or rewrite committed instance command `up`/`down` logic
- See `packages/twenty-server/docs/UPGRADE_COMMANDS.md` for full documentation

### Utility Helpers
Use existing helpers from `twenty-shared` instead of manual type guards:
- `isDefined()`, `isNonEmptyString()`, `isNonEmptyArray()`

## Development Workflow

IMPORTANT: Use Context7 for code generation, setup or configuration steps, or library/API documentation. Automatically use the Context7 MCP tools to resolve library IDs and get library docs without waiting for explicit requests.

### Before Making Changes
1. Always run linting (`lint:diff-with-main`) and type checking after code changes
2. Test changes with relevant test suites (prefer single-file test runs)
3. Ensure instance commands are generated for entity changes (`database:migrate:generate`)
4. Check that GraphQL schema changes are backward compatible
5. Run `graphql:generate` after any GraphQL schema changes

### Code Style Notes
- Use **Linaria** for styling with zero-runtime CSS-in-JS (styled-components pattern)
- Follow **Nx** workspace conventions for imports
- Use **Lingui** for internationalization
- Apply security first, then formatting (sanitize before format)

### Testing Strategy
- **Test behavior, not implementation** — focus on user perspective
- **Test pyramid**: 70% unit, 20% integration, 10% E2E
- Query by user-visible elements (text, roles, labels) over test IDs
- Use `@testing-library/user-event` for realistic interactions
- Descriptive test names: "should [behavior] when [condition]"
- Clear mocks between tests with `jest.clearAllMocks()`

## Dev Environment Setup

All dev environments (Claude Code web, Cursor, local) use one script:

```bash
bash packages/twenty-utils/setup-dev-env.sh
```

This handles everything: starts Postgres + Redis (auto-detects local services vs Docker), creates databases, copies `.env` files, and initializes the database schema (runs migrations) on a fresh database. Idempotent — safe to run multiple times.

- `--docker` — force Docker mode (uses `packages/twenty-docker/docker-compose.dev.yml`)
- `--down` — stop services
- `--reset` — wipe data and restart fresh
- **Skip the setup script** for tasks that only read code — architecture questions, code review, documentation, etc.

**Note:** CI workflows (GitHub Actions) manage services via Actions service containers and run setup steps individually — they don't use this script.

## Important Files
- `nx.json` - Nx workspace configuration with task definitions
- `tsconfig.base.json` - Base TypeScript configuration
- `package.json` - Root package with workspace definitions
- `.cursor/rules/` - Detailed development guidelines and best practices

---

# Saldo Platform Integration (this fork)

This repo is **not** stock upstream Twenty — it is the **Saldo platform's** Twenty CRM,
deployed live to a single-VPS k3s cluster (`crm.saldo.chat`) and integrated with the
live Chatwoot (`chat.saldo.chat`) via a standalone **bridge** service (`bridge/`,
namespace `twenty-bridge`, public panel at `bridge.saldo.chat`). The platform is a set
of cooperating services (Chatwoot, llm-core, Twenty, bridge, work-services), each in its
own k3s namespace with its own DB, deployed via the WinSCP → `bash deploy.sh` pattern.

## Canonical documentation lives outside this repo

The single source of truth for platform/server architecture is the central catalog
`../general_docs/` (sibling of this repo). The live-server map is
`../general_docs/SERVER_ARCHITECTURE.md` (Twenty = §8B, bridge = §8C); the logging
contract is `../general_docs/LOGGING_INCIDENTINATOR.md` §0.1. Service repos do **not**
keep copies of these files — only a pointer.

**RULE — always update the central docs on infra changes (same task as the change).**
Whenever you change anything that alters the live platform — a namespace, service,
Deployment/StatefulSet, Ingress host, public URL, Secret/ConfigMap, DB, backup CronJob,
Helm release/revision, webhook wiring, an API contract another service consumes, or the
logging surface — update the relevant file in `../general_docs/` (almost always
`SERVER_ARCHITECTURE.md`) in the **same** task, before considering the work done. The doc
must always match the actual server. If this CLAUDE.md and the canon ever disagree, the
canon wins and you fix this file in the same task. This mirrors the catalog rule the
other Saldo repos follow (e.g. `../saldo-wiki/CLAUDE.md`).

## Reproducibility — files and idempotent scripts, not ad-hoc commands

**RULE — anything reusable is a committed file, not a terminal command.** Setup, deploy,
cron, configuration, backups, migrations, any repeatable server wiring → version-controlled
files + an **idempotent** `install-*.sh` / `deploy.sh` the operator runs once (WinSCP →
PuTTY; agents have no SSH). Ad-hoc commands are allowed ONLY for transient, non-reusable
actions: viewing logs/status, post-deploy smoke checks, one-off diagnostics, interactive
auth. Test: *"will this run again, on another service or a fresh server?"* — yes → file; no
→ a command is fine. Never hand-edit installed scripts/cron on the server; change the repo
file and re-run the installer. Full standard: `../general_docs/REPRODUCIBILITY_STANDARD.md`.
Reference implementation: `bridge/deploy/backups/install-backups.sh` + `saldo-backups.cron`.

## Data chains — analyze the flow before touching a cross-service contract

Integration bugs live at the **seams** between services (Chatwoot ⇄ bridge ⇄ Twenty),
where a payload is produced by one system, transits the bridge, and is consumed by
another. Before editing any data contract at such a seam — a webhook payload shape, a
field mapping, the panel/postMessage contract, an HMAC signing scheme, an ID-mapping
table — write out the **DATA-FLOW ANALYSIS** first:

```text
DATA-FLOW ANALYSIS
- Source:    <who produces it: Chatwoot webhook / Twenty REST / panel postMessage / DB>
- Transits:  <what it passes through: bridge router → sync service → client → mapping table>
- Consumer:  <who reads it: Twenty Person/Company / Chatwoot contact / the iframe panel>
- Risks:     <HMAC scheme match, SSRF/private-net reachability, echo loop (A↔B),
              idempotency / dedup key, field-shape mismatch, secret/token leakage,
              Chatwoot ~5s webhook timeout>
- Status:    <safe | requires synchronous change to the consumer on the other side>
```

A contract is a two-sided agreement: if you change what the bridge sends to Twenty (or
writes back to Chatwoot), the consumer on the other side must change in lockstep, or be
verified unaffected. Never change one side of a seam blind to the other. (Concept adapted
from the `АНАЛИЗ ПОТОКА` invariant in `../saldo-wiki/CLAUDE.md`.)
