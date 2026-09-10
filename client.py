import asyncio
import anyio
import mcp_types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError
from contextlib import asynccontextmanager

import os
from pydantic import ValidationError
from mcp.shared.message import SessionMessage

from config import (
    OBSIDIAN_VAULT,
    STDIO_SERVER_COMMAND,
    STDIO_SERVER_ARGS,
    WS_URL,
)

@asynccontextmanager
async def websocket_client(url: str):
    async with websockets.connect(url) as websocket:
        read_send, read_recv = anyio.create_memory_object_stream(0)
        write_send, write_recv = anyio.create_memory_object_stream(0)

        async def ws_reader():
            try:
                async for raw in websocket:
                    try:
                        msg = types.jsonrpc_message_adapter.validate_json(raw, by_name=False)
                    except ValidationError as exc:
                        await read_send.send(exc)
                        continue
                    session_message = SessionMessage(msg)
                    await read_send.send(session_message)
            except ConnectionClosedError:
                pass
            finally:
                await read_send.aclose()

        async def ws_writer():
            try:
                async for session_message in write_recv: #SessionMessage.message
                    raw = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    await websocket.send(raw)
            except ConnectionClosed:
                pass

        async with asyncio.TaskGroup() as tg:
            tg.create_task(ws_reader())
            tg.create_task(ws_writer())
            yield read_recv, write_send

async def run_std_client():
    server_params = StdioServerParameters(
        command = STDIO_SERVER_COMMAND,
        args = STDIO_SERVER_ARGS,
        env = {"OBSIDIAN_VAULT_PATH" : str(OBSIDIAN_VAULT),
              "MCP_TRANSPORT": "stdio"}  # Клиент сам запускает сервер как subprocess
    )

    async with stdio_client(server_params) as (read_stream, write_stream): # потоки из коробки stdio_client(server_params)
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"{123} Available tools: {[t.name for t in tools.tools]}")

            result = await session.call_tool("list_files", arguments={"path": ""})
            res = await session.call_tool("list_folders", arguments={"path": ""})
            print(res.content[0].text)
            print(result.content)

async def run_ws_client():

    #if os.getenv("MCP_TRANSPORT") != "ws":  #Не будет ли это мешать stdio?
        #os.environ["MCP_TRANSPORT"] = "ws"

    async with websocket_client(WS_URL) as (read_stream, write_stream): # потоки из yield в своём contextmanager websocket_client

        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"{os.getenv("MCP_TRANSPORT")} Available tools: {[t.name for t in tools.tools]}")

            result = await session.call_tool("list_files", arguments={"path": ""})
            res = await session.call_tool("list_folders", arguments={"path": ""})
            print(res.content[0].text)
            print(result.content)

async def main():
    ws_server = asyncio.create_task(run_ws_client())
    std_server = asyncio.create_task(run_std_client())
    await asyncio.gather(ws_server, std_server)   #Это два полностью изолированных MCP-сеанса. Общее у них - только путь к vault на диске

if __name__ == "__main__":
    asyncio.run(main())