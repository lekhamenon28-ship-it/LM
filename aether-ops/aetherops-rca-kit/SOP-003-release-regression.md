# SOP-003: Service regression following a deployment

Status: Illustrative draft, unapproved. Version: 0.1.
Topics: dynatrace,rca,sop,draft,deployment,release,errors,latency
Source incident: None supplied. Owner/approver/review date: to be assigned.

## Example scenario
A service shows increased errors or latency around a release. Timing alone does not establish causation.

## Diagnosis
1. Record the problem ID, onset, Davis rootCauseEntity, evidence IDs and impact.
2. Compare release/change timestamps against symptom onset, including clock/timezone differences.
3. Compare changed versus unchanged instances, request paths and dependencies under comparable traffic.
4. Inspect representative failing traces and logs. Check alternative explanations such as dependency degradation, capacity saturation and configuration changes.

## Conditional remediation
If evidence implicates the release, have the service owner assess an approved rollback or feature disablement. Confirm database migration and configuration compatibility before rollback. If rollback is unsafe, escalate for a forward fix or approved traffic mitigation. Do not close the problem based only on successful deployment status.

## Rollback and validation
Record current and target versions and the recovery plan before action. Use the team's approved deployment procedure. Validate actual request success, error rate, latency, dependency health and business checks over an agreed observation window. Record adverse effects and reverse an unsuccessful mitigation through the approved procedure.

## Convert to an incident-based SOP
Attach the resolved incident, release identifier, causal evidence, action approval, exact resolution and before/after measurements. Obtain owner approval and replace draft status.
