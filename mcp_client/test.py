from __future__ import print_function
import os.path
import datetime
import re

from bs4 import BeautifulSoup

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Nếu thay đổi scope, xoá token_*.json để re-auth
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def safe_token_filename(email: str) -> str:
    """
    Chuyển email thành tên file an toàn, ví dụ:
    'abc@gmail.com' -> 'token_abc_gmail_com.json'
    """
    sanitized = re.sub(r"[^a-zA-Z0-9]", "_", email)
    return f"token_{sanitized}.json"


def get_gmail_service(user_email: str):
    """
    Khởi tạo service Gmail API với OAuth2 cho đúng tài khoản user_email.

    - Nếu đã có token cho email này -> dùng lại, không cần login.
    - Nếu chưa có -> tự mở trình duyệt cho user login + bấm Allow, rồi lưu token riêng.
    """
    creds = None
    token_file = safe_token_filename(user_email)

    # token_...json lưu token người dùng sau lần đăng nhập đầu
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    # Nếu chưa có hoặc token hết hạn, refresh/đăng nhập lại
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "Không tìm thấy credentials.json. Hãy tải từ Google Cloud Console và đặt cùng thư mục."
                )
            print(f"👉 Chưa có token cho email {user_email}.")
            print("   Chương trình sẽ mở trình duyệt, bạn hãy:")
            print("   - Đăng nhập đúng tài khoản Gmail muốn dùng")
            print("   - Bấm Allow / Cho phép để cấp quyền Gmail API\n")
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Lưu token vào file riêng cho email này
        with open(token_file, "w") as token:
            token.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    return service


def get_today_range():
    """Trả về (start_datetime, end_datetime) cho ngày hôm nay theo local time."""
    today = datetime.date.today()
    start = datetime.datetime.combine(today, datetime.time.min)
    end = datetime.datetime.combine(today, datetime.time.max)
    return start, end


def get_today_unread(service):
    """
    Lấy tất cả email chưa đọc trong ngày hôm nay.

    Trả về list message object (đã gọi messages().get, có đầy đủ headers + body).
    """
    start, end = get_today_range()

    # Gmail search dùng format epoch seconds (hoặc RFC 2822). Ở đây dùng epoch.
    start_epoch = int(start.timestamp())
    end_epoch = int(end.timestamp())

    # Query: is:unread sau:... trước:...
    query = f"is:unread after:{start_epoch} before:{end_epoch}"

    results = service.users().messages().list(
        userId="me", q=query
    ).execute()

    messages = results.get("messages", [])
    all_messages = []

    if not messages:
        return []

    for msg in messages:
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()
        all_messages.append(full)

    return all_messages


def decode_body(payload):
    """Giải mã body email (ưu tiên text/plain, fallback text/html)."""
    import base64

    def _get_parts(pl):
        if pl.get("parts"):
            for p in pl["parts"]:
                yield from _get_parts(p)
        else:
            yield pl

    # Ưu tiên text/plain
    text_plain = None
    text_html = None

    for part in _get_parts(payload):
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        if not body_data:
            continue

        decoded_bytes = base64.urlsafe_b64decode(body_data.encode("UTF-8"))
        decoded_str = decoded_bytes.decode("utf-8", errors="ignore")

        if mime_type == "text/plain" and text_plain is None:
            text_plain = decoded_str
        elif mime_type == "text/html" and text_html is None:
            text_html = decoded_str

    if text_plain:
        return text_plain.strip()

    if text_html:
        # Chuyển HTML sang text đơn giản
        soup = BeautifulSoup(text_html, "html.parser")
        return soup.get_text(separator="\n").strip()

    return ""


def get_header(headers, name):
    """Lấy header bất kỳ (From, Subject, Date...) từ list headers."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


# -------------------------- NLP đơn giản -------------------------- #

STOPWORDS_VI = [
    "cho", "tôi", "toi", "tớ", "minh", "mình", "xin", "xem",
    "cái", "cai", "nào", "nao", "gì", "gi", "nói", "noi", "về", "ve",
    "mail", "email", "thư", "thu", "gửi", "gui", "nữa", "nua", "đi",
    "di", "với", "voi", "và", "va", "là", "la", "bị", "bi", "được",
    "duoc", "của", "cua", "ở", "tai", "trong", "trên", "tren", "này",
    "nay", "kia", "ấy", "ay",
]

STOPWORDS_EN = [
    "show", "me", "the", "email", "mail", "about", "please", "give",
    "what", "did", "say", "from", "to", "of", "a", "an",
]


def normalize_text(text: str) -> str:
    # Lower + bỏ dấu chấm, phẩy đơn giản (có thể dùng unidecode nếu muốn bỏ dấu tiếng Việt)
    text = text.lower()
    text = re.sub(r"[.,!?;:()\[\]\"']", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_keyword(query: str):
    """
    Rút keyword chính từ câu tự nhiên.
    Ở đây: chỉ là lọc stopwords + trả về các từ quan trọng nhất (2–4 từ).
    Bạn có thể thay bằng NLP/LLM sau.
    """
    q_norm = normalize_text(query)
    tokens = q_norm.split()

    filtered = []
    for t in tokens:
        if t in STOPWORDS_VI or t in STOPWORDS_EN:
            continue
        filtered.append(t)

    # Nếu không còn gì, fallback dùng original đã normalize
    if not filtered:
        return q_norm

    # Lấy tối đa 3–4 từ khóa để query cho “tiêu biểu”
    main_keywords = filtered[:4]
    return " ".join(main_keywords)


def detect_intent(query: str):
    """
    Phân loại intent rất đơn giản:
    - 'list_unread'  : user muốn xem danh sách / hỏi có mail mới không
    - 'search_email' : user muốn tìm 1 email cụ thể theo nội dung
    """
    q = normalize_text(query)

    # Các cụm từ gợi ý xem danh sách / mail mới
    list_words = ["danh sách", "danh sach", "list"]
    unread_words = ["chưa đọc", "chua doc", "unread"]
    new_words = ["mail mới", "mail moi", "mới không", "moi khong", "mail mới không"]

    if any(w in q for w in list_words) and any(w in q for w in unread_words):
        return "list_unread"

    if any(w in q for w in new_words):
        return "list_unread"

    # mặc định là intent tìm kiếm email theo nội dung
    return "search_email"


# -------------------------- Tìm email theo ngôn ngữ tự nhiên -------------------------- #

def find_email_natural(service, query: str):
    """
    Hiểu câu hỏi tự nhiên:
      - rút keyword
      - tìm email (subject, from) có chứa các keyword đó
      - trả nội dung text
    """
    keywords = extract_keyword(query)
    print(f"[DEBUG] Keyword rút ra: '{keywords}'")

    gmail_query_unread = f'is:unread "{keywords}"'
    gmail_query_all = f'"{keywords}"'

    # Thử với is:unread trước
    msg = _search_single_email(service, gmail_query_unread)
    if msg:
        return msg

    # Không có, thử full mailbox
    msg = _search_single_email(service, gmail_query_all)
    if msg:
        return msg

    return None


def _search_single_email(service, gmail_query: str):
    """
    Tìm 1 email phù hợp với query Gmail, trả về dict:
      {
        "id": ...,
        "subject": ...,
        "from": ...,
        "date": ...,
        "snippet": ...,
        "body": ...
      }
    hoặc None nếu không thấy.
    """
    results = service.users().messages().list(
        userId="me", q=gmail_query, maxResults=5
    ).execute()
    messages = results.get("messages", [])
    if not messages:
        return None

    # Lấy email mới nhất (message đầu tiên)
    msg_id = messages[0]["id"]
    full = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    payload = full.get("payload", {})
    headers = payload.get("headers", [])
    subject = get_header(headers, "Subject")
    from_ = get_header(headers, "From")
    date_ = get_header(headers, "Date")
    snippet = full.get("snippet", "")
    body = decode_body(payload)

    return {
        "id": msg_id,
        "subject": subject,
        "from": from_,
        "date": date_,
        "snippet": snippet,
        "body": body,
    }


# -------------------------- Demo CLI đơn giản -------------------------- #

def main():
    user_email = input("Nhập email Gmail bạn muốn dùng: ").strip()
    if not user_email:
        print("Bạn chưa nhập email, thoát.")
        return

    service = get_gmail_service(user_email)

    print(f"\nĐang làm việc với tài khoản: {user_email}\n")

    print("=== DANH SÁCH EMAIL CHƯA ĐỌC HÔM NAY ===")
    today_unread = get_today_unread(service)

    if not today_unread:
        print("Không có email chưa đọc nào trong hôm nay.")
    else:
        for i, m in enumerate(today_unread, start=1):
            headers = m.get("payload", {}).get("headers", [])
            subject = get_header(headers, "Subject")
            from_ = get_header(headers, "From")
            date_ = get_header(headers, "Date")
            print(f"{i}. [{date_}] {subject} - From: {from_}")

    print("\nBạn có thể nhập câu hỏi, ví dụ:")
    print("  - 'báo cáo tuần'")
    print("  - 'email HR gửi nói gì'")
    print("  - 'google gửi gì cho tôi'")
    print("  - 'có mail nào mới không?'")
    print("  - 'danh sách mail chưa đọc hôm nay'")
    print("Nhập trống để thoát.\n")

    while True:
        q = input("Nhập câu hỏi: ").strip()
        if not q:
            print("Thoát.")
            break

        intent = detect_intent(q)

        # 1) Intent: xem danh sách mail chưa đọc hôm nay
        if intent == "list_unread":
            unread = get_today_unread(service)
            if not unread:
                print("📭 Hôm nay không có email chưa đọc nào.\n")
            else:
                print("\n📨 DANH SÁCH EMAIL CHƯA ĐỌC HÔM NAY:")
                for i, m in enumerate(unread, start=1):
                    headers = m.get("payload", {}).get("headers", [])
                    subject = get_header(headers, "Subject")
                    from_ = get_header(headers, "From")
                    date_ = get_header(headers, "Date")
                    print(f"{i}. [{date_}] {subject} - From: {from_}")
                print()
            continue

        # 2) Intent: tìm nội dung email theo từ khóa
        result = find_email_natural(service, q)
        if not result:
            print("❌ Không tìm thấy email phù hợp với câu hỏi của bạn.\n")
        else:
            print("\n✅ Tìm thấy email:")
            print(f"Subject : {result['subject']}")
            print(f"From    : {result['from']}")
            print(f"Date    : {result['date']}")
            print("-" * 60)
            print(result["body"])
            print("-" * 60 + "\n")


if __name__ == "__main__":
    main()
