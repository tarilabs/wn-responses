"""OGX Responses API demo: a remote MCP server + a local "pizza-topping" skill.

Run the server first (see README.md) -- note that `ogx go` will NOT work here,
because it does not enable the `skills` API:

    uv run ogx run run.yaml --insecure

Then:

    export OGX_MCP_TOKEN=<bearer token for the MCP server>
    uv run python app.py
"""

import os
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
from openai import OpenAI

OGX_URL = os.environ.get("OGX_URL", "http://localhost:8321")
MODEL = os.environ.get("OGX_MODEL", "openai/demo-model")

MCP_SERVER_URL = os.environ.get(
    "OGX_MCP_URL",
    "https://rhoai-mcp-rhoai-mcp.apps.rosa.mmortari-rosa2.54u1.p3.openshiftapps.com/mcp",
)
MCP_TOKEN = os.environ.get("OGX_MCP_TOKEN")

SKILL_DIR = Path(__file__).parent / "skills" / "pizza-topping"

client = OpenAI(base_url=f"{OGX_URL}/v1", api_key="fake")


def ensure_skill() -> str:
    """Upload skills/pizza-topping as a skill bundle, returning its skill ID.

    The Skills API lives at /v1alpha/skills and takes a multipart field named
    `file`. The stock `client.skills.*` helpers in the OpenAI SDK target
    /v1/skills with a field named `files`, so they do not work against OGX
    1.3.1 -- hence the raw httpx calls here.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SKILL_DIR.rglob("*")):
            if path.is_file():
                # SKILL.md must sit at the archive root, not under a top-level
                # folder, or the bundle is rejected.
                zf.write(path, path.relative_to(SKILL_DIR).as_posix())
    bundle = buf.getvalue()

    skills_url = f"{OGX_URL}/v1alpha/skills"
    with httpx.Client(timeout=60) as http:
        existing = http.get(skills_url)
        existing.raise_for_status()
        for skill in existing.json()["data"]:
            if skill["name"] == "pizza-topping":
                # Already there: push the current SKILL.md as a new default version.
                resp = http.post(
                    f"{skills_url}/{skill['id']}/versions",
                    files={"file": ("pizza-topping.zip", bundle, "application/zip")},
                    data={"default": "true"},
                )
                resp.raise_for_status()
                print(f"updated skill {skill['id']} to version {resp.json()['version']}")
                return skill["id"]

        resp = http.post(
            skills_url,
            files={"file": ("pizza-topping.zip", bundle, "application/zip")},
        )
        resp.raise_for_status()
        skill_id = resp.json()["id"]
        print(f"created skill {skill_id}")
        return skill_id


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

    skill_id = ensure_skill()
    tools = [mcp_tool()]

    # Hits the skill: the SKILL.md policy answers this one.
    ask("What topping should I put on my pizza?", skill_id, tools)

    # Hits the MCP server instead; the skill stays out of the way.
    ask("What tools do you have available, and what can you tell me about the cluster?", skill_id, tools)


if __name__ == "__main__":
    main()
