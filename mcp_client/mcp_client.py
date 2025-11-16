from __future__ import annotations

import asyncio
import json
import threading
import os
import time
import traceback
import re
from contextlib import AsyncExitStack
from typing import Optional, Dict, Any, List

import requests
import pygame
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

# ======= CẤU HÌNH =======

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma3:1b"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(BASE_DIR, "..", "mcp_server_music", "mcp_server.py")

# ======= INIT PYGAME MIXER =======
_mixer_initialized = False

def init_mixer_once() -> None:
    global _mixer_initialized
    if _mixer_initialized:
        return
    try:
        pygame.mixer.init()
        _mixer_initialized = True
        print("🔊 pygame.mixer đã được khởi tạo.")
    except Exception as e:
        print(f"⚠️ Không khởi tạo được pygame.mixer: {e}")

# ======= HÀM GỌI OLLAMA =======

def call_ollama(
    user_prompt: str,
    system_prompt: str | None = None,
) -> str:
    chat_url = f"{OLLAMA_BASE_URL}/api/chat"

    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    chat_payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }

    try:
        resp = requests.post(chat_url, json=chat_payload, timeout=120)
    except requests.exceptions.RequestException as e:
        return f"[Lỗi kết nối Ollama /api/chat: {e}]"

    if resp.status_code == 404:
        gen_url = f"{OLLAMA_BASE_URL}/api/generate"
        prompt_text = ""
        if system_prompt:
            prompt_text += f"System: {system_prompt}\n\n"
        prompt_text += f"User: {user_prompt}"

        gen_payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt_text,
            "stream": False,
        }

        try:
            resp2 = requests.post(gen_url, json=gen_payload, timeout=120)
            resp2.raise_for_status()
        except requests.exceptions.RequestException as e:
            return f"[Lỗi gọi Ollama /api/generate: {e}]"

        try:
            data = resp2.json()
        except Exception as e:
            return f"[Lỗi parse JSON /api/generate: {e}, body={resp2.text!r}]"

        text = data.get("response")
        if isinstance(text, str):
            return text
        return json.dumps(data, ensure_ascii=False)

    try:
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return f"[Lỗi HTTP /api/chat: {e}, body={resp.text!r}]"

    try:
        data = resp.json()
    except Exception as e:
        return f"[Lỗi parse JSON /api/chat: {e}, body={resp.text!r}]"

    if "message" in data and isinstance(data["message"], dict):
        content = data["message"].get("content")
        if isinstance(content, str):
            return content

    if "choices" in data and data["choices"]:
        return data["choices"][0].get("message", {}).get("content", "")

    return json.dumps(data, ensure_ascii=False)

# ======= PHÁT NHẠC LOCAL BẰNG PYGAME (THẬT) =======

def play_audio_from_path(path: str) -> None:
    """
    Phát file nhạc local bằng pygame.mixer trong thread riêng.
    """

    def _worker(p: str) -> None:
        try:
            init_mixer_once()
            if not pygame.mixer.get_init():
                print("⚠️ pygame.mixer chưa sẵn sàng, không phát được.")
                return
            if not os.path.exists(p):
                print(f"⚠️ File không tồn tại: {p}")
                return

            print(f"▶️ Đang phát file: {p}")
            # Dừng bài đang phát (nếu có)
            pygame.mixer.music.stop()
            pygame.mixer.music.load(p)
            pygame.mixer.music.play()

            # Đợi đến khi phát xong
            while pygame.mixer.music.get_busy():
                time.sleep(0.1)
                
            print("✅ Phát nhạc hoàn tất")
                
        except Exception as e:
            print(f"⚠️ Lỗi phát nhạc: {e}")
            traceback.print_exc()

    t = threading.Thread(target=_worker, args=(path,), daemon=True)
    t.start()

# ======= MCP CLIENT =======

class MusicMCPClient:
    def __init__(self) -> None:
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()

    async def connect_to_server(self) -> None:
        print(f"🔧 Đang spawn MCP server: {SERVER_SCRIPT}")
        params = StdioServerParameters(
            command="python",
            args=[SERVER_SCRIPT],
            env=None,
        )

        stdio_transport = await self.exit_stack.enter_async_context(
            stdio_client(params)
        )
        read, write = stdio_transport

        self.session = await self.exit_stack.enter_async_context(
            ClientSession(read, write)
        )

        await self.session.initialize()

        tools_resp = await self.session.list_tools()
        tool_names = [t.name for t in tools_resp.tools]
        print("✅ MCP server đã spawn & kết nối. Tools:", tool_names)

    def _clean_query_from_user_input(self, query: str) -> str:
        """Làm sạch query từ input người dùng - loại bỏ các từ chỉ hành động"""
        action_words = ['phát', 'mở', 'bật', 'nghe', 'play', 'bài', 'nhạc', 'cho', 'tôi', 'tớ', 'mình']
        
        words = query.lower().split()
        cleaned_words = [word for word in words if word not in action_words]
        
        return ' '.join(cleaned_words).strip()

    def _extract_track_info(self, track_data: Dict[str, Any]) -> tuple[str, str]:
        """
        Extract track name và file path từ track data với nhiều format khác nhau
        """
        # Các key có thể có cho track name
        name_keys = ['track_name', 'name', 'title', 'song_name']
        track_name = 'Unknown track'
        
        for key in name_keys:
            if key in track_data and track_data[key]:
                track_name = track_data[key]
                break
        
        # Các key có thể có cho file path  
        path_keys = ['file_path', 'path', 'file', 'filepath']
        file_path = None
        
        for key in path_keys:
            if key in track_data and track_data[key]:
                file_path = track_data[key]
                break
        
        return track_name, file_path

    def _parse_tool_result(self, tool_result) -> List[Dict[str, Any]]:
        """
        Parse kết quả từ MCP tool call thành list tracks
        Xử lý đặc biệt cho cấu trúc {'result': [...]}
        """
        tracks = []
        
        try:
            print(f"[DEBUG] Tool result type: {type(tool_result)}")
            
            # Lấy structuredContent
            if hasattr(tool_result, "structuredContent") and tool_result.structuredContent:
                content = tool_result.structuredContent
                print(f"[DEBUG] structuredContent: {content}")
                
                # GIẢI QUYẾT TRIỆT ĐỂ: luôn tìm list tracks cuối cùng
                def extract_tracks(data):
                    if isinstance(data, dict) and 'result' in data:
                        return extract_tracks(data['result'])
                    elif isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        return [data]
                    else:
                        return []
                
                tracks = extract_tracks(content)
                print(f"[DEBUG] Extracted {len(tracks)} tracks")
                
        except Exception as e:
            print(f"[DEBUG] Lỗi parse tool result: {e}")
            traceback.print_exc()
        
        print(f"[DEBUG] Final tracks: {len(tracks)}")
        if tracks:
            print(f"[DEBUG] First track: {tracks[0]}")
        
        return tracks

    def ask_router(self, query: str) -> Dict[str, Any]:
        """
        Router thông minh hơn với fallback mạnh mẽ
        """
        # Fallback dựa trên từ khóa trước khi gọi LLM
        query_lower = query.lower()
        
        # QUY TẮC FALLBACK RÕ RÀNG
        if any(word in query_lower for word in ['phát', 'mở', 'bật', 'nghe', 'play']):
            clean_query = self._clean_query_from_user_input(query)
            return {
                "mode": "search_and_play", 
                "arguments": {"query": clean_query, "limit": 1}
            }
        elif any(word in query_lower for word in ['danh sách', 'liệt kê', 'hiển thị', 'có những bài', 'tất cả']):
            return {"mode": "list", "arguments": {"limit": 10}}
        elif any(word in query_lower for word in ['tìm', 'gợi ý', 'nhạc', 'bài hát']):
            clean_query = self._clean_query_from_user_input(query)
            return {
                "mode": "search", 
                "arguments": {"query": clean_query, "limit": 5}
            }
        else:
            # Chỉ gọi LLM khi không rõ ràng
            system_prompt = (
                "BẠN LÀ ROUTER - CHỈ TRẢ VỀ JSON. KHÔNG CHÀO, KHÔNG GIẢI THÍCH.\n\n"
                "PHÂN LOẠI:\n"
                "- 'chat': câu hỏi thông thường, không liên quan nhạc\n"
                "- 'list': yêu cầu danh sách nhạc\n" 
                "- 'search': tìm nhạc nhưng không phát\n"
                "- 'search_and_play': tìm và phát nhạc ngay\n\n"
                "CHỈ TRẢ VỀ JSON, KHÔNG TEXT NÀO KHÁC.\n"
                "VÍ DỤ: {\"mode\": \"chat\", \"arguments\": {}}"
            )

            user_prompt = f"Câu hỏi: {query}"

            raw = call_ollama(user_prompt=user_prompt, system_prompt=system_prompt)
            
            # Xử lý response để tìm JSON
            text = raw.strip()
            
            # Loại bỏ markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            
            # Tìm JSON bằng regex
            json_match = re.search(r'\{[^{}]*"[^"]*"[^{}]*\}', text)
            if json_match:
                text = json_match.group()
            
            try:
                parsed = json.loads(text)
            except Exception:
                # Nếu parse thất bại, fallback về chat
                return {"mode": "chat", "arguments": {}}

            if not isinstance(parsed, dict):
                return {"mode": "chat", "arguments": {}}

            mode = parsed.get("mode", "chat")
            arguments = parsed.get("arguments", {}) or {}

            if mode not in ("chat", "list", "search", "search_and_play"):
                mode = "chat"

            # Đảm bảo query được làm sạch
            if mode in ("search", "search_and_play"):
                if "query" in arguments:
                    arguments["query"] = self._clean_query_from_user_input(arguments["query"])
                else:
                    arguments["query"] = self._clean_query_from_user_input(query)
                
                arguments.setdefault("limit", 5 if mode == "search" else 1)
            elif mode == "list":
                arguments.setdefault("limit", 10)

            return {"mode": mode, "arguments": arguments}

    async def process_query(self, query: str) -> str:
        try:
            if not self.session:
                return "❌ Chưa kết nối MCP server."

            router = self.ask_router(query)
            mode = router["mode"]
            args = router["arguments"]

            print(f"[DEBUG] Router Mode: {mode}, Args: {args}")

            # --- CHAT ---
            if mode == "chat":
                answer = call_ollama(
                    user_prompt=query,
                    system_prompt=(
                        "Bạn là trợ lý AI nói tiếng Việt, thân thiện, ngắn gọn, dễ hiểu."
                    ),
                )
                return answer

            # --- LIST ---
            if mode == "list":
                try:
                    tool_result = await self.session.call_tool(
                        "list_local_music", 
                        arguments={"limit": args.get("limit", 10)}
                    )
                except Exception as e:
                    return f"❌ Lỗi khi gọi tool 'list_local_music': {e}"

                tracks = self._parse_tool_result(tool_result)
                
                if not tracks:
                    return "😕 Không tìm thấy file nhạc nào trong thư mục cấu hình."

                # Format kết quả đơn giản không cần LLM
                track_list = []
                for i, track in enumerate(tracks[:args.get("limit", 10)], 1):
                    track_name, _ = self._extract_track_info(track)
                    track_list.append(f"{i}. {track_name}")
                
                result = "🎵 Danh sách nhạc có sẵn:\n" + "\n".join(track_list)
                result += "\n\n💡 Bạn có thể yêu cầu 'phát [tên bài]' để nghe nhạc."
                return result

            # --- SEARCH & SEARCH_AND_PLAY ---
            try:
                tool_result = await self.session.call_tool(
                    "search_local_music",
                    arguments={
                        "query": args.get("query", self._clean_query_from_user_input(query)),
                        "limit": args.get("limit", 5),
                    },
                )
            except Exception as e:
                return f"❌ Lỗi khi gọi tool 'search_local_music': {e}"

            tracks = self._parse_tool_result(tool_result)
            
            if not tracks:
                search_query = args.get("query", self._clean_query_from_user_input(query))
                return f"😕 Không tìm thấy bài hát nào trùng với từ khóa '{search_query}'."

            # --- MODE SEARCH: chỉ gợi ý ---
            if mode == "search":
                # Format kết quả đơn giản không cần LLM
                track_list = []
                for i, track in enumerate(tracks[:args.get("limit", 5)], 1):
                    track_name, _ = self._extract_track_info(track)
                    track_list.append(f"{i}. {track_name}")
                
                result = f"🎵 Tìm thấy {len(tracks)} bài phù hợp với '{args.get('query', '')}':\n"
                result += "\n".join(track_list)
                result += "\n\n💡 Gõ 'phát [tên bài]' để nghe bài hát bạn muốn."
                return result

            # --- MODE SEARCH_AND_PLAY: phát bài đầu tiên ---
            selected = tracks[0]
            
            # DEBUG: In toàn bộ selected track để xem cấu trúc
            print(f"[DEBUG] Selected track full: {selected}")
            print(f"[DEBUG] Selected track type: {type(selected)}")

            track_name, file_path = self._extract_track_info(selected)

            print(f"[DEBUG] After extraction - Name: '{track_name}', Path: '{file_path}'")

            if file_path and os.path.exists(file_path):
                print(f"🎵 Đang phát: {track_name}")
                play_audio_from_path(file_path)
                return f"▶️ Đang phát: **{track_name}**\n\nĐây là bài hát phù hợp nhất với yêu cầu của bạn."
            else:
                if not file_path:
                    return f"❌ Không thể phát bài '{track_name}'. File path không tồn tại trong dữ liệu."
                else:
                    return f"❌ Không thể phát bài '{track_name}'. File không tồn tại: {file_path}"

        except Exception as e:
            print("⚠️ Lỗi trong process_query:")
            traceback.print_exc()
            return f"❌ Lỗi nội bộ: {str(e)}"

    async def chat_loop(self) -> None:
        print("\n🎧 MCP Music Client (gemma3:1b + local music + PYGAME) đã sẵn sàng!")
        print(
            "Ví dụ:\n"
            "- 'Hiển thị danh sách nhạc có trong máy'\n"
            "- 'Tìm nhạc buồn'\n"
            "- 'Phát bài Em Của Ngày Hôm Qua'\n"
            "- 'Bật nhạc Sơn Tùng'\n"
            "Gõ 'exit' hoặc 'quit' để thoát."
        )

        while True:
            try:
                query = input("\nBạn: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nThoát.")
                break

            if not query:
                continue
            if query.lower() in ("exit", "quit"):
                print("Tạm biệt 👋")
                break

            answer = await self.process_query(query)
            print("\nBot:", answer)

    async def cleanup(self) -> None:
        # Dọn dẹp pygame khi thoát
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        await self.exit_stack.aclose()

async def main() -> None:
    client = MusicMCPClient()
    try:
        await client.connect_to_server()
        await client.chat_loop()
    finally:
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main())