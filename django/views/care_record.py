import datetime

from django.shortcuts import render, redirect
from django.utils import timezone
from zoneinfo import ZoneInfo

from core.models import CareStatus, CareRecord, FamilyMember
from django.db import transaction, models as djmodels
from views.pregnancycase import url_with_active_selection, resolve_active_pregnancy_case
from views.session_utils import get_current_user_profile
from views import baby_utils

def _parse_selected_date(raw):
    try:
        return datetime.date.fromisoformat(raw) if raw else timezone.localdate()
    except Exception:
        return timezone.localdate()

def _check_care_permission(user, case, required='view'):
    """胎數擁有者永遠可讀寫；協助者依 permissions['care_records'] 判斷。"""
    if case.user_id == user.user_id:
        return True
    membership = FamilyMember.objects.filter(pregnancycase=case, user=user).first()
    return baby_utils.has_permission(membership, 'care_records', required,  default='view')


def add_care_reminder(request):
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    case = resolve_active_pregnancy_case(request, current_user)
    if not case:
        return redirect('pregnancy_case')

    if not _check_care_permission(current_user, case, required='edit'):
        return redirect('/')

    selected_date = _parse_selected_date(request.GET.get('date'))
    error_message = None
    form_data = {}

    if request.method == 'POST':
        form_data = {
            'record_date': request.POST.get('record_date', ''),
            'record_time': request.POST.get('record_time', ''),
            'carestatus_id': request.POST.get('carestatus_id', ''),
            'content': request.POST.get('content', ''),
        }

        try:
            rd = datetime.date.fromisoformat(form_data['record_date'])
            rt = datetime.time.fromisoformat(form_data['record_time'])
            record_dt = datetime.datetime.combine(rd, rt)
        except Exception:
            error_message = '請提供有效的日期與時間。'

        carestatus = None
        if not error_message:
            carestatus = CareStatus.objects.filter(carestatus_id=form_data['carestatus_id']).first()
            if not carestatus:
                error_message = '請選擇有效的類別。'

        if not error_message:
            # Some environments have carerecord_id without a DB default/sequence.
            # Ensure we provide a non-null PK by selecting max+1 inside a transaction.
            with transaction.atomic():
                max_row = CareRecord.objects.aggregate(max_id=djmodels.Max('carerecord_id'))
                next_id = (max_row.get('max_id') or 0) + 1
                CareRecord.objects.create(
                    carerecord_id=next_id,
                    pregnancycase=case,
                    user=current_user,
                    carestatus=carestatus,
                    recordtime=record_dt,
                    content=form_data['content'][:255],
                    create_time=datetime.datetime.now(ZoneInfo('Asia/Taipei')).replace(tzinfo=None),
                )
            return redirect(url_with_active_selection(request, '/', {'date': form_data['record_date']}))

    carestatus_list = list(CareStatus.objects.all())
    return render(request, 'index/care_reminder.html', {
        'selected_date_iso': selected_date.isoformat(),
        'selected_date_label': selected_date.strftime('%Y年%m月%d日'),
        'carestatus_list': carestatus_list,
        'error_message': error_message,
        'form_data': form_data,
    })


def edit_care_reminder(request):
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    case = resolve_active_pregnancy_case(request, current_user)
    if not case:
        return redirect('pregnancy_case')

    if not _check_care_permission(current_user, case, required='edit'):
        return redirect('/')

    # carerecord_id 可能來自查詢字串 (GET 進入編輯頁) 或表單 (POST 送出更新)
    care_id = request.GET.get('carerecord_id') or request.POST.get('carerecord_id')
    # 用 case 篩選，不限定 user=current_user，讓同一胎數的協助者也能編輯同一份清單
    care_record = CareRecord.objects.filter(carerecord_id=care_id, pregnancycase=case).first()
    if not care_record:
        return redirect('/')

    selected_date = _parse_selected_date(request.GET.get('date') or request.POST.get('selected_date'))
    error_message = None

    if request.method == 'POST':
        form_data = {
            'record_date': request.POST.get('record_date', ''),
            'record_time': request.POST.get('record_time', ''),
            'carestatus_id': request.POST.get('carestatus_id', ''),
            'content': request.POST.get('content', ''),
        }

        try:
            rd = datetime.date.fromisoformat(form_data['record_date'])
            rt = datetime.time.fromisoformat(form_data['record_time'])
            record_dt = datetime.datetime.combine(rd, rt)
        except Exception:
            error_message = '請提供有效的日期與時間。'

        carestatus = None
        if not error_message:
            carestatus = CareStatus.objects.filter(carestatus_id=form_data['carestatus_id']).first()
            if not carestatus:
                error_message = '請選擇有效的類別。'

        if not error_message:
            care_record.carestatus = carestatus
            care_record.recordtime = record_dt
            care_record.content = form_data['content'][:255]
            care_record.save(update_fields=['carestatus', 'recordtime', 'content'])
            return redirect(url_with_active_selection(request, '/', {'date': form_data['record_date']}))

        carestatus_list = list(CareStatus.objects.all())
        return render(request, 'index/care_reminder.html', {
            'carerecord_id': care_record.carerecord_id,
            'selected_date_iso': selected_date.isoformat(),
            'selected_date_label': selected_date.strftime('%Y年%m月%d日'),
            'carestatus_list': carestatus_list,
            'error_message': error_message,
            'form_data': form_data,
        })

    # GET：帶入既有資料作為表單預設值
    carestatus_list = list(CareStatus.objects.all())
    form_data = {
        'record_date': care_record.recordtime.date().isoformat(),
        'record_time': care_record.recordtime.time().strftime('%H:%M'),
        'carestatus_id': str(care_record.carestatus_id) if care_record.carestatus_id else '',
        'content': care_record.content or '',
    }
    return render(request, 'index/care_reminder.html', {
        'carerecord_id': care_record.carerecord_id,
        'selected_date_iso': selected_date.isoformat(),
        'selected_date_label': selected_date.strftime('%Y年%m月%d日'),
        'carestatus_list': carestatus_list,
        'error_message': error_message,
        'form_data': form_data,
    })


def set_care_status(request):
    if request.method != 'POST':
        return redirect('/')

    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    selected_date = _parse_selected_date(request.POST.get('selected_date'))
    case = resolve_active_pregnancy_case(request, current_user)

    if not case or not _check_care_permission(current_user, case, required='view'):
        return redirect(url_with_active_selection(request, '/', {'date': selected_date.isoformat()}))

    care_id = request.POST.get('carerecord_id')
    new_state = request.POST.get('state') in ('1', 'true', 'True', 'on')

    if care_id:
        # 用 case 篩選，不再限定 user=current_user，這樣同一胎數的協助者才能操作同一份清單
        care_record = CareRecord.objects.filter(carerecord_id=care_id, pregnancycase=case).first()
        if care_record:
            care_record.state = new_state
            care_record.save(update_fields=['state'])

    return redirect(url_with_active_selection(request, '/', {'date': selected_date.isoformat()}))


def delete_care_reminder(request):
    if request.method != 'POST':
        return redirect('/')

    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    selected_date = _parse_selected_date(request.POST.get('selected_date'))
    case = resolve_active_pregnancy_case(request, current_user)

    if not case or not _check_care_permission(current_user, case, required='edit'):
        return redirect(url_with_active_selection(request, '/', {'date': selected_date.isoformat()}))

    care_id = request.POST.get('carerecord_id')
    if care_id:
        # 用 case 篩選，不再限定 user=current_user，這樣同一胎數的協助者才能操作同一份清單
        care_record = CareRecord.objects.filter(carerecord_id=care_id, pregnancycase=case).first()
        if care_record:
            care_record.delete()

    return redirect(url_with_active_selection(request, '/', {'date': selected_date.isoformat()}))