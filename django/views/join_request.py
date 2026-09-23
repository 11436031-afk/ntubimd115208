from core.models import JoinRequest, PregnancyCase


def safe_int(value):
    """把外部傳進來的 id 安全轉成 int，轉不出來回傳 None。

    以前直接在函式裡 int()，使用者亂傳 request_id 會炸成 ValueError → 500。
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def add_request(case_id, user_id):
    #新增申請。如果已存在（同一人對同一 case 申請過），不會重複建立。回傳 True 表示新建、False 表示已存在
    case_id = safe_int(case_id)
    user_id = safe_int(user_id)
    if case_id is None or user_id is None:
        return False
    _obj, created = JoinRequest.objects.get_or_create(
        pregnancycase_id=case_id,
        user_id=user_id,
    )
    return created

def remove_request(case_id, user_id):
    #核准或拒絕後，移除這筆申請紀錄
    case_id = safe_int(case_id)
    user_id = safe_int(user_id)
    if case_id is None or user_id is None:
        return 0
    deleted, _ = JoinRequest.objects.filter(
        pregnancycase_id=case_id,
        user_id=user_id,
    ).delete()
    return deleted

def get_pending_requests(case_id):
    case_id = safe_int(case_id)
    if case_id is None:
        return []

    return [
        {
            'case_id':   r.pregnancycase_id,
            'user_id':   r.user_id,
            'join_time': r.join_time.isoformat(),
        }
        for r in JoinRequest.objects.filter(pregnancycase_id=case_id)
    ]

def has_pending_request(case_id, user_id):
    #檢查特定使用者是否已對該 case 提出申請
    case_id = safe_int(case_id)
    user_id = safe_int(user_id)
    if case_id is None or user_id is None:
        return False
    return JoinRequest.objects.filter(
        pregnancycase_id=case_id,
        user_id=user_id,
    ).exists()


def owned_case_ids(owner_user_id):
    """回傳這位使用者擁有（建立）的所有胎數 id。"""
    owner_user_id = safe_int(owner_user_id)
    if owner_user_id is None:
        return []
    return list(
        PregnancyCase.objects
        .filter(user_id=owner_user_id)
        .order_by('pregnancycase_id')
        .values_list('pregnancycase_id', flat=True)
    )


def get_requests_for_owner(owner_user_id):
    """列出這位使用者「所有胎數」的待審申請。

    以前只看目前作用中的那一個 case，只要養育者有兩個以上的胎數，
    申請就可能永遠沒有出現在任何頁面上，等於沒人審。
    回傳的每筆都帶著 case，模板要顯示是哪一個胎數的申請。
    """
    case_ids = owned_case_ids(owner_user_id)
    if not case_ids:
        return []
    return list(
        JoinRequest.objects
        .filter(pregnancycase_id__in=case_ids)
        .select_related('user', 'pregnancycase')
        .order_by('join_time', 'joinrequest_id')
    )


def get_requests_by_applicant(applicant_user_id):
    """列出這位使用者自己送出、尚未被處理的申請（申請者端用來撤回）。"""
    applicant_user_id = safe_int(applicant_user_id)
    if applicant_user_id is None:
        return []
    return list(
        JoinRequest.objects
        .filter(user_id=applicant_user_id)
        .select_related('pregnancycase', 'pregnancycase__user')
        .order_by('join_time', 'joinrequest_id')
    )


def get_request(case_id, user_id):
    """取得指定的申請紀錄，找不到回傳 None。"""
    case_id = safe_int(case_id)
    user_id = safe_int(user_id)
    if case_id is None or user_id is None:
        return None
    return (
        JoinRequest.objects
        .filter(pregnancycase_id=case_id, user_id=user_id)
        .select_related('user', 'pregnancycase')
        .first()
    )
