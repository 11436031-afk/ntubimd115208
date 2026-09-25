from django.shortcuts import render, redirect
from django.utils import timezone
from core.models import BabyInformation, BabyGrowthMap, BabyStatus, BabyRecord, Prenatalrecord
from views import baby_utils
from views.session_utils import get_current_user_profile

def social_sharing_card_view(request):
    """圖文分享卡生成器頁面"""
    user = get_current_user_profile(request)
    if not user:
        return redirect('login')

    # 取得當前活躍寶寶
    baby = baby_utils.get_active_baby(request)
    
    # 預設值
    baby_name = "寶寶"
    baby_age_text = "3個月"
    baby_birthday = None
    
    if baby:
        baby_name = baby.name
        baby_birthday = baby.birthdaytime.date() if baby.birthdaytime else None
        
        # 計算年齡 / 月齡
        if baby_birthday:
            today = timezone.localdate()
            age_days = (today - baby_birthday).days
            if age_days < 0:
                baby_age_text = "尚未出生"
            else:
                months = int(age_days / 30.4375)
                days_rem = int(age_days % 30.4375)
                if months == 0:
                    baby_age_text = f"{age_days}天"
                elif days_rem == 0:
                    baby_age_text = f"{months}個月"
                else:
                    baby_age_text = f"{months}個月{days_rem}天"

    # 取得所有已達成的里程碑
    completed_milestones = []
    if baby:
        statuses = BabyStatus.objects.filter(babyrecord__baby=baby).select_related('babyrecord', 'babygrowthmap')
        for s in statuses:
            completed_milestones.append({
                'id': s.babygrowthmap.babygrowthmap_id,
                'name': s.babygrowthmap.growthrecord,
                'timecourse': s.babygrowthmap.timecourse,
                'date': s.babyrecord.date.strftime('%Y-%m-%d') if s.babyrecord.date else '',
                'photo': s.babyrecord.photo if s.babyrecord.photo else '',
                'record_text': s.babyrecord.record if s.babyrecord.record else '',
            })

    # 取得所有嬰幼兒紀錄與產檢紀錄中的相片
    record_photos = []
    if baby:
        # 1. 寶寶生長與里程碑紀錄相片
        baby_records = BabyRecord.objects.filter(baby=baby).exclude(photo__isnull=True).exclude(photo='').order_by('-date')
        for r in baby_records:
            status = BabyStatus.objects.filter(babyrecord=r).select_related('babygrowthmap').first()
            label = f"里程碑：{status.babygrowthmap.growthrecord}" if status else "成長紀錄"
            record_photos.append({
                'url': r.photo if r.photo else '',
                'label': label,
                'date': r.date.strftime('%Y-%m-%d'),
                'description': r.record or ''
            })
            
        # 2. 產檢紀錄相片 (超音波照)
        prenatal_records = Prenatalrecord.objects.filter(pregnancyrecord__user=user).exclude(photo__isnull=True).exclude(photo='').select_related('pregnancyrecord').order_by('-pregnancyrecord__check_date')
        for pr in prenatal_records:
            record_photos.append({
                'url': pr.photo if pr.photo else '',
                'label': "產檢超音波",
                'date': pr.pregnancyrecord.check_date.strftime('%Y-%m-%d'),
                'description': pr.pregnancyrecord.record or ''
            })

    context = {
        'baby': baby,
        'baby_name': baby_name,
        'baby_age_text': baby_age_text,
        'completed_milestones': completed_milestones,
        'record_photos': record_photos,
        'current_date': timezone.localdate().strftime('%Y-%m-%d'),
    }
    return render(request, 'user/social_sharing_card.html', context)


import os
import re
import base64
import binascii
import logging
import uuid
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST
from django.conf import settings

from views.upload_utils import InvalidImageError, validate_image_bytes

logger = logging.getLogger(__name__)

SHARING_CARD_DIR = 'sharing_cards'
# 分享頁的 filename 直接來自網址，一定要用白名單擋掉 ../ 之類的路徑穿越
SHARING_CARD_FILENAME_RE = re.compile(r'^card_[0-9a-f]{8,64}\.(?:png|jpg|gif|webp)$')


@require_POST
def upload_sharing_card(request):
    """將前端 Canvas 生成的圖卡照片上傳至伺服器媒體庫，並回傳專用分享頁面 URL 以供 LINE 分享與預覽"""
    # 這支 API 會把檔案寫進站內公開目錄，一定要登入才可以用
    user = get_current_user_profile(request)
    if not user:
        return JsonResponse({'status': 'error', 'message': '請先登入。'}, status=401)

    image_data = request.POST.get('image_data') or ''
    if not image_data.startswith('data:image') or ';base64,' not in image_data:
        return JsonResponse({'status': 'error', 'message': '無效的請求'}, status=400)

    _header, _sep, data = image_data.partition(';base64,')
    try:
        file_bytes = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return JsonResponse({'status': 'error', 'message': '圖片內容無法解碼。'}, status=400)

    # 副檔名一律由實際檔頭決定，不採用前端宣告的 mime type，
    # 避免 .html / .svg 被存到同源路徑造成儲存型 XSS
    try:
        extension = validate_image_bytes(file_bytes)
    except InvalidImageError as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    filename = f'card_{uuid.uuid4().hex}{extension}'
    media_dir = os.path.join(settings.MEDIA_ROOT, SHARING_CARD_DIR)

    try:
        os.makedirs(media_dir, exist_ok=True)
        with open(os.path.join(media_dir, filename), 'wb') as f:
            f.write(file_bytes)
    except OSError:
        logger.exception('寫入分享圖卡失敗')
        # 不要把 str(e) 回給前端，那會洩漏伺服器路徑
        return JsonResponse({'status': 'error', 'message': '圖卡儲存失敗，請稍後再試。'}, status=500)

    image_url = request.build_absolute_uri(f"{settings.MEDIA_URL}{SHARING_CARD_DIR}/{filename}")
    share_page_url = request.build_absolute_uri(f"/share_card/{filename}/")
    return JsonResponse({'status': 'success', 'image_url': image_url, 'share_page_url': share_page_url})


def share_card_detail_view(request, filename):
    """專用里程碑分享頁面，包含 OpenGraph 標籤讓 LINE 抓取圖片呈現預覽。

    這一頁維持公開（LINE 的爬蟲要能讀到 OpenGraph 標籤），
    但 filename 必須先過白名單，而且檔案不存在時回 404。
    """
    filename = os.path.basename(filename or '')
    if not SHARING_CARD_FILENAME_RE.match(filename):
        raise Http404('分享圖卡不存在')

    filepath = os.path.join(settings.MEDIA_ROOT, SHARING_CARD_DIR, filename)
    if not os.path.isfile(filepath):
        raise Http404('分享圖卡不存在')

    image_url = request.build_absolute_uri(f"{settings.MEDIA_URL}{SHARING_CARD_DIR}/{filename}")
    context = {
        'image_url': image_url,
        'filename': filename,
    }
    return render(request, 'user/share_card_detail.html', context)
