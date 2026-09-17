from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.shortcuts import render, redirect
from django.db import ProgrammingError, transaction
from django.utils import timezone
import datetime
import calendar
from pathlib import Path

from core.models import Feeling, PhysicalCondition, PregnancyRecord, Prenatalrecord, Userfeeling, Userphysicalcondition, PregnancyCase, UserProfile
from .pregnancyrecords import records_for_case
from views.pregnancycase import resolve_active_pregnancy_case, url_with_active_selection
from views.session_utils import get_current_user_profile
from views import baby_utils
from core.models import FamilyMember


def _records_for_scope(pregnancy_case, current_user):
    if pregnancy_case:
        return records_for_case(pregnancy_case)

    if current_user and current_user.user_id:
        return PregnancyRecord.objects.filter(user_id=current_user.user_id)

    return PregnancyRecord.objects.none()


def _get_unique_record_for_date(pregnancy_case, current_user, selected_date):
    if not current_user:
        return None

    records = list(
        _records_for_scope(pregnancy_case, current_user)
        .filter(check_date=selected_date)
        .order_by('-check_date', '-pregnancyrecord_id')
    )
    if not records:
        return None

    primary_record = records[0]
    duplicate_ids = [record.pregnancyrecord_id for record in records[1:]]
    if duplicate_ids:
        PregnancyRecord.objects.filter(pregnancyrecord_id__in=duplicate_ids).delete()

    return primary_record


FEELING_EMOJI_MAP = {
    '快樂': '😊',
    '幸福': '🥰',
    '開心': '😆',
    '心跳加速': '😳',
    '還好': '😐',
    '煩': '😮‍💨',
    '怒': '😡',
    '累': '😫',
    '不安': '😰',
    '難受': '😭',
    '不舒服': '🤢',
}

MARKER_VALUE_MAP = {
    '-': '陰性',
    '+': '陽性',
    '++': '中度',
    '+++': '高度嚴重',
    '++++': '極度嚴重',
}

MARKER_LABEL_TO_VALUE = {value: key for key, value in MARKER_VALUE_MAP.items()}


def _save_prenatal_photo(image_file):
    if not image_file:
        return ''
    storage = FileSystemStorage(
        location=settings.BASE_DIR / 'core' / 'static' / 'media',
        base_url='/static/media/',
    )
    filename = storage.save(f'prenatalrecord/{image_file.name}', image_file)
    return storage.url(filename)


def _delete_prenatal_photo(photo_url):
    if not photo_url:
        return
    storage = FileSystemStorage(
        location=settings.BASE_DIR / 'core' / 'static' / 'media',
        base_url='/static/media/',
    )
    relative_name = str(photo_url)
    if relative_name.startswith('/static/media/'):
        relative_name = relative_name[len('/static/media/'):]
    elif relative_name.startswith('static/media/'):
        relative_name = relative_name[len('static/media/'):]
    elif relative_name.startswith('/static/'):
        relative_name = relative_name[len('/static/'):]
    if relative_name:
        storage.delete(relative_name.lstrip('/'))


def _store_prenatal_photo(image_file, prenatalrecord_id, existing_photo=''):
    if not image_file or not prenatalrecord_id:
        return existing_photo or ''

    storage = FileSystemStorage(
        location=settings.BASE_DIR / 'core' / 'static' / 'media',
        base_url='/static/media/',
    )
    _delete_prenatal_photo(existing_photo)
    suffix = Path(image_file.name).suffix.lower()
    filename = storage.save(f'prenatalrecord/{prenatalrecord_id}{suffix}', image_file)
    return storage.url(filename)


def _parse_selected_date(raw_value):
    try:
        return datetime.date.fromisoformat(raw_value) if raw_value else datetime.date.today()
    except Exception:
        return datetime.date.today()


def _date_to_safe_datetime(date_value):
    check_datetime = datetime.datetime.combine(date_value, datetime.time(hour=12))
    if timezone.is_naive(check_datetime):
        check_datetime = timezone.make_aware(check_datetime)
    return check_datetime


def pregnancyrecord(request):
    current_user = get_current_user_profile(request)
    pregnancy_case = resolve_active_pregnancy_case(request, current_user) if current_user else None

    if pregnancy_case and current_user and pregnancy_case.user_id != current_user.user_id:
        membership = FamilyMember.objects.filter(pregnancycase=pregnancy_case, user=current_user).first()
        if not baby_utils.has_permission(membership, 'mom_records', 'view'):
            return redirect('/userprofile/?perm_error=mom_records')

    raw = request.GET.get('date')

    today_date = datetime.date.today()

    try:
        selected_date = datetime.date.fromisoformat(raw) if raw else None
    except Exception:
        selected_date = None

    if not selected_date:
        scope_records = _records_for_scope(pregnancy_case, current_user)
        has_this_month = scope_records.filter(
            check_date__year=today_date.year, check_date__month=today_date.month
        ).exists()
        if has_this_month:
            selected_date = today_date
        else:
            latest_rec = scope_records.order_by('-check_date').first()
            if latest_rec and latest_rec.check_date:
                selected_date = latest_rec.check_date
            else:
                selected_date = today_date

    year = selected_date.year
    month = selected_date.month
    month_records = list(
        _records_for_scope(pregnancy_case, current_user)
        .filter(check_date__gte=datetime.date(year, month, 1), check_date__lt=datetime.date(year + (month // 12), (month % 12) + 1, 1))
        .order_by('check_date', 'pregnancyrecord_id')
        .values('pregnancyrecord_id', 'user_id', 'check_date', 'weight', 'record')
    )

    records_by_date = {}
    for rec in month_records:
        rec_date = rec['check_date'].date() if hasattr(rec['check_date'], 'date') else rec['check_date']
        records_by_date.setdefault(rec_date, []).append(rec)

    month_record_dates = set(records_by_date.keys())
    has_weight_dates = {
        record_date
        for record_date, rec_list in records_by_date.items()
        if any(rec['weight'] not in (None, '', '-') for rec in rec_list)
    }
    has_record_text_dates = {
        record_date
        for record_date, rec_list in records_by_date.items()
        if any(rec.get('record') not in (None, '', '-') for rec in rec_list)
    }

    latest_record_ids = [rec['pregnancyrecord_id'] for rec_list in records_by_date.values() for rec in rec_list]

    has_prenatalrecord_dates = set()
    if latest_record_ids:
        prenatal_rows = (
            Prenatalrecord.objects
            .filter(pregnancyrecord_id__in=latest_record_ids)
            .values_list('pregnancyrecord_id', 'fetal_heart_rate')
        )
        prenatal_values_by_record_id = {}
        for rec_id, fetal_heart_rate in prenatal_rows:
            prenatal_values_by_record_id.setdefault(rec_id, []).append(fetal_heart_rate)

        for record_date, rec_list in records_by_date.items():
            if any(prenatal_values_by_record_id.get(rec['pregnancyrecord_id']) for rec in rec_list):
                has_prenatalrecord_dates.add(record_date)

    has_feelings_dates = set()
    if latest_record_ids:
        feeling_record_ids = set(
            Userfeeling.objects
            .filter(pregnancyrecord_id__in=latest_record_ids)
            .exclude(feeling__feeling_name__isnull=True)
            .exclude(feeling__feeling_name='')
            .exclude(feeling__feeling_name='-')
            .values_list('pregnancyrecord_id', flat=True)
        )
        for record_date, rec_list in records_by_date.items():
            if any(rec['pregnancyrecord_id'] in feeling_record_ids for rec in rec_list):
                has_feelings_dates.add(record_date)

    has_physical_conditions_dates = set()
    if latest_record_ids:
        physical_condition_record_ids = set(
            Userphysicalcondition.objects
            .filter(pregnancyrecord_id__in=latest_record_ids)
            .exclude(physicalcondition__physicalcondition_name__isnull=True)
            .exclude(physicalcondition__physicalcondition_name='')
            .exclude(physicalcondition__physicalcondition_name='-')
            .values_list('pregnancyrecord_id', flat=True)
        )
        for record_date, rec_list in records_by_date.items():
            if any(rec['pregnancyrecord_id'] in physical_condition_record_ids for rec in rec_list):
                has_physical_conditions_dates.add(record_date)

    first_weekday, days_in_month = calendar.monthrange(year, month)
    leading_blanks = (first_weekday + 1) % 7

    cells = []
    for _ in range(leading_blanks):
        cells.append({'empty': True})

    for day in range(1, days_in_month + 1):
        d = datetime.date(year, month, day)
        cells.append({
            'empty': False,
            'day': day,
            'date_iso': d.isoformat(),
            'is_selected': d == selected_date,
            'is_future': d > today_date,
            'has_record': d in month_record_dates,
            'has_weight': d in has_weight_dates,
            'has_prenatalrecord': d in has_prenatalrecord_dates,
            'has_feelings': d in has_feelings_dates,
            'has_physical_conditions': d in has_physical_conditions_dates,
            'has_record_text': d in has_record_text_dates,
        })

    while len(cells) % 7 != 0:
        cells.append({'empty': True})

    calendar_weeks = [cells[i:i+7] for i in range(0, len(cells), 7)]
    while len(calendar_weeks) < 5:
        empty_week = [{'empty': True} for _ in range(7)]
        calendar_weeks.append(empty_week)

    selected_day_records = list(
        _records_for_scope(pregnancy_case, current_user)
        .filter(check_date=selected_date)
        .order_by('-check_date', '-pregnancyrecord_id')
    )
    selected_day_record = selected_day_records[0] if selected_day_records else None

    selected_day_weight = None
    for record_item in selected_day_records:
        if record_item.weight not in (None, '', '-'):
            selected_day_weight = record_item.weight
            break

    selected_day_fetal_heart_rate = None
    selected_day_prenatal = None
    if selected_day_record:
        prenatal_candidates = list(
            Prenatalrecord.objects
            .filter(pregnancyrecord=selected_day_record)
            .order_by('-prenatalrecord_id')
        )
        for prenatal_item in prenatal_candidates:
            if prenatal_item.fetal_heart_rate not in (None, '', '-', 0):
                selected_day_prenatal = prenatal_item
                selected_day_fetal_heart_rate = prenatal_item.fetal_heart_rate
                break
        if selected_day_prenatal is None and prenatal_candidates:
            selected_day_prenatal = prenatal_candidates[0]

    selected_day_feelings = []
    if selected_day_records:
        selected_day_record_ids = [record_item.pregnancyrecord_id for record_item in selected_day_records]
        user_feelings = (
            Userfeeling.objects
            .filter(pregnancyrecord_id__in=selected_day_record_ids)
            .select_related('feeling')
        )
        seen_feeling_ids = set()
        for user_feeling in user_feelings:
            if not user_feeling.feeling_id or not user_feeling.feeling:
                continue
            if user_feeling.feeling.feeling_name in (None, '', '-'):
                continue
            if user_feeling.feeling_id in seen_feeling_ids:
                continue
            seen_feeling_ids.add(user_feeling.feeling_id)
            selected_day_feelings.append(FEELING_EMOJI_MAP.get(user_feeling.feeling.feeling_name, '🙂'))

    selected_day_physical_conditions = []
    if selected_day_records:
        selected_day_record_ids = [record_item.pregnancyrecord_id for record_item in selected_day_records]
        user_physical_conditions = (
            Userphysicalcondition.objects
            .filter(pregnancyrecord_id__in=selected_day_record_ids)
            .select_related('physicalcondition')
        )
        seen_physical_condition_ids = set()
        for user_physical_condition in user_physical_conditions:
            if not user_physical_condition.physicalcondition_id or not user_physical_condition.physicalcondition:
                continue
            if user_physical_condition.physicalcondition.physicalcondition_name in (None, '', '-'):
                continue
            if user_physical_condition.physicalcondition_id in seen_physical_condition_ids:
                continue
            seen_physical_condition_ids.add(user_physical_condition.physicalcondition_id)
            selected_day_physical_conditions.append(user_physical_condition.physicalcondition.physicalcondition_name)

    has_day_data = bool(selected_day_records)

    context = {
        'selected_date': selected_date,
        'selected_date_iso': selected_date.isoformat(),
        'today_iso': today_date.isoformat(),
        'selected_month_label': f'{selected_date.year}年 {selected_date.month}月',
        'user_name': current_user.name if current_user else '',
        'selected_day': selected_date.day,
        'calendar_weeks': calendar_weeks,
        'selected_day_weight': selected_day_weight,
        'selected_day_fetal_heart_rate': selected_day_fetal_heart_rate,
        'selected_day_feelings': selected_day_feelings,
        'selected_day_physical_conditions': selected_day_physical_conditions,
        'selected_day_record_text': selected_day_record.record if selected_day_record else '',
        'has_day_data': has_day_data,
        'selected_day_record_id': selected_day_record.pregnancyrecord_id if selected_day_record else None,
    }
    return render(request, 'pregnancy/pregnancyrecord.html', context)


def pregnancyrecord_new(request):
    query = request.META.get('QUERY_STRING')
    target = '/pregnancyrecord/'
    if query:
        target = f'{target}?{query}'
    return redirect(target)


def _build_feelings():
    feelings = Feeling.objects.order_by('feeling_id').all()
    return [
        {
            'id': feeling.feeling_id,
            'name': feeling.feeling_name,
            'emoji': FEELING_EMOJI_MAP.get(feeling.feeling_name, '🙂'),
        }
        for feeling in feelings
    ]


def _build_physical_conditions():
    try:
        physicalconditions = PhysicalCondition.objects.order_by('physicalcondition_id').all()
    except ProgrammingError:
        return []

    return [
        {
            'id': physicalcondition.physicalcondition_id,
            'name': physicalcondition.physicalcondition_name,
        }
        for physicalcondition in physicalconditions
    ]


def pregnancyrecord_add(request):
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    pregnancy_case = resolve_active_pregnancy_case(request, current_user)
    membership = None
    if pregnancy_case and pregnancy_case.user_id != current_user.user_id:
        membership = FamilyMember.objects.filter(pregnancycase=pregnancy_case, user=current_user).first()
        if not baby_utils.has_permission(membership, 'mom_records', 'view'):
            return redirect('/userprofile/?perm_error=mom_records')
    def _can_edit_records():
        if not pregnancy_case:
            return True
        if pregnancy_case.user_id == current_user.user_id:
            return True
        return baby_utils.has_permission(membership, 'mom_records', 'edit')

    selected_date = _parse_selected_date(request.GET.get('date') or request.POST.get('check_date'))

    preg_from_get = None
    preg_id_get = request.GET.get('record_id')
    if preg_id_get:
        try:
            preg_id_int = int(preg_id_get)
        except Exception:
            preg_id_int = None
        if preg_id_int:
            preg_from_get = PregnancyRecord.objects.filter(pregnancyrecord_id=preg_id_int).first()
            if preg_from_get:
                try:
                    selected_date = preg_from_get.check_date.date()
                except Exception:
                    pass

    if preg_from_get:
        selected_day_record = preg_from_get
    else:
        selected_day_record = _get_unique_record_for_date(pregnancy_case, current_user, selected_date)

    selected_day_prenatal = (
        Prenatalrecord.objects
        .filter(pregnancyrecord=selected_day_record)
        .first()
        if selected_day_record else None
    )

    if request.method == 'POST':
        if not _can_edit_records():
            return redirect(url_with_active_selection(
                request, '/pregnancyrecord/', {'date': selected_date.isoformat()}
            ))
        with transaction.atomic():
            weight = request.POST.get('weight') or None
            record = request.POST.get('record') or ''
            official_record_enabled = request.POST.get('official_record') in ('1', 'on', 'true', 'True')

            try:
                weight_val = float(weight) if weight not in (None, '') else None
            except ValueError:
                weight_val = None

            orig_id = request.POST.get('record_id')
            preg = None
            if orig_id:
                try:
                    orig_id_int = int(orig_id)
                except Exception:
                    orig_id_int = None
                if orig_id_int:
                    preg = PregnancyRecord.objects.filter(pregnancyrecord_id=orig_id_int).first()

            check_datetime = _date_to_safe_datetime(selected_date)

            if preg:
                conflict_record = _get_unique_record_for_date(pregnancy_case, current_user, selected_date)
                if conflict_record and conflict_record.pregnancyrecord_id != preg.pregnancyrecord_id:
                    conflict_record.record = record
                    conflict_record.weight = weight_val
                    conflict_record.check_date = check_datetime
                    conflict_record.save(update_fields=['check_date', 'record', 'weight'])
                    preg.delete()
                    preg = conflict_record
                else:
                    preg.check_date = check_datetime
                    preg.record = record
                    preg.weight = weight_val
                    preg.save(update_fields=['check_date', 'record', 'weight'])
            else:
                selected_day_record = _get_unique_record_for_date(pregnancy_case, current_user, selected_date)
                if selected_day_record:
                    preg = selected_day_record
                    preg.record = record
                    preg.weight = weight_val
                    preg.save(update_fields=['record', 'weight'])
                else:
                    preg = PregnancyRecord.objects.create(
                        user=current_user,
                        check_date=check_datetime,
                        record=record,
                        weight=weight_val,
                    )

            if preg and getattr(preg, 'check_date', None):
                selected_date = preg.check_date.date() if hasattr(preg.check_date, 'date') else preg.check_date

            sbp = request.POST.get('sbp')
            dbp = request.POST.get('dbp')
            fetal = request.POST.get('fetal_heart_rate')
            urine_glucose_raw = request.POST.get('urine_glucose') or ''
            urine_protein_raw = request.POST.get('urine_protein') or ''
            edema_raw = request.POST.get('edema') or ''
            urine_glucose = MARKER_VALUE_MAP.get(urine_glucose_raw, '')
            urine_protein = MARKER_VALUE_MAP.get(urine_protein_raw, '')
            edema = MARKER_VALUE_MAP.get(edema_raw, '')

            uploaded_photo = request.FILES.get('photo')

            def to_int(v):
                try:
                    return int(v) if v not in (None, '') else None
                except ValueError:
                    return None

            if official_record_enabled:
                latest_prenatal = (
                    Prenatalrecord.objects
                    .filter(pregnancyrecord=preg)
                    .order_by('-prenatalrecord_id')
                    .first()
                )
                existing_photo = latest_prenatal.photo if latest_prenatal else ''

                if latest_prenatal:
                    latest_prenatal.sbp = to_int(sbp) or 0
                    latest_prenatal.dbp = to_int(dbp) or 0
                    latest_prenatal.fetal_heart_rate = to_int(fetal) or 0
                    latest_prenatal.urine_glucose = urine_glucose
                    latest_prenatal.urine_protein = urine_protein
                    latest_prenatal.edema = edema
                    if uploaded_photo:
                        latest_prenatal.photo = _store_prenatal_photo(
                            uploaded_photo,
                            latest_prenatal.prenatalrecord_id,
                            existing_photo,
                        )
                    latest_prenatal.save()
                else:
                    latest_prenatal = Prenatalrecord.objects.create(
                        pregnancyrecord=preg,
                        sbp=to_int(sbp) or 0,
                        dbp=to_int(dbp) or 0,
                        fetal_heart_rate=to_int(fetal) or 0,
                        urine_glucose=urine_glucose,
                        urine_protein=urine_protein,
                        edema=edema,
                        photo='',
                    )
                    if uploaded_photo:
                        latest_prenatal.photo = _store_prenatal_photo(
                            uploaded_photo,
                            latest_prenatal.prenatalrecord_id,
                            '',
                        )
                        latest_prenatal.save(update_fields=['photo'])
            else:
                Prenatalrecord.objects.filter(pregnancyrecord=preg).delete()

            # ─── 終極修正：改用原生先刪後蓋（Purge & Re-insert）處理特殊關聯表 ───
            feelings_selected = request.POST.getlist('feelings')
            valid_feeling_ids = set(Feeling.objects.values_list('feeling_id', flat=True))
            selected_feeling_ids = {int(fid) for fid in feelings_selected if fid.isdigit() and int(fid) in valid_feeling_ids}

            # 1. 刪除這筆日記當天的舊心情
            Userfeeling.objects.filter(pregnancyrecord=preg).delete()

            # 2. 重新建立心情紀錄，由資料庫自動產生主鍵
            for feeling_id in selected_feeling_ids:
                Userfeeling.objects.create(
                    pregnancyrecord=preg,
                    feeling_id=feeling_id,
                )

            # 身體狀態處理
            phys_selected = request.POST.getlist('physical_conditions')
            Userphysicalcondition.objects.filter(pregnancyrecord=preg).delete()
            if phys_selected:
                up_objs = [
                    Userphysicalcondition(pregnancyrecord=preg, physicalcondition_id=int(pid))
                    for pid in phys_selected if pid.isdigit()
                ]
                if up_objs:
                    Userphysicalcondition.objects.bulk_create(up_objs)

        return redirect(url_with_active_selection(
            request, '/pregnancyrecord/', {'date': selected_date.isoformat()}
        ))

    selected_day_feeling_ids = []
    selected_day_physical_condition_ids = []
    if selected_day_record:
        selected_day_feeling_ids = list(
            Userfeeling.objects
            .filter(pregnancyrecord=selected_day_record)
            .values_list('feeling_id', flat=True)
        )
        selected_day_physical_condition_ids = list(
            Userphysicalcondition.objects
            .filter(pregnancyrecord=selected_day_record)
            .values_list('physicalcondition_id', flat=True)
        )

    has_prenatalrecord = selected_day_prenatal is not None

    context = {
        'feelings': _build_feelings(),
        'physical_conditions': _build_physical_conditions(),
        'selected_date': selected_date,
        'selected_date_iso': selected_date.isoformat(),
        'today_iso': datetime.date.today().isoformat(),
        'selected_month_abbr': selected_date.strftime('%b').upper(),
        'selected_day': selected_date.day,
        'form_weight': selected_day_record.weight if selected_day_record else '',
        'form_record': selected_day_record.record if selected_day_record else '',
        'form_fetal_heart_rate': (
            selected_day_prenatal.fetal_heart_rate if selected_day_prenatal and selected_day_prenatal.fetal_heart_rate else ''
        ),
        'form_sbp': selected_day_prenatal.sbp if selected_day_prenatal else '',
        'form_dbp': selected_day_prenatal.dbp if selected_day_prenatal else '',
        'form_urine_glucose': MARKER_LABEL_TO_VALUE.get(selected_day_prenatal.urine_glucose, '') if selected_day_prenatal else '',
        'form_urine_protein': MARKER_LABEL_TO_VALUE.get(selected_day_prenatal.urine_protein, '') if selected_day_prenatal else '',
        'form_edema': MARKER_LABEL_TO_VALUE.get(selected_day_prenatal.edema, '') if selected_day_prenatal else '',
        'form_photo_url': selected_day_prenatal.photo if selected_day_prenatal and selected_day_prenatal.photo else '',
        'selected_day_feeling_ids': selected_day_feeling_ids,
        'selected_day_physical_condition_ids': selected_day_physical_condition_ids,
        'has_prenatalrecord': has_prenatalrecord,
        'submit_button_text': '更新紀錄' if selected_day_record else '完成並儲存紀錄',
        'selected_day_record_id': selected_day_record.pregnancyrecord_id if selected_day_record else None,
    }
    return render(request, 'pregnancy/pregnancyrecordadd.html', context)


