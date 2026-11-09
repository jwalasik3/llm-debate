# GitHub Actions → Azure Container Apps

This deployment reuses the existing `llm-debate` Container App in France Central.
No VM, paid subscription upgrade, public database, or registry admin password is needed.
Keep the Azure for Students spending limit enabled.

## One-time Azure setup

Deploy `container-bootstrap.json` to the existing `llm-debate` resource group,
with repository `jwalasik3/llm-debate` and location `francecentral`.
The `oidcSubjectPrefix` parameter must match GitHub's token exactly. This fork uses
`repo:jwalasik3@119112221/llm-debate@1451692272`, including immutable owner/repository IDs.
The two federated credentials append `:environment:production` and `:environment:demo-power`.
New GitHub repositories use this format; name-only subjects do not match their tokens.
See [GitHub's OIDC reference](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).
Review permissions and costs before creating resources. It creates:

- A Basic Azure Container Registry: private images, with an ongoing registry charge even while the app is stopped.
- Standard LRS Blob Storage with anonymous access and shared-key authentication disabled.
  The private `debates` container holds JSON snapshots; seven-day soft delete is enabled.
- Three user-assigned identities: runtime, deployment, and demo power.
- GitHub OIDC trust restricted to this repository's `production` and `demo-power` environments.
- Runtime permissions to pull registry images and read/write snapshots; deployment permissions on this app,
  registry push, and assignment of the runtime identity; a custom power role restricted to read/start/stop/tags.
  The power identity cannot read secrets, run commands, or deploy code.

The template does not change the running sample. Its deployment outputs are non-secret GitHub variable values.
Storage and registry charges continue when compute is off. This setup does not create a budget or a billing cap.

## Runtime secrets

In the Container App's Secrets screen, create these names (do not commit their values):

| Azure secret | Purpose |
| --- | --- |
| `app-username` | Shared browser login |
| `app-password` | Long unique password |
| `groq-api-key` | Groq key |
| `gemini-api-key` | Gemini key |

The deployment requires both login secrets and at least one provider secret.
Omit the unused provider's secret; it will remain unavailable in the application.
The login grants access to all saved debates and controls.
Enter new authentication credentials yourself when browser automation hands control back.

## GitHub setup

Create environments `production` and `demo-power`, restricted to `main`.
Do not require manual environment approval for `demo-power`: scheduled cleanup must be able to run.
Only trusted maintainers should have permission to modify workflows or environment settings.

Create one repository Actions variable named `AZURE_CONFIG`, containing a JSON object
whose keys and values come from the bootstrap deployment outputs:

- `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_CONTAINER_APP`
- `AZURE_ACR_NAME`, `AZURE_STORAGE_ACCOUNT_URL`
- `AZURE_CLIENT_ID`, `AZURE_POWER_CLIENT_ID`
- `AZURE_RUNTIME_IDENTITY_ID`, `AZURE_RUNTIME_CLIENT_ID`

Include `APP_DOMAIN` in that same JSON object, set to
`llm-debate.politepond-cd58fd48.francecentral.azurecontainerapps.io` (hostname only).
All these values are configuration identifiers, not passwords or API keys.

Include these additional keys in the same `AZURE_CONFIG` JSON object (values are strings):

| Variable | Value |
| --- | --- |
| `DEPLOY_ENABLED` | `true` when Azure, secrets and cleanup are ready |
| `DEMO_ENABLED` | `true` before enabling deployment |
| `DEMO_OPERATORS` | Comma-separated GitHub logins, including your own `jwalasik3` |
| `DEMO_CONTROL_ISSUE` | Number of the dedicated control issue, if using comment commands |

No Azure client secret or LLM key is needed in GitHub. Before enabling deployment,
ensure scheduled Actions are enabled in the fork; forks may have schedules disabled by default.

## Deployment and use

Push to `main` or run **Test and deploy to Azure** manually. It tests, builds and pushes
an immutable image, checks runtime secret names, stops the previous engine, updates port 8000,
HTTPS, probes, identity and secret references, then starts a two-hour session.
It verifies `/healthz` and checks that application endpoints challenge anonymous requests.
A failed deployment/verification attempts to stop the app. It does not automatically roll back.

Use **Demo service - start or stop** → **Run workflow** → `start` / `stop` on `main`.
Maintainers need repository write access for manual runs. Allowlisted classmates with issue-comment
access can instead post exactly `/demo start` or `/demo stop` on the control issue.
Do not grant classmates workflow-editing permissions just to operate the demo.

Start extends expiry to two hours from the latest start, no later than 1 January 2027 UTC.
A scheduled job checks expiry approximately every 15 minutes. Missing/invalid expiry fails closed.
GitHub schedules can be delayed or disabled, so automatic stop is best effort and not a spending cap.
Stop manually after demos and verify **Stopped** in Azure. Starts after 31 December 2026 are rejected
according to the UTC cutoff in `container_power.py`.

## Data and background debates

Exactly one replica and one uvicorn worker run while enabled. This prevents an HTTP idle
period from interrupting a background debate. Explicit Stop shuts compute down; a URL visit
cannot start a stopped app. Each committed debate snapshot is stored in Blob Storage before
it is acknowledged locally, survives replica replacement, and uses conditional writes to reject
stale writers. An interrupted debate is marked interrupted after restart; it does not silently resume.

Local development still uses SQLite WAL. Never mount that database on Azure Files.
Blob storage is synchronous and intended for small college demos; startup loads saved snapshots
into memory. Large histories or high traffic require a different storage design.
