from types import SimpleNamespace

from django.contrib import messages
from django.db import transaction
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import FamilyMember, BabyInformation, PregnancyCase, UserProfile
from views import join_request
from views.pregnancycase import resolve_active_pregnancy_case
from views.session_utils import get_current_user_profile
from views import baby_utils


# 只允許導回站內這幾個頁面，避免使用者塞任意網址造成 open redirect
_RETURN_TARGETS = ('profile', 'edit_family_member')


def _redirect_back(request, default='edit_family_member'):
    """POST 處理完一律 redirect（PRG），避免重新整理重送同一個動作。"""
    target = (request.POST.get('next') or '').strip()
    if target not in _RETURN_TARGETS:
        target = default
    return redirect(target)


def _parse_permissions_from_post(post_data, prefix='perm_'):
    result = {}
    for key in baby_utils.FEATURE_KEYS:
        default = 'off' if key == 'mom_records' else 'view'
        value = (post_data.get(f'{prefix}{key}') or default).strip()
        result[key] = value if value in baby_utils.PERMISSION_LEVELS else default
    return result


def _pending_members_for_owner(current_user):
    """目前使用者擁有的「所有」胎數的待審申請（不是只有作用中的那一個）。"""
    return [
        SimpleNamespace(
            applicant=req.user,
            case=req.pregnancycase,
            case_code=req.pregnancycase.code if req.pregnancycase else '',
            case_id=req.pregnancycase_id,
            join_time=req.join_time,
        )
        for req in join_request.get_requests_for_owner(current_user.user_id)
    ]


def _owned_case_or_none(current_user, case_id):
    """取得這位使用者擁有的胎數；不是擁有者或找不到就回傳 None。"""
    case_id = join_request.safe_int(case_id)
    if case_id is None:
        return None
    return PregnancyCase.objects.filter(
        pregnancycase_id=case_id,
        user_id=current_user.user_id,
    ).first()


def edit_family_member(request):
    """協助者管理頁（只負責顯示；所有異動都由下面各個 POST-only 的 view 處理）。"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    if request.method == 'POST':
        # 舊版把所有 action 都 POST 到這個網址；統一改成 PRG，這裡只收 GET
        return redirect('edit_family_member')

    case = resolve_active_pregnancy_case(request, current_user)

    # 協助者清單只有 case owner 能進入
    if case and case.user_id != current_user.user_id:
        return redirect('profile')

    babies = list(BabyInformation.objects.filter(pregnancycase=case).order_by('baby_id')) if case else []
    members = list(
        FamilyMember.objects
        .filter(pregnancycase_id=case)
        .select_related('user')
        .order_by('join_time')
    ) if case else []

    pending_members = _pending_members_for_owner(current_user)

    baby_id = request.GET.get('baby_id')
    selected_baby = None
    if baby_id:
        selected_baby = next((b for b in babies if str(b.baby_id) == str(baby_id)), None)
    if selected_baby is None and babies:
        selected_baby = babies[0]

    # 不再 fallback 到 members[0]：找不到就是沒有選取，
    # 以免之後的操作套用到「名單第一個人」而改錯人。
    member_id = request.GET.get('member_id')
    selected_member = None
    if member_id:
        selected_member = next(
            (m for m in members if str(m.familymember_id) == str(member_id)), None
        )

    return render(request, 'user/edit_family_member.html', {
        'pregnancy_case': case,
        'babies': babies,
        'selected_baby': selected_baby,
        'family_members': members,
        'pending_members': pending_members,
        'selected_member': selected_member,
        'is_case_owner': bool(case and case.user_id == current_user.user_id),
        'feature_keys': baby_utils.FEATURE_KEYS,
    })


@require_POST
def save_permissions(request):
    """儲存某位協助者的功能權限（只有該胎數的擁有者可以操作）。"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    member_id = join_request.safe_int(request.POST.get('member_id'))
    member = None
    if member_id is not None:
        member = (
            FamilyMember.objects
            .filter(familymember_id=member_id)
            .select_related('user', 'pregnancycase')
            .first()
        )

    # 找不到就直接報錯，不可以退而求其次改到名單上的其他人
    if not member:
        messages.error(request, '找不到要設定的協助者。')
        return _redirect_back(request)

    if not member.pregnancycase or member.pregnancycase.user_id != current_user.user_id:
        messages.error(request, '只有此胎數的擁有者才能修改權限')
        return _redirect_back(request)

    member.permissions = _parse_permissions_from_post(request.POST)
    member.save(update_fields=['permissions'])
    messages.success(request, f'已更新「{member.user.name}」的權限設定。')
    return _redirect_back(request)


@require_POST
def handle_join_request(request):
    """養育者同意／拒絕加入申請。

    case_id 由表單帶入並在這裡驗證擁有權，所以個人資料頁與管理頁
    都能處理「自己擁有的任何一個胎數」的申請。
    """
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    action = (request.POST.get('action') or '').strip()
    if action not in ('approve_request', 'reject_request'):
        messages.error(request, '不支援的操作。')
        return _redirect_back(request, default='profile')

    case = _owned_case_or_none(current_user, request.POST.get('case_id'))
    if not case:
        messages.error(request, '只有此胎數的擁有者才能審核申請')
        return _redirect_back(request, default='profile')

    target_user_id = join_request.safe_int(request.POST.get('target_user_id'))
    if target_user_id is None:
        messages.error(request, '找不到該筆申請紀錄')
        return _redirect_back(request, default='profile')

    pending = join_request.get_request(case.pregnancycase_id, target_user_id)
    if not pending:
        messages.error(request, '找不到該筆申請紀錄')
        return _redirect_back(request, default='profile')

    target_user = pending.user or UserProfile.objects.filter(user_id=target_user_id).first()
    applicant_name = target_user.name if target_user else '申請者'

    if action == 'reject_request':
        with transaction.atomic():
            join_request.remove_request(case.pregnancycase_id, target_user_id)
        messages.success(request, f'已拒絕「{applicant_name}」的加入申請。')
        return _redirect_back(request, default='profile')

    if not target_user:
        messages.error(request, '找不到此使用者')
        return _redirect_back(request, default='profile')

    default_perms = {key: 'view' for key in baby_utils.FEATURE_KEYS}
    default_perms['mom_records'] = 'off'
    with transaction.atomic():
        # get_or_create：避免同一個人被重複核准而產生兩筆 FamilyMember
        _member, created = FamilyMember.objects.get_or_create(
            pregnancycase=case,
            user=target_user,
            defaults={'permissions': default_perms},
        )
        join_request.remove_request(case.pregnancycase_id, target_user_id)

    if created:
        messages.success(request, f'已同意「{applicant_name}」加入，養育者記錄預設關閉，可至管理頁調整。')
    else:
        messages.info(request, f'「{applicant_name}」已經是此胎數的協助者了。')
    return _redirect_back(request, default='profile')


@require_POST
def cancel_join_request(request):
    """申請者自己撤回還沒被審核的加入申請。"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    case_id = join_request.safe_int(request.POST.get('case_id'))
    if case_id is None:
        messages.error(request, '找不到該筆申請紀錄')
        return _redirect_back(request, default='profile')

    # 只能撤回自己送出的那一筆
    removed = join_request.remove_request(case_id, current_user.user_id)
    if removed:
        messages.success(request, '已撤回加入申請。')
    else:
        messages.error(request, '找不到該筆申請紀錄')
    return _redirect_back(request, default='profile')


@require_POST
def remove_family_member(request):
    """養育者把協助者移出家庭。"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    member_id = join_request.safe_int(request.POST.get('member_id'))
    member = None
    if member_id is not None:
        member = (
            FamilyMember.objects
            .filter(familymember_id=member_id)
            .select_related('user', 'pregnancycase')
            .first()
        )

    if not member:
        messages.error(request, '找不到要移除的協助者。')
        return _redirect_back(request)

    if not member.pregnancycase or member.pregnancycase.user_id != current_user.user_id:
        messages.error(request, '只有此胎數的擁有者才能移除協助者')
        return _redirect_back(request)

    member_name = member.user.name if member.user else '協助者'
    member.delete()
    messages.success(request, f'已將「{member_name}」移出家庭成員。')
    return _redirect_back(request)


@require_POST
def leave_family(request):
    """協助者自己退出家庭。"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    member_id = join_request.safe_int(request.POST.get('member_id'))
    member = None
    if member_id is not None:
        member = (
            FamilyMember.objects
            .filter(familymember_id=member_id, user_id=current_user.user_id)
            .select_related('pregnancycase')
            .first()
        )

    if not member:
        messages.error(request, '找不到您要退出的家庭。')
        return _redirect_back(request, default='profile')

    case_code = member.pregnancycase.code if member.pregnancycase else ''
    member.delete()
    # 退出後原本選取的胎數可能已經看不到了，清掉避免頁面停在沒有權限的資料上
    request.session.pop('active_case_id', None)
    request.session.pop('active_baby_id', None)
    request.session.modified = True
    messages.success(request, f'已退出「{case_code}」的家庭成員。' if case_code else '已退出此家庭。')
    return _redirect_back(request, default='profile')


def edit_helper_permissions(request):
    """保留 URL 相容，直接 redirect 到合併後的 edit_family_member 頁面。"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    member_id = join_request.safe_int(request.GET.get('member_id') or request.POST.get('member_id'))
    url = reverse('edit_family_member')
    if member_id is not None:
        url += f'?member_id={member_id}'
    return redirect(url)
