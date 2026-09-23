"""健康類內容的共用安全機制（qa 與 assistant 共用）。

包含三類共用邏輯：
1. 免責聲明文字（前端固定顯示，後端也提供常數方便沿用）
2. 紅旗症狀關鍵字偵測：命中時在 AI 回覆最前面加上「請立即就醫或撥打 119」提示
3. 提問長度上限與每使用者簡易速率限制（以 session 記錄時間戳）
"""

import time


# ======================
# 免責聲明
# ======================
MEDICAL_DISCLAIMER = "本內容為一般衛教資訊，不能取代專業醫療診斷，如有不適請諮詢醫師。"


# ======================
# 紅旗症狀關鍵字
# ======================
# 孕期紅旗症狀
PREGNANCY_RED_FLAG_KEYWORDS = (
    "出血",
    "大量出血",
    "陰道出血",
    "劇烈腹痛",
    "腹部劇痛",
    "肚子劇痛",
    "破水",
    "羊水流出",
    "胎動減少",
    "胎動變少",
    "胎動變弱",
    "沒有胎動",
    "沒胎動",
    "感覺不到胎動",
    "劇烈頭痛",
    "頭痛欲裂",
    "視力模糊",
    "視線模糊",
    "眼前發黑",
    "抽搐",
    "痙攣",
)

# 嬰幼兒紅旗症狀
INFANT_RED_FLAG_KEYWORDS = (
    "高燒不退",
    "燒不退",
    "呼吸困難",
    "呼吸急促",
    "呼吸費力",
    "發紺",
    "嘴唇發紫",
    "臉色發紫",
    "嘴唇發黑",
    "抽搐",
    "痙攣",
    "活力極差",
    "活力很差",
    "叫不醒",
    "叫不起來",
    "昏睡",
    "意識不清",
    "持續嘔吐",
    "一直吐",
    "噴射性嘔吐",
    "脫水",
    "尿量變少",
    "哭沒有眼淚",
)

# 所有單一關鍵字（module 層常數，方便組員後續增修）
RED_FLAG_KEYWORDS = tuple(dict.fromkeys(PREGNANCY_RED_FLAG_KEYWORDS + INFANT_RED_FLAG_KEYWORDS))

# 「三個月以下發燒」屬於紅旗，但單看「發燒」太寬鬆，
# 因此要同時出現年齡字眼與發燒字眼才算命中。
YOUNG_INFANT_AGE_TERMS = (
    "新生兒",
    "剛出生",
    "未滿月",
    "滿月",
    "未滿三個月",
    "未滿3個月",
    "三個月以下",
    "3個月以下",
    "三個月內",
    "3個月內",
    "一個月大",
    "1個月大",
    "兩個月大",
    "2個月大",
    "三個月大",
    "3個月大",
)

FEVER_TERMS = (
    "發燒",
    "發熱",
    "高燒",
)

# 命中紅旗症狀時加在回覆最前面的提示
EMERGENCY_NOTICE = (
    "【緊急提醒】你描述的狀況可能是需要立即處理的警訊，"
    "請立即就醫或撥打 119，不要等待線上回覆。"
)


def detect_red_flags(text):
    """回傳 text 中命中的紅旗症狀關鍵字（沒有命中則回傳空 list）。"""
    if not text:
        return []

    normalized_text = str(text)
    matched = [keyword for keyword in RED_FLAG_KEYWORDS if keyword in normalized_text]

    has_fever = any(term in normalized_text for term in FEVER_TERMS)
    is_young_infant = any(term in normalized_text for term in YOUNG_INFANT_AGE_TERMS)
    if has_fever and is_young_infant:
        matched.append("三個月以下發燒")

    # 去除重複並保持順序
    return list(dict.fromkeys(matched))


def prepend_emergency_notice(answer_text, question_text):
    """問題命中紅旗症狀時，在回覆最前面加上就醫提示。"""
    matched = detect_red_flags(question_text)
    if not matched:
        return answer_text, matched

    answer_text = str(answer_text or "").strip()
    if not answer_text:
        return EMERGENCY_NOTICE, matched

    if answer_text.startswith(EMERGENCY_NOTICE):
        return answer_text, matched

    return f"{EMERGENCY_NOTICE}\n\n{answer_text}", matched


# ======================
# 提問長度上限
# ======================
MAX_QUESTION_LENGTH = 500
TOO_LONG_MESSAGE = f"問題太長了，請縮短到 {MAX_QUESTION_LENGTH} 字以內再送出。"


def validate_question_length(question_text):
    """超過長度上限時回傳錯誤訊息字串，否則回傳 None。"""
    if question_text and len(question_text) > MAX_QUESTION_LENGTH:
        return TOO_LONG_MESSAGE
    return None


# ======================
# 簡易速率限制（以 session 記錄時間戳）
# ======================
RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MESSAGE = "你提問的速度太快了，請稍候一分鐘再試。"


def check_rate_limit(
    request,
    session_key,
    max_requests=RATE_LIMIT_MAX_REQUESTS,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
):
    """每使用者（以 session 為單位）的簡易速率限制。

    超過上限時回傳錯誤訊息字串，否則記下這次的時間戳並回傳 None。
    """
    now = time.time()
    try:
        timestamps = request.session.get(session_key) or []
    except Exception:
        return None

    if not isinstance(timestamps, list):
        timestamps = []

    recent = []
    for raw_timestamp in timestamps:
        try:
            timestamp = float(raw_timestamp)
        except (TypeError, ValueError):
            continue
        if now - timestamp < window_seconds:
            recent.append(timestamp)

    if len(recent) >= max_requests:
        request.session[session_key] = recent
        request.session.modified = True
        return RATE_LIMIT_MESSAGE

    recent.append(now)
    request.session[session_key] = recent
    request.session.modified = True
    return None
