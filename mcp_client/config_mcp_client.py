from __future__ import annotations

import asyncio
import json
import os
import traceback
from contextlib import AsyncExitStack
from typing import Dict, Any, List, Optional

import yaml
import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ========= ĐỌC CONFIG =========

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
CONFIG_PATH = os.path.join(ROOT_DIR, "config.yaml")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ========= OLLAMA HELPER =========

def call_ollama_chat(
    base_url: str,
    model: str,
    user_prompt: str,
    system_prompt: Optional[str] = None,
) -> str:
    url = f"{base_url}/api/chat"

    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"❌ Lỗi gọi Ollama: {e}"

    # Chuẩn OpenAI style
    if "message" in data and "content" in data["message"]:
        return data["message"]["content"]

    if "choices" in data and data["choices"]:
        return data["choices"][0].get("message", {}).get("content", "")

    return json.dumps(data, ensure_ascii=False)


def call_ollama_for_json(
    base_url: str,
    model: str,
    user_prompt: str,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Giống call_ollama_chat nhưng yêu cầu LLM trả JSON thuần.
    Có thêm lớp parse robust.
    """
    raw = call_ollama_chat(base_url, model, user_prompt, system_prompt)

    # Thử tìm block JSON trong raw
    try:
        # Nếu chuỗi đã là JSON
        return json.loads(raw)
    except Exception:
        pass

    # Thử cắt từ dấu { đầu tiên đến } cuối cùng
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        return json.loads(raw[start:end])
    except Exception:
        # Cuối cùng: trả fallback
        return {"tool": "none", "arguments": {}, "raw": raw}


# ========= GENERIC MCP CLIENT =========

class ConfigMCPClient:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.ollama_base = config["ollama"]["base_url"]
        self.ollama_model = config["ollama"]["model"]

        self.exit_stack = AsyncExitStack()

        # server_id -> ClientSession
        self.sessions: Dict[str, ClientSession] = {}

        # tool_name -> info {session, description, schema}
        self.tools: Dict[str, Dict[str, Any]] = {}

    async def connect_all_servers(self) -> None:
        servers_conf = self.config.get("servers", [])
        if not servers_conf:
            raise RuntimeError("config.yaml không có servers nào.")

        for srv in servers_conf:
            srv_id = srv["id"]
            cmd = srv["command"]
            args = srv.get("args", [])

            print(f"🔧 Spawn MCP server [{srv_id}]: {cmd} {' '.join(args)}")

            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=cmd,
                        args=args,
                        env=None,
                    )
                )
            )
            read, write = stdio_transport

            session = await self.exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await session.initialize()

            self.sessions[srv_id] = session

            # Lấy danh sách tools từ server này
            tools_resp = await session.list_tools()
            for t in tools_resp.tools:
                print(f"  ➕ Tool discovered: {t.name} ({srv_id})")
                self.tools[t.name] = {
                    "session": session,
                    "server_id": srv_id,
                    "description": t.description,
                    "schema": getattr(t, "inputSchema", None),
                }

        print(f"✅ Tổng số tools: {len(self.tools)}")

    async def cleanup(self) -> None:
        await self.exit_stack.aclose()

    # ---------- ROUTER ----------

    def _build_tools_description_for_router(self) -> str:
        """
        Chuẩn bị text mô tả tools cho LLM router.
        """
        items = []
        for name, info in self.tools.items():
            desc = info.get("description", "")
            schema = info.get("schema")
            items.append(
                {
                    "name": name,
                    "description": desc,
                    "inputSchema": schema,
                }
            )
        return json.dumps(items, ensure_ascii=False, indent=2)

    def ask_router(self, user_query: str) -> Dict[str, Any]:
        """
        Hỏi LLM: nên dùng tool nào (hoặc none) + arguments gì.
        """
        tools_desc = self._build_tools_description_for_router()

        system_prompt = (
            "Bạn là ROUTER CHO TOOLS.\n"
            "- Nhiệm vụ: chọn đúng tool (hoặc 'none') và arguments tương ứng.\n"
            "- CHỈ TRẢ VỀ JSON THUẦN, KHÔNG GIẢI THÍCH, KHÔNG TEXT THỪA.\n"
        )

        user_prompt = f"""
User hỏi: {user_query!r}

ĐÂY LÀ DANH SÁCH TOOLS:

{tools_desc}

YÊU CẦU:
- Nếu không tool nào phù hợp, trả về:
  {{"tool": "none", "arguments": {{}}}}

- Nếu có tool phù hợp, trả về:
  {{
    "tool": "<tên tool>",
    "arguments": {{
        // key:value đúng theo inputSchema nếu có
    }}
  }}

- Ví dụ: nếu user hỏi về email chưa đọc hôm nay, có thể:
  {{
    "tool": "gmail_list_today_unread",
    "arguments": {{"user_email": "synopex.no.reply@gmail.com"}}
  }}
"""
        result = call_ollama_for_json(
            self.ollama_base, self.ollama_model, user_prompt, system_prompt
        )
        # Chuẩn hóa
        tool = result.get("tool", "none")
        arguments = result.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        return {"tool": tool, "arguments": arguments}

    async def process_question(self, question: str) -> str:
        try:
            if not self.tools:
                return "❌ Chưa có tool nào được load."

            # 1. Hỏi router
            router = self.ask_router(question)
            tool_name = router["tool"]
            arguments = router["arguments"]

            print(f"[ROUTER] tool={tool_name}, args={arguments}")

            # 2. Nếu router chọn "none" -> chat thuần
            if tool_name == "none" or tool_name not in self.tools:
                answer = call_ollama_chat(
                    self.ollama_base,
                    self.ollama_model,
                    user_prompt=question,
                    system_prompt=(
                        "Bạn là trợ lý AI tiếng Việt thân thiện, ngắn gọn, "
                        "giải thích dễ hiểu."
                    ),
                )
                return answer

            # 3. Gọi tool tương ứng
            info = self.tools[tool_name]
            session: ClientSession = info["session"]

            try:
                tool_result = await session.call_tool(
                    tool_name, arguments=arguments
                )
            except Exception as e:
                traceback.print_exc()
                return f"❌ Lỗi khi gọi tool '{tool_name}': {e}"

            # tool_result.structured_content chứa JSON từ server
            structured = tool_result.structuredContent

            # 4. Nhờ LLM format JSON thành câu trả lời
            pretty = call_ollama_chat(
                self.ollama_base,
                self.ollama_model,
                user_prompt=(
                    "User hỏi: " + question + "\n\n"
                    "Dưới đây là JSON dữ liệu lấy được từ tool "
                    f"{tool_name}:\n\n"
                    + json.dumps(structured, ensure_ascii=False, indent=2)
                    + "\n\nHãy trả lời user bằng tiếng Việt, ngắn gọn, dễ hiểu. "
                      "Nếu là danh sách thì liệt kê rõ ràng."
                ),
                system_prompt="Bạn là trợ lý AI chuyên diễn giải dữ liệu JSON cho người dùng cuối.",
            )
            return pretty

        except Exception as e:
            traceback.print_exc()
            return f"❌ Lỗi nội bộ: {e}"

    async def chat_loop(self) -> None:
        print("🤖 Config MCP Client đã sẵn sàng! (dùng config.yaml)")
        print("Gõ 'exit' hoặc 'quit' để thoát.\n")

        while True:
            try:
                user_input = input("Bạn: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 Tạm biệt!")
                break

            if not user_input:
                continue

            if user_input.lower() in {"exit", "quit"}:
                print("👋 Tạm biệt!")
                break

            answer = await self.process_question(user_input)
            print(f"\nAssistant: {answer}\n")


async def main() -> None:
    cfg = load_config()
    client = ConfigMCPClient(cfg)
    try:
        await client.connect_all_servers()
        await client.chat_loop()
    finally:
        await client.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
