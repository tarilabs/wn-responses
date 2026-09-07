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
With the *builtin* skills provider, it can't — a skill is a runtime API object,
not a file on disk. `BuiltinSkillsImpl.initialize()` is a no-op, its config has
only a `persistence` key, and `RegisteredResources` (models, vector_stores,
tool_groups) has no skills entry, so there is nothing declarative to hook. The
only way in is to POST a zip to `/v1alpha/skills` after the server is up.

So use a different provider. `ogx_skills_fs/` is a small out-of-tree `skills`
provider that reads skills off the filesystem, which makes the skill part of
the manifest — no Job, no initContainer, no client-side registration. See
[Serving skills from the filesystem](#serving-skills-from-the-filesystem);
`deploy/ogxserver.yaml` is the whole deployment.

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

**`ogx run --dry-run` cannot validate `module:` external providers.** Validation
takes the "listing" code path, which never imports the module, so it fails with
`Provider 'inline::filesystem' is not available for API 'Api.skills'` on a
config the real server starts from happily. Don't trust `--dry-run` as a gate
for external providers — start the server and check `GET /v1/providers`.

**Changing `provider_model_id` for an existing `model_id` fails at startup.**
The registry is persisted in the KV store, so re-pointing `demo-model` from
`gpt-4o-mini` to `gpt-4.1-mini` gives
`Object of type 'model' and identifier 'openai/demo-model' already exists with
conflicting field`. Use a fresh `OGX_STORAGE_DIR`, or a new `model_id`. That's
why `run.yaml` defaults to `~/.ogx/wn-responses-fs` rather than
`~/.ogx/wn-responses` — the latter may still hold the `gpt-4o-mini` binding.

**Skills are inherited across `previous_response_id`.** If a follow-up omits
`skills`, the previous response's skills carry over
(`effective_skills = request.skills if request.skills is not None else inherited_skills`).
Pass `skills: []` to deliberately drop them.

## Running it locally

```bash
export OPENAI_API_KEY=...
PYTHONPATH=. uv run ogx run run.yaml --insecure     # NOT `ogx go`

export OGX_MCP_TOKEN=<bearer token>
uv run python app.py
```

`PYTHONPATH=.` is what makes `module: ogx_skills_fs` importable, and running
from the repo root is what makes the config's default `skills_dir: ./skills`
resolve. Override it with `OGX_SKILLS_DIR` if you run from elsewhere.

`app.py` uploads nothing — the skill is already there — it looks the ID up by
name, then asks two questions: one the skill answers, one the MCP server
answers. Verified output, against a local MCP server standing in for the ROSA
one:

```
using skill skill-pizza-topping (version 1)

> What topping should I put on my pizza?
  [mcp] rhoai exposes: get_cluster_name
  any toppings will do, but Pineapple is forbidded as Matteo said so.

> What tools do you have available, and what can you tell me about the cluster?
  [mcp] rhoai exposes: get_cluster_name
  [mcp] called get_cluster_name -> rosa-mmortari-cluster-42
  I have a tool that can tell us the name of the current cluster. The name of the
  cluster we're currently working with is "rosa-mmortari-cluster-42". [...]
```

## Serving skills from the filesystem

`ogx_skills_fs/` is a ~150-line out-of-tree `skills` provider that serves skills
from a mounted directory instead of from uploads. With it, the skill exists as
soon as the server starts — no Job, no initContainer, no client registration.

It hangs off `module:` in the run config. When a provider entry carries one, the
resolver imports `<module>.provider`, calls `get_provider_spec()`, and uses the
returned spec — so an external provider needs a `provider.py` at the package
root and a `get_provider_impl(config, deps)`:

```yaml
skills:
- provider_id: filesystem
  provider_type: inline::filesystem
  module: ogx_skills_fs
  config:
    skills_dir: /skills
```

Each immediate subdirectory of `skills_dir` holding a `SKILL.md` becomes one
skill. Two properties fall out of this that the builtin provider doesn't have:

- **Skill IDs are deterministic** — `skills/pizza-topping/` becomes
  `skill-pizza-topping`, so clients can hardcode the ID instead of discovering
  it by listing. With the upload path you only learn the UUID at runtime.
- **Editing SKILL.md takes effect without a restart**, because the directory is
  rescanned per call. Verified live: editing the file mid-run changed the next
  response.

Writes are rejected with 501 (`create_skill`, `delete_skill`, …); the mounted
directory is the source of truth. Unknown IDs give the usual 400.

This is the only provider `run.yaml` configures, so
`curl -s localhost:8321/v1alpha/skills` returns `skill-pizza-topping` on a
freshly started server, with nothing having uploaded it. To go back to the
stock behaviour, swap that one block for
`provider_type: inline::builtin` with a `persistence` config.

Two caveats. The provider imports `parse_skill_manifest` from
`ogx.providers.inline.skills.builtin.manifest` to avoid reimplementing
frontmatter parsing — that's an internal path that could move between OGX
releases. And a ConfigMap mount is not a plain directory: it contains a `..data`
symlink to a timestamped directory plus one symlink per key, so a naive
`rglob("*")` yields every file twice (`['..2026_09_07_120000.0/SKILL.md',
'SKILL.md']` — measured against a simulated mount). `_is_hidden()` filters
those, so the bundle ends up with exactly one `SKILL.md` at the root.

## Deploying to OpenShift/Kubernetes

Install the [ogx-k8s-operator](https://github.com/ogx-ai/ogx-k8s-operator), set
the API key, apply one file:

```bash
kubectl apply -f deploy/ogxserver.yaml
kubectl edit  secret ogx-demo-secrets     # OPENAI_API_KEY, ships as "replace-me"
```

That file carries the Secret, the provider code, the skill, the run config and
the CR. Nothing runs after the apply — the skill is there as soon as the pod is
ready. It also drops the skills-on-PVC requirement, since the skill comes from
the ConfigMap and survives restarts and rescheduling for free.

The MCP bearer token is deliberately *not* in the server's config — it belongs
to whatever calls the Responses API. Only the client needs it.

### Packaging the provider: ConfigMap vs wheel

The ConfigMap-on-`PYTHONPATH` trick here is a workaround, not the operator's
recommendation. The operator has a design for this — `specs/001-deploy-time-
providers-l1/` — and it is **wheels, shipped in an OCI provider image**:

- `FR-002`: provider images MUST contain Python wheels in `/lls-provider/packages/`
- `FR-001`: plus a metadata file at `/lls-provider/lls-provider-spec.yaml`
- `FR-004` / `NFR-001`: wheels MUST vendor all dependencies — the installer has
  no network
- `FR-005`: the package MUST expose `get_provider_spec()` (which
  [ogx_skills_fs/provider.py](ogx_skills_fs/provider.py) already does)

An initContainer would `pip install` those wheels into a shared `emptyDir` and
prepend it to `PYTHONPATH`; a PVC for this was explicitly *rejected* in the
spec as needless complexity.

The catch: **it isn't built yet.** The spec is marked `Status: Draft`, its
`tasks.md` checkboxes are all unchecked, and there is no `externalProviders`
field in `api/v1beta1` or in the CRD YAML — `grep -rn "ExternalProvider" api/
pkg/ controllers/` returns nothing. So today the choice is really:

| | Works today | Effort | Use for |
|---|---|---|---|
| ConfigMap on `PYTHONPATH` | yes | none | demos, iteration |
| Wheel baked into a custom image | yes | build + registry | production |
| Wheel in an OCI provider image | no — unimplemented | — | the future |

For anything real, bake the wheel into the image
(`FROM ogxai/distribution-starter` + `pip install ogx_skills_fs-*.whl`) and drop
the `PYTHONPATH` env and the code ConfigMap from the CR. Building it as a proper
wheel now is also what makes the migration to `externalProviders` a repackaging
job rather than a rewrite. The ConfigMap route additionally inherits the 1 MiB
etcd object limit and can't carry compiled dependencies.

## Why not an initContainer or sidecar on the OGXServer pod

Uploading the skill from an initContainer was the obvious alternative to a Job.
It doesn't work, for two independent reasons — which is what pushed this repo to
the filesystem provider instead.

A plain initContainer must run to completion *before* the main container starts,
so there is no server to upload to yet — it would poll until timeout. A native
sidecar (`restartPolicy: Always`, k8s ≥1.29) would be logically fine, since
those start first but run concurrently.

Except the CRD can't express either. `WorkloadOverrides` is exactly
`ServiceAccountName`, `Env`, `Command`, `Args`, `Volumes`, `VolumeMounts` — no
`InitContainers`, no `Containers`, no `Lifecycle`, so no sidecar and no
`postStart` hook. `grep -rn InitContainers api/` in the operator returns
nothing.

Patching the Deployment out-of-band isn't durable either: `ApplyResources`
switches from server-side apply to a full `Update` whenever
`deploymentNeedsFullReplacement` trips (legacy CA-bundle volumes, a stale
user-config volume, a strategy change), and there's a test asserting
`"legacy init containers should be removed"`. It may survive a plain SSA
reconcile and then vanish on the next storage change.

Note that the operator does intend to grow initContainers — the external-
provider spec is built on them — but for installing provider wheels, not for
seeding data. Today no non-test Go file in the operator so much as mentions
`InitContainers`.

### Caveat on the deploy manifests

The local stack, the MCP wiring and the filesystem skills provider were all run
and verified against a live server. `deploy/ogxserver.yaml` was **not** applied
to a live cluster — I have no cluster access here — so treat it as
reviewed-but-unrun. What narrows the gap: the provider code and SKILL.md
embedded in it were diffed against the repo files that the local run exercised
and are byte-identical, and the config it carries is the same shape as the
`run.yaml` the server actually started from.

The ROSA MCP endpoint itself is also unverified — probing it returns 401 and I
was never given a token — so the MCP results above come from a local stand-in
server that speaks the same streamable-HTTP transport.
