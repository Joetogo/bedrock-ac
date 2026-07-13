# neat-graph-bedrock Web Console (Phase 1)

Authenticated chat UI for the AgentCore Runtime. Design:
`docs/superpowers/specs/2026-07-08-web-console-design.md`.

## Deploy (us-east-1)

> **No `make`?** If `make` is not installed (e.g. on a bare Windows host), run the
> commands inside the corresponding Makefile target directly — `webapp-deploy`,
> `webapp-build`, and `webapp-sync` are plain `sam`/`npm`/`aws` commands (see the
> root `Makefile` for the exact invocations and substitute the variables by hand).

1. Get the existing stack's user pool id:
   `aws cloudformation describe-stacks --stack-name neat-graph-bedrock --query "Stacks[0].Outputs" --region us-east-1`
   → note `UserPoolId`.
2. Deploy the backend + hosting:
   `make webapp-deploy POOL_ID=<UserPoolId>`
   Record the outputs: `ApiBaseUrl`, `AppClientId`, `CognitoDomain`, `SiteBucketName`,
   `SiteUrl`, `DistributionId`.
3. Tighten CORS to the CloudFront origin (recommended, defense-in-depth). The
   `AllowedOrigin` SAM parameter defaults to `*` because at first deploy the
   CloudFormation parameter default can't reference the not-yet-created
   `SiteUrl`/CloudFront domain. Now that you have `SiteUrl` from step 2, redeploy
   pinning CORS to it:
   `make webapp-deploy POOL_ID=<UserPoolId> ORIGIN=<SiteUrl>`
   Leaving `AllowedOrigin` as `*` still works and is not a security hole by
   itself — the API is protected by the Cognito JWT authorizer, so a valid
   bearer token is required on every request, and browsers don't auto-attach
   `Authorization` headers cross-origin. Tightening `AllowedOrigin` to the
   CloudFront `SiteUrl` is still recommended as defense-in-depth.
4. Configure the frontend env (`webapp/frontend/.env.local`, copy from
   `.env.local.example`) with `NEXT_PUBLIC_API_BASE=<ApiBaseUrl>`,
   `NEXT_PUBLIC_COGNITO_DOMAIN=<CognitoDomain>`, `NEXT_PUBLIC_CLIENT_ID=<AppClientId>`,
   `NEXT_PUBLIC_REDIRECT_URI=<SiteUrl>/callback`.
5. Build + upload:
   `make webapp-build && make webapp-sync BUCKET=<SiteBucketName> DIST_ID=<DistributionId>`
6. Create a test user:
   `aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username you@example.com`
   then set a permanent password with `admin-set-user-password`.

## Manual E2E checklist

- [ ] Visit `<SiteUrl>` → redirected to the Cognito Hosted UI; sign in.
- [ ] Ask "List the Neat rooms." → an answer returns.
- [ ] Follow up "How many is that?" in the same thread → memory recall works.
- [ ] Reload the page → the conversation reappears in the sidebar.
- [ ] Delete the conversation → it disappears; the thread clears.
- [ ] Sign in as a second Cognito user → the first user's threads are NOT visible.
- [ ] Confirm read-only: the agent only answers; no upstream writes occur.
