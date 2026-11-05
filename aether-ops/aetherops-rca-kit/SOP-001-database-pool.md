# SOP-001: Database connection pool exhaustion

Status: Illustrative draft, unapproved. Version: 0.1.
Topics: dynatrace,rca,sop,draft,database,connection pool,latency,timeouts
Source incident: None supplied. Owner/approver/review date: to be assigned.

## Example scenario
A service develops elevated latency and database connection acquisition timeouts. Pool exhaustion is a hypothesis until telemetry confirms it; database slowness or application connection leaks can produce similar symptoms.

## Diagnosis
1. Record problem ID, onset, affected service and Davis rootCauseEntity/evidence IDs.
2. Compare connection usage, acquisition wait, active queries and database capacity against baseline during the same window.
3. Inspect traces and logs for pool timeout errors, long transactions and failure to return connections. Check recent deployment/configuration changes.
4. Separate a saturated database from an undersized or leaking application pool. Missing metrics must remain missing in the report.

## Conditional remediation
Ask the service/database owner to review the findings. If a recent release is demonstrated to cause a connection leak, propose an approved rollback to a known compatible release. If slow queries or lock contention are confirmed, follow the database team's procedure. Increasing a pool requires capacity review; it can worsen database overload. Do not terminate transactions or restart services automatically.

## Rollback and validation
Record the original configuration and deployment version before changes. Revert an unsuccessful change using the approved release/configuration procedure. Confirm connection waits, error rate, database load and service latency return to agreed service thresholds over the team's observation window. Record those thresholds and measurements; do not invent values.

## Convert to an incident-based SOP
Attach the resolved incident, actual cause, approved action, rollback outcome and before/after measurements. Obtain owner approval and replace this draft status.
