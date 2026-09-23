from django.shortcuts import render, redirect
from views.pregnancycase import baby_switcher, sync_active_selection_from_request
from views.session_utils import get_current_user_profile

# 注意：1/ai_growth.html、1/m.html、1/s.html 都是純靜態的設計展示頁，
# 內容為範例資料而非使用者的真實紀錄，模板頂端已加上明確橫幅告知使用者。


def _demo_page(request, template, tab):
    """三個設計展示頁共用：先檢查登入，再組切換器 context。"""
    user = get_current_user_profile(request)
    if not user:
        return redirect('login')
    sync_active_selection_from_request(request, user)
    context = {'active_v2_tab': tab, 'is_demo_page': True}
    context.update(baby_switcher(request))
    return render(request, template, context)


def ai_growth(request):
    return _demo_page(request, "1/ai_growth.html", '1')

def m(request):
    return _demo_page(request, "1/m.html", 'm')

def s(request):
    return _demo_page(request, "1/s.html", 's')


