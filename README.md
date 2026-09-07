# wn-responses

An OGX Responses API demo that wires together a remote MCP server and a
`pizza-topping` skill.

Everything below was verified against **OGX 1.3.1** and **openai-python 3.8.0**,
running the server locally and calling it.

## The two questions this answers

**Does the MCP connection belong on the OGXServer or in the Responses API?**
In the Responses API, per request. The builtin responses provider imports
`list_mcp_tools` / `invoke_mcp_tool` directly from `ogx.providers.utils.tools.mcp`
and dials the MCP endpoint itself — it does not route through the
`remote::model-context-protocol` tool_runtime provider. That provider exists for
the older tool_groups/Agents path. So there is nothing to register server-side:
put the MCP server in the request's `tools` array and the credentials stay with
the caller.

**How does an OGXServer "mount" a SKILL.md?**
It doesn't. A skill is a runtime API object, not a file on disk. Grepping
`ogx-k8s-operator` for "skill" turns up only the `skills` entry in the sample
config's `apis` list — there is no CRD field for it. You upload a zip to
`/v1alpha/skills` after the server is up, and you give the server a PVC so the
upload survives restarts. `deploy/03-skill-upload-job.yaml` does exactly that,
keeping SKILL.md in a ConfigMap as the source of truth.

## Gotchas worth knowing up front

These all cost real debugging time, so they're listed with what actually happens.

**`ogx go` cannot run this demo.** It auto-detects providers and hardcodes
`responses` + `tool_runtime` into the API list, but never `skills`. Against the
`ogx go` server, `/v1alpha/skills` returns 404. Use `ogx run run.yaml`.

**`skills` depends on the `files` API.** The provider spec declares
`api_dependencies=[Api.files]` — bundle blobs are stored through the Files API.
Enabling `skills` without `files` fails to start.

**SKILL.md must be at the zip root.** OGX checks `entry.filename == "SKILL.md"`.
OpenAI's hosted Skills API expects a single top-level *folder* containing
SKILL.md; do that here and you get
`Failed to validate skill bundle: SKILL.md not found at archive root`.
Frontmatter must also include `name`.

**`client.skills.*` in the OpenAI SDK does not work against OGX 1.3.1.** The SDK
POSTs to `/v1/skills` with a multipart field named `files`. OGX serves
`/v1alpha/skills` with a field named `file`. Both the path and the field name
differ, so `app.py` uses raw `httpx`.

**`skills` needs `extra_body`.** It's a top-level field on OGX's
`CreateResponseRequest`, but `responses.create()` has no such kwarg, so pass
`extra_body={"skills": [skill_id]}`.

**The MCP token goes in `authorization`, not `headers`.** Pass the bare token —
OGX prepends `Bearer `. Putting an Authorization key in `headers` is rejected:
`400 ... Authorization credentials must be passed via the 'authorization'
parameter, not 'headers'`. The field is declared `exclude=True`, so the token is
not persisted onto the stored response object.

**OGX skills are unconditional context injection, and weak models over-apply
them.** Every ID in `skills` has its SKILL.md concatenated into a system message
on every request — there is no model-driven decision about whether the skill is
relevant, unlike Claude's progressive disclosure. With `gpt-4o-mini`, the pizza
policy hijacked `"What is 2+2?"`. `gpt-4.1-mini` and `gpt-4o` both gate it
correctly, which is why `run.yaml` uses `gpt-4.1-mini`. The SKILL.md also needs
an explicit "when this does NOT apply" section.

**Skills are inherited across `previous_response_id`.** If a follow-up omits
`skills`, the previous response's skills carry over
(`effective_skills = request.skills if request.skills is not None else inherited_skills`).
Pass `skills: []` to deliberately drop them.

## Running it locally

```bash
export OPENAI_API_KEY=...
uv run ogx run run.yaml --insecure          # NOT `ogx go`

export OGX_MCP_TOKEN=<bearer token>
uv run python app.py
```

`app.py` zips `skills/pizza-topping/`, uploads it (creating a new default
version if the skill already exists), then asks two questions — one the skill
answers, one the MCP server answers.

Verified output, against a local MCP server standing in for the ROSA one:

```
updated skill skill-5b9ac225... to version 3

> What topping should I put on my pizza?
  [mcp] rhoai exposes: get_cluster_name
  any toppings will do, but Pineapple is forbidded as Matteo said so.

> What tools do you have available, and what can you tell me about the cluster?
  [mcp] rhoai exposes: get_cluster_name
  [mcp] called get_cluster_name -> rosa-mmortari-cluster-42
  The available tool is used to return the name of the current cluster, which is "rosa-mmortari-cluster-42."
```

## Deploying to OpenShift/Kubernetes

Install the [ogx-k8s-operator](https://github.com/ogx-ai/ogx-k8s-operator), then:

```bash
kubectl apply -f deploy/01-ogx-config.yaml    # apis: must include skills + files
kubectl edit  secret ogx-demo-secrets          # or set OPENAI_API_KEY first
kubectl apply -f deploy/02-ogxserver.yaml      # OGXServer + PVC at /.ogx
kubectl apply -f deploy/03-skill-upload-job.yaml
```

The Job waits for `/v1/health`, then zips the ConfigMap's SKILL.md and POSTs it.
Re-running it pushes a new default version rather than duplicating the skill, so
it is safe as a post-sync hook.

The MCP bearer token is deliberately *not* in the server's config — it belongs
to whatever calls the Responses API. Only the client needs it.

### Caveat on the deploy manifests

The local stack, the skill upload flow, and the MCP wiring were all run and
verified. The Kubernetes manifests were not applied to a live cluster — I have
no cluster access here — so treat `deploy/` as reviewed-but-unrun. The upload
Job's script *was* extracted from the YAML and executed against the real server,
so the multipart encoding and endpoints in it are known-good.
