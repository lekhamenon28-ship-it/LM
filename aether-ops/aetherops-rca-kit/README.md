# AetherOps: Dynatrace and SOP root cause analysis

Workspace: https://aether-ops-aiops.lekhamenon28.chatgpt.site/

Prepared 19 October 2026. Configuration was inspected through Sites and the local application source. No live Dynatrace call, model call, configuration change or Knowledge Fabric upload was performed. The app requires its own authenticated session for writes; the Site does not expose an MCP server. No incident records were supplied, so the three SOPs are illustrative drafts, not established incident history.

## What needs correcting

The current Dynatrace tool uses `/api/v2/entities`. That endpoint returns entities, not problems. Its `entitySelector` cannot be used as a problem selector. Keep that tool for entity investigation; add a separate Problems tool and bind the RCA agent to it.

The existing Groq profile uses `qwen/qwen3.8-27b` and `MODEL_GROQ_KEY`. Credential validity and account billing tier have not been checked. Reasoning agents do not automatically receive Knowledge Fabric documents. Conversations retrieves up to four documents using keyword matches; this is not semantic/vector retrieval. Orchestrator stages pass inputs or previous results, but do not add knowledge retrieval.

## 1. Configure the Problems tool

Sign in with a persona that can configure tools. In Tools, register a GET tool named `Dynatrace Problems with Davis Evidence`.

- Endpoint: `https://vri13969.live.dynatrace.com/api/v2/problems`
- Authentication: API token (`Authorization: Api-Token <token>`)
- Credential reference: `TOOL_DYNATRACE_TOKEN`
- Enabled: yes; approval: not required for this read-only request
- Ensure the token has `problems.read`; an entities-only token is insufficient.
- Input schema: copy `problems-input-schema.json` from this folder.

Test with this input:

```json
{
  "problemSelector": "status(\"open\")",
  "from": "now-30d",
  "to": "now",
  "pageSize": 5,
  "sort": "-startTime",
  "fields": "evidenceDetails,impactAnalysis"
}
```

The API time window matters: this sample can omit open problems that began earlier than 30 days ago. Use an initial discovery window covering your oldest outstanding problem, and retain/recheck previously discovered open IDs. Do not describe a bounded lookback as all open problems.

For subsequent pages send only `nextPageKey` and `fields`; omit the initial filters and pageSize. Use small pages: AetherOps bounds tool responses to 100 KB and agent runs to eight tool calls and six model steps. A large evidence payload can exceed this even with five problems. Reduce page size or process one selected problem at a time. Report incomplete coverage rather than claiming completeness.

Reference: https://docs.dynatrace.com/docs/dynatrace-api/environment-api/problems-v2/problems/get-problems-list

## 2. Use the existing free-tier model option

In Credentials, ensure `MODEL_GROQ_KEY` contains your Groq key. In Model Management, inspect the existing Groq connection:

- Model: `qwen/qwen3.8-27b`
- Endpoint: `https://api.groq.com/openai/v1/chat/completions`
- Credential: `MODEL_GROQ_KEY`

Use a Groq Free Plan account. The published free limits currently list 30 requests/minute, 1,000 requests/day, 8,000 tokens/minute and 200,000 tokens/day for this model; your account Limits page is authoritative. Each planning/tool/final-answer turn can consume a model request. Start with one incident per run and concise evidence. The app currently reports provider failures rather than automatically retrying 429s. A paid account using the same model is not automatically free.

Reference: https://console.groq.com/docs/rate-limits

Alternative: self-host Qwen using Ollama. Model inference has no provider per-token fee, but requires hardware/hosting. AetherOps accepts public HTTPS model endpoints, not `localhost:11434`; use an authenticated HTTPS proxy reachable from the hosted app. Set the full `/v1/chat/completions` endpoint and a MODEL_ credential reference. Do not expose unauthenticated Ollama publicly. Validate structured output and tool calling before using Agent Builder.

Reference: https://docs.ollama.com/api/openai-compatibility

## 3. Import the SOP drafts

In Knowledge Fabric → Add source, import each SOP Markdown file separately, enter its title and comma-separated topics, and save. Each file is below the 50 KB import limit. Suggested common topics: `dynatrace,rca,sop,draft` plus the incident-specific topics in each document. Confirm all three titles appear after Refresh.

These are unvalidated examples. Replace the example scenario with a resolved incident ID/link, incident timeline, Davis evidence, verified remediation and actual recovery measurements. Add the owner, approver, revision and review date before treating a draft as an approved operational SOP. A closed Dynatrace problem alone does not establish which remediation worked.

## 4. Build the read-only evidence agent

In Natural Language Builder select the Groq model and only the new Problems tool. Describe this agent:

```text
Create Dynatrace Davis RCA Evidence. Use the selected Problems tool to retrieve
open problems in the input time window, including evidenceDetails and
impactAnalysis. Use problemSelector, never entitySelector. Do not modify
infrastructure or close problems. Return the exact problem ID, title, status,
start time, affected and impacted entities, rootCauseEntity, relevant evidence
IDs and timestamps, and impact summary. Preserve missing or null values.
Separate Davis-reported findings from hypotheses. Describe the query window
and pagination coverage. If the run limits prevent fetching more pages, return
the nextPageKey and explicitly mark the result partial. Never claim that an
incident was resolved or an SOP was retrieved without supporting evidence.
```

Review the draft and its missing-capability notes before saving. Test one problem first. Inspect Executions for the real API request and response. An empty result means no matching problems in that window; do not generate sample incidents as a fallback.

## 5. Perform SOP-grounded RCA using the current app

The supported sequence today is an evidence-agent run followed by Conversations. It is not yet an unattended end-to-end workflow.

1. Run the evidence agent and select one problem.
2. Open Conversations, select Groq, and paste a compact evidence summary with its problem ID, timestamps and relevant evidence IDs.
3. Ask the following, including explicit symptom keywords such as database, connection pool, Kubernetes, OOMKilled, deployment, CPU or latency so the current keyword search can locate documents:

```text
Analyze this Dynatrace problem using Davis evidence and matching Knowledge
Fabric SOPs. [Paste evidence here.]
Return impact, Davis-reported cause, your separate RCA hypothesis, supporting
and contradicting evidence, missing checks, and confidence with justification.
Cite each SOP by title and version and each observation by evidence ID when
available. Do not treat an illustrative draft or a historical similarity as
proof. If no relevant SOP is retrieved, say so. Propose diagnostic steps,
conditional remediation requiring operator approval, rollback, and measurable
recovery checks. Do not execute remediation or claim recovery.
```

Check the source excerpts shown beneath the answer. If unrelated documents were retrieved, refine the keywords or paste the relevant approved SOP explicitly. Limit evidence to stay below the 4,000-character message limit. The interface displays source titles and excerpts; model-generated citations still need checking.

## 6. Required extension for a single automatic workflow

Add an authenticated, permission-checked Knowledge Fabric search operation callable by reasoning agents, or retrieve approved documents on the server and inject them into the agent context. Do not register the app's current workspace API as a REST tool using a model credential: it requires a user session and is not a service-token search API.

Then implement this sequence:

Trigger → fetch open problems → paginate and deduplicate → for each problem,
collect Davis evidence → retrieve approved SOPs by symptom/service/environment
→ generate cited RCA → store report → operator review.

Support per-problem iteration, result-size limits, retry-after/backoff for 429s,
and durable run state. Use a deduplication key including tenant, problem ID and
evidence version/hash so changed evidence can trigger a new analysis. Configure
a scheduler or webhook only after the complete flow works; the current
Orchestrator does not provide pagination loops or automatic knowledge search.

After resolution, use a separate learning flow: incident record plus verified
resolution → draft SOP → owner review → save versioned knowledge document.
This prevents an unverified RCA hypothesis becoming established history.

## Acceptance checks

- A known open problem appears with the same ID and root-cause entity as Dynatrace.
- A problem older than the initial lookback is not silently missed.
- Multiple pages and oversized evidence are explicitly handled or marked partial.
- Invalid token/model failures are reported without fabricated results.
- A matching SOP is cited and visible in the retrieved excerpts.
- No match and conflicting evidence produce uncertainty, not a confident cause.
- Draft SOPs are labeled as unapproved.
- No infrastructure changes occur in the read-only analysis flow.
- Check the Groq account's plan and Limits page before relying on free usage.
