import httpx
from openai import OpenAI

client = OpenAI(base_url="https://localhost:8321/v1", api_key="fake", http_client=httpx.Client(verify=False))

response = client.responses.create(
    model="claude-opus-4-7",
    input="What is OGX?",
)
print(response.output_text)
