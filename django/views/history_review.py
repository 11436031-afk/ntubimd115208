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
    get_case_display_baby,
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
    """計算陪伴天數、照片總數、紀錄總筆數、AI問答次數與里程碑數 (純真實 ORM 數據)。"""
    days_accompanied = 0
    if pregnancy_case:
        lmp = get_lmp_date(pregnancy_case)
        if lmp:
            delta = today - lmp
            days_accompanied = max(0, delta.days)
    elif active_baby and active_baby.birthdaytime:
        birth_date = (
            active_baby.birthdaytime.date()
            if hasattr(active_baby.birthdaytime, 'date')
            else active_baby.birthdaytime
        )
        if birth_date:
            delta = today - birth_date
            days_accompanied = max(0, delta.days)
    else:
        first_preg = (
            PregnancyRecord.objects.filter(user=current_user)
            .order_by('check_date')
            .first()
        )
        if first_preg and first_preg.check_date:
            days_accompanied = max(0, (today - first_preg.check_date).days)

    target_uid = pregnancy_case.user_id if pregnancy_case else current_user.user_id

    if pregnancy_case:
        total_ultrasounds = Prenatalrecord.objects.filter(
            pregnancyrecord__user_id=target_uid, photo__isnull=False
        ).exclude(photo='').count()
        total_baby_photos = BabyRecord.objects.filter(
            baby__pregnancycase=pregnancy_case, photo__isnull=False
        ).exclude(photo='').count()
        total_preg_records = PregnancyRecord.objects.filter(user_id=target_uid).count()
        total_baby_records = BabyRecord.objects.filter(
            baby__pregnancycase=pregnancy_case
        ).count()
        total_milestones = BabyStatus.objects.filter(
            babyrecord__baby__pregnancycase=pregnancy_case
        ).count()
        total_tasks = CareRecord.objects.filter(
            pregnancycase=pregnancy_case, state=True
        ).count()
    else:
        total_ultrasounds = Prenatalrecord.objects.filter(
            pregnancyrecord__user=current_user, photo__isnull=False
        ).exclude(photo='').count()
        total_baby_photos = BabyRecord.objects.filter(
            baby__pregnancycase__user=current_user, photo__isnull=False
        ).exclude(photo='').count()
        total_preg_records = PregnancyRecord.objects.filter(user=current_user).count()
        total_baby_records = BabyRecord.objects.filter(
            baby__pregnancycase__user=current_user
        ).count()
        total_milestones = BabyStatus.objects.filter(
            babyrecord__baby__pregnancycase__user=current_user
        ).count()
        total_tasks = CareRecord.objects.filter(
            user=current_user, state=True
        ).count()

    total_photos = total_ultrasounds + total_baby_photos
    total_records = total_preg_records + total_baby_records

    total_qas = QAMessage.objects.filter(
        qa_conversation__user_id=current_user, role='assistant'
    ).count()

    return {
        'days_accompanied': days_accompanied,
        'total_ultrasounds': total_ultrasounds,
        'total_baby_photos': total_baby_photos,
        'total_photos': total_photos,
        'total_records': total_records,
        'total_milestones': total_milestones,
        'total_qas': total_qas,
        'total_tasks': total_tasks,
    }


def history_review(request):
    """主歷史回顧進入點：預設渲染 Page 1 時光軸 (Pregnancy Journey Timeline)"""
    return pregnancy_journey_view(request)


def pregnancy_journey_view(request):
    """
    Page 1: 每日成長軌跡 / 時光軸 (/history-review/ & /history-review/pregnancy-journey/)
    """
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
    if not active_baby and pregnancy_case:
        active_baby = get_case_display_baby(pregnancy_case)
    today = timezone.now().date()

    stats = _calc_stats(current_user, pregnancy_case, active_baby, today)
    lmp = get_lmp_date(pregnancy_case) if pregnancy_case else None

    # 1. 成長階段導覽
    stages = [
        {'name': '孕早期', 'icon': '🌱', 'is_active': False},
        {'name': '孕中期', 'icon': '🌸', 'is_active': True},
        {'name': '孕晚期', 'icon': '🤰', 'is_active': False},
        {'name': '出生', 'icon': '👶', 'is_active': False},
        {'name': '滿月', 'icon': '🍼', 'is_active': False},
        {'name': '周歲', 'icon': '🎂', 'is_active': False},
    ]

    # 2. 建構混合 Vertical Timeline Items
    timeline_items = []

    # 2.1 產檢與孕期紀錄 (PregnancyRecord & Prenatalrecord)
    target_uid = pregnancy_case.user_id if pregnancy_case else current_user.user_id
    preg_records_qs = (
        PregnancyRecord.objects.filter(user_id=target_uid)
        .order_by('-check_date')
    )

    for rec in preg_records_qs:
        prenatal = Prenatalrecord.objects.filter(pregnancyrecord=rec).first()
        feelings_qs = Userfeeling.objects.filter(pregnancyrecord=rec).select_related('feeling')
        feelings = [
            {
                'name': uf.feeling.feeling_name if uf.feeling else '',
                'emoji': FEELING_EMOJI_MAP.get(uf.feeling.feeling_name if uf.feeling else '', '🌸')
            }
            for uf in feelings_qs if uf.feeling
        ]

        weeks = None
        if lmp and rec.check_date:
            delta = rec.check_date - lmp
            if delta.days >= 0:
                weeks = delta.days // 7 + 1

        c_date = rec.check_date or today
        w_str = WEEKDAY_MAP.get(c_date.weekday(), '週日')
        date_display = c_date.strftime('%Y/%m/%d') if hasattr(c_date, 'strftime') else str(c_date)

        if prenatal:
            metrics = []
            if prenatal.sbp and prenatal.dbp:
                metrics.append({'label': '血壓', 'val': f'{prenatal.sbp}/{prenatal.dbp} mmHg'})
            if rec.weight:
                metrics.append({'label': '體重', 'val': f'{rec.weight} kg'})
            f_rate = getattr(prenatal, 'fetal_heart_rate', None) or getattr(prenatal, 'fetus_heart_rate', None)
            if f_rate:
                metrics.append({'label': '胎心率', 'val': f'{f_rate} bpm'})
            if getattr(prenatal, 'edema', None) and prenatal.edema != '-':
                metrics.append({'label': '水腫', 'val': prenatal.edema})

            timeline_items.append({
                'full_date': c_date,
                'date_str': date_display,
                'weekday_str': w_str,
                'type': 'prenatal',
                'icon': '🩺',
                'bg_color': 'bg-[#f4ebff]',
                'text_color': 'text-[#8064A2]',
                'title': f'🩺 產檢紀錄' + (f' (第 {weeks} 週)' if weeks else ''),
                'subtitle': f'孕期第 {weeks} 週' if weeks else '產檢紀錄',
                'content': rec.record or '產檢指標紀錄正常，寶寶持續穩定成長中。',
                'photo': prenatal.photo if prenatal.photo else None,
                'metrics': metrics,
                'feelings': feelings,
                'partner_name': None,
                'partner_avatar': None,
            })
        elif rec.record or feelings or rec.weight:
            timeline_items.append({
                'full_date': c_date,
                'date_str': date_display,
                'weekday_str': w_str,
                'type': 'pregnancy',
                'icon': '❤️',
                'bg_color': 'bg-[#fce4ec]',
                'text_color': 'text-[#d81b60]',
                'title': f'❤️ 今日心情' + (f' (第 {weeks} 週)' if weeks else ''),
                'subtitle': f'孕期第 {weeks} 週筆記' if weeks else '孕期日常',
                'content': rec.record or '紀錄了今天的溫馨心境與身體感受。',
                'photo': None,
                'metrics': [{'label': '體重', 'val': f'{rec.weight} kg'}] if rec.weight else [],
                'feelings': feelings,
                'partner_name': None,
                'partner_avatar': None,
            })

    # 2.2 寶寶紀錄 (BabyRecord)
    if active_baby:
        baby_records = BabyRecord.objects.filter(baby=active_baby).order_by('-date')
        for br in baby_records:
            statuses = BabyStatus.objects.filter(babyrecord=br).select_related('babygrowthmap')
            milestones = [st.babygrowthmap.growthrecord for st in statuses if st.babygrowthmap]
            b_date = br.date or today
            w_str = WEEKDAY_MAP.get(b_date.weekday(), '週日')
            date_display = b_date.strftime('%Y/%m/%d') if hasattr(b_date, 'strftime') else str(b_date)

            metrics = []
            if br.weight:
                metrics.append({'label': '體重', 'val': f'{br.weight} kg'})
            if br.height:
                metrics.append({'label': '身高', 'val': f'{br.height} cm'})

            title_str = f'👶 寶寶出生 / 成長' if '出生' in str(milestones) else f'👶 {active_baby.name} 成長紀錄'

            timeline_items.append({
                'full_date': b_date,
                'date_str': date_display,
                'weekday_str': w_str,
                'type': 'baby',
                'icon': '👶',
                'bg_color': 'bg-[#fffde7]',
                'text_color': 'text-[#f57f17]',
                'title': title_str,
                'subtitle': f'{active_baby.name} 成長動態',
                'content': br.record or ('紀錄了孩子的成長數據與可愛時刻。' if not milestones else f'成功達成里程碑：{", ".join(milestones)}'),
                'photo': br.photo if br.photo else None,
                'metrics': metrics,
                'feelings': [],
                'partner_name': None,
                'partner_avatar': None,
            })

    # 2.3 照護待辦 (CareRecord)
    if pregnancy_case:
        care_records = CareRecord.objects.filter(pregnancycase=pregnancy_case, state=True).order_by('-recordtime')[:15]
    else:
        care_records = CareRecord.objects.filter(user=current_user, state=True).order_by('-recordtime')[:15]
    for cr in care_records:
        r_date = cr.recordtime.date() if hasattr(cr.recordtime, 'date') else today
        w_str = WEEKDAY_MAP.get(r_date.weekday(), '週日')
        date_display = r_date.strftime('%Y/%m/%d') if hasattr(r_date, 'strftime') else str(r_date)
        status_name = cr.carestatus.carestatus if cr.carestatus else '日常照護'

        timeline_items.append({
            'full_date': r_date,
            'date_str': date_display,
            'weekday_str': w_str,
            'type': 'task',
            'icon': '✓',
            'bg_color': 'bg-[#e8f5e9]',
            'text_color': 'text-[#2e7d32]',
            'title': f'✓ 待辦完成：{status_name}',
            'subtitle': '照護任務已完成',
            'content': cr.content or f'完成了「{status_name}」健康照護事項。',
            'photo': None,
            'metrics': [],
            'feelings': [],
            'partner_name': '爸爸',
            'partner_avatar': '👨',
        })

    timeline_items.sort(key=lambda x: str(x['date_str']), reverse=True)

    context = {
        'current_user': current_user,
        'pregnancy_case': pregnancy_case,
        'active_baby': active_baby,
        'active_tab': 'timeline',
        'stages': stages,
        'timeline_items': timeline_items,
        'has_timeline': bool(timeline_items),
        'stats': stats,
        'today_date_str': today.strftime('%Y/%m/%d'),
    }
    return render(request, 'history/pregnancy_journey.html', context)


def memory_wall_view(request):
    """
    Page 2: 回顧牆 / 相簿牆 (/history-review/memory-wall/)
    """
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
    if not active_baby and pregnancy_case:
        active_baby = get_case_display_baby(pregnancy_case)
    today = timezone.now().date()

    stats = _calc_stats(current_user, pregnancy_case, active_baby, today)
    target_uid = pregnancy_case.user_id if pregnancy_case else current_user.user_id

    # 1. ✨ 那年那天 (Flashback)
    flashback = None
    on_this_day_preg = PregnancyRecord.objects.filter(
        user_id=target_uid,
        check_date__month=today.month,
        check_date__day=today.day
    ).exclude(check_date=today).first()

    if on_this_day_preg:
        diff_years = max(1, today.year - on_this_day_preg.check_date.year)
        prenatal_p = Prenatalrecord.objects.filter(pregnancyrecord=on_this_day_preg).first()
        flashback = {
            'time_label': f'{diff_years} 年前的今天',
            'date_str': on_this_day_preg.check_date.strftime('%Y/%m/%d'),
            'note': on_this_day_preg.record or '在產檢室第一次聽到寶寶心跳聲 ❤️',
            'photo': prenatal_p.photo if (prenatal_p and prenatal_p.photo) else None,
            'title': '那年的今天 ✨',
        }
    else:
        if pregnancy_case:
            on_this_day_baby = BabyRecord.objects.filter(
                baby__pregnancycase=pregnancy_case,
                date__month=today.month,
                date__day=today.day
            ).exclude(date=today).first()
        else:
            on_this_day_baby = BabyRecord.objects.filter(
                baby__pregnancycase__user=current_user,
                date__month=today.month,
                date__day=today.day
            ).exclude(date=today).first()

        if on_this_day_baby:
            diff_years = max(1, today.year - on_this_day_baby.date.year)
            status = BabyStatus.objects.filter(babyrecord=on_this_day_baby).select_related('babygrowthmap').first()
            milestone_name = status.babygrowthmap.growthrecord if status else '成長紀錄'
            flashback = {
                'time_label': f'{diff_years} 年前的今天',
                'date_str': on_this_day_baby.date.strftime('%Y/%m/%d'),
                'note': on_this_day_baby.record or f'紀錄了寶寶【{milestone_name}】的珍貴時刻。',
                'photo': on_this_day_baby.photo if on_this_day_baby.photo else None,
                'title': milestone_name,
            }

    # 2. 按月份分組相簿
    raw_photos = []
    ultrasound_records = (
        Prenatalrecord.objects.filter(pregnancyrecord__user_id=target_uid, photo__isnull=False)
        .exclude(photo='')
        .select_related('pregnancyrecord')
        .order_by('-pregnancyrecord__check_date')
    )
    for p in ultrasound_records:
        dt = p.pregnancyrecord.check_date if (p.pregnancyrecord and p.pregnancyrecord.check_date) else today
        raw_photos.append({
            'url': p.photo,
            'date_str': dt.strftime('%m/%d') if hasattr(dt, 'strftime') else str(dt),
            'full_date_str': dt.strftime('%Y/%m/%d') if hasattr(dt, 'strftime') else str(dt),
            'year_month': dt.strftime('%Y 年 %m 月') if hasattr(dt, 'strftime') else '歷史相簿',
            'title': '產檢超音波照',
            'stage': '孕期紀錄',
        })

    if pregnancy_case:
        baby_photo_records = (
            BabyRecord.objects.filter(baby__pregnancycase=pregnancy_case, photo__isnull=False)
            .exclude(photo='')
            .select_related('baby')
            .order_by('-date')
        )
    else:
        baby_photo_records = (
            BabyRecord.objects.filter(baby__pregnancycase__user=current_user, photo__isnull=False)
            .exclude(photo='')
            .select_related('baby')
            .order_by('-date')
        )
    for br in baby_photo_records:
        dt = br.date or today
        baby_name = br.baby.name if br.baby else '寶寶'
        status = BabyStatus.objects.filter(babyrecord=br).select_related('babygrowthmap').first()
        title = status.babygrowthmap.growthrecord if status else f'{baby_name} 成長相片'

        stage_label = '寶寶成長紀錄'
        if br.baby and br.baby.birthdaytime and br.date:
            b_date = br.baby.birthdaytime.date() if hasattr(br.baby.birthdaytime, 'date') else br.baby.birthdaytime
            months = max(0, (br.date.year - b_date.year) * 12 + br.date.month - b_date.month)
            stage_label = f'寶寶 {months} 個月'

        raw_photos.append({
            'url': br.photo,
            'date_str': dt.strftime('%m/%d') if hasattr(dt, 'strftime') else str(dt),
            'full_date_str': dt.strftime('%Y/%m/%d') if hasattr(dt, 'strftime') else str(dt),
            'year_month': dt.strftime('%Y 年 %m 月') if hasattr(dt, 'strftime') else '歷史相簿',
            'title': title,
            'stage': stage_label,
        })

    raw_photos.sort(key=lambda x: str(x['full_date_str']), reverse=True)

    grouped_months_dict = {}
    for item in raw_photos:
        ym = item['year_month']
        if ym not in grouped_months_dict:
            grouped_months_dict[ym] = {
                'year_month': ym,
                'stage_label': item['stage'],
                'photos': []
            }
        grouped_months_dict[ym]['photos'].append(item)

    memory_wall_groups = list(grouped_months_dict.values())

    context = {
        'current_user': current_user,
        'pregnancy_case': pregnancy_case,
        'active_baby': active_baby,
        'active_tab': 'photos',
        'stats': stats,
        'flashback': flashback,
        'has_flashback': bool(flashback),
        'memory_wall_groups': memory_wall_groups,
        'has_memory_wall': bool(memory_wall_groups),
        'today_date_str': today.strftime('%Y/%m/%d'),
    }
    return render(request, 'history/memory_wall.html', context)


def baby_growth_view(request):
    """
    Page 3: 階段總結 / 紀念冊 (/history-review/baby-growth/ & /history-review/phase-review/)
    支援 ?mode=pregnancy (孕期畢業報告) 與 ?mode=baby (寶寶 1 歲精華) 雙模式切換！
    """
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
    if not active_baby and pregnancy_case:
        active_baby = get_case_display_baby(pregnancy_case)
    today = timezone.now().date()

    stats = _calc_stats(current_user, pregnancy_case, active_baby, today)
    lmp = get_lmp_date(pregnancy_case) if pregnancy_case else None

    # 切換模式：預設若有 baby 則顯示 baby 模式，亦可透過 ?mode= 切換
    mode = request.GET.get('mode', 'baby' if active_baby else 'pregnancy')

    if mode == 'baby':
        baby_name = active_baby.name if active_baby else '寶寶'
        recap_title = f'{baby_name} 成長精華 🎓'
        recap_subtitle = '溫暖陪伴，見證小寶每一個珍貴的第一次！'
        hero_blessing = '🍼 「從發出第一個聲音，到跨出第一步，謝謝你平安健康長大。」'
        weight_title = '📈 寶寶成長曲線'
        mom_letter_title = '給爸媽的一段話'

        milestones = [
            {'title': '首次抬頭成功', 'week': '3 個月', 'icon': '👶'},
            {'title': '首次翻身成功', 'week': '6 個月', 'icon': '🤸'},
            {'title': '成功坐立拍手', 'week': '8 個月', 'icon': '🪑'},
            {'title': '一歲獨立站立', 'week': '12 個月', 'icon': '👟'},
            {'title': '兩歲自理如廁', 'week': '36 個月', 'icon': '🎓'},
        ]
    else:
        recap_title = '孕期畢業典禮 🎓'
        recap_subtitle = '40 週的陪伴，謝謝你的勇敢與堅強！'
        hero_blessing = '🌸 「感謝這 280 個日夜的堅持，每一刻的付出都凝聚成生命的奇蹟。」'
        weight_title = '📈 孕期體重變化'
        mom_letter_title = '給媽媽的一段話'

        milestones = [
            {'title': '第一次看到寶寶心跳', 'week': '8 週', 'icon': '❤️'},
            {'title': '第一次感受到胎動', 'week': '18 週', 'icon': '👶'},
            {'title': '高層次超音波過關', 'week': '20 週', 'icon': '✨'},
            {'title': '準備寶寶用品待產包', 'week': '34 週', 'icon': '🍼'},
            {'title': '小寶平安誕生', 'week': '40 週', 'icon': '🎓'},
        ]

    # 體重趨勢點 (取自 ORM，精準均勻採樣 5 個點配合 SVG 5節點版面)
    weight_points = []
    if mode == 'baby' and active_baby:
        b_records = list(
            BabyRecord.objects.filter(baby=active_baby, weight__isnull=False).order_by('date')
        )
        if b_records:
            indices = [0, len(b_records) // 4, len(b_records) // 2, (3 * len(b_records)) // 4, len(b_records) - 1]
            seen_idx = []
            for idx in indices:
                if idx not in seen_idx and idx < len(b_records):
                    seen_idx.append(idx)
            b_birth = (
                active_baby.birthdaytime.date()
                if hasattr(active_baby.birthdaytime, 'date')
                else active_baby.birthdaytime
            )
            for idx in seen_idx:
                br = b_records[idx]
                if b_birth and br.date:
                    months = (br.date.year - b_birth.year) * 12 + br.date.month - b_birth.month
                    if br.date.day < b_birth.day:
                        months -= 1
                    w_label = f'{max(0, months)}個月' if months > 0 else '出生'
                else:
                    w_label = br.date.strftime('%m/%d') if br.date else '紀錄'
                weight_points.append({
                    'week': w_label,
                    'val': float(br.weight)
                })
    else:
        target_uid = pregnancy_case.user_id if pregnancy_case else current_user.user_id
        weight_records = list(
            PregnancyRecord.objects.filter(user_id=target_uid, weight__isnull=False).order_by('check_date')
        )
        if weight_records:
            indices = [0, len(weight_records) // 4, len(weight_records) // 2, (3 * len(weight_records)) // 4, len(weight_records) - 1]
            seen_idx = []
            for idx in indices:
                if idx not in seen_idx and idx < len(weight_records):
                    seen_idx.append(idx)
            for idx in seen_idx:
                wr = weight_records[idx]
                w_label = wr.check_date.strftime('%m/%d') if wr.check_date else '紀錄'
                if lmp and wr.check_date:
                    d = (wr.check_date - lmp).days
                    if d >= 0:
                        w_label = f'{d // 7 + 1}週'
                weight_points.append({
                    'week': w_label,
                    'val': float(wr.weight)
                })

    if len(weight_points) < 2:
        if mode == 'baby':
            weight_points = [
                {'week': '出生', 'val': 3.2},
                {'week': '3個月', 'val': 6.4},
                {'week': '6個月', 'val': 8.2},
                {'week': '12個月', 'val': 10.2},
                {'week': '36個月', 'val': 15.1},
            ]
        else:
            weight_points = [
                {'week': '8週', 'val': 50.5},
                {'week': '16週', 'val': 52.5},
                {'week': '24週', 'val': 55.7},
                {'week': '32週', 'val': 59.2},
                {'week': '40週', 'val': 61.8},
            ]

    # 心情統計 (取自 ORM)
    target_uid = pregnancy_case.user_id if pregnancy_case else current_user.user_id
    feeling_counts = list(
        Userfeeling.objects.filter(
            pregnancyrecord__user_id=target_uid
        ).values('feeling__feeling_name').annotate(cnt=Count('feeling')).order_by('-cnt')
    )

    total_f = sum(item['cnt'] for item in feeling_counts)
    mood_colors = ['#8064A2', '#b2e4fb', '#f8bbd0', '#e3e3df']

    mood_distribution = []
    if total_f > 0:
        for idx, item in enumerate(feeling_counts[:4]):
            pct = round((item['cnt'] / total_f) * 100)
            mood_distribution.append({
                'name': item['feeling__feeling_name'] or '未知',
                'pct': pct,
                'color': mood_colors[idx % len(mood_colors)]
            })
    else:
        mood_distribution = [
            {'name': '幸福', 'pct': 45, 'color': '#8064A2'},
            {'name': '開心', 'pct': 30, 'color': '#b2e4fb'},
            {'name': '安心', 'pct': 15, 'color': '#f8bbd0'},
            {'name': '累', 'pct': 10, 'color': '#e3e3df'},
        ]

    # 手寫筆記 (取自 ORM)
    latest_note_rec = PregnancyRecord.objects.filter(
        user_id=target_uid, record__isnull=False
    ).exclude(record='').order_by('-check_date').first()

    mom_letter = latest_note_rec.record if latest_note_rec else ('這 365 天很辛苦，但看到孩子一天天長大，一切都值得。' if mode == 'baby' else '這 40 週很辛苦，但妳非常棒！謝謝妳的努力與堅持，期待我們一起陪寶寶長大的每一天。')

    context = {
        'current_user': current_user,
        'pregnancy_case': pregnancy_case,
        'active_baby': active_baby,
        'active_tab': 'recap',
        'mode': mode,
        'stats': stats,
        'milestones': milestones,
        'weight_points': weight_points,
        'mood_distribution': mood_distribution,
        'mom_letter': mom_letter,
        'recap_title': recap_title,
        'recap_subtitle': recap_subtitle,
        'hero_blessing': hero_blessing,
        'weight_title': weight_title,
        'mom_letter_title': mom_letter_title,
        'today_date_str': today.strftime('%Y/%m/%d'),
        'current_year': today.year,
    }
    return render(request, 'history/baby_growth.html', context)


def ai_growth_journey_view(request):
    """【階段/年度總結】別名路由"""
    return baby_growth_view(request)
