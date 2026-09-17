import datetime
from datetime import timedelta
from urllib import request

from django.db.models import Q
from django.shortcuts import render, redirect
from django.utils import timezone
from zoneinfo import ZoneInfo

from core.models import CareRecord, UserProfile, PregnancyRecord, BabyRecord, FamilyMember
from views import baby_utils
from .pregnancyrecords import records_for_case
from views.pregnancycase import (
    build_pregnancy_progress,
    resolve_active_baby,
    resolve_active_pregnancy_case,
    sync_active_selection_from_request,
)
from views.session_utils import get_current_user_profile

DEFAULT_USER_ID = 'ab63df64-b61f-480e-a61c-d54b851d2b5e'
TAIWAN_TZ = ZoneInfo('Asia/Taipei')


def _parse_selected_date(raw):
    try:
        return datetime.date.fromisoformat(raw) if raw else datetime.date.today()
    except Exception:
        return datetime.date.today()


def _day_bounds_in_taiwan(date_value):
    start_naive = datetime.datetime.combine(date_value, datetime.time.min)
    end_naive = start_naive + timedelta(days=1)
    return start_naive, end_naive


def _build_pregnancy_chart_data(user, pregnancy_case=None):
    if not user:
        return []

    chart_rows = []
    if pregnancy_case:
        records = (
            records_for_case(pregnancy_case)
            .filter(weight__isnull=False)
            .order_by('check_date', 'pregnancyrecord_id')
        )
    else:
        records = (
            PregnancyRecord.objects
            .filter(user=user, weight__isnull=False)
            .order_by('check_date', 'pregnancyrecord_id')
        )

    for record in records:
        check_date = (
            record.check_date.date()
            if isinstance(record.check_date, datetime.datetime)
            else record.check_date
        )
        if not check_date:
            continue
        chart_rows.append({
            'date_iso': check_date.isoformat(),
            'label': f'{check_date.month}/{check_date.day}',
            'weight': float(record.weight),
        })

    # 左下角的日期改成每一胎的最後月經日期
    if pregnancy_case and pregnancy_case.menstruation:
        lmp = pregnancy_case.menstruation
        lmp_iso = lmp.isoformat()
        lmp_label = f'{lmp.month}/{lmp.day}'

        existing_lmp_idx = next((i for i, r in enumerate(chart_rows) if r['date_iso'] == lmp_iso), None)
        if existing_lmp_idx is not None:
            lmp_row = chart_rows.pop(existing_lmp_idx)
            lmp_row['label'] = lmp_label
            lmp_row['is_lmp'] = True
            chart_rows.insert(0, lmp_row)
        else:
            initial_weight = chart_rows[0]['weight'] if chart_rows else None
            if initial_weight is not None:
                chart_rows.insert(0, {
                    'date_iso': lmp_iso,
                    'label': lmp_label,
                    'weight': initial_weight,
                    'is_lmp': True,
                })
            else:
                chart_rows.insert(0, {
                    'date_iso': lmp_iso,
                    'label': lmp_label,
                    'weight': None,
                    'is_lmp': True,
                })

    return chart_rows


def _build_baby_chart_data(baby):
    if not baby:
        return []

    chart_rows = []
    records = (
        BabyRecord.objects
        .filter(baby=baby)
        .filter(Q(weight__isnull=False) | Q(height__isnull=False))
        .order_by('date', 'babyrecord_id')
    )
    for record in records:
        row = {
            'date_iso': record.date.isoformat(),
            'label': f'{record.date.month}/{record.date.day}',
        }
        if record.weight is not None:
            row['weight'] = float(record.weight)
        if record.height is not None:
            row['height'] = float(record.height)
        chart_rows.append(row)
    return chart_rows


def index(request):
    selected_date = _parse_selected_date(request.GET.get('date'))
    today = datetime.date.today()
    window_start = selected_date - timedelta(days=3)
    window_end = selected_date + timedelta(days=3)

    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    sync_active_selection_from_request(request, current_user)
    has_baby_selection = bool(
        request.session.get('active_baby_id') or request.GET.get('baby_id')
    )
    active_baby = resolve_active_baby(
        request, current_user, fallback=has_baby_selection
    )
    pregnancy_case = resolve_active_pregnancy_case(request, current_user)
    pregnancy_chart_data = _build_pregnancy_chart_data(current_user, pregnancy_case)
    baby_chart_data = _build_baby_chart_data(active_baby)
    pregnancy_progress = build_pregnancy_progress(pregnancy_case, today)

    can_view_care = True
    can_edit_care = True
    #哪位user新增的代辦清單
    care_queryset = CareRecord.objects.select_related('carestatus', 'user').order_by('recordtime', 'carerecord_id')
    if pregnancy_case:
        if pregnancy_case.user_id != current_user.user_id:
            membership = FamilyMember.objects.filter(pregnancycase=pregnancy_case, user=current_user).first()
            can_view_care = baby_utils.has_permission(membership, 'care_records', 'view', default='view')
            can_edit_care = baby_utils.has_permission(membership, 'care_records', 'edit', default='view')
        care_queryset = care_queryset.filter(pregnancycase=pregnancy_case) if can_view_care else care_queryset.none()
    else:
        # 還沒有任何 active case 時退回舊行為，避免整段壞掉
        care_queryset = care_queryset.filter(user=current_user)

    window_start_dt, _ = _day_bounds_in_taiwan(window_start)
    _, window_end_exclusive = _day_bounds_in_taiwan(window_end)
    window_records = list(
        care_queryset.filter(recordtime__gte=window_start_dt, recordtime__lt=window_end_exclusive)
    )

    record_days = set()
    completion_by_day = {}
    for rec in window_records:
        rec_time = rec.recordtime
        if isinstance(rec_time, datetime.datetime):
            d = rec_time.date()
        else:
            d = rec_time
        record_days.add(d)
        completion_by_day.setdefault(d, {'total': 0, 'done': 0})
        completion_by_day[d]['total'] += 1
        if rec.state:
            completion_by_day[d]['done'] += 1

    care_day_cards = []
    weekday_labels = ['一', '二', '三', '四', '五', '六', '日']
    for offset in range(-3, 4):
        d = selected_date + timedelta(days=offset)
        day_completion = completion_by_day.get(d)
        total = day_completion['total'] if day_completion else 0
        done = day_completion['done'] if day_completion else 0
        care_day_cards.append({
            'day': d.day,
            'weekday': weekday_labels[d.weekday()],
            'date_iso': d.isoformat(),
            'month_label': f'{d.month}/{d.day}',
            'is_selected': d == selected_date,
            'is_future': d > today,
            'has_record': d in record_days,
            'completion': f'{done}/{total}' if total else '',
            'all_done': bool(total) and done == total,
            'total': total,
            'done': done,
        })

    selected_start_dt, selected_end_dt = _day_bounds_in_taiwan(selected_date)
    selected_day_records = list(
        care_queryset.filter(recordtime__gte=selected_start_dt, recordtime__lt=selected_end_dt).order_by('recordtime', 'carerecord_id')
    )
    selected_day_total = len(selected_day_records)
    selected_day_done = sum(1 for r in selected_day_records if r.state)

    context = {
        'selected_date': selected_date,
        'selected_date_iso': selected_date.isoformat(),
        'selected_month_label': f'{selected_date.year}年{selected_date.month}月',
        'selected_day_label': f'{selected_date.month}/{selected_date.day}',
        'window_start_iso': window_start.isoformat(),
        'window_end_iso': window_end.isoformat(),
        'today_iso': today.isoformat(),
        'care_day_cards': care_day_cards,
        'care_records': selected_day_records,
        'care_done_count': selected_day_done,
        'care_total_count': selected_day_total,
        'care_progress_percent': int((selected_day_done / selected_day_total) * 100) if selected_day_total else 0,
        'pregnancy_case': pregnancy_case,
        'pregnancy_chart_data': pregnancy_chart_data,
        'pregnancy_chart_has_data': bool(pregnancy_chart_data),
        'baby_chart_data': baby_chart_data,
        'pregnancy_progress': pregnancy_progress,
        'active_baby': active_baby,
        'current_user': current_user,
        'can_view_care': can_view_care,
        'can_edit_care': can_edit_care,
    }
    return render(request, 'index/index.html', context)