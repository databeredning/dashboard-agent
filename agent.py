import asyncio
import json

from openai import AsyncOpenAI

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


LLM_BASE_URL = "http://127.0.0.1:8080/v1"
LLM_MODEL = "qwen3-8b"

client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key="not-needed",
)


SYSTEM_PROMPT = """
You are a dashboard management agent.

You manage Grafana dashboards backed by InfluxDB.

Use the available tools to inspect the actual environment before making
assumptions about measurements, fields, tags, datasources, dashboards,
or dashboard versions.

When modifying a dashboard:
1. Read the dashboard first.
2. Use its current version for version-protected write operations.
3. Discover InfluxDB fields/tags when necessary.
4. Never invent measurement or field names.
5. Prefer high-level Grafana tools over constructing an entire dashboard
   manually.
6. Explain briefly what you changed after completing the operation.

For InfluxDB schema discovery:
- Use influx_list_measurements to discover measurements.
- Use influx_list_fields to discover fields.
- Use influx_list_tag_keys to discover tags.
- Use influx_list_tag_values to discover values of a specific tag.
- Do NOT use influx_inspect_measurement for schema discovery.
- Only use influx_inspect_measurement when actual sample values are required.
- Minimize tool calls and prefer the most specific tool available.
"""


def mcp_tools_to_openai(mcp_tools):
    tools = []

    for tool in mcp_tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
        )

    return tools


async def run_agent(session, user_message):
    tool_result = await session.list_tools()
    openai_tools = mcp_tools_to_openai(tool_result.tools)

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_message,
        },
    ]

    for _ in range(12):
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            tools=openai_tools,
            tool_choice="auto",
            temperature=0,
        )

        message = response.choices[0].message

        messages.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or ""

        for tool_call in message.tool_calls:
            name = tool_call.function.name

            try:
                arguments = json.loads(
                    tool_call.function.arguments or "{}"
                )
            except json.JSONDecodeError as exc:
                result_text = f"Invalid tool arguments: {exc}"

            else:
                print(f"\n[tool] {name}({arguments})")

                try:
                    result = await session.call_tool(
                        name,
                        arguments=arguments,
                    )

                    parts = []

                    for content in result.content:
                        text = getattr(content, "text", None)

                        if text is not None:
                            parts.append(text)

                    result_text = "\n".join(parts)

                except Exception as exc:
                    result_text = f"Tool error: {exc}"

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text,
                }
            )

    return "Agent stopped after reaching the tool-call limit."


async def main():
    server = StdioServerParameters(
        command="/opt/dashboard-agent/.venv/bin/python",
        args=["/opt/dashboard-agent/server.py"],
        cwd="/opt/dashboard-agent",
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()

            print("Dashboard Agent")
            print(f"Model: {LLM_MODEL}")
            print(f"MCP tools: {len(tools.tools)}")
            print("Type 'quit' to exit.\n")

            while True:
                try:
                    user_message = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                if not user_message:
                    continue

                if user_message.lower() in {
                    "quit",
                    "exit",
                    "q",
                }:
                    break

                try:
                    answer = await run_agent(
                        session,
                        user_message,
                    )

                    print(f"\n{answer}\n")

                except Exception as exc:
                    print(f"\nAgent error: {exc}\n")


if __name__ == "__main__":
    asyncio.run(main())
