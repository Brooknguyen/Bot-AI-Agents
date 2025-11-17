import os.path
import re
import datetime
import asyncio
import base64

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

# DÙNG FastMCP THAY VÌ Server LOW-LEVEL
from mcp.server.fastmcp import FastMCP

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Tạo MCP server instance
mcp = FastMCP("gmail_data_server")


def safe_token_filename(email: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", email)
    return f"token_{sanitized}.json"


def get_gmail_service(user_email: str):
    creds = None
    token_file = safe_token_filename(user_email)

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "Thiếu credentials.json (tải từ Google Cloud Console)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(token_file, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def decode_body(payload):
    def _iter_parts(p):
        if p.get("parts"):
            for c in p["parts"]:
                yield from _iter_parts(c)
        else:
            yield p

    text_plain, text_html = None, None

    for part in _iter_parts(payload):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if not data:
            continue
        decoded = base64.urlsafe_b64decode(data.encode("utf-8")).decode(
            "utf-8", "ignore"
        )
        if mime == "text/plain" and text_plain is None:
            text_plain = decoded
        elif mime == "text/html" and text_html is None:
            text_html = decoded

    if text_plain:
        return text_plain.strip()
    if text_html:
        soup = BeautifulSoup(text_html, "html.parser")
        return soup.get_text(separator="\n").strip()
    return ""


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


# ------------- MCP TOOLS (chỉ lấy dữ liệu) -------------


@mcp.tool()
async def gmail_list_today_unread(user_email: str) -> dict:
    """
    Trả về danh sách email CHƯA ĐỌC trong NGÀY HÔM NAY cho 1 tài khoản Gmail.
    """
    service = get_gmail_service(user_email)

    today = datetime.date.today()
    start = datetime.datetime.combine(today, datetime.time.min)
    end = datetime.datetime.combine(today, datetime.time.max)
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())

    query = f"is:unread after:{start_epoch} before:{end_epoch}"
    res = service.users().messages().list(userId="me", q=query).execute()
    messages = res.get("messages", [])

    emails = []
    for msg in messages or []:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()
        payload = full.get("payload", {})
        headers = payload.get("headers", [])
        emails.append(
            {
                "id": full["id"],
                "threadId": full.get("threadId"),
                "subject": get_header(headers, "Subject"),
                "from": get_header(headers, "From"),
                "date": get_header(headers, "Date"),
                "snippet": full.get("snippet", ""),
            }
        )

    return {"emails": emails}


@mcp.tool()
async def gmail_search_by_query(
    user_email: str,
    gmail_query: str,
    max_results: int = 5,
) -> dict:
    """
    Search Gmail bằng Gmail search query (ví dụ: 'is:unread \"báo cáo tuần\"', 'from:hr').
    """
    service = get_gmail_service(user_email)

    res = service.users().messages().list(
        userId="me", q=gmail_query, maxResults=max_results
    ).execute()
    messages = res.get("messages", [])

    emails = []
    for msg in messages or []:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()
        payload = full.get("payload", {})
        headers = payload.get("headers", [])
        body = decode_body(payload)

        emails.append(
            {
                "id": full["id"],
                "threadId": full.get("threadId"),
                "subject": get_header(headers, "Subject"),
                "from": get_header(headers, "From"),
                "date": get_header(headers, "Date"),
                "snippet": full.get("snippet", ""),
                "body": body,
            }
        )

    return {"emails": emails}


if __name__ == "__main__":
    # FastMCP hỗ trợ trực tiếp stdio
    mcp.run(transport="stdio")
