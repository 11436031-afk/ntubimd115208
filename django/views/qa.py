import json
import os
import re
import socket
from urllib import error, request as urlrequest
from urllib.parse import urlparse
import logging
import time

from django.db.models import Max
from django.db import transaction
from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.utils import timezone
from django.urls import reverse
from views.session_utils import get_current_user_profile
from core.models import QAConversation, QAMessage, UserProfile

DEFAULT_USER_ID = None

logger = logging.getLogger(__name__)


ACTIVE_CONVERSATION_SESSION_KEY = "qa_active_conversation_id"
# DEFAULT_N8N_RAG_WEBHOOK_URL = "http://localhost:5678/webhook/b2489eda-0b01-425d-be17-3c817fb4cdcd"
DEFAULT_N8N_RAG_WEBHOOK_URL = "https://kathy1023.app.n8n.cloud/webhook/b2489eda-0b01-425d-be17-3c817fb4cdcd"

EXPECTED_N8N_RAG_WEBHOOK_PATH = "/webhook/b2489eda-0b01-425d-be17-3c817fb4cdcd"


def _normalize_webhook_url(webhook_url):
    webhook_url = str(webhook_url or "").strip()
    if not webhook_url:
        return ""

    if "://" not in webhook_url:
        webhook_url = f"http://{webhook_url}"

    return webhook_url


def _host_is_resolvable(webhook_url):
    parsed = urlparse(webhook_url)
    host = parsed.hostname
    if not host:
        return False

    try:
        socket.getaddrinfo(host, parsed.port or 80)
        return True
    except OSError:
        return False


def _has_expected_webhook_path(webhook_url):
    parsed = urlparse(webhook_url)
    return parsed.path.rstrip("/") == EXPECTED_N8N_RAG_WEBHOOK_PATH


def _get_webhook_url():
    webhook_url = _normalize_webhook_url(os.getenv("N8N_RAG_WEBHOOK_URL", ""))
    if webhook_url and _host_is_resolvable(webhook_url) and _has_expected_webhook_path(webhook_url):
        return webhook_url

    return DEFAULT_N8N_RAG_WEBHOOK_URL


def _get_timeout_seconds():
    raw_timeout = os.getenv("N8N_RAG_TIMEOUT_SECONDS", "60").strip()
    try:
        return max(5, int(raw_timeout))
    except ValueError:
        return 60


def _stringify_source(source_item):
    if isinstance(source_item, str):
        return source_item.strip()

    if isinstance(source_item, dict):
        title = source_item.get("title") or source_item.get("source") or source_item.get("name")
        page = source_item.get("page") or source_item.get("page_number")
        excerpt = source_item.get("excerpt") or source_item.get("content") or source_item.get("text")

        parts = []
        if title:
            parts.append(str(title))
        if page is not None and str(page).strip():
            parts.append(f"第 {page} 頁")
        if excerpt:
            parts.append(str(excerpt).strip())

        if parts:
            return "｜".join(parts)

        return json.dumps(source_item, ensure_ascii=False)

    return str(source_item).strip()


def _strip_markdown_emphasis(text):
    if not text:
        return ""

    normalized_text = str(text).strip()
    normalized_text = re.sub(r"\*\*(.+?)\*\*", r"\1", normalized_text, flags=re.S)
    normalized_text = re.sub(r"__(.+?)__", r"\1", normalized_text, flags=re.S)
    return normalized_text


def _extract_response_data(raw_body):
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"answer": raw_body, "sources": [], "raw": raw_body}

    if isinstance(data, list):
        data = data[0] if data else {}

    if not isinstance(data, dict):
        return {"answer": raw_body, "sources": [], "raw": data}

    answer = data.get("answer") or data.get("text") or data.get("output") or data.get("response") or ""
    if not answer and len(data) == 1:
        only_value = next(iter(data.values()))
        if isinstance(only_value, (str, int, float, bool)):
            answer = only_value

    if not answer:
        answer = json.dumps(data, ensure_ascii=False)

    sources = data.get("sources") or data.get("context") or data.get("documents") or []
    if isinstance(sources, dict):
        sources = [sources]
    elif isinstance(sources, str):
        sources = [sources]
    elif not isinstance(sources, list):
        sources = [sources] if sources else []

    normalized_sources = []
    for source_item in sources:
        source_text = _stringify_source(source_item)
        if source_text:
            normalized_sources.append(source_text)

    return {"answer": str(answer).strip(), "sources": normalized_sources, "raw": data}


def _post_to_n8n(question_text):
    webhook_url = _get_webhook_url()
    debug_info = {
        "request_url": webhook_url,
        "request_payload": (question_text or "")[:1000],
        "response_status": None,
        "response_body": None,
        "exception": None,
        "duration_seconds": None,
    }

    if not webhook_url:
        debug_info["exception"] = "missing_webhook_url"
        return None, "請先設定 N8N_RAG_WEBHOOK_URL，讓 Django 可以呼叫 n8n Webhook。", debug_info

    logger.info("Posting to n8n webhook: %s", webhook_url)
    logger.debug("Payload question (truncated): %s", (question_text or '')[:200])
    payload = json.dumps({"message": question_text, "question": question_text}).encode("utf-8")
    req = urlrequest.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.time()
    try:
        with urlrequest.urlopen(req, timeout=_get_timeout_seconds()) as response:
            raw_body = response.read().decode("utf-8", errors="ignore").strip()
            debug_info["response_status"] = getattr(response, 'status', None) or getattr(response, 'getcode', lambda: None)()
            debug_info["response_body"] = raw_body
            debug_info["duration_seconds"] = round(time.time() - start, 3)

            if not raw_body:
                return None, "n8n 回傳了空結果。", debug_info

            data = _extract_response_data(raw_body)
            return data, None, debug_info
    except error.HTTPError as exc:
        try:
            body = exc.read().decode('utf-8', errors='ignore')
        except Exception:
            body = ''
        debug_info["response_status"] = getattr(exc, 'code', None)
        debug_info["response_body"] = body
        debug_info["exception"] = f"HTTPError: {exc.reason}"
        debug_info["duration_seconds"] = round(time.time() - start, 3)
        logger.warning("n8n HTTPError %s %s: %s", exc.code, exc.reason, body)
        return None, f"n8n Webhook 回應失敗：{exc.code} {exc.reason}", debug_info
    except error.URLError as exc:
        debug_info["exception"] = f"URLError: {exc.reason}"
        debug_info["duration_seconds"] = round(time.time() - start, 3)
        logger.warning("n8n URLError: %s", exc.reason)
        return None, f"無法連線到 n8n Webhook：{exc.reason}", debug_info
    except Exception as exc:
        debug_info["exception"] = str(exc)
        debug_info["duration_seconds"] = round(time.time() - start, 3)
        logger.exception("Unexpected error while posting to n8n")
        return None, f"發生未知錯誤：{str(exc)}", debug_info


def _store_exchange(question_text, answer_text, request=None):
    if not question_text or not answer_text:
        return None, "缺少問題或回答內容"

    try:
        user = None
        if request is not None:
            session_user_id = request.session.get("user_id")
            if session_user_id not in (None, ""):
                try:
                    user = UserProfile.objects.filter(user_id=int(session_user_id)).first()
                except (TypeError, ValueError):
                    user = None

        if user is None and DEFAULT_USER_ID is not None:
            try:
                user = UserProfile.objects.filter(user_id=int(DEFAULT_USER_ID)).first()
            except (TypeError, ValueError):
                user = None

        if user is None:
            user = UserProfile.objects.order_by("user_id").first()
    except Exception:
        user = None

    if not user:
        return None, "找不到可用的使用者資料，無法寫入 qaconversation"

    try:
        now = timezone.now()
        conversation_title = (question_text[:50] or "孕期知識問答")
        with transaction.atomic():
            conversation = QAConversation.objects.create(
                user_id=user,
                title=conversation_title,
                create_time=now,
            )
            QAMessage.objects.create(
                qa_conversation=conversation,
                role="user",
                message=question_text,
                create_time=now,
            )
            QAMessage.objects.create(
                qa_conversation=conversation,
                role="assistant",
                message=answer_text,
                create_time=now,
            )
        return conversation, None
    except Exception as exc:
        logger.exception("Failed to store QA exchange")
        return None, str(exc)


def _append_message(conversation, role, message_text):
    if not conversation or not message_text:
        return "缺少對話或訊息內容"

    try:
        with transaction.atomic():
            QAMessage.objects.create(
                qa_conversation=conversation,
                role=role,
                message=message_text,
                create_time=timezone.now(),
            )
        return None
    except Exception as exc:
        logger.exception("Failed to append QA message")
        return str(exc)


def _get_conversation_by_id(conversation_id):
    if not conversation_id:
        return None

    return QAConversation.objects.filter(qaconversation_id=conversation_id).first()


def _get_current_conversation_id(request):
    request_conversation_id = request.GET.get("conversation_id") or request.POST.get("conversation_id")
    if request_conversation_id:
        return request_conversation_id

    return request.session.get(ACTIVE_CONVERSATION_SESSION_KEY)


def _set_current_conversation_id(request, conversation_id):
    if conversation_id:
        request.session[ACTIVE_CONVERSATION_SESSION_KEY] = str(conversation_id)
    else:
        request.session.pop(ACTIVE_CONVERSATION_SESSION_KEY, None)


def _load_conversation_messages(conversation):
    if not conversation:
        return []

    try:
        messages = list(conversation.messages.order_by("create_time", "serno").all())
    except Exception:
        return []

    return [
        {
            "role": message.role,
            "message": message.message,
            "create_time": message.create_time,
            "is_user": str(message.role).lower() == "user",
        }
        for message in messages
    ]


def _build_conversation_item(conversation, active_conversation_id=None):
    messages = _load_conversation_messages(conversation)
    user_messages = [message for message in messages if message["is_user"]]
    assistant_messages = [message for message in messages if not message["is_user"]]

    latest_message_time = messages[-1]["create_time"] if messages else None

    return {
        "id": conversation.qaconversation_id,
        "title": conversation.title,
        "create_time": conversation.create_time,
        "latest_message_time": latest_message_time,
        "question": user_messages[-1]["message"] if user_messages else conversation.title,
        "answers": [message["message"] for message in assistant_messages],
        "messages": messages,
        "is_active": str(conversation.qaconversation_id) == str(active_conversation_id),
    }


def _load_recent_items(limit=20, user=None):
    items = []

    try:
        qs = QAConversation.objects.annotate(latest_message_time=Max("messages__create_time"))
        if user is not None:
            qs = qs.filter(user_id=user)

        conversations = list(qs.order_by("-latest_message_time", "-create_time")[:limit])
    except Exception:
        return items

    for conversation in conversations:
        item = _build_conversation_item(conversation)
        if not item["messages"]:
            continue

        items.append(item)

    return items


def _normalize_answer_text(answer_text, sources=None):
    if answer_text:
        normalized = _strip_markdown_emphasis(answer_text)
        if normalized and normalized != "目前知識庫沒有相關資訊":
            return normalized

    if sources:
        return ""

    return "目前資料庫沒有此資訊"

def qa_conversation(request):
    error_message = ""
    sources = []
    current_user = get_current_user_profile(request)
    accept_header = ""
    try:
        accept_header = request.META.get("HTTP_ACCEPT", "") or (request.headers.get("Accept") if hasattr(request, 'headers') else "")
    except Exception:
        accept_header = request.META.get("HTTP_ACCEPT", "")

    is_ajax = (
        request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        or "application/json" in (accept_header or "")
    )

    if not current_user:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "登入狀態已失效或尚未登入，請重新登入後再送出訊息。"}, status=401)
        return redirect('login')

    if request.method == "POST" and request.POST.get("action") == "new_conversation":
        _set_current_conversation_id(request, None)
        request.session["qa_skip_auto_load"] = True
        request.session.modified = True
        if request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest" or "application/json" in (request.META.get("HTTP_ACCEPT", "") or ""):
            return JsonResponse(
                {
                    "ok": True,
                    "conversation_id": None,
                    "redirect_url": reverse("qa_conversation"),
                    "items": _load_recent_items(user=current_user),
                }
            )

        return redirect(reverse("qa_conversation"))

    if request.method == "POST":
        question = request.POST.get("question", "").strip()
        # 更寬容的 AJAX 判斷：同時檢查 HTTP_X_REQUESTED_WITH、HTTP_ACCEPT，
        # 並退回到 request.headers（與 Django 版本相容性保護）。
        if question:
            payload, error_message, debug_info = _post_to_n8n(question)
            if payload:
                sources = payload.get("sources") or []
                answer = _normalize_answer_text(payload.get("answer") or "", sources)
                if answer:
                    current_conversation_id = _get_current_conversation_id(request)
                    conversation = _get_conversation_by_id(current_conversation_id)

                    if not conversation:
                        conversation, store_error = _store_exchange(question, answer, request=request)
                        if store_error:
                            debug_info["store_error"] = store_error
                    else:
                        user_error = _append_message(conversation, "user", question)
                        assistant_error = _append_message(conversation, "assistant", answer)
                        if user_error or assistant_error:
                            debug_info["store_error"] = user_error or assistant_error
                            conversation = None

                    if conversation:
                        _set_current_conversation_id(request, conversation.qaconversation_id)
                        request.session.modified = True
                        if is_ajax:
                            return JsonResponse(
                                {
                                    "ok": True,
                                    "conversation_id": conversation.qaconversation_id,
                                    "answer": answer,
                                    "sources": sources,
                                    "debug": debug_info,
                                }
                            )
                        # store debug info to session for next page render
                        request.session["qa_last_debug"] = debug_info
                        return redirect(f"{reverse('qa_conversation')}?conversation_id={conversation.qaconversation_id}")

            # If we reach here, there was an error from n8n or empty payload
            if is_ajax:
                return JsonResponse({"ok": False, "error": error_message or debug_info.get("store_error") or "n8n 未回傳有效結果", "debug": debug_info})
            # store debug info for non-ajax flow
            request.session["qa_last_debug"] = debug_info

    current_conversation_id = _get_current_conversation_id(request)
    current_conversation = _get_conversation_by_id(current_conversation_id)
    skip_auto_load = request.session.pop("qa_skip_auto_load", False)

    if not current_conversation and not skip_auto_load:
        try:
            current_conversation = (
                QAConversation.objects.annotate(latest_message_time=Max("messages__create_time"))
                .filter(user_id=current_user)
                .order_by("-latest_message_time", "-create_time")
                .first()
            )
        except Exception:
            current_conversation = None

    if current_conversation:
        _set_current_conversation_id(request, current_conversation.qaconversation_id)

    active_conversation_id = request.session.get(ACTIVE_CONVERSATION_SESSION_KEY)
    recent_items = _load_recent_items(user=current_user)
    for item in recent_items:
        item["is_active"] = str(item["id"]) == str(active_conversation_id)

    current_messages = _load_conversation_messages(current_conversation)
    current_item = _build_conversation_item(current_conversation, active_conversation_id) if current_conversation else None
    current_webhook_url = _get_webhook_url()
    current_debug = request.session.pop("qa_last_debug", None)

    context = {
        "error_message": error_message,
        "sources": sources,
        "items": recent_items,
        "current_conversation": current_conversation,
        "current_conversation_id": active_conversation_id,
        "current_item": current_item,
        "current_messages": current_messages,
        "current_webhook_url": current_webhook_url,
        "current_debug": current_debug,
        'current_user': current_user
    }
    return render(request, "AI/qa_conversation.html", context)


def test_n8n_webhook(request):
    """Temporary test endpoint to verify Django can POST to the configured n8n webhook.

    Returns JsonResponse with debug info from _post_to_n8n.
    """
    test_message = request.GET.get('msg', 'healthcheck')
    data, error_message, debug_info = _post_to_n8n(test_message)
    result = {
        'ok': bool(data and not error_message),
        'error': error_message,
        'debug': debug_info,
        'payload': data,
    }
    return JsonResponse(result)
