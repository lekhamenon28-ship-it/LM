# AETHER.OPS

An authenticated operations workspace with durable knowledge, REST tool integrations, executable agent workflows, model connections, conversations, private-network gateways and execution evidence. The deployed application contains no simulator screens, seeded incidents, fabricated agents or sample operational metrics.

## Run and build

Use Node.js 22+ and Python 3.10+ for gateway development. Install with `npm ci`, build with `npm run build`, and start with `npm run dev`. The local server uses SQLite in `.data/`; deployment uses the existing D1 binding in `.openai/hosting.json`.

For an empty database, set `BOOTSTRAP_ADMIN_USERNAME` and `BOOTSTRAP_ADMIN_PASSWORD` before first start. There is no hardcoded bootstrap account or password. Existing users and password changes survive deployments. New passwords require 12–128 characters. The existing deployment's accounts are retained.

## Set up real operations

1. Save a token in **Credentials**, using a `TOOL_` or `MODEL_` reference. Values are encrypted with AES-GCM and never returned through read APIs. Production requires a secret `CREDENTIAL_ENCRYPTION_KEY` (64 hex characters); keep this key stable across deployments. The local server generates a separate private key in `.data/credential-key`.
2. Register a tool in **Tool Integrations** with its HTTPS endpoint, HTTP method, authentication scheme and JSON input schema. GET parameters become query parameters; other methods send JSON. Bearer, Api-Token, Basic (pre-encoded credential), and common API key headers are supported. Public tools need no credential.
3. Create an **Agent** and bind its ordered tool steps. A step consumes the run input, the previous tool output, or a fixed JSON object. Execution stops on failure and records actual inputs, outputs, errors and timing. Choose a tool workflow for fixed ordered steps, or use the Natural Language Builder to create a reasoning agent that selects bound tools at runtime. Write methods always require explicit administrator approval for the run.
4. Add real runbooks and documents in **Knowledge Fabric**. Knowledge is shared across the workspace; matching tags create visible relationships. Text/Markdown imports are limited to 50 KB.
5. Connect a real model in **Model Management** using an OpenAI-compatible chat-completions endpoint, exact model identifier and a credential reference. **Conversations** either retrieve source excerpts or request live provider responses with source context. Conversation history belongs to its signed-in owner. Conversations do not perform tool execution.

Tools use fixed administrator-configured endpoints, public DNS checks, no redirects, input validation, request timeouts and bounded responses. Private endpoints belong on the gateway. Schema support is explicitly limited to `type`, `properties`, `required`, `items`, `enum`, `additionalProperties`, `title` and `description`. Runs are synchronous; interrupted runs expire and retain completed-step evidence. Inspect evidence before retrying a write operation.

## Personas

| Persona | Permissions |
| --- | --- |
| Platform Administrator | Manage users, credentials, tools and model connections; configure agents and knowledge; approve and execute changes |
| Agent Builder | Maintain agents and knowledge; execute read-only workflows; use conversations |
| Operations Analyst | Inspect data; execute read-only workflows; use conversations |
| Auditor | Inspect configuration, shared knowledge, executions and audit evidence; no mutation or execution |

Create accounts in **User Management**, then assign their persona in **Personas & Access**. Personas are enforced by authenticated backend routes and do not create fictional user accounts. Existing non-admin accounts default to Operations Analyst. Python onboarding and infrastructure configuration remain administrator-managed.

## Private network and Python agents

Use `/gateway-setup` to register an outbound gateway. Product credentials stay inside the private network. The read-only adapters support NetApp ONTAP, VMware vCenter, ServiceNow, Kubernetes and Dynatrace Classic; other integrations use configured REST paths or locally installed adapters.

Use `/python-agent-setup` to onboard existing Python agents with a locally configured command alias. The gateway receives JSON input and reports actual output; change-mode scripts require local opt-in and administrator approval. The gateway runs Python using its host permissions. Usage & Observability aggregates reported Python execution telemetry; unknown costs and usage remain unknown. Tool workflow results are in Execution History.

## Source and validation

- `app/features/00-workspace.js`: overview, tools, credentials, agents, executions and personas.
- `app/features/knowledge-workspace.js`: knowledge, models and conversations.
- `worker/workspace.mjs`: durable records, encrypted credentials, role authorization and execution.
- `worker/index.mjs`: authentication and routing.
- `app/styles.css`: locally served Inter font and responsive workspace layout.
- `scripts/build.mjs`: bundles authenticated app assets and the Worker into `dist/`.

Run `npm test` for authentication, gateway, telemetry and workspace backend checks. Start the app and use `npm run test:browser` for browser checks; `TEST_APP_URL`, `TEST_USER`, `TEST_PASSWORD` and `BROWSER_PATH` configure the test environment. Test fixtures are confined to test databases and are not published as operational records.

Publish to the existing project recorded in `.openai/hosting.json` at https://aether-ops-aiops.lekhamenon28.chatgpt.site. Source must be pushed before packaging/saving a version. Keep hosting credentials and encryption keys out of Git.

## Natural-language agents and orchestration

Open **Natural Language Builder** (or **Agent Garden → Build with natural language**). Select integrated tools, insert their `@Tool Name` references into your request, and select a configured model. The selected model must support JSON output and tool calling. The builder receives only selected tool names, descriptions, input schemas and permission metadata. It creates a persisted draft with proposed instructions, required run input and bound tool IDs. Review and edit the draft before **Save to Agent Garden**. Draft generation does not invoke operational tools. Missing capabilities block saving; unknown bindings and stale tool revisions are rejected.

Reasoning agents use the connected model to choose and invoke only their bound integrations. The backend validates every requested tool input, keeps credentials server-side, serializes calls, stops on failures, and limits model steps and tool calls. Conditional goals are evaluated by the model using observed results. Administrator approval is required before a run with write capabilities. A reasoning agent's completed tool calls, provider-reported token usage and final answer are recorded in Execution History. Provider compatibility and actual connected-tool behavior determine what an agent can accomplish.

Existing deterministic tool workflows remain in **Agent Garden**. **Agent Orchestrator** composes saved reasoning agents and tool workflows into up to six sequential stages. Each stage uses the original input, the previous agent result, or fixed JSON. Write permissions are checked across the whole plan before the first stage starts. The orchestration stores child run IDs, stage outputs and failures, and stops when a stage fails. This version supports ordered execution; it does not claim parallel scheduling or arbitrary DAG execution.

The implementation uses the AI SDK's `ToolLoopAgent` and your existing OpenAI-compatible connections; model IDs are taken from configured profiles. `tests/agent-studio-api.mjs` verifies grounded generation and real tool-execution boundaries with an isolated provider fixture. `tests/agent-studio-browser.mjs` checks the authoring/review flow and actual public REST orchestration in an isolated workspace.
