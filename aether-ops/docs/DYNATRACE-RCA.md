# Dynatrace as a workflow use case

Dynatrace incident analysis is an editable template inside **Workflow Orchestrator → Create analysis workflow**. There is no separate RCA menu. See [WORKFLOWS.md](WORKFLOWS.md) for the shared workflow and agent model.

Register a read-only Problems API tool in **Tool Integrations** with endpoint `https://<tenant>.live.dynatrace.com/api/v2/problems`, `Api-Token` authentication, and your `TOOL_` credential reference. The token requires `problems.read`. Its input schema should allow problemSelector, from, to, sort, fields and nextPageKey as strings, and pageSize as an integer. Do not require initial filters in the schema, because cursor requests omit them.

Choose the **Dynatrace incident analysis** starting point in the shared builder, select that tool and your model, and review its editable instructions, query, response paths and cursor mapping. The example uses two problems per page, evidenceDetails and impactAnalysis, an initial open-problem selector and a now-30d discovery window. Expand that window to cover older outstanding problems. Subsequent pages use nextPageKey and fields.

Import and review SOPs in Knowledge Fabric; only Approved sources ground record-analysis reports. Run a batch in Orchestrator, read its report, and inspect captured evidence and source revisions. After actual resolution, supply verified findings, actions and recovery measurements to draft a procedure into Knowledge Fabric.

Existing incident workflows and reports remain visible in the shared Orchestrator. Editing an old workflow saves it in the shared format under its existing ID. The old #rca URL redirects to Orchestrator. Tenant credentials, model billing plans and recurring schedules are unchanged.
