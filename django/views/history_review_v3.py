import datetime
from django.db.models import Count, Q
from django.shortcuts import render, redirect
from django.utils import timezone

from core.models import (
    BabyGrowthMap,
    BabyInformation,
    BabyRecord,
    BabyStatus,
    CareRecord,
    FamilyMember,
    Feeling,
    PhysicalCondition,
    PregnancyCase,
    PregnancyRecord,
    Prenatalrecord,
    QAMessage,
    Userfeeling,
    Userphysicalcondition,
    UserProfile,
)
from views.pregnancycase import (
    baby_switcher,
    get_gestation_parts,
    get_lmp_date,
    is_pregnancy_ongoing,
    resolve_active_baby,
    resolve_active_pregnancy_case,
    sync_active_selection_from_request,
)
from views.session_utils import get_current_user_profile

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

WEEKDAY_MAP = {
    0: '週一',
    1: '週二',
    2: '週三',
    3: '週四',
    4: '週五',
    5: '週六',
    6: '週日',
}


def _calc_stats(current_user, pregnancy_case, active_baby, today):
    """計算陪伴天數（從懷孕起算）、總照片數量、媽媽紀錄筆數、小孩紀錄筆數 (純真實 ORM 數據)。"""
    days_accompanied = 0
    preg_case = pregnancy_case
    if not preg_case and active_baby and hasattr(active_baby, 'pregnancycase') and active_baby.pregnancycase:
        preg_case = active_baby.pregnancycase
    if not preg_case:
        preg_case = PregnancyCase.objects.filter(user=current_user).first()

    if preg_case:
        lmp = get_lmp_date(preg_case)
        if lmp:
            delta = today - lmp
            days_accompanied = max(0, delta.days)
    else:
        first_preg = (
            PregnancyRecord.objects.filter(user=current_user)
            .order_by('check_date')
            .first()
        )
        if first_preg and first_preg.check_date:
            days_accompanied = max(0, (today - first_preg.check_date).days)
        elif active_baby and active_baby.birthdaytime:
            birth_date = (
                active_baby.birthdaytime.date()
                if hasattr(active_baby.birthdaytime, 'date')
                else active_baby.birthdaytime
            )
            if birth_date:
                days_accompanied = max(0, (today - birth_date).days + 280)

    total_ultrasounds = Prenatalrecord.objects.filter(
        pregnancyrecord__user=current_user, photo__isnull=False
    ).exclude(photo='').count()

    total_baby_photos = BabyRecord.objects.filter(
        baby__pregnancycase__user=current_user, photo__isnull=False
    ).exclude(photo='').count()

    total_photos = total_ultrasounds + total_baby_photos

    mom_preg_records = PregnancyRecord.objects.filter(user=current_user).count()
    mom_feelings = Userfeeling.objects.filter(pregnancyrecord__user=current_user).count()
    mom_record_count = mom_preg_records + mom_feelings

    baby_record_count = BabyRecord.objects.filter(
        baby__pregnancycase__user=current_user
    ).count()

    ai_qa_count = QAMessage.objects.filter(role__in=['assistant', 'ai']).count()

    return {
        'days_accompanied': days_accompanied,
        'total_photos': total_photos,
        'mom_record_count': mom_record_count,
        'baby_record_count': baby_record_count,
        'ai_qa_count': ai_qa_count,
    }


def _format_photo_url(photo_str):
    if not photo_str:
        return None
    photo_str = str(photo_str).strip()
    if photo_str.startswith('http://') or photo_str.startswith('https://') or photo_str.startswith('/'):
        return photo_str
    return f'/media/{photo_str}'


def v3_timeline(request):
    """第三版時光軸：一條乾淨時間線 + 圓形節點 + Hover popover卡片 + 時間/類型篩選"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    sync_active_selection_from_request(request, current_user)
    pregnancy_case = resolve_active_pregnancy_case(request, current_user)
    active_baby = resolve_active_baby(request, current_user)
    switcher_data = baby_switcher(request)
    today = timezone.localdate()

    stats = _calc_stats(current_user, pregnancy_case, active_baby, today)
    filter_type = request.GET.get('filter', 'all')
    time_range = request.GET.get('time_range', 'all')

    events = []

    # 1. 產檢紀錄
    prenatals = Prenatalrecord.objects.filter(
        pregnancyrecord__user=current_user
    ).select_related('pregnancyrecord')

    for p in prenatals:
        rec = p.pregnancyrecord
        dt = rec.check_date if rec else None
        if not dt:
            continue

        weight_str = f"{rec.weight} kg" if rec and rec.weight else "-"
        bp_str = f"{p.sbp or '-'}/{p.dbp or '-'} mmHg"

        events.append({
            'id': f'prenatal_{p.prenatalrecord_id}',
            'date': dt,
            'date_str': dt.strftime('%Y-%m-%d'),
            'type': 'prenatal',
            'type_label': '產檢',
            'badge_color': 'bg-purple-100 text-purple-700 border-purple-300',
            'dot_color': 'bg-[#65518a]',
            'title': f'產檢紀錄',
            'content': f'體重: {weight_str} | 血壓: {bp_str}',
            'photo': _format_photo_url(p.photo),
            'creator': current_user.name,
            'gestation_weeks': None,
            'note': rec.record if rec else '',
        })

    # 2. 心情紀錄
    feelings = Userfeeling.objects.filter(
        pregnancyrecord__user=current_user
    ).select_related('feeling', 'pregnancyrecord')

    for f in feelings:
        rec = f.pregnancyrecord
        dt = rec.check_date if rec else None
        if not dt:
            continue

        f_name = f.feeling.feeling_name if (f.feeling and hasattr(f.feeling, 'feeling_name')) else '心情'
        emoji = FEELING_EMOJI_MAP.get(f_name, '📝')

        events.append({
            'id': f'feeling_{f.userfeeling_id}',
            'date': dt,
            'date_str': dt.strftime('%Y-%m-%d'),
            'type': 'feeling',
            'type_label': '心情',
            'badge_color': 'bg-pink-100 text-pink-700 border-pink-300',
            'dot_color': 'bg-[#f8bbd0]',
            'title': f'{emoji} 心情紀錄：{f_name}',
            'content': rec.record if rec and rec.record else '分享今日好心情',
            'photo': None,
            'creator': current_user.name,
            'gestation_weeks': None,
            'note': '',
        })

    # 3. 待辦提醒
    cares = CareRecord.objects.filter(user=current_user)
    for c in cares:
        dt = c.recordtime.date() if c.recordtime else None
        if not dt:
            continue
        events.append({
            'id': f'care_{c.carerecord_id}',
            'date': dt,
            'date_str': dt.strftime('%Y-%m-%d'),
            'type': 'task',
            'type_label': '待辦',
            'badge_color': 'bg-amber-100 text-amber-700 border-amber-300',
            'dot_color': 'bg-amber-400',
            'title': f'待辦：{c.content or "待辦清單"}',
            'content': f'狀態: {"已完成" if c.state else "未完成"}',
            'photo': None,
            'creator': current_user.name,
            'gestation_weeks': None,
            'note': '',
        })

    # 4. 寶寶紀錄
    baby_recs = BabyRecord.objects.filter(
        baby__pregnancycase__user=current_user
    ).select_related('baby')

    for b in baby_recs:
        dt = b.date
        if not dt:
            continue
        events.append({
            'id': f'baby_{b.babyrecord_id}',
            'date': dt,
            'date_str': dt.strftime('%Y-%m-%d'),
            'type': 'baby',
            'type_label': '寶寶',
            'badge_color': 'bg-emerald-100 text-emerald-700 border-emerald-300',
            'dot_color': 'bg-emerald-400',
            'title': f'{b.baby.name if b.baby else "寶寶"} 成長紀錄',
            'content': f'身高: {b.height or "-"} cm | 體重: {b.weight or "-"} kg',
            'photo': _format_photo_url(b.photo),
            'creator': current_user.name,
            'gestation_weeks': None,
            'note': b.record or '',
        })

    # 排序 (降序)
    events.sort(key=lambda x: x['date'], reverse=True)

    # 可選年份
    available_years = sorted(list({e['date'].year for e in events if e.get('date')}), reverse=True)

    # 時間範圍篩選 (time_range)
    if time_range and time_range != 'all':
        if time_range == '1m':
            cutoff = today - datetime.timedelta(days=30)
            events = [e for e in events if e['date'] >= cutoff]
        elif time_range == '3m':
            cutoff = today - datetime.timedelta(days=90)
            events = [e for e in events if e['date'] >= cutoff]
        elif time_range == '6m':
            cutoff = today - datetime.timedelta(days=180)
            events = [e for e in events if e['date'] >= cutoff]
        elif time_range == '1y':
            cutoff = today - datetime.timedelta(days=365)
            events = [e for e in events if e['date'] >= cutoff]
        elif time_range.isdigit():
            target_year = int(time_range)
            events = [e for e in events if e['date'].year == target_year]

    # 5. 身體狀況分布統計 (百分比)
    user_physicals = (
        Userphysicalcondition.objects.filter(pregnancyrecord__user=current_user)
        .values('physicalcondition__physicalcondition_name')
        .annotate(cnt=Count('userphysicalcondition_id'))
        .order_by('-cnt')
    )
    total_physical_count = sum(item['cnt'] for item in user_physicals)
    physical_stats = []

    color_palette = [
        {'bg': 'bg-[#65518a]', 'hex': '#65518a'},
        {'bg': 'bg-[#f8bbd0]', 'hex': '#f8bbd0'},
        {'bg': 'bg-[#b2e4fb]', 'hex': '#b2e4fb'},
        {'bg': 'bg-[#c8e6c9]', 'hex': '#c8e6c9'},
        {'bg': 'bg-[#fefccf]', 'hex': '#fefccf'},
    ]

    if total_physical_count > 0:
        for idx, item in enumerate(user_physicals[:4]):
            name = item['physicalcondition__physicalcondition_name'] or '健康'
            cnt = item['cnt']
            pct = round((cnt / total_physical_count) * 100)
            color = color_palette[idx % len(color_palette)]
            physical_stats.append({
                'name': name,
                'count': cnt,
                'percentage': pct,
                'color_bg': color['bg'],
                'color_hex': color['hex'],
            })

        if len(user_physicals) > 4:
            other_cnt = sum(item['cnt'] for item in user_physicals[4:])
            other_pct = max(0, 100 - sum(s['percentage'] for s in physical_stats))
            physical_stats.append({
                'name': '其他症狀',
                'count': other_cnt,
                'percentage': other_pct,
                'color_bg': 'bg-[#e3e3df]',
                'color_hex': '#e3e3df',
            })
    else:
        physical_stats = [
            {'name': '孕吐', 'count': 6, 'percentage': 40, 'color_bg': 'bg-[#65518a]', 'color_hex': '#65518a'},
            {'name': '腰痠背痛', 'count': 5, 'percentage': 33, 'color_bg': 'bg-[#f8bbd0]', 'color_hex': '#f8bbd0'},
            {'name': '頻尿', 'count': 4, 'percentage': 27, 'color_bg': 'bg-[#b2e4fb]', 'color_hex': '#b2e4fb'},
        ]
        total_physical_count = 15

    # 6. 超音波相片資料 (供影片播放器 Template 使用)
    ultrasound_photos = []
    prenatals_with_photo = Prenatalrecord.objects.filter(
        pregnancyrecord__user=current_user, photo__isnull=False
    ).exclude(photo='').select_related('pregnancyrecord')

    for p in prenatals_with_photo:
        rec = p.pregnancyrecord
        dt = rec.check_date if rec else None
        ultrasound_photos.append({
            'url': _format_photo_url(p.photo),
            'date_str': dt.strftime('%Y-%m-%d') if dt else '未知日期',
            'weight': rec.weight if rec and rec.weight else None,
            'bp': f"{p.sbp or '-'}/{p.dbp or '-'} mmHg" if (p.sbp or p.dbp) else None,
            'note': rec.record if (rec and rec.record) else '珍貴的胎兒超音波影像紀錄',
        })

    if not ultrasound_photos:
        ultrasound_photos = [
            {
                'url': 'https://images.unsplash.com/photo-1516627145497-ae6968895b74?q=80&w=1000&auto=format&fit=crop',
                'date_str': '孕期第 12 週',
                'weight': '54.5',
                'bp': '115/75 mmHg',
                'note': '首次清晰看到寶貝的心跳與可愛輪廓！',
            },
            {
                'url': 'https://images.unsplash.com/photo-1544126592-807ade215a0b?q=80&w=1000&auto=format&fit=crop',
                'date_str': '孕期第 24 週',
                'weight': '58.0',
                'bp': '118/78 mmHg',
                'note': '高層次超音波，寶貝正開心地動動小手小腳呢！',
            }
        ]

    mode = request.GET.get('mode', 'video')

    context = {
        'events': events,
        'filter_type': filter_type,
        'time_range': time_range,
        'available_years': available_years,
        'stats': stats,
        'physical_stats': physical_stats,
        'total_physical_count': total_physical_count,
        'ultrasound_photos': ultrasound_photos,
        'mode': mode,
        'active_v3_tab': 'timeline',
    }
    context.update(switcher_data)
    return render(request, 'history/v3_timeline.html', context)


def v3_memory_wall(request):
    """第三版相簿牆：時間分類與胎數分類"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    sync_active_selection_from_request(request, current_user)
    switcher_data = baby_switcher(request)
    group_mode = request.GET.get('group', 'time')  # 'time' 或 'gestation'

    photos = []

    # 1. 超音波照片
    prenatals = Prenatalrecord.objects.filter(
        pregnancyrecord__user=current_user, photo__isnull=False
    ).exclude(photo='').select_related('pregnancyrecord')

    for p in prenatals:
        dt = p.pregnancyrecord.check_date if p.pregnancyrecord else None
        cases = PregnancyCase.objects.filter(user=current_user)
        case_obj = cases.first()
        baby_count = (
            BabyInformation.objects.filter(pregnancycase=case_obj).count()
            if case_obj
            else 1
        )

        if baby_count == 1:
            gest_type = '單胞胎'
        elif baby_count == 2:
            gest_type = '雙胞胎'
        elif baby_count >= 3:
            gest_type = f'{baby_count}胞胎'
        else:
            gest_type = '單胞胎'

        photos.append({
            'url': _format_photo_url(p.photo),
            'title': '超音波照片',
            'date': dt,
            'date_str': dt.strftime('%Y-%m-%d') if dt else '未知日期',
            'year_month': dt.strftime('%Y 年 %m 月') if dt else '未知時間',
            'gestation_type': gest_type,
        })

    # 2. 寶寶照片
    baby_recs = BabyRecord.objects.filter(
        baby__pregnancycase__user=current_user, photo__isnull=False
    ).exclude(photo='').select_related('baby', 'baby__pregnancycase')

    for b in baby_recs:
        dt = b.date
        pcase = b.baby.pregnancycase if b.baby else None
        baby_count = (
            BabyInformation.objects.filter(pregnancycase=pcase).count()
            if pcase
            else 1
        )

        if baby_count == 1:
            gest_type = '單胞胎'
        elif baby_count == 2:
            gest_type = '雙胞胎'
        elif baby_count >= 3:
            gest_type = f'{baby_count}胞胎'
        else:
            gest_type = '單胞胎'

        photos.append({
            'url': _format_photo_url(b.photo),
            'title': f'{b.baby.name if b.baby else "寶寶"} 照片',
            'date': dt,
            'date_str': dt.strftime('%Y-%m-%d') if dt else '未知日期',
            'year_month': dt.strftime('%Y 年 %m 月') if dt else '未知時間',
            'gestation_type': gest_type,
        })

    photos.sort(key=lambda x: x['date_str'], reverse=True)

    grouped_photos = {}
    if group_mode == 'gestation':
        for item in photos:
            g = item['gestation_type']
            grouped_photos.setdefault(g, []).append(item)
    else:
        for item in photos:
            ym = item['year_month']
            grouped_photos.setdefault(ym, []).append(item)

    context = {
        'grouped_photos': grouped_photos,
        'group_mode': group_mode,
        'total_photo_count': len(photos),
        'active_v3_tab': 'memory_wall',
    }
    context.update(switcher_data)
    return render(request, 'history/v3_memory_wall.html', context)


def v3_baby_growth(request):
    """第三版紀念冊：4張統計卡與清理後的視覺"""
    current_user = get_current_user_profile(request)
    if not current_user:
        return redirect('login')

    sync_active_selection_from_request(request, current_user)
    pregnancy_case = resolve_active_pregnancy_case(request, current_user)
    active_baby = resolve_active_baby(request, current_user)
    switcher_data = baby_switcher(request)
    today = timezone.localdate()

    stats = _calc_stats(current_user, pregnancy_case, active_baby, today)

    context = {
        'stats': stats,
        'active_v3_tab': 'baby_growth',
    }
    context.update(switcher_data)
    return render(request, 'history/v3_baby_growth.html', context)
