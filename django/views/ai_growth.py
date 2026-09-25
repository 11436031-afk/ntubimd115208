from django.shortcuts import render, redirect

from views.session_utils import get_current_user_profile


def ai_growth(request):
    """history/ai_growth.html 為純靜態的設計展示頁，內容是範例資料而非真實紀錄；
    模板頂端已加上明確橫幅告知使用者。這裡仍要求登入，避免未登入者直接開啟。"""
    if not get_current_user_profile(request):
        return redirect('login')
    return render(request, "history/ai_growth.html", {'is_demo_page': True})
