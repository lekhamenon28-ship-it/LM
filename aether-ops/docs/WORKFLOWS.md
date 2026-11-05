# Agents and workflows for different use cases

Use **Natural Language Builder** to describe an agent's goal and select its tools and model. Optional incident investigation, service desk and capacity examples are editable starting points. Review the generated specification and save it to **Agent Garden**. Agents are reusable and can have different goals, tool bindings, models and input schemas. Reasoning agents can search Knowledge Fabric and retain source revisions in execution evidence.

Use **Workflow Orchestrator** for both workflow types:

- **Create agent workflow:** compose up to six saved agents with explicit original-input, previous-output or fixed-input handoffs. This supports investigation, actions and verification when the necessary tools exist. Write tools continue to require administrator-approved execution.
- **Create analysis workflow:** select any enabled read-only GET source tool, a model, an editable goal, source query and response mappings. The engine retrieves approved knowledge for each record and saves reports. Configure record array, unique ID and optional title paths. Optionally configure the next-page cursor path, query parameter and additional subsequent-page query. Field paths use dot notation, for example data.items or result; an empty record-array path means the response itself is an array.

The analysis starting points—custom, Dynatrace incident analysis, service desk triage and capacity review—do not create tools or assume a provider is connected. Choose your actual registered integration and verify its schema. Every field remains editable. Endpoint and credential configuration belongs in Tool Integrations and Credentials.

Both workflow types appear in the same Orchestrator. Execution History records inputs, outputs and failures. Analysis reports show their workflow name, source record, approval status and source revisions. After verifying an outcome, use **Draft procedure from verified outcome** to save an unapproved Knowledge Fabric document. Review it before approval.

## Limits and compatibility

Analysis batches fetch one source page and process up to two records. Unprocessed records and the pagination cursor persist for the next batch. Source pages must fit the tool response limit and contain no more than 100 records. Configure the provider's page-size input appropriately. Errors preserve pending records for retry. Reports are deduplicated by workflow configuration, tool/model revisions, evidence and matched knowledge revisions. Modified goals, queries or mappings start a new scan. Knowledge search uses bounded keyword matching rather than semantic search.

Only cursor-based response pagination is supported automatically. For offset/header-based APIs, configure a bounded query or use an adapter exposing a compatible cursor response. Missing record IDs and invalid array mappings fail explicitly. Reports use bounded model context and retain full captured records separately. The screen shows the latest 50 reports across workflows. Batches are manually initiated; this does not add a scheduler, webhook or arbitrary DAG execution.

Existing agent sequences remain compatible. Prior RCA workflows and reports are preserved in Orchestrator, and editing those workflows migrates their configuration under the same ID. The old RCA API remains for compatibility; new analysis workflows use the shared orchestration API. Free-model eligibility and quotas depend on your provider account, selected in Model Management.

## Model request limits

Model Management → Edit → Output and rate limits controls the maximum completion length and optional local per-minute budgets. Existing connections default to 1,024 maximum output tokens. Groq connections default to conservative local budgets of 8,000 tokens/minute and 30 requests/minute; other endpoints have no local per-minute budget unless configured. Set these from the actual provider Limits page. Setting a local budget to zero disables that local control; it does not remove the provider's quota. The context window is a separate setting.

Every model request, including agent authoring, reasoning, analysis and conversations, supplies an explicit output-token cap. Groq receives max_completion_tokens. Estimated input plus output is checked against the configured context and budget before sending. Reservations are shared in the existing workspace database across runs and Worker instances, then reconciled against returned usage. The estimate is conservative and is not an exact tokenizer count; provider enforcement remains authoritative. Keys on the same provider/model share the local budget.

When a minute budget is full, requests can wait within a bounded 55-second allowance and the enclosing run deadline. A short provider Retry-After is honored for one retry. Longer cooldowns are persisted and returned as actionable errors. Daily exhaustion and requests larger than the allowance are not automatically retried. Error messages expose only parsed quota type, numeric usage and reset duration, never the provider's raw error body or credentials. Existing historical 429 records contain only the earlier generic message and cannot be reconstructed.

Reasoning agents receive bounded structured tool results and short knowledge excerpts. Full tool responses remain in Execution History, alongside the modelContext actually supplied to the model. Truncation is explicit; the agent must not claim exhaustive evidence. Smaller page sizes can reduce later tool-result context, but cannot fix a first-request model rejection or an exhausted daily quota.

## Verification

The build, API tests and test:workflows-browser cover independent use cases, response mappings, cursor continuation, source grounding, report/procedure persistence, reusable agents, multi-stage execution, legacy migration, permissions and the absence of a standalone RCA menu. Provider integrations use isolated fixtures; validate live credentials with a small run.
