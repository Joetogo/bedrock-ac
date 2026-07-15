# Runbook — Secret Rotation (Cognito gateway client)

**Use when:** a credential is exposed — committed to a tracked file, pushed to a
remote, logged, or pasted somewhere untrusted. Treat a secret as **compromised
the instant it leaves a trusted boundary**; rotate, don't just delete.

## Principles

- Real secrets live **only** in a git-ignored `.env` or in **Secrets Manager**.
  Never in a tracked file. `env.example` / `*.env.local.example` hold
  **placeholders only** (`REPLACE_ME`).
- **Editing a file does not remove a secret from git history.** Every past
  commit still contains the old value. Once exposed, the *only* real fix is to
  **rotate the credential** — scrubbing the working tree is not enough.
- Client-side git hooks (the repo's `pre-push` guard) are an **advisory speed
  bump**, not enforcement. Real enforcement is server-side (GitHub push
  protection) and in never having the secret in history.

## Rotate the Cognito app client (machine-to-machine, client-credentials)

Cognito **cannot regenerate a client secret in place** — create a replacement,
cut over, then delete the old one. Run these in order so there is **no outage**.
Region is the stack's region (e.g. `us-east-1` for the POC). Each command is a
single line.

**1. Find the user pool:**

```bash
aws cognito-idp list-user-pools --max-results 60 --region <REGION> --query "UserPools[?contains(Name,'neat-graph-bedrock')].[Id,Name]" --output table
```

**2. Inspect the OLD client to copy its config:**

```bash
aws cognito-idp describe-user-pool-client --user-pool-id <POOL_ID> --client-id <OLD_CLIENT_ID> --region <REGION>
```

**3. Create the replacement (returns the new id + secret; match the old scopes):**

```bash
aws cognito-idp create-user-pool-client --user-pool-id <POOL_ID> --client-name neat-graph-bedrock-gw-client-v2 --generate-secret --allowed-o-auth-flows client_credentials --allowed-o-auth-scopes "neat-graph-bedrock-api/invoke" --allowed-o-auth-flows-user-pool-client --region <REGION> --query "UserPoolClient.[ClientId,ClientSecret]" --output text
```

**4. Find the Secrets Manager secret name:**

```bash
aws secretsmanager list-secrets --region <REGION> --query "SecretList[?contains(Name,'neat-graph-bedrock')].Name" --output table
```

**5. Update the secret.** Put the JSON in a git-ignored file `newsecret.json` (`{"client_id":"<NEW_CLIENT_ID>","client_secret":"<NEW_CLIENT_SECRET>"}`) so quoting works in PowerShell and bash alike:

```bash
aws secretsmanager put-secret-value --secret-id neat-graph-bedrock/gateway-client --secret-string file://newsecret.json --region <REGION>
```

If the runtime bakes the client id from SSM (not just the secret), also:

```bash
aws ssm put-parameter --name /neat-graph-bedrock/runtime/CLIENT_ID --value "<NEW_CLIENT_ID>" --type String --overwrite --region <REGION>
```

**6. VERIFY the new client mints a token BEFORE deleting the old one:**

```bash
curl -s -X POST "https://<COGNITO_DOMAIN>.auth.<REGION>.amazoncognito.com/oauth2/token" -H "Content-Type: application/x-www-form-urlencoded" -u "<NEW_CLIENT_ID>:<NEW_CLIENT_SECRET>" -d "grant_type=client_credentials&scope=neat-graph-bedrock-api/invoke"
```

Expect a JSON `access_token`. If the client id is env-baked, redeploy: `python scripts/redeploy_runtime.py --build`

**7. Kill the compromised client (only after step 6 succeeds):**

```bash
aws cognito-idp delete-user-pool-client --user-pool-id <POOL_ID> --client-id <OLD_CLIENT_ID> --region <REGION>
```

**8. Update local `.env`** with the new `CLIENT_ID` / `CLIENT_SECRET`; delete `newsecret.json`.

## Enforce server-side — GitHub secret-scanning push protection

The real wall that would have rejected the bad push. Free on **public** repos;
on a **private** repo it needs GitHub Advanced Security / Secret Protection.

Check current state:

```bash
gh api repos/<owner>/<repo> --jq '.security_and_analysis'
```

Enable secret scanning + push protection:

```bash
echo '{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}' | gh api -X PATCH repos/<owner>/<repo> --input -
```

## Guardrails already in this repo

- `.gitignore` ignores `.env`, `.env.*`, `.bedrock_agentcore*`, `.publish-redact`.
- `env.example` and `webapp/frontend/.env.local.example` are **placeholders only**,
  and `scripts/publish-public.sh` regenerates them from placeholders on every
  publish — so a real value can never reach the public repo through the pipeline.
- `scripts/publish-public.sh` publishes a **scrubbed, fresh-history** snapshot
  and aborts on any secret the guard scan finds.
- `.git/hooks/pre-push` blocks a raw push of local history to the public remote
  (advisory; override only with `ALLOW_RAW_PUSH=1`).
