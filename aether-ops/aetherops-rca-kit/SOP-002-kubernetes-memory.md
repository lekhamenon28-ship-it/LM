# SOP-002: Kubernetes container memory exhaustion

Status: Illustrative draft, unapproved. Version: 0.1.
Topics: dynatrace,rca,sop,draft,kubernetes,memory,OOMKilled,restarts
Source incident: None supplied. Owner/approver/review date: to be assigned.

## Example scenario
Pods restart and service errors rise. Container memory exhaustion is a candidate cause, not an established fact.

## Diagnosis
1. Capture the Dynatrace problem, Davis evidence IDs, cluster, namespace, workload and affected service.
2. Check termination reasons, restart counts, container memory versus limits and node pressure at the incident time. A restart alone does not prove OOM.
3. Distinguish container-limit OOMKilled from node eviction, failing probes and deployment churn.
4. Correlate memory growth with traffic, release version and representative application diagnostics. Confirm whether a leak or workload increase is supported.

## Conditional remediation
If a regression is demonstrated, propose an approved compatible release rollback. Consider memory-limit/request changes only after checking node capacity and workload requirements. Preserve relevant evidence before replacement where feasible. Avoid repeated restarts as a claimed permanent fix.

## Rollback and validation
Record manifest and image versions before changes. Restore the prior approved configuration if the change fails, while accounting for compatibility requirements. Confirm stable replicas, no new OOM events, memory headroom and recovery of service error/latency indicators during the team's observation window. Escalate persistent growth to the application owner.

## Convert to an incident-based SOP
Add the resolved incident, termination evidence, verified cause, exact approved action and measured recovery. Obtain owner approval and replace draft status.
