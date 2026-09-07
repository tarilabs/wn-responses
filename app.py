"""OGX Responses API demo: a remote MCP server + a local "pizza-topping" skill.

Run the server first (see README.md) -- note that `ogx go` will NOT work here,
because it does not enable the `skills` API:

    PYTHONPATH=. uv run ogx run run.yaml --insecure

Then:

    export OGX_MCP_TOKEN=<bearer token for the MCP server>
    uv run python app.py

Nothing here uploads the skill: run.yaml serves skills/ off the filesystem, so
the skill already exists when the server comes up.
"""

import os

import httpx
from openai import OpenAI

OGX_URL = os.environ.get("OGX_URL", "http://localhost:8321")
MODEL = os.environ.get("OGX_MODEL", "openai/demo-model")

MCP_SERVER_URL = os.environ.get(
    "OGX_MCP_URL",
    "https://rhoai-mcp-rhoai-mcp.apps.rosa.mmortari-rosa2.54u1.p3.openshiftapps.com/mcp",
)
MCP_TOKEN = os.environ.get("OGX_MCP_TOKEN")

SKILL_NAME = "pizza-topping"

client = OpenAI(base_url=f"{OGX_URL}/v1", api_key="fake")


def find_skill() -> str:
    """Look up the pizza-topping skill ID from /v1alpha/skills.

    The Skills API is served at /v1alpha/skills, not the /v1/skills that the
    OpenAI SDK's `client.skills.*` helpers target, so this uses raw httpx.

    With the filesystem provider the ID is deterministic and could just be
    hardcoded as "skill-pizza-topping". Looking it up by name keeps this
    working against the builtin provider too, where the ID is a random UUID
    assigned at upload time.
    """
    with httpx.Client(timeout=30) as http:
        resp = http.get(f"{OGX_URL}/v1alpha/skills")
        resp.raise_for_status()
        skills = resp.json()["data"]

    for skill in skills:
        if skill["name"] == SKILL_NAME:
            print(f"using skill {skill['id']} (version {skill['default_version']})")
            return skill["id"]

    found = ", ".join(s["name"] for s in skills) or "(none)"
    raise SystemExit(
        f"skill {SKILL_NAME!r} not found on {OGX_URL}; server has: {found}.\n"
        "Check that run.yaml's skills_dir points at this repo's skills/ directory."
    )


def mcp_tool() -> dict:
    """The remote MCP server, wired in per-request as a Responses tool.

    MCP is a property of the request, not of the server: OGX dials the MCP
    endpoint itself on each call, so nothing has to be registered in run.yaml.

    The token goes in `authorization` as the bare token -- OGX adds the
    "Bearer " prefix. Putting an Authorization key in `headers` is rejected
    with a 400.
    """
    tool = {
        "type": "mcp",
        "server_label": "rhoai",
        "server_url": MCP_SERVER_URL,
        "require_approval": "never",
    }
    if MCP_TOKEN:
        tool["authorization"] = MCP_TOKEN
    return tool


def ask(question: str, skill_id: str, tools: list[dict]) -> None:
    response = client.responses.create(
        model=MODEL,
        input=question,
        tools=tools,
        # `skills` is an OGX extension with no kwarg in the OpenAI SDK, so it
        # has to ride along in extra_body.
        extra_body={"skills": [skill_id]},
    )

    print(f"\n> {question}")
    for item in response.output:
        if item.type == "mcp_list_tools":
            names = ", ".join(t.name for t in item.tools) or "(none)"
            print(f"  [mcp] {item.server_label} exposes: {names}")
        elif item.type == "mcp_call":
            status = f"error: {item.error}" if item.error else str(item.output)[:80]
            print(f"  [mcp] called {item.name} -> {status}")
    print(f"  {response.output_text}")


def main() -> None:
    if not MCP_TOKEN:
        print("warning: OGX_MCP_TOKEN is not set; the MCP server will reject the connection.\n")

    skill_id = find_skill()
    tools = [mcp_tool()]

    # Hits the skill: the SKILL.md policy answers this one.
    ask("What topping should I put on my pizza?", skill_id, tools)

    # Hits the MCP server instead; the skill stays out of the way.
    ask("What tools do you have available, and what can you tell me about the cluster?", skill_id, tools)


if __name__ == "__main__":
    main()
