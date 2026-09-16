import json
import logging
import os
import time
from urllib import error, request as urlrequest

from django.http import JsonResponse
from django.shortcuts import redirect, render

from core.models import BabyInformation
from views.session_utils import get_current_user_profile


logger = logging.getLogger(__name__)

DEFAULT_ASSISTANT_WEBHOOK_URL = "https://kathy1023.app.n8n.cloud/webhook/CoLoGrowth"


def _get_webhook_url():
    return os.getenv("N8N_ASSISTANT_WEBHOOK_URL", DEFAULT_ASSISTANT_WEBHOOK_URL).strip()


def _get_timeout_seconds():
    try:
        return max(5, int(os.getenv("N8N_ASSISTANT_TIMEOUT_SECONDS", "60")))
    except ValueError:
        return 60


def _format_answer(raw_answer):
    if isinstance(raw_answer, dict):
        data = raw_answer
    else:
        try:
            data = json.loads(str(raw_answer))
        except (TypeError, json.JSONDecodeError):
            return str(raw_answer).strip()

    if not isinstance(data, dict):
        return str(raw_answer).strip()

    metric_labels = {
        "height_percentile": "身高百分位",
        "weight_percentile": "體重百分位",
        "head_percentile": "頭圍百分位",
    }
    lines = []
    if data.get("baby_name"):
        lines.append(f"{data['baby_name']} 的成長評估")
    if data.get("gender") or data.get("current_age"):
        details = []
        if data.get("gender"):
            details.append(f"性別：{data['gender']}")
        if data.get("current_age"):
            details.append(f"目前月齡：{data['current_age']}")
        lines.append("｜".join(details))

    latest_metrics = data.get("latest_metrics") or {}
    if latest_metrics:
        metric_values = [
            f"身高 {latest_metrics['height']} cm" if latest_metrics.get("height") is not None else "",
            f"體重 {latest_metrics['weight']} kg" if latest_metrics.get("weight") is not None else "",
            f"頭圍 {latest_metrics['head_circumference']} cm" if latest_metrics.get("head_circumference") is not None else "",
        ]
        metric_values = [value for value in metric_values if value]
        if metric_values:
            lines.append(f"最新紀錄（{latest_metrics.get('date', '日期未知')}）：" + "、".join(metric_values))

    percentiles = data.get("who_percentiles") or {}
    for key, label in metric_labels.items():
        if percentiles.get(key):
            lines.append(f"{label}：{percentiles[key]}")
    if data.get("growth_evaluation"):
        lines.extend(["", "成長評估", str(data["growth_evaluation"])])
    if data.get("care_suggestions"):
        lines.extend(["", "照護建議", str(data["care_suggestions"])])

    return "\n".join(lines).strip() or json.dumps(data, ensure_ascii=False, indent=2)


def _unwrap_n8n_response(response_data):
    """Extract the final node output from common n8n webhook envelopes."""
    if isinstance(response_data, list):
        if not response_data:
            return {}
        return _unwrap_n8n_response(response_data[0])

    if not isinstance(response_data, dict):
        return response_data

    for key in ("body", "data", "result", "response"):
        nested_value = response_data.get(key)
        if isinstance(nested_value, (dict, list)):
            return _unwrap_n8n_response(nested_value)

    return response_data


def _extract_n8n_answer(raw_body):
    try:
        response_data = json.loads(raw_body)
    except json.JSONDecodeError:
        response_data = {"answer": raw_body}

    response_data = _unwrap_n8n_response(response_data)
    if not isinstance(response_data, dict):
        return _format_answer(response_data), None

    started_message = str(response_data.get("message", "")).strip().lower()
    if started_message == "workflow was started":
        return None, (
            "n8n 在工作流程完成前就回覆了結果。請將 Webhook 的 Response Mode "
            "設為「When Last Node Finishes」，再重新啟用 workflow。"
        )

    answer = (
        response_data.get("answer")
        or response_data.get("output")
        or response_data.get("text")
        or response_data.get("message")
    )
    if answer is None:
        answer = response_data
    return _format_answer(answer), None


def _post_to_n8n(question, user_id):
    webhook_url = _get_webhook_url()
    payload = {
        "body": {
            "events": [{
                "type": "message",
                "source": {"type": "user", "userId": str(user_id)},
                "message": {"type": "text", "text": question},
            }],
        }
    }
    request = urlrequest.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started_at = time.time()
    try:
        with urlrequest.urlopen(request, timeout=_get_timeout_seconds()) as response:
            raw_body = response.read().decode("utf-8", errors="ignore").strip()
        if not raw_body:
            return None, "n8n 沒有回傳內容。"
        answer, response_error = _extract_n8n_answer(raw_body)
        if response_error:
            return None, response_error
        logger.info("Assistant n8n request completed in %.3fs", time.time() - started_at)
        return _format_answer(answer), None
    except error.HTTPError as exc:
        logger.warning("Assistant n8n HTTP error %s", exc.code)
        if exc.code == 404:
            return None, (
                "找不到成長助手的 n8n Webhook（CoLoGrowth）。"
                "請在 n8n 開啟 CoLoGrowth workflow 並確認 Webhook 已啟用。"
            )
        return None, f"n8n Webhook 回應失敗：{exc.code}"
    except error.URLError as exc:
        logger.warning("Assistant n8n connection error: %s", exc.reason)
        return None, f"無法連線到 n8n：{exc.reason}"
    except Exception:
        logger.exception("Unexpected error while calling assistant n8n webhook")
        return None, "呼叫成長評估助手時發生錯誤，請稍後再試。"


def assistant(request):
    current_user = get_current_user_profile(request)
    if not current_user:
        if request.headers.get("Accept", "").find("application/json") >= 0:
            return JsonResponse({"ok": False, "error": "請先登入。"}, status=401)
        return redirect("login")

    if request.method == "POST":
        question = request.POST.get("question", "").strip()
        if not question:
            return JsonResponse({"ok": False, "error": "請輸入問題。"}, status=400)
        answer, error_message = _post_to_n8n(question, current_user.user_id)
        if error_message:
            return JsonResponse({"ok": False, "error": error_message}, status=502)
        return JsonResponse({"ok": True, "answer": answer})

    born_babies = BabyInformation.objects.filter(
        pregnancycase__user=current_user,
        birthdaytime__isnull=False,
    ).order_by("birthdaytime", "baby_id")
    return render(request, "base/assistant.html", {
        "current_user": current_user,
        "born_babies": born_babies,
    })
